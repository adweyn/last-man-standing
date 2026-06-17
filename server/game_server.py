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
    for conn, client_info in tier_connections[tier_id].items():
        if client_info["player_id"] == player_id:
            client_info["is_alive"] = False
            target_conn = conn
            target_username = client_info["username"]
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
        "reason": reason
    }
    await broadcast_to_tier(tier_id, payload)

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

        # Disconnect clients in the room to force lobby reset
        to_disconnect = list(tier_connections[tier_id].keys())
        for conn in to_disconnect:
            try:
                await conn.close()
            except Exception:
                pass


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
            "is_alive": True
        }
        tier_connections[tier_id][websocket] = client_info
        logger.info(f"Player {username} (ID: {player_id}) connected to Tier {tier_id}")

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

            elif msg_type == "chat":
                chat_text = str(msg.get("message", ""))[:120].strip()
                # Sanitize: strip HTML tags and shell-like chars
                import re
                chat_text = re.sub(r'<[^>]*>', '', chat_text)
                chat_text = re.sub(r'[$(){}\\`|;&!#%^~]', '', chat_text).strip()
                if chat_text:
                    await broadcast_to_tier(tier_id, {
                        "type": "chat_message",
                        "username": username,
                        "message": chat_text
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

def get_obstacles() -> list[dict]:
    """Generates the same deterministic obstacles list as the web client."""
    obstacles = []
    for i in range(40):
        seed_val = math.sin(i * 927.32) * 1000
        ox = 150.0 + abs(seed_val % (WORLD_WIDTH - 300))
        oy = 150.0 + abs((seed_val * 1.5) % (WORLD_HEIGHT - 300))
        rad = 25.0 + abs((seed_val * 2.3) % 25)

        cx = WORLD_WIDTH / 2
        cy = WORLD_HEIGHT / 2
        dist = ((ox - cx) ** 2 + (oy - cy) ** 2) ** 0.5
        if dist > 200:
            obstacles.append({"x": ox, "y": oy, "rad": rad})
    return obstacles


async def spawn_crystal(tier_id: int, obstacles: list[dict]):
    """Finds a clean spot and spawns a collectible crystal in a tier room."""
    global crystal_id_counter
    for _ in range(10):  # 10 attempts to find clean coordinate
        x = float(random.randint(100, WORLD_WIDTH - 100))
        y = float(random.randint(100, WORLD_HEIGHT - 100))
        
        # Check collision with obstacles
        collides = False
        for obs in obstacles:
            dist = ((x - obs["x"]) ** 2 + (y - obs["y"]) ** 2) ** 0.5
            if dist < obs["rad"] + CRYSTAL_SPAWN_RADIUS + 10:
                collides = True
                break
        
        if not collides:
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


async def spawn_objective(tier_id: int, obstacles: list[dict]):
    """Spawn an interactive map objective that rewards movement and risk."""
    global objective_id_counter
    objective_types = ("scan", "relay", "cache")

    for _ in range(12):
        x = float(random.randint(140, WORLD_WIDTH - 140))
        y = float(random.randint(140, WORLD_HEIGHT - 140))

        collides = False
        for obs in obstacles:
            dist = ((x - obs["x"]) ** 2 + (y - obs["y"]) ** 2) ** 0.5
            if dist < obs["rad"] + MAP_OBJECTIVE_RADIUS + 20:
                collides = True
                break
        if collides:
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
