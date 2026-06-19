"""
game_server.py — Real-time WebSockets server managing gameplay loops,
player positions, movement validation, chat broadcasts, and win checks.
"""

import asyncio
import json
import logging
import time
import jwt
from typing import Dict, Any, Set, Optional

import websockets
from websockets.exceptions import ConnectionClosed

from config import (
    SERVER_HOST, WS_PORT, SECRET_KEY, JWT_ALGORITHM,
    WORLD_WIDTH, WORLD_HEIGHT, PLAYER_SPEED,
    PLAYER_LIST_INTERVAL, BOSS_UPDATE_INTERVAL,
    CRYSTAL_SPAWN_INTERVAL, CRYSTAL_MAX_PER_TIER,
    CRYSTAL_MIN_VALUE, CRYSTAL_MAX_VALUE, CRYSTAL_SPAWN_RADIUS,
    HAZARD_MAX_PER_TIER, HAZARD_TICK_INTERVAL, HAZARD_DAMAGE_STILL_SECONDS,
    MAP_OBJECTIVE_SPAWN_INTERVAL, MAP_OBJECTIVE_MAX_PER_TIER,
    MAP_OBJECTIVE_RADIUS, MAP_OBJECTIVE_MIN_REWARD, MAP_OBJECTIVE_MAX_REWARD
)
import database
import notifications

logger = logging.getLogger(__name__)

# Active client connections grouped by tier:
# { tier_id: { websocket_conn: { player_id, username, x, y, last_move_time, is_alive } } }
tier_connections: Dict[int, Dict[Any, Dict[str, Any]]] = {1: {}, 2: {}, 3: {}}

# Active crystals spawned on the map per tier:
# { tier_id: [ { id: int, x: float, y: float, value: float } ] }
tier_crystals: Dict[int, list] = {1: [], 2: [], 3: []}
crystal_id_counter = 0

# Active danger zones per tier:
# { tier_id: [ { id, x, y, radius, expires_at } ] }
tier_hazards: Dict[int, list] = {1: [], 2: [], 3: []}
hazard_id_counter = 0

# Active map objectives per tier:
# { tier_id: [ { id, type, x, y, radius, reward, expires_at } ] }
tier_objectives: Dict[int, list] = {1: [], 2: [], 3: []}
objective_id_counter = 0

# Persistent tombstones on the map per tier:
# { tier_id: [ { id, username, x, y, reason, time } ] }
tier_tombstones: Dict[int, list] = {1: [], 2: [], 3: []}

# Global reference to BossAI instances per tier. Set during startup.
boss_instances: Dict[int, Any] = {}


def get_tier_players_list(tier_id: int) -> list:
    """Callback for Boss AI to get real-time player positions for this tier."""
    return list(tier_connections.get(tier_id, {}).values())


async def kill_player_in_game(player_id: int, tier_id: int, reason: str = "boss"):
    """Callback for Boss/AFK Checker to eliminate a player."""
    # 1. Update database
    await database.eliminate_player(player_id, tier_id, reason)
    
    # 2. Update memory state & notify socket if online
    target_conn = None
    target_username = "Unknown"
    target_x = None
    target_y = None
    for conn, client_info in tier_connections[tier_id].items():
        if client_info["player_id"] == player_id:
            client_info["is_alive"] = False
            target_conn = conn
            target_username = client_info["username"]
            target_x = client_info.get("x")
            target_y = client_info.get("y")
            break

    # Send push notification if they have FCM token
    player_data = await database.get_player_by_id(player_id)
    if player_data and player_data.get("fcm_token"):
        asyncio.create_task(notifications.send_elimination(player_data["fcm_token"], reason))

    # Broadcast elimination message to tier
    payload = {
        "type": "player_eliminated",
        "player_id": player_id,
        "username": target_username,
        "reason": reason,
        "x": target_x,
        "y": target_y
    }
    await broadcast_to_tier(tier_id, payload)

    # Create persistent tombstone in memory for this tier
    tombstone = {
        "id": player_id,
        "username": target_username,
        "x": target_x if target_x is not None else (client_info["x"] if target_conn else 2000.0),
        "y": target_y if target_y is not None else (client_info["y"] if target_conn else 2000.0),
        "reason": reason,
        "time": time.time()
    }
    tier_tombstones[tier_id].append(tombstone)

    # Broadcast updated tombstones list to all clients in the tier
    await broadcast_to_tier(tier_id, {
        "type": "tombstone_list",
        "tombstones": tier_tombstones[tier_id]
    })

    # Disconnect or send dead state
    if target_conn:
        try:
            await target_conn.send(json.dumps({"type": "you_died", "reason": reason}))
        except Exception:
            pass

    # 3. Check win condition
    await check_win_condition(tier_id)


async def check_win_condition(tier_id: int):
    """If only 1 player remains in the tournament room (after at least 1 death or multiple players joined), they win."""
    alive_players = await database.get_alive_players_in_tier(tier_id)
    
    # Ensure there's a winner and it's not just 1 person starting a lobby
    # We query all sessions in this tier.
    stats = await database.get_tier_stats(tier_id)
    # If there is exactly 1 alive and total > 1, then the last survivor wins!
    if stats["alive"] == 1 and stats["total"] > 1:
        winner = alive_players[0]
        winner_id = winner["id"]
        winner_username = winner["username"]
        prize_pool = stats["prize_pool"]

        logger.info(f"🏆 Player {winner_username} (ID: {winner_id}) WINS TIER {tier_id}! Prize: ${prize_pool:.2f}")

        # Add prize to balance
        await database.update_balance(winner_id, prize_pool)
        # End tier session as victory
        async with database.aiosqlite.connect(database.DATABASE_URL) as db:
            await db.execute(
                "UPDATE tier_sessions SET is_alive=0, eliminated_by='victory' WHERE player_id=? AND tier_id=? AND is_alive=1",
                (winner_id, tier_id)
            )
            await db.commit()

        # Send victory push
        if winner.get("fcm_token"):
            asyncio.create_task(notifications.send_victory(winner["fcm_token"], prize_pool, tier_id))

        # Broadcast win to tier
        await broadcast_to_tier(tier_id, {
            "type": "game_won",
            "winner_id": winner_id,
            "username": winner_username,
            "prize": prize_pool
        })

        # Reset tombstones for the next round
        tier_tombstones[tier_id] = []

        # Start a round reset task instead of disconnecting everyone!
        asyncio.create_task(reset_round_after_delay(tier_id))


async def reset_round_after_delay(tier_id: int, delay: float = 5.0):
    """Resets the round for all players in a tier after a short victory celebration."""
    await asyncio.sleep(delay)
    
    import random
    obstacles = get_obstacles()
    
    async with database.aiosqlite.connect(database.DATABASE_URL) as db:
        for conn, client_info in list(tier_connections[tier_id].items()):
            player_id = client_info["player_id"]
            
            # Spawn at a new random position
            spawn_x = float(random.randint(500, WORLD_WIDTH - 500))
            spawn_y = float(random.randint(500, WORLD_HEIGHT - 500))
            
            client_info["is_alive"] = True
            client_info["hp"] = PLAYER_MAX_HP
            client_info["x"] = spawn_x
            client_info["y"] = spawn_y
            client_info["last_move_time"] = time.time()
            
            # Reset active session in DB to make sure they are alive
            await db.execute(
                "UPDATE tier_sessions SET is_alive=1, entry_time=unixepoch() WHERE player_id=? AND tier_id=?",
                (player_id, tier_id)
            )
            
            # Send snapback/reset to client
            try:
                await conn.send(json.dumps({
                    "type": "snap_back",
                    "x": spawn_x,
                    "y": spawn_y
                }))
            except Exception:
                pass
        await db.commit()
        
    # Reset tombstones
    tier_tombstones[tier_id] = []
    
    # Clear and respawn crystals
    tier_crystals[tier_id] = []
    for _ in range(5):
        await spawn_crystal(tier_id, obstacles)
        
    # Broadcast round start and new crystals
    await broadcast_to_tier(tier_id, {
        "type": "round_start"
    })
    await broadcast_to_tier(tier_id, {
        "type": "crystal_list",
        "crystals": tier_crystals[tier_id]
    })


async def broadcast_to_tier(tier_id: int, message: dict):
    """Broadcast JSON message to all connected clients in a tier."""
    if not tier_connections[tier_id]:
        return
    raw = json.dumps(message)
    conns = list(tier_connections[tier_id].keys())
    await asyncio.gather(
        *(conn.send(raw) for conn in conns),
        return_exceptions=True
    )


async def handle_connection(websocket, path=None):
    """Client websocket handler."""
    player_id: Optional[int] = None
    tier_id: Optional[int] = None
    username: str = ""

    try:
        # 1. AUTHENTICATION
        auth_msg = await websocket.recv()
        try:
            data = json.loads(auth_msg)
            if data.get("type") != "auth":
                await websocket.send(json.dumps({"type": "error", "message": "Expected auth message first"}))
                await websocket.close()
                return
            
            token = data.get("token")
            payload = jwt.decode(token, SECRET_KEY, algorithms=[JWT_ALGORITHM])
            player_id = int(payload["sub"])
            username = payload.get("username", f"Player_{player_id}")
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, ValueError, KeyError, json.JSONDecodeError) as e:
            await websocket.send(json.dumps({"type": "error", "message": f"Auth failed: {str(e)}"}))
            await websocket.close()
            return

        # 2. VALIDATE ACTIVE TIER SESSION
        # Find which tier this player is alive in
        active_session = None
        async with database.aiosqlite.connect(database.DATABASE_URL) as db:
            db.row_factory = database.aiosqlite.Row
            async with db.execute(
                "SELECT * FROM tier_sessions WHERE player_id=? AND is_alive=1", (player_id,)
            ) as cur:
                active_session = await cur.fetchone()

        if not active_session:
            await websocket.send(json.dumps({"type": "error", "message": "No active alive tier session found. Join a tier via API first."}))
            await websocket.close()
            return

        tier_id = active_session["tier_id"]
        
        # Check if already connected (kick previous session)
        for existing_conn, client in list(tier_connections[tier_id].items()):
            if client["player_id"] == player_id:
                try:
                    await existing_conn.send(json.dumps({"type": "kicked", "message": "Logged in from another location"}))
                    await existing_conn.close()
                except Exception:
                    pass
                tier_connections[tier_id].pop(existing_conn, None)

        # 3. JOIN TIER ROOM
        import random
        # Spawn position: try to load last activity position, otherwise spawn randomly
        last_act = None
        async with database.aiosqlite.connect(database.DATABASE_URL) as db:
            db.row_factory = database.aiosqlite.Row
            async with db.execute(
                "SELECT * FROM daily_activity WHERE player_id=? ORDER BY date DESC LIMIT 1",
                (player_id,)
            ) as cur:
                last_act = await cur.fetchone()

        spawn_x = last_act["last_move_x"] if last_act else float(random.randint(100, WORLD_WIDTH - 100))
        spawn_y = last_act["last_move_y"] if last_act else float(random.randint(100, WORLD_HEIGHT - 100))

        client_info = {
            "player_id": player_id,
            "username": username,
            "x": spawn_x,
            "y": spawn_y,
            "last_move_time": time.time(),
            "last_attack_time": 0.0,
            "hp": PLAYER_MAX_HP,
            "max_hp": PLAYER_MAX_HP,
            "is_alive": True
        }
        tier_connections[tier_id][websocket] = client_info
        logger.info(f"Player {username} (ID: {player_id}) connected to Tier {tier_id}")

        obstacles = get_obstacles()
        await ensure_initial_map_content(tier_id, obstacles, spawn_x, spawn_y)

        # Send welcome/handshake message
        await websocket.send(json.dumps({
            "type": "welcome",
            "player_id": player_id,
            "tier_id": tier_id,
            "spawn_x": spawn_x,
            "spawn_y": spawn_y,
            "world_width": WORLD_WIDTH,
            "world_height": WORLD_HEIGHT
        }))

        # Send active crystals list to new player
        await websocket.send(json.dumps({
            "type": "crystal_list",
            "crystals": tier_crystals[tier_id]
        }))

        await websocket.send(json.dumps({
            "type": "hazard_list",
            "hazards": tier_hazards[tier_id]
        }))

        await websocket.send(json.dumps({
            "type": "objective_list",
            "objectives": tier_objectives[tier_id]
        }))

        await websocket.send(json.dumps({
            "type": "death_trap_list",
            "traps": get_death_traps()
        }))

        await websocket.send(json.dumps({
            "type": "tombstone_list",
            "tombstones": tier_tombstones[tier_id]
        }))

        # 4. RECEIVE LOOP
        async for msg_str in websocket:
            try:
                msg = json.loads(msg_str)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")

            if msg_type == "move":
                if not client_info["is_alive"]:
                    continue

                new_x = float(msg.get("x", spawn_x))
                new_y = float(msg.get("y", spawn_y))

                # Bounds checking
                new_x = max(10.0, min(new_x, float(WORLD_WIDTH - 10)))
                new_y = max(10.0, min(new_y, float(WORLD_HEIGHT - 10)))

                now = time.time()
                dt = now - client_info["last_move_time"]

                # Speedhack prevention
                if dt > 0.05:  # small cooldown
                    dx = new_x - client_info["x"]
                    dy = new_y - client_info["y"]
                    dist = (dx*dx + dy*dy) ** 0.5
                    max_allowed = PLAYER_SPEED * dt * 1.5  # 1.5x buffer for latency / delta spikes
                    
                    if dist > max_allowed and dist > 150: # don't punish tiny latency jumps
                        logger.warning(f"Speed validation failed for player {username}. Dist={dist:.1f}, MaxAllowed={max_allowed:.1f}")
                        # Snapped back to original position
                        await websocket.send(json.dumps({
                            "type": "snap_back",
                            "x": client_info["x"],
                            "y": client_info["y"]
                        }))
                        continue

                # Update memory
                dx_actual = new_x - client_info["x"]
                dy_actual = new_y - client_info["y"]
                dist_actual = (dx_actual * dx_actual + dy_actual * dy_actual) ** 0.5

                client_info["x"] = new_x
                client_info["y"] = new_y
                client_info["last_move_time"] = now

                # Log to daily_activity DB
                await database.log_movement(player_id, new_x, new_y)

                # Increment explorer daily quest progress (distance traveled)
                if dist_actual > 0:
                    asyncio.create_task(database.increment_quest_progress(player_id, "explorer", dist_actual))

                # Permanent lethal caves/pits. Server-side so clients cannot ignore them.
                for trap in get_death_traps():
                    t_dist = ((new_x - trap["x"]) ** 2 + (new_y - trap["y"]) ** 2) ** 0.5
                    if t_dist < trap["radius"] + 8:
                        await kill_player_in_game(player_id, tier_id, trap["type"])
                        break

                if not client_info["is_alive"]:
                    continue

                # Check collision with crystals
                collected_crystals = []
                for crystal in tier_crystals[tier_id]:
                    # Check collision (player radius 12 + crystal radius 15 = 27px)
                    c_dist = ((new_x - crystal["x"]) ** 2 + (new_y - crystal["y"]) ** 2) ** 0.5
                    if c_dist < 24.0:  # slightly smaller than theoretical for better visual feel
                        collected_crystals.append(crystal)
                
                for crystal in collected_crystals:
                    tier_crystals[tier_id].remove(crystal)
                    # Award balance
                    await database.update_balance(player_id, crystal["value"])
                    # Increment scavenger daily quest progress
                    await database.increment_quest_progress(player_id, "scavenger", 1.0)
                    # Broadcast collection event to room
                    await broadcast_to_tier(tier_id, {
                        "type": "crystal_collected",
                        "crystal_id": crystal["id"],
                        "player_id": player_id,
                        "username": username,
                        "value": crystal["value"]
                    })
                    # Broadcast updated crystal list
                    await broadcast_to_tier(tier_id, {
                        "type": "crystal_list",
                        "crystals": tier_crystals[tier_id]
                    })

                if collected_crystals and len(tier_crystals[tier_id]) < 2:
                    await spawn_crystal(tier_id, get_obstacles(), near=(new_x, new_y))

                completed_objectives = []
                for objective in tier_objectives[tier_id]:
                    o_dist = ((new_x - objective["x"]) ** 2 + (new_y - objective["y"]) ** 2) ** 0.5
                    if o_dist < objective["radius"] + 12:
                        completed_objectives.append(objective)

                for objective in completed_objectives:
                    if objective not in tier_objectives[tier_id]:
                        continue
                    tier_objectives[tier_id].remove(objective)
                    await database.update_balance(player_id, objective["reward"])

                    if objective["type"] == "scan":
                        await database.increment_quest_progress(player_id, "explorer", 800.0)
                    elif objective["type"] == "cache":
                        await database.increment_quest_progress(player_id, "scavenger", 2.0)
                    elif objective["type"] == "relay":
                        await database.increment_quest_progress(player_id, "survivor", 25.0)

                    await broadcast_to_tier(tier_id, {
                        "type": "objective_completed",
                        "objective_id": objective["id"],
                        "objective_type": objective["type"],
                        "player_id": player_id,
                        "username": username,
                        "reward": objective["reward"]
                    })
                    await broadcast_to_tier(tier_id, {
                        "type": "objective_list",
                        "objectives": tier_objectives[tier_id]
                    })

                    if len(tier_objectives[tier_id]) < 3:
                        await spawn_objective(tier_id, get_obstacles(), near=(new_x, new_y))

            elif msg_type == "attack":
                if not client_info["is_alive"]:
                    continue

                now = time.time()
                if now - client_info.get("last_attack_time", 0.0) < PLAYER_ATTACK_COOLDOWN:
                    continue
                client_info["last_attack_time"] = now

                target_conn = None
                target_info = None
                best_dist = PLAYER_ATTACK_RANGE
                for other_conn, other in tier_connections[tier_id].items():
                    if other_conn == websocket or not other.get("is_alive"):
                        continue
                    dist = ((client_info["x"] - other["x"]) ** 2 + (client_info["y"] - other["y"]) ** 2) ** 0.5
                    if dist <= best_dist:
                        target_conn = other_conn
                        target_info = other
                        best_dist = dist

                if not target_info:
                    await websocket.send(json.dumps({
                        "type": "attack_missed",
                        "cooldown": PLAYER_ATTACK_COOLDOWN
                    }))
                    continue

                target_info["hp"] = max(0, int(target_info.get("hp", PLAYER_MAX_HP)) - 1)
                await broadcast_to_tier(tier_id, {
                    "type": "combat_hit",
                    "attacker_id": player_id,
                    "attacker_username": username,
                    "target_id": target_info["player_id"],
                    "target_username": target_info["username"],
                    "target_hp": target_info["hp"],
                    "max_hp": PLAYER_MAX_HP,
                    "x": target_info["x"],
                    "y": target_info["y"],
                    "damage": 1
                })

                if target_info["hp"] <= 0:
                    await database.update_balance(player_id, PLAYER_ATTACK_REWARD)
                    await broadcast_to_tier(tier_id, {
                        "type": "pvp_reward",
                        "player_id": player_id,
                        "username": username,
                        "value": PLAYER_ATTACK_REWARD
                    })
                    await kill_player_in_game(target_info["player_id"], tier_id, "pvp")

            elif msg_type == "chat":
                chat_text = str(msg.get("message", ""))[:120].strip()
                # Sanitize: strip HTML tags and shell-like chars
                import re
                chat_text = re.sub(r'<[^>]*>', '', chat_text)
                chat_text = re.sub(r'[$(){}\\`|;&!#%^~]', '', chat_text).strip()
                if chat_text:
                    await broadcast_to_tier(tier_id, {
                        "type": "chat_message",
                        "player_id": player_id,
                        "username": username,
                        "message": chat_text,
                        "x": client_info["x"],
                        "y": client_info["y"]
                    })

            elif msg_type == "respawn":
                import random
                # Reset health and mark alive
                client_info["is_alive"] = True
                client_info["hp"] = PLAYER_MAX_HP
                # Set a new random position
                client_info["x"] = float(random.randint(500, WORLD_WIDTH - 500))
                client_info["y"] = float(random.randint(500, WORLD_HEIGHT - 500))
                client_info["last_move_time"] = time.time()
                
                # Update database to mark tier session is_alive=1 again
                async with database.aiosqlite.connect(database.DATABASE_URL) as db:
                    await db.execute(
                        "UPDATE tier_sessions SET is_alive=1 WHERE player_id=? AND tier_id=? AND is_alive=0",
                        (player_id, tier_id)
                    )
                    await db.commit()
                
                # Send snapback/reset to this player
                await websocket.send(json.dumps({
                    "type": "snap_back",
                    "x": client_info["x"],
                    "y": client_info["y"]
                }))
                
                # Broadcast player respawn update to all tier players
                await broadcast_to_tier(tier_id, {
                    "type": "player_respawned",
                    "player_id": player_id,
                    "username": username,
                    "x": client_info["x"],
                    "y": client_info["y"]
                })

            elif msg_type == "ping":
                await websocket.send(json.dumps({"type": "pong"}))

    except ConnectionClosed:
        pass
    except Exception as e:
        logger.error(f"Error handling connection: {e}", exc_info=True)
    finally:
        # Cleanup connection
        if tier_id and websocket in tier_connections[tier_id]:
            tier_connections[tier_id].pop(websocket, None)
            logger.info(f"Player {username} (ID: {player_id}) disconnected from Tier {tier_id}")


async def player_broadcast_loop():
    """Periodically broadcasts lists of alive player locations."""
    while True:
        try:
            for tier_id in [1, 2, 3]:
                if not tier_connections[tier_id]:
                    continue
                players_list = []
                for client in tier_connections[tier_id].values():
                    players_list.append({
                        "id": client["player_id"],
                        "username": client["username"],
                        "x": client["x"],
                        "y": client["y"],
                        "hp": client.get("hp", PLAYER_MAX_HP),
                        "max_hp": client.get("max_hp", PLAYER_MAX_HP),
                        "is_alive": client["is_alive"]
                    })
                await broadcast_to_tier(tier_id, {
                    "type": "player_list",
                    "players": players_list
                })
            await asyncio.sleep(PLAYER_LIST_INTERVAL)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in player broadcast loop: {e}", exc_info=True)


async def boss_broadcast_loop():
    """Periodically broadcasts Boss locations and timers to active game rooms."""
    while True:
        try:
            for tier_id in [1, 2, 3]:
                boss = boss_instances.get(tier_id)
                if boss and boss.state != "sleeping":
                    await broadcast_to_tier(tier_id, {
                        "type": "boss_update",
                        "boss": boss.get_state()
                    })
            await asyncio.sleep(BOSS_UPDATE_INTERVAL)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in boss broadcast loop: {e}", exc_info=True)


async def start_websocket_server():
    """Starts ws server."""
    async with websockets.serve(handle_connection, SERVER_HOST, WS_PORT):
        logger.info(f"WebSocket Game Server running on ws://{SERVER_HOST}:{WS_PORT}")
        # Run local loops
        await asyncio.gather(
            player_broadcast_loop(),
            boss_broadcast_loop(),
            crystal_spawner_loop(),
            objective_spawner_loop(),
            hazard_director_loop(),
            quest_survivor_loop()
        )


import math
import random

PLAYER_MAX_HP = 3
PLAYER_ATTACK_RANGE = 52.0
PLAYER_ATTACK_COOLDOWN = 0.9
PLAYER_ATTACK_REWARD = 0.35

def get_obstacles() -> list[dict]:
    """Generates the same deterministic obstacles list as the web client."""
    obstacles = []
    for i in range(44):
        seed_a = (math.sin(i * 127.13 + 4.7) * 43758.5453) % 1
        seed_b = (math.sin(i * 311.91 + 9.2) * 24634.6345) % 1
        seed_c = (math.sin(i * 719.17 + 1.9) * 13579.2468) % 1
        ox = 160.0 + seed_a * (WORLD_WIDTH - 320)
        oy = 160.0 + seed_b * (WORLD_HEIGHT - 320)
        rad = 14.0 + seed_c * 18

        cx = WORLD_WIDTH / 2
        cy = WORLD_HEIGHT / 2
        dist = ((ox - cx) ** 2 + (oy - cy) ** 2) ** 0.5
        in_spawn_lane = abs(ox - cx) < 260 and abs(oy - cy) < 780
        if dist > 280 and not in_spawn_lane:
            obstacles.append({"x": ox, "y": oy, "rad": rad})
    return obstacles


def get_death_traps() -> list[dict]:
    """Deterministic lethal caves/pits shared by every client."""
    traps = []
    for i in range(9):
        seed_a = (math.sin(i * 211.37 + 12.4) * 30123.817) % 1
        seed_b = (math.sin(i * 97.81 + 33.2) * 18291.441) % 1
        seed_c = (math.sin(i * 404.19 + 7.8) * 9911.27) % 1
        x = 180.0 + seed_a * (WORLD_WIDTH - 360)
        y = 180.0 + seed_b * (WORLD_HEIGHT - 360)
        radius = 34.0 + seed_c * 22
        if not isInsideSpawnLane_py(x, y):
            traps.append({
                "id": i + 1,
                "type": "cave" if i % 2 else "pit",
                "x": x,
                "y": y,
                "radius": radius,
            })
    return traps


def isInsideSpawnLane_py(x: float, y: float) -> bool:
    dx = abs(x - WORLD_WIDTH / 2)
    dy = abs(y - WORLD_HEIGHT / 2)
    return dx < 260 and dy < 780


def is_clean_map_spot(x: float, y: float, radius: float, obstacles: list[dict]) -> bool:
    for obs in obstacles:
        dist = ((x - obs["x"]) ** 2 + (y - obs["y"]) ** 2) ** 0.5
        if dist < obs["rad"] + radius + 20:
            return False
    return True


def nearby_point(origin_x: float, origin_y: float, min_dist: int = 180, max_dist: int = 420) -> tuple[float, float]:
    angle = random.random() * math.pi * 2
    dist = random.randint(min_dist, max_dist)
    x = max(100.0, min(float(WORLD_WIDTH - 100), origin_x + math.cos(angle) * dist))
    y = max(100.0, min(float(WORLD_HEIGHT - 100), origin_y + math.sin(angle) * dist))
    return x, y


async def spawn_crystal(tier_id: int, obstacles: list[dict], near: Optional[tuple[float, float]] = None):
    """Finds a clean spot and spawns a collectible crystal in a tier room."""
    global crystal_id_counter
    for _ in range(10):  # 10 attempts to find clean coordinate
        if near:
            x, y = nearby_point(near[0], near[1], 140, 320)
        else:
            x = float(random.randint(100, WORLD_WIDTH - 100))
            y = float(random.randint(100, WORLD_HEIGHT - 100))
        
        if is_clean_map_spot(x, y, CRYSTAL_SPAWN_RADIUS, obstacles):
            crystal_id_counter += 1
            val = round(random.uniform(CRYSTAL_MIN_VALUE, CRYSTAL_MAX_VALUE), 2)
            crystal = {
                "id": crystal_id_counter,
                "x": x,
                "y": y,
                "value": val
            }
            tier_crystals[tier_id].append(crystal)
            
            # Broadcast the updated list
            await broadcast_to_tier(tier_id, {
                "type": "crystal_list",
                "crystals": tier_crystals[tier_id]
            })
            break


async def crystal_spawner_loop():
    """Periodically spawns crystals in active rooms."""
    obstacles = get_obstacles()
    while True:
        try:
            await asyncio.sleep(CRYSTAL_SPAWN_INTERVAL)
            for tier_id in [1, 2, 3]:
                if not tier_connections[tier_id]:
                    if tier_crystals[tier_id]:
                        tier_crystals[tier_id] = []
                    continue
                
                if len(tier_crystals[tier_id]) < CRYSTAL_MAX_PER_TIER:
                    await spawn_crystal(tier_id, obstacles)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in crystal spawner loop: {e}", exc_info=True)


async def spawn_objective(tier_id: int, obstacles: list[dict], near: Optional[tuple[float, float]] = None):
    """Spawn an interactive map objective that rewards movement and risk."""
    global objective_id_counter
    objective_types = ("scan", "relay", "cache")

    for _ in range(12):
        if near:
            x, y = nearby_point(near[0], near[1])
        else:
            x = float(random.randint(140, WORLD_WIDTH - 140))
            y = float(random.randint(140, WORLD_HEIGHT - 140))

        if not is_clean_map_spot(x, y, MAP_OBJECTIVE_RADIUS, obstacles):
            continue

        objective_id_counter += 1
        obj_type = random.choice(objective_types)
        reward = round(random.uniform(MAP_OBJECTIVE_MIN_REWARD, MAP_OBJECTIVE_MAX_REWARD), 2)
        objective = {
            "id": objective_id_counter,
            "type": obj_type,
            "x": x,
            "y": y,
            "radius": float(MAP_OBJECTIVE_RADIUS),
            "reward": reward,
            "expires_at": time.time() + random.uniform(70, 140),
        }
        tier_objectives[tier_id].append(objective)
        await broadcast_to_tier(tier_id, {
            "type": "objective_list",
            "objectives": tier_objectives[tier_id]
        })
        break


async def ensure_initial_map_content(tier_id: int, obstacles: list[dict], spawn_x: float, spawn_y: float):
    """Make a newly joined room immediately show nearby goals and rewards."""
    if len(tier_crystals[tier_id]) < 2:
        await spawn_crystal(tier_id, obstacles, near=(spawn_x, spawn_y))
    while len(tier_objectives[tier_id]) < 3:
        before = len(tier_objectives[tier_id])
        await spawn_objective(tier_id, obstacles, near=(spawn_x, spawn_y))
        if len(tier_objectives[tier_id]) == before:
            break


async def objective_spawner_loop():
    """Periodically creates visible quest points in active rooms."""
    obstacles = get_obstacles()
    while True:
        try:
            await asyncio.sleep(MAP_OBJECTIVE_SPAWN_INTERVAL)
            now = time.time()
            for tier_id in [1, 2, 3]:
                if not tier_connections[tier_id]:
                    if tier_objectives[tier_id]:
                        tier_objectives[tier_id] = []
                    continue

                before = len(tier_objectives[tier_id])
                tier_objectives[tier_id] = [o for o in tier_objectives[tier_id] if o["expires_at"] > now]
                if before != len(tier_objectives[tier_id]):
                    await broadcast_to_tier(tier_id, {
                        "type": "objective_list",
                        "objectives": tier_objectives[tier_id]
                    })

                if len(tier_objectives[tier_id]) < MAP_OBJECTIVE_MAX_PER_TIER:
                    await spawn_objective(tier_id, obstacles)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in objective spawner loop: {e}", exc_info=True)


async def quest_survivor_loop():
    """Periodically increments survivor quest progress for active, alive players."""
    while True:
        try:
            await asyncio.sleep(5)
            for tier_id in [1, 2, 3]:
                for client in list(tier_connections[tier_id].values()):
                    if client["is_alive"]:
                        asyncio.create_task(
                            database.increment_quest_progress(client["player_id"], "survivor", 5.0)
                        )
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in quest survivor loop: {e}", exc_info=True)


async def hazard_director_loop():
    """Spawns and enforces temporary danger zones during hard boss hunts."""
    global hazard_id_counter
    while True:
        try:
            await asyncio.sleep(HAZARD_TICK_INTERVAL)
            now = time.time()
            for tier_id in [1, 2, 3]:
                boss = boss_instances.get(tier_id)
                difficulty = int(getattr(boss, "difficulty_level", 0) or 0) if boss else 0
                active_hunt = bool(boss and boss.state == "hunting")

                before_count = len(tier_hazards[tier_id])
                tier_hazards[tier_id] = [h for h in tier_hazards[tier_id] if h["expires_at"] > now]
                changed = before_count != len(tier_hazards[tier_id])

                if active_hunt and difficulty >= 2 and tier_connections[tier_id]:
                    target_count = min(HAZARD_MAX_PER_TIER, 1 + difficulty)
                    if len(tier_hazards[tier_id]) < target_count and random.random() < 0.45:
                        hazard_id_counter += 1
                        tier_hazards[tier_id].append({
                            "id": hazard_id_counter,
                            "x": float(random.randint(120, WORLD_WIDTH - 120)),
                            "y": float(random.randint(120, WORLD_HEIGHT - 120)),
                            "radius": float(70 + difficulty * 12),
                            "expires_at": now + random.uniform(12, 24),
                        })
                        changed = True

                    for client in list(tier_connections[tier_id].values()):
                        if not client["is_alive"]:
                            continue
                        if now - client.get("last_move_time", 0) < HAZARD_DAMAGE_STILL_SECONDS:
                            continue
                        for hazard in tier_hazards[tier_id]:
                            dist = ((client["x"] - hazard["x"]) ** 2 + (client["y"] - hazard["y"]) ** 2) ** 0.5
                            if dist < hazard["radius"]:
                                await kill_player_in_game(client["player_id"], tier_id, "hazard")
                                break

                elif not active_hunt and tier_hazards[tier_id]:
                    tier_hazards[tier_id] = []
                    changed = True

                if changed:
                    await broadcast_to_tier(tier_id, {
                        "type": "hazard_list",
                        "hazards": tier_hazards[tier_id]
                    })
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in hazard director loop: {e}", exc_info=True)
