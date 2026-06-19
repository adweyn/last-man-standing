"""
game.py — Active gameplay arena screen.
Features smooth camera interpolation, movement broadcasts, particle engines,
shake alerts, in-game chat, radar systems, and victory/defeat screens.
"""

import pygame
import time
import math
import random
from typing import Dict, Any, List, Optional, Tuple

from config import (
    COLORS, SCREEN_WIDTH, SCREEN_HEIGHT, WORLD_WIDTH, WORLD_HEIGHT,
    PLAYER_RADIUS, BOSS_RADIUS, CAMERA_SPEED, TIER_INFO,
    SCREEN_SHAKE_FRAMES, SCREEN_SHAKE_MAGNITUDE, PARTICLE_POOL_MAX
)
from assets import (
    draw_player, draw_boss,
    generate_particle, update_particles, draw_particles,
    draw_boss_warning_overlay, draw_minimap,
    create_cyber_floor_tile
)
from network import NetworkManager
from hud import HUD
from chat import ChatSystem
from sound import play_sound


class GameplayScreen:
    def __init__(self, screen: pygame.Surface, network: NetworkManager, tier_id: int):
        self.screen = screen
        self.network = network
        self.tier_id = tier_id
        
        self.width, self.height = screen.get_size()

        # Engine fonts
        self.giant_font = pygame.font.SysFont("Outfit", 90, bold=True)
        self.large_font = pygame.font.SysFont("Outfit", 32, bold=True)
        self.medium_font = pygame.font.SysFont("Inter", 20)

        # Core Game State
        self.self_player_id: Optional[int] = None
        self.self_x = float(WORLD_WIDTH // 2)
        self.self_y = float(WORLD_HEIGHT // 2)
        
        # Dictionary of players: {id: {username, x, y, is_alive}}
        self.players: Dict[int, Dict[str, Any]] = {}
        self.boss_state: Dict[str, Any] = {"state": "sleeping", "x": 0.0, "y": 0.0, "time_remaining": 0}
        self.crystals: List[Dict[str, Any]] = []
        self.hazards: List[Dict[str, Any]] = []
        self.objectives: List[Dict[str, Any]] = []
        self.death_traps: List[Dict[str, Any]] = []
        self.death_markers: List[Dict[str, Any]] = []
        self.speech_bubbles: List[Dict[str, Any]] = []
        self.npcs: List[Dict[str, Any]] = []

        # Subsystems
        self.hud = HUD()
        self.chat = ChatSystem()
        
        # Camera offsets
        self.camera_x = self.self_x
        self.camera_y = self.self_y

        # Graphics / Animations
        self.particles: List[Dict[str, Any]] = []
        self.shake_remaining_frames = 0
        
        # Pre-allocated overlay surface for death/victory fades to avoid per-frame allocations
        self.overlay_surf = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        self.boss_overlay_alpha = 0.0
        self.cyber_floor_tile = create_cyber_floor_tile()

        # Local movement parameters
        self.move_speed = 160.0  # px/s
        self.last_network_update = 0.0
        self.net_update_rate = 1.0 / 30.0  # 30 Hz position update rate
        self.last_attack_time = 0.0
        self.attack_cooldown = 0.9

        # Play status: "playing" | "game_over" | "victory"
        self.status = "playing"
        self.status_time = 0.0
        self.status_reason = ""
        self.prize_won = 0.0
        
        self.is_connected = True

        # Cached HUD data (updated in background, never in draw)
        self.cached_prize_pool = 0.0
        self.cached_daily_moves = 0
        self._last_hud_fetch = 0.0
        self._hud_fetch_interval = 5.0  # seconds between REST fetches

        # Procedural obstacles (deterministic generation based on tier)
        self.obstacles: List[Tuple[float, float, float]] = []  # x, y, radius
        self.districts: List[Dict[str, Any]] = []
        self._generate_obstacles()

        # Setup callbacks
        self._register_network_callbacks()

    def _generate_obstacles(self):
        """Generates visual ruins and district routes to hide behind."""
        random.seed(self.tier_id * 100)
        self.districts = [
            {"x": 700, "y": 820, "w": 760, "h": 420, "label": "RUINS", "kind": "ruins"},
            {"x": 2450, "y": 520, "w": 880, "h": 520, "label": "SIGNAL FIELD", "kind": "signal"},
            {"x": 530, "y": 2520, "w": 940, "h": 620, "label": "BLACK FOG", "kind": "fog"},
            {"x": 2320, "y": 2460, "w": 1060, "h": 760, "label": "VAULT WARD", "kind": "vault"},
            {"x": 1650, "y": 1540, "w": 760, "h": 720, "label": "DEAD CENTER", "kind": "center"},
        ]
        for _ in range(44):
            ox = random.uniform(100, WORLD_WIDTH - 100)
            oy = random.uniform(100, WORLD_HEIGHT - 100)
            rad = random.uniform(16, 34)
            # Ensure not spawning directly in the center spawning room
            cx, cy = WORLD_WIDTH // 2, WORLD_HEIGHT // 2
            if ((ox - cx)**2 + (oy - cy)**2) ** 0.5 > 200:
                self.obstacles.append((ox, oy, rad))

        # Generate Trees deterministically
        self.trees = []
        random.seed(self.tier_id * 200)
        for _ in range(35):
            tx = random.uniform(200, WORLD_WIDTH - 200)
            ty = random.uniform(200, WORLD_HEIGHT - 200)
            trunk_rad = random.uniform(8, 12)
            canopy_rad = random.uniform(26, 40)
            cx, cy = WORLD_WIDTH // 2, WORLD_HEIGHT // 2
            if ((tx - cx)**2 + (ty - cy)**2) ** 0.5 > 300:
                self.trees.append({"x": tx, "y": ty, "trunk_rad": trunk_rad, "canopy_rad": canopy_rad})

        # Generate Bushes deterministically
        self.bushes = []
        random.seed(self.tier_id * 300)
        for _ in range(30):
            bx = random.uniform(200, WORLD_WIDTH - 200)
            by = random.uniform(200, WORLD_HEIGHT - 200)
            rad = random.uniform(18, 30)
            cx, cy = WORLD_WIDTH // 2, WORLD_HEIGHT // 2
            if ((bx - cx)**2 + (by - cy)**2) ** 0.5 > 300:
                self.bushes.append({"x": bx, "y": by, "rad": rad})

        self.npcs = [
            {"x": 890.0, "y": 1030.0, "name": "Mara", "lines": ["Do not trust quiet tunnels.", "CR shines brightest near danger."]},
            {"x": 2850.0, "y": 760.0, "name": "Relay-7", "lines": ["Signal weak. Hunters close.", "Run when the sky blinks red."]},
            {"x": 1050.0, "y": 2840.0, "name": "Gravekeeper", "lines": ["Every marker was a player.", "Survive first. Loot second."]},
            {"x": 2880.0, "y": 2920.0, "name": "Cache Broker", "lines": ["Vaults pay. Vaults bite.", "Bring friends, leave rivals."]},
        ]

    def _register_network_callbacks(self):
        self.network.register_callback("welcome", self._on_welcome)
        self.network.register_callback("player_list", self._on_player_list)
        self.network.register_callback("boss_update", self._on_boss_update)
        self.network.register_callback("chat_message", self._on_chat_message)
        self.network.register_callback("player_eliminated", self._on_player_eliminated)
        self.network.register_callback("game_won", self._on_game_won)
        self.network.register_callback("you_died", self._on_you_died)
        self.network.register_callback("snap_back", self._on_snap_back)
        self.network.register_callback("disconnect", self._on_disconnect)
        self.network.register_callback("kicked", self._on_kicked)
        self.network.register_callback("crystal_list", self._on_crystal_list)
        self.network.register_callback("hazard_list", self._on_hazard_list)
        self.network.register_callback("objective_list", self._on_objective_list)
        self.network.register_callback("crystal_collected", self._on_crystal_collected)
        self.network.register_callback("objective_completed", self._on_objective_completed)
        self.network.register_callback("death_trap_list", self._on_death_trap_list)
        self.network.register_callback("combat_hit", self._on_combat_hit)
        self.network.register_callback("attack_missed", self._on_attack_missed)
        self.network.register_callback("pvp_reward", self._on_pvp_reward)
        self.network.register_callback("tombstone_list", self._on_tombstone_list)
        self.network.register_callback("round_start", self._on_round_start)

    # ─────────────────────────────────────────────────────────────────────────────
    # Socket Event Receivers
    # ─────────────────────────────────────────────────────────────────────────────

    def _on_welcome(self, data):
        self.self_player_id = data.get("player_id")
        self.self_x = data.get("spawn_x", self.self_x)
        self.self_y = data.get("spawn_y", self.self_y)
        self.camera_x = self.self_x
        self.camera_y = self.self_y
        logger = self.network.username
        self.chat.add_message("System", f"Connected as {logger}. Good luck.")

    def _on_player_list(self, data):
        raw_players = data.get("players", [])
        for rp in raw_players:
            pid = rp["id"]
            if pid == self.self_player_id:
                # Sync self position roughly if we drifts too much
                # but let client movement lead
                pass
            self.players[pid] = rp

    def _on_boss_update(self, data):
        prev_boss_state = self.boss_state.get("state", "sleeping")
        self.boss_state = data.get("boss", {})
        
        # Shake screen when boss wakes up or shifts state
        curr_state = self.boss_state.get("state", "sleeping")
        if curr_state != prev_boss_state:
            if curr_state == "warning":
                self.shake_remaining_frames = SCREEN_SHAKE_FRAMES
                self.chat.add_message("ALERT", "The Boss has woken up! Hide/Move immediately.")
                play_sound("alert")
            elif curr_state == "hunting":
                self.shake_remaining_frames = SCREEN_SHAKE_FRAMES * 2
                self.chat.add_message("ALERT", "THE HUNT IS ON!")

    def _on_chat_message(self, data):
        name = data.get("username", "Anon")
        message = data.get("message", "")
        self.chat.add_message(name, message)
        self._add_speech_bubble(data.get("player_id"), name, message, data.get("x"), data.get("y"))

    def _on_player_eliminated(self, data):
        username = data.get("username", "Someone")
        reason = data.get("reason", "boss")
        self.hud.add_elimination_alert(username, reason)
        self.shake_remaining_frames = SCREEN_SHAKE_FRAMES // 2
        
        # Add red explosion particles at their death coordinates
        pid = data.get("player_id")
        px = data.get("x")
        py = data.get("y")
        if pid in self.players:
            px = self.players[pid]["x"]
            py = self.players[pid]["y"]
            self.players[pid]["is_alive"] = False
        elif pid == self.self_player_id:
            px = self.self_x
            py = self.self_y
        if px is not None and py is not None:
            self.death_markers.append({
                "x": float(px),
                "y": float(py),
                "username": username,
                "reason": reason,
                "created_at": time.time(),
            })
            for _ in range(15):
                self.particles.append(generate_particle(px, py, COLORS["RED"], speed=2.5))

    def _on_game_won(self, data):
        winner_id = data.get("winner_id")
        self.winner_username = data.get("username", "Someone")
        self.prize_won = data.get("prize", 0.0)
        
        if winner_id == self.self_player_id:
            self.status = "victory"
            play_sound("victory")
        else:
            self.status = "round_over"
            play_sound("hit")
        self.status_time = time.time()

    def _on_you_died(self, data):
        self.status = "respawning"
        self.status_time = time.time()
        self.status_reason = data.get("reason", "boss")
        play_sound("death")

    def _on_snap_back(self, data):
        self.self_x = data.get("x", self.self_x)
        self.self_y = data.get("y", self.self_y)

    def _on_disconnect(self, msg=""):
        self.is_connected = False
        self.chat.add_message("SYSTEM", "WebSocket Disconnected.")

    def _on_kicked(self, data):
        self.status = "game_over"
        self.status_time = time.time()
        self.status_reason = "kicked"

    def _on_crystal_list(self, data):
        self.crystals = data.get("crystals", [])

    def _on_hazard_list(self, data):
        self.hazards = data.get("hazards", [])

    def _on_objective_list(self, data):
        self.objectives = data.get("objectives", [])

    def _on_crystal_collected(self, data):
        username = data.get("username", "Someone")
        value = float(data.get("value", 0.0) or 0.0)
        self.chat.add_message("System", f"{username} collected {value:.2f} CR")
        play_sound("pickup")
        
        pid = data.get("player_id")
        px, py = None, None
        if pid == self.self_player_id:
            px, py = self.self_x, self.self_y
        elif pid in self.players:
            px, py = self.players[pid]["x"], self.players[pid]["y"]
            
        if px is not None and py is not None:
            for _ in range(15):
                self.particles.append(generate_particle(px, py, COLORS["YELLOW"], speed=1.8))

    def _on_objective_completed(self, data):
        username = data.get("username", "Someone")
        reward = float(data.get("reward", 0.0) or 0.0)
        obj = self._format_objective(data.get("objective_type", "objective"))
        self.chat.add_message("System", f"{username} completed {obj} +{reward:.2f} CR")

    def _on_death_trap_list(self, data):
        self.death_traps = data.get("traps", [])

    def _on_tombstone_list(self, data):
        raw_tombstones = data.get("tombstones", [])
        self.death_markers = []
        for rt in raw_tombstones:
            self.death_markers.append({
                "x": float(rt.get("x", 2000.0)),
                "y": float(rt.get("y", 2000.0)),
                "username": rt.get("username", "Someone"),
                "reason": rt.get("reason", "unknown"),
            })

    def _on_round_start(self, data):
        self.status = "playing"
        self.death_markers = []
        self.chat.add_message("System", "A new round has started! Fight!")
        play_sound("victory")

    def _on_combat_hit(self, data):
        attacker = data.get("attacker_username", "Someone")
        target = data.get("target_username", "someone")
        hp = int(data.get("target_hp", 0) or 0)
        max_hp = int(data.get("max_hp", 3) or 3)
        self.chat.add_message("Combat", f"{attacker} hit {target} ({hp}/{max_hp} HP)")
        play_sound("hit")

        tid = data.get("target_id")
        if tid in self.players:
            self.players[tid]["hp"] = hp
            self.players[tid]["max_hp"] = max_hp
        px = float(data.get("x", self.self_x) or self.self_x)
        py = float(data.get("y", self.self_y) or self.self_y)
        self.shake_remaining_frames = max(self.shake_remaining_frames, 4)
        for _ in range(9):
            self.particles.append(generate_particle(px, py, COLORS["RED"], speed=1.8))

    def _on_attack_missed(self, data):
        self.chat.add_message("Combat", "No target in range.")

    def _on_pvp_reward(self, data):
        value = float(data.get("value", 0.0) or 0.0)
        self.chat.add_message("System", f"{data.get('username', 'Someone')} earned {value:.2f} CR for a takedown")

    def _add_speech_bubble(self, player_id, name: str, text: str, x=None, y=None):
        if player_id in self.players:
            x = self.players[player_id]["x"]
            y = self.players[player_id]["y"]
        elif player_id == self.self_player_id:
            x = self.self_x
            y = self.self_y
        if x is None or y is None:
            return
        self.speech_bubbles.append({
            "player_id": player_id,
            "name": name,
            "text": str(text)[:70],
            "x": float(x),
            "y": float(y),
            "created_at": time.time(),
            "ttl": 4.0,
        })

    # ─────────────────────────────────────────────────────────────────────────────
    # Updates & inputs
    # ─────────────────────────────────────────────────────────────────────────────

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Processes keystrokes. Returns True if we should quit back to menu."""
        # Escape key returns to menu
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.network.disconnect()
            return True

        # Check game over exits
        if self.status == "game_over" or self.status == "victory":
            if event.type == pygame.KEYDOWN or event.type == pygame.MOUSEBUTTONDOWN:
                if time.time() - self.status_time > 5.0:
                    self.network.disconnect()
                    return True
            return False

        if self.status == "respawning":
            return False

        # Route to chat input first
        def dispatch_chat(msg):
            self.network.send_chat(msg)

        chat_active = self.chat.handle_event(event, dispatch_chat)
        if chat_active:
            return False

        # Manual escape key returning to menu (disconnects)
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.network.disconnect()
                return True
            if event.key == pygame.K_SPACE and not self.chat.typing:
                self._try_attack()

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and not self.chat.typing:
            self._try_attack()

        return False

    def _try_attack(self):
        now = time.time()
        if now - self.last_attack_time < self.attack_cooldown:
            return
        self.last_attack_time = now
        self.network.send_attack()
        self.chat.add_message("Combat", "Attack")
        play_sound("shoot")
        for _ in range(5):
            self.particles.append(generate_particle(self.self_x, self.self_y, COLORS["WHITE"], speed=1.4))

    def update(self, dt: float):
        if self.status == "respawning":
            self.particles = update_particles(self.particles, dt)
            if time.time() - self.status_time >= 3.0:
                self.network.send_respawn()
                self.status = "playing"
            return

        if self.status != "playing" and self.status != "victory" and self.status != "round_over":
            # Just drift particles
            self.particles = update_particles(self.particles, dt)
            return

        # Player inputs (check if chat is active, if so don't move)
        keys = pygame.key.get_pressed()
        move_x = 0.0
        move_y = 0.0

        if not self.chat.typing:
            if keys[pygame.K_w] or keys[pygame.K_UP]:
                move_y -= 1.0
            if keys[pygame.K_s] or keys[pygame.K_DOWN]:
                move_y += 1.0
            if keys[pygame.K_a] or keys[pygame.K_LEFT]:
                move_x -= 1.0
            if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
                move_x += 1.0

        # Normalise vector
        dist = (move_x*move_x + move_y*move_y) ** 0.5
        if dist > 0:
            move_x /= dist
            move_y /= dist
            
            # Apply displacement
            prev_x, prev_y = self.self_x, self.self_y
            self.self_x += move_x * self.move_speed * dt
            self.self_y += move_y * self.move_speed * dt

            # Constrain to map bounds
            self.self_x = max(15.0, min(self.self_x, float(WORLD_WIDTH - 15)))
            self.self_y = max(15.0, min(self.self_y, float(WORLD_HEIGHT - 15)))

            # Collision with obstacles
            for ox, oy, orad in self.obstacles:
                p_dist = ((self.self_x - ox)**2 + (self.self_y - oy)**2) ** 0.5
                if p_dist < orad + PLAYER_RADIUS:
                    # push back
                    overlap = (orad + PLAYER_RADIUS) - p_dist
                    safe_dist = p_dist or 1.0
                    dx = (self.self_x - ox) / safe_dist
                    dy = (self.self_y - oy) / safe_dist
                    self.self_x += dx * overlap
                    self.self_y += dy * overlap

            # Collision with trees
            for tree in self.trees:
                tx, ty, trad = tree["x"], tree["y"], tree["trunk_rad"]
                p_dist = ((self.self_x - tx)**2 + (self.self_y - ty)**2) ** 0.5
                if p_dist < trad + PLAYER_RADIUS:
                    overlap = (trad + PLAYER_RADIUS) - p_dist
                    safe_dist = p_dist or 1.0
                    dx = (self.self_x - tx) / safe_dist
                    dy = (self.self_y - ty) / safe_dist
                    self.self_x += dx * overlap
                    self.self_y += dy * overlap

            # Spawn trail particles when moving
            if len(self.particles) < PARTICLE_POOL_MAX and random.random() < 0.3:
                self.particles.append(generate_particle(self.self_x, self.self_y, COLORS["GRAY"], speed=0.4))

            # Send movement updates to network throttled at 30Hz
            now = time.time()
            if now - self.last_network_update > self.net_update_rate:
                self.network.send_move(self.self_x, self.self_y)
                self.last_network_update = now

        # Update Boss alert screen overlays
        b_state = self.boss_state.get("state", "sleeping")
        if b_state == "warning":
            # Flashing border alert
            self.boss_overlay_alpha = int(120 * abs(math.sin(time.time() * 4.0)))
        elif b_state == "hunting":
            self.boss_overlay_alpha = int(70 * abs(math.sin(time.time() * 2.0)))
            # Spawn red alert particles tracking boss
            bx = self.boss_state.get("x", 0.0)
            by = self.boss_state.get("y", 0.0)
            if len(self.particles) < PARTICLE_POOL_MAX and random.random() < 0.5:
                self.particles.append(generate_particle(bx, by, COLORS["RED"], speed=1.2))
        else:
            self.boss_overlay_alpha = max(0.0, self.boss_overlay_alpha - 200 * dt)

        # Update all particles
        self.particles = update_particles(self.particles, dt)
        now = time.time()
        self.speech_bubbles = [
            b for b in self.speech_bubbles
            if now - b.get("created_at", now) < b.get("ttl", 4.0)
        ]
        for bubble in self.speech_bubbles:
            pid = bubble.get("player_id")
            if pid in self.players:
                bubble["x"] = self.players[pid]["x"]
                bubble["y"] = self.players[pid]["y"]
            elif pid == self.self_player_id:
                bubble["x"] = self.self_x
                bubble["y"] = self.self_y

        # Smooth camera following
        # target camera is self coordinates
        self.camera_x += (self.self_x - self.camera_x) * CAMERA_SPEED * dt
        self.camera_y += (self.self_y - self.camera_y) * CAMERA_SPEED * dt

        # Periodic non-blocking REST fetch for HUD data (every 5 seconds)
        if now - self._last_hud_fetch > self._hud_fetch_interval:
            self._last_hud_fetch = now
            def _fetch_hud():
                try:
                    stats = self.network.get_tier_stats(self.tier_id)
                    self.cached_prize_pool = stats.get("prize_pool", self.cached_prize_pool)
                    profile = self.network.get_profile()
                    self.cached_daily_moves = profile.get("daily_moves_today", self.cached_daily_moves)
                except Exception:
                    pass
            import threading
            threading.Thread(target=_fetch_hud, daemon=True).start()

    # ─────────────────────────────────────────────────────────────────────────────
    # Rendering
    # ─────────────────────────────────────────────────────────────────────────────

    def draw(self):
        # Apply Screen Shake
        cam_x_shaked = self.camera_x
        cam_y_shaked = self.camera_y
        if self.shake_remaining_frames > 0:
            cam_x_shaked += random.uniform(-SCREEN_SHAKE_MAGNITUDE, SCREEN_SHAKE_MAGNITUDE)
            cam_y_shaked += random.uniform(-SCREEN_SHAKE_MAGNITUDE, SCREEN_SHAKE_MAGNITUDE)
            self.shake_remaining_frames -= 1

        # Calculate offset coordinates (Camera coordinates map to Center of Screen)
        offset_x = self.width / 2 - cam_x_shaked
        offset_y = self.height / 2 - cam_y_shaked

        # Tiled cyber floor background rendering (super fast, cached)
        tile_size = 256
        start_tile_x = int(offset_x) % tile_size - tile_size
        start_tile_y = int(offset_y) % tile_size - tile_size
        for x in range(start_tile_x, self.width + tile_size, tile_size):
            for y in range(start_tile_y, self.height + tile_size, tile_size):
                self.screen.blit(self.cyber_floor_tile, (x, y))

        self._draw_districts(offset_x, offset_y)
        self._draw_routes(offset_x, offset_y)

        # Render obstacles
        for idx, (ox, oy, orad) in enumerate(self.obstacles):
            self._draw_map_prop(idx, ox + offset_x, oy + offset_y, orad)

        # Draw bushes and tree trunks below players
        self._draw_bushes(offset_x, offset_y)
        self._draw_tree_trunks(offset_x, offset_y)

        # Render boundary walls
        bx_min = int(offset_x)
        bx_max = int(WORLD_WIDTH + offset_x)
        by_min = int(offset_y)
        by_max = int(WORLD_HEIGHT + offset_y)
        
        # Border lines
        pygame.draw.line(self.screen, COLORS["WHITE"], (bx_min, by_min), (bx_max, by_min), 2)
        pygame.draw.line(self.screen, COLORS["WHITE"], (bx_max, by_min), (bx_max, by_max), 2)
        pygame.draw.line(self.screen, COLORS["WHITE"], (bx_max, by_max), (bx_min, by_max), 2)
        pygame.draw.line(self.screen, COLORS["WHITE"], (bx_min, by_max), (bx_min, by_min), 2)

        # Draw particles behind players
        draw_particles(self.screen, [
            {**p, "x": p["x"] + offset_x, "y": p["y"] + offset_y} for p in self.particles
        ])

        self._draw_crystals(offset_x, offset_y)
        self._draw_objectives(offset_x, offset_y)
        self._draw_death_traps(offset_x, offset_y)
        self._draw_death_markers(offset_x, offset_y)
        self._draw_npcs(offset_x, offset_y)
        self._draw_hazards(offset_x, offset_y)

        # Draw other players
        for pid, p in self.players.items():
            if pid == self.self_player_id:
                continue
            is_alive = p.get("is_alive", True)
            if is_alive and self._is_in_bush(p["x"], p["y"]):
                # Draw on a temporary transparent surface for stealth blending
                temp_surf = pygame.Surface((120, 120), pygame.SRCALPHA)
                draw_player(
                    temp_surf,
                    60,
                    60,
                    COLORS["GRAY"],
                    is_self=False,
                    username=p["username"],
                    is_alive=is_alive,
                    font=self.medium_font
                )
                self._draw_hp_bar(60, 60 + 24, p.get("hp", 3), p.get("max_hp", 3), temp_surf)
                temp_surf.set_alpha(100)
                self.screen.blit(temp_surf, (int(p["x"] + offset_x - 60), int(p["y"] + offset_y - 60)))
            else:
                draw_player(
                    self.screen,
                    p["x"] + offset_x,
                    p["y"] + offset_y,
                    COLORS["GRAY"],
                    is_self=False,
                    username=p["username"],
                    is_alive=is_alive,
                    font=self.medium_font
                )
                if is_alive:
                    self._draw_hp_bar(p["x"] + offset_x, p["y"] + offset_y + 24, p.get("hp", 3), p.get("max_hp", 3))

        # Draw self player
        is_self_alive = (self.status != "game_over")
        if is_self_alive and self._is_in_bush(self.self_x, self.self_y):
            # Draw self transparently in bush
            temp_surf = pygame.Surface((120, 120), pygame.SRCALPHA)
            draw_player(
                temp_surf,
                60,
                60,
                COLORS["WHITE"],
                is_self=True,
                username=self.network.username,
                is_alive=is_self_alive,
                font=self.medium_font
            )
            me = self.players.get(self.self_player_id, {})
            self._draw_hp_bar(60, 60 + 24, me.get("hp", 3), me.get("max_hp", 3), temp_surf)
            temp_surf.set_alpha(100)
            self.screen.blit(temp_surf, (int(self.self_x + offset_x - 60), int(self.self_y + offset_y - 60)))
        else:
            draw_player(
                self.screen,
                self.self_x + offset_x,
                self.self_y + offset_y,
                COLORS["WHITE"],
                is_self=True,
                username=self.network.username,
                is_alive=is_self_alive,
                font=self.medium_font
            )
            if self.status == "playing":
                me = self.players.get(self.self_player_id, {})
                self._draw_hp_bar(self.self_x + offset_x, self.self_y + offset_y + 24, me.get("hp", 3), me.get("max_hp", 3))

        # Draw tree canopies above players
        self._draw_tree_canopies(offset_x, offset_y)

        self._draw_speech_bubbles(offset_x, offset_y)

        self._draw_goal_pointer(offset_x, offset_y)

        # Draw Boss if active
        if self.boss_state and self.boss_state.get("state") != "sleeping":
            bx = self.boss_state.get("x", 0.0)
            by = self.boss_state.get("y", 0.0)
            draw_boss(self.screen, bx + offset_x, by + offset_y, pulse_time=time.time())

        # Render boss alert border overlay
        draw_boss_warning_overlay(self.screen, self.boss_overlay_alpha)

        # Gather HUD parameters (all from cache — no blocking calls in draw)
        alive_count = len([p for p in self.players.values() if p.get("is_alive")])
        total_count = len(self.players)
        if self.status == "playing":
            alive_count += 1
            total_count += 1
        hud_state = {
            "tier_id": self.tier_id,
            "prize_pool": self.cached_prize_pool,
            "alive_count": max(1, alive_count),
            "total_count": max(1, total_count),
            "daily_moves": self.cached_daily_moves,
            "boss_state": self.boss_state
        }

        # Draw UI components
        self.hud.draw(self.screen, hud_state)
        self.chat.draw(self.screen)
        
        # Minimap
        draw_minimap(
            self.screen,
            list(self.players.values()) + [{"id": self.self_player_id, "x": self.self_x, "y": self.self_y, "is_alive": self.status == "playing"}],
            self.boss_state,
            self.camera_x,
            self.camera_y,
            self.self_player_id
        )

        # Draw GameOver or Victory Screen overlays
        if self.status == "game_over":
            self._draw_death_overlay()
        elif self.status == "victory":
            self._draw_victory_overlay()
        elif self.status == "respawning":
            self._draw_respawn_overlay()
        elif self.status == "round_over":
            self._draw_round_over_overlay()

    def _draw_districts(self, offset_x: float, offset_y: float):
        font = self.small_font = getattr(self, "small_font", pygame.font.SysFont("Inter", 14, bold=True))
        palettes = {
            "ruins": ((22, 22, 22), (70, 70, 70), COLORS["GRAY"]),
            "signal": ((7, 26, 14), (0, 120, 54), COLORS["GREEN"]),
            "fog": ((5, 5, 5), (80, 80, 80), COLORS["GRAY"]),
            "vault": ((30, 24, 5), (130, 105, 0), COLORS["YELLOW"]),
            "center": ((28, 10, 10), (130, 0, 0), COLORS["RED"]),
        }
        for zone in self.districts:
            x = int(zone["x"] + offset_x)
            y = int(zone["y"] + offset_y)
            rect = pygame.Rect(x, y, int(zone["w"]), int(zone["h"]))
            fill, border, text = palettes.get(zone["kind"], palettes["ruins"])
            overlay = pygame.Surface(rect.size, pygame.SRCALPHA)
            overlay.fill((*fill, 48))
            self.screen.blit(overlay, rect.topleft)
            pygame.draw.rect(self.screen, border, rect, 2)
            label = font.render(zone["label"], True, text)
            self.screen.blit(label, (x + 14, y + 12))

    def _draw_routes(self, offset_x: float, offset_y: float):
        center = (WORLD_WIDTH / 2 + offset_x, WORLD_HEIGHT / 2 + offset_y)
        t = time.time()
        for idx, zone in enumerate(self.districts):
            hub = (zone["x"] + zone["w"] / 2 + offset_x, zone["y"] + zone["h"] / 2 + offset_y)
            color = (70 + int(40 * abs(math.sin(t + idx))),) * 3
            pygame.draw.line(self.screen, color, center, hub, 2)
            pygame.draw.circle(self.screen, COLORS["WHITE"], (int(hub[0]), int(hub[1])), 4)
        pygame.draw.circle(self.screen, COLORS["WHITE"], (int(center[0]), int(center[1])), 44, 2)

    def _draw_map_prop(self, idx: int, x: float, y: float, r: float):
        kind = idx % 4
        ix, iy, ir = int(x), int(y), int(r)
        if kind == 0:
            pygame.draw.line(self.screen, COLORS["DARK_GRAY"], (ix, iy + ir), (ix, iy - ir), max(3, ir // 4))
            pygame.draw.circle(self.screen, COLORS["DARK_GRAY"], (ix - ir // 2, iy - ir), max(8, ir // 2))
            pygame.draw.circle(self.screen, COLORS["GRAY"], (ix + ir // 3, iy - ir - 4), max(7, ir // 2))
            pygame.draw.circle(self.screen, COLORS["GREEN"], (ix, iy - ir), 2)
        elif kind == 1:
            rect = pygame.Rect(ix - ir, iy - ir // 2, ir * 2, ir)
            pygame.draw.rect(self.screen, COLORS["DARK_GRAY"], rect, border_radius=2)
            pygame.draw.rect(self.screen, COLORS["GRAY"], rect, 1, border_radius=2)
        elif kind == 2:
            pygame.draw.ellipse(self.screen, COLORS["BLACK"], (ix - ir, iy - ir // 2, ir * 2, ir), 0)
            pygame.draw.ellipse(self.screen, COLORS["RED"], (ix - ir, iy - ir // 2, ir * 2, ir), 2)
            pygame.draw.line(self.screen, COLORS["GRAY"], (ix - ir // 2, iy), (ix + ir // 2, iy), 1)
        else:
            pygame.draw.circle(self.screen, COLORS["YELLOW"], (ix, iy), max(4, ir // 2), 2)
            pygame.draw.circle(self.screen, COLORS["WHITE"], (ix, iy), 2)

    def _draw_hp_bar(self, x: float, y: float, hp: int, max_hp: int, surface=None):
        if surface is None:
            surface = self.screen
        max_hp = max(1, int(max_hp or 1))
        hp = max(0, min(max_hp, int(hp or 0)))
        width, height = 32, 4
        rect = pygame.Rect(int(x - width / 2), int(y), width, height)
        pygame.draw.rect(surface, COLORS["DARK_GRAY"], rect)
        fill = pygame.Rect(rect.x, rect.y, int(width * hp / max_hp), height)
        pygame.draw.rect(surface, COLORS["GREEN"] if hp > 1 else COLORS["RED"], fill)

    def _draw_death_traps(self, offset_x: float, offset_y: float):
        t = time.time()
        for trap in self.death_traps:
            x = int(float(trap.get("x", 0)) + offset_x)
            y = int(float(trap.get("y", 0)) + offset_y)
            radius = int(float(trap.get("radius", 44)))
            ttype = trap.get("type", "pit")
            
            if ttype == "cave":
                pygame.draw.circle(self.screen, (10, 0, 20), (x, y), radius)
                pygame.draw.circle(self.screen, (170, 0, 255), (x, y), radius, 2)
                pulse = int(radius * (0.3 + 0.5 * ((t * 0.5) % 1.0)))
                pygame.draw.circle(self.screen, (170, 0, 255), (x, y), pulse, 1)
                label = self.small_font.render("LETHAL CAVE", True, (170, 0, 255))
            else:
                pygame.draw.circle(self.screen, COLORS["BLACK"], (x, y), radius)
                pygame.draw.circle(self.screen, (255, 85, 0), (x, y), radius, 2)
                label = self.small_font.render("LETHAL PIT", True, (255, 85, 0))
                
            self.screen.blit(label, (x - label.get_width() // 2, y - radius - 14))

    def _draw_bushes(self, offset_x: float, offset_y: float):
        for bush in self.bushes:
            x = int(bush["x"] + offset_x)
            y = int(bush["y"] + offset_y)
            r = int(bush["rad"])
            
            surf = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
            pygame.draw.circle(surf, (0, 153, 51, 95), (r, r), r)
            pygame.draw.circle(surf, (0, 204, 68, 140), (r, r), r, 2)
            
            pygame.draw.circle(surf, (0, 153, 51, 95), (int(r * 0.7), int(r * 0.8)), int(r * 0.6))
            pygame.draw.circle(surf, (0, 153, 51, 95), (int(r * 1.3), int(r * 1.1)), int(r * 0.5))
            
            self.screen.blit(surf, (x - r, y - r))

    def _draw_tree_trunks(self, offset_x: float, offset_y: float):
        for tree in self.trees:
            x = int(tree["x"] + offset_x)
            y = int(tree["y"] + offset_y)
            r = int(tree["trunk_rad"])
            pygame.draw.circle(self.screen, (77, 38, 0), (x, y), r)
            pygame.draw.circle(self.screen, (38, 19, 0), (x, y), r, 1)

    def _draw_tree_canopies(self, offset_x: float, offset_y: float):
        t = time.time()
        for idx, tree in enumerate(self.trees):
            x = int(tree["x"] + offset_x)
            y = int(tree["y"] + offset_y)
            r = int(tree["canopy_rad"])
            wind = int(2.0 * math.sin(t * 1.5 + idx))
            cx = x + wind
            cy = y + wind
            
            surf = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
            pygame.draw.circle(surf, (0, 128, 43, 200), (r, r), r)
            pygame.draw.circle(surf, (0, 179, 60, 220), (r, r), int(r * 0.8))
            pygame.draw.circle(surf, (0, 77, 26, 220), (r, r), r, 2)
            
            self.screen.blit(surf, (cx - r, cy - r))

    def _is_in_bush(self, px: float, py: float) -> bool:
        for bush in self.bushes:
            bx, by, brad = bush["x"], bush["y"], bush["rad"]
            if ((px - bx)**2 + (py - by)**2) ** 0.5 < brad:
                return True
        return False

    def _draw_death_markers(self, offset_x: float, offset_y: float):
        for marker in self.death_markers[-40:]:
            x = int(float(marker.get("x", 0)) + offset_x)
            y = int(float(marker.get("y", 0)) + offset_y)
            pygame.draw.rect(self.screen, COLORS["GRAY"], (x - 9, y - 15, 18, 25), border_radius=4)
            pygame.draw.line(self.screen, COLORS["DARK_GRAY"], (x, y - 10), (x, y + 4), 2)
            pygame.draw.line(self.screen, COLORS["DARK_GRAY"], (x - 5, y - 5), (x + 5, y - 5), 2)
            label = self.small_font.render(marker.get("username", ""), True, COLORS["GRAY"])
            self.screen.blit(label, (x - label.get_width() // 2, y + 12))

    def _draw_npcs(self, offset_x: float, offset_y: float):
        now = time.time()
        for npc in self.npcs:
            x = int(npc["x"] + offset_x)
            y = int(npc["y"] + offset_y)
            pygame.draw.circle(self.screen, COLORS["DARK_GRAY"], (x, y), 13)
            pygame.draw.circle(self.screen, COLORS["WHITE"], (x, y - 5), 5)
            pygame.draw.rect(self.screen, COLORS["GRAY"], (x - 6, y, 12, 16), border_radius=3)
            name = self.small_font.render(npc["name"], True, COLORS["YELLOW"])
            self.screen.blit(name, (x - name.get_width() // 2, y - 34))
            line = npc["lines"][int(now / 4) % len(npc["lines"])]
            self._draw_world_bubble(x, y - 48, line, alpha=190)

    def _draw_speech_bubbles(self, offset_x: float, offset_y: float):
        now = time.time()
        for bubble in self.speech_bubbles:
            age = now - bubble.get("created_at", now)
            alpha = max(0, min(230, int(230 * (1 - age / bubble.get("ttl", 4.0)))))
            x = int(float(bubble.get("x", 0)) + offset_x)
            y = int(float(bubble.get("y", 0)) + offset_y - 48)
            self._draw_world_bubble(x, y, bubble.get("text", ""), alpha=alpha)

    def _draw_world_bubble(self, x: int, y: int, text: str, alpha: int = 220):
        text = str(text)[:70]
        surf_text = self.small_font.render(text, True, COLORS["WHITE"])
        w = min(260, surf_text.get_width() + 18)
        h = surf_text.get_height() + 10
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(surf, (0, 0, 0, alpha), surf.get_rect(), border_radius=6)
        pygame.draw.rect(surf, (255, 255, 255, min(120, alpha)), surf.get_rect(), 1, border_radius=6)
        surf.blit(surf_text, ((w - surf_text.get_width()) // 2, 5))
        self.screen.blit(surf, (x - w // 2, y - h // 2))

    def _draw_crystals(self, offset_x: float, offset_y: float):
        for crystal in self.crystals:
            x = int(float(crystal.get("x", 0)) + offset_x)
            y = int(float(crystal.get("y", 0)) + offset_y)
            pulse = int(2 * abs(math.sin(time.time() * 5 + int(crystal.get("id", 0)))))
            points = [(x, y - 10 - pulse), (x + 8 + pulse, y), (x, y + 10 + pulse), (x - 8 - pulse, y)]
            pygame.draw.polygon(self.screen, COLORS["YELLOW"], points)
            pygame.draw.polygon(self.screen, COLORS["WHITE"], points, 1)
            label = self.small_font.render(f"{float(crystal.get('value', 0.0)):.1f} CR", True, COLORS["WHITE"])
            self.screen.blit(label, (x - label.get_width() // 2, y - 28))

    def _draw_objectives(self, offset_x: float, offset_y: float):
        for obj in self.objectives:
            x = int(float(obj.get("x", 0)) + offset_x)
            y = int(float(obj.get("y", 0)) + offset_y)
            radius = int(float(obj.get("radius", 28)))
            color = self._objective_color(obj.get("type"))
            pygame.draw.circle(self.screen, color, (x, y), radius, 2)
            pygame.draw.circle(self.screen, (*color[:3],) if len(color) == 3 else color, (x, y), max(4, radius // 4))
            glyph = self._objective_glyph(obj.get("type"))
            glyph_surf = self.medium_font.render(glyph, True, COLORS["WHITE"])
            self.screen.blit(glyph_surf, (x - glyph_surf.get_width() // 2, y - glyph_surf.get_height() // 2))

    def _draw_hazards(self, offset_x: float, offset_y: float):
        for hazard in self.hazards:
            x = int(float(hazard.get("x", 0)) + offset_x)
            y = int(float(hazard.get("y", 0)) + offset_y)
            radius = int(float(hazard.get("radius", 80)))
            pygame.draw.circle(self.screen, COLORS["RED"], (x, y), radius, 2)
            overlay = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
            pygame.draw.circle(overlay, (*COLORS["RED"], 28), (radius, radius), radius)
            self.screen.blit(overlay, (x - radius, y - radius))

    def _draw_goal_pointer(self, offset_x: float, offset_y: float):
        goal = self._nearest_goal()
        if not goal:
            return
        dx = goal["x"] - self.self_x
        dy = goal["y"] - self.self_y
        dist = math.hypot(dx, dy)
        if dist < 80:
            return
        angle = math.atan2(dy, dx)
        sx = self.self_x + offset_x
        sy = self.self_y + offset_y
        px = sx + math.cos(angle) * 70
        py = sy + math.sin(angle) * 70
        tip = (px + math.cos(angle) * 12, py + math.sin(angle) * 12)
        left = (px + math.cos(angle + 2.5) * 10, py + math.sin(angle + 2.5) * 10)
        right = (px + math.cos(angle - 2.5) * 10, py + math.sin(angle - 2.5) * 10)
        pygame.draw.polygon(self.screen, COLORS["YELLOW"] if goal["kind"] == "crystal" else COLORS["WHITE"], [tip, left, right])
        label = self.small_font.render(f"{goal['label']} {int(dist)}m", True, COLORS["WHITE"])
        self.screen.blit(label, (int(px - label.get_width() // 2), int(py - 28)))

    def _nearest_goal(self) -> Optional[Dict[str, Any]]:
        goals = []
        for c in self.crystals:
            goals.append({"kind": "crystal", "label": "CRYSTAL", "x": float(c.get("x", 0)), "y": float(c.get("y", 0))})
        for o in self.objectives:
            goals.append({"kind": "objective", "label": self._format_objective(o.get("type")).upper(), "x": float(o.get("x", 0)), "y": float(o.get("y", 0))})
        if not goals:
            return None
        return min(goals, key=lambda g: math.hypot(g["x"] - self.self_x, g["y"] - self.self_y))

    def _objective_glyph(self, obj_type: str) -> str:
        return {"scan": "S", "relay": "R", "cache": "C"}.get(obj_type, "?")

    def _objective_color(self, obj_type: str):
        return {"scan": COLORS["GREEN"], "relay": COLORS["WHITE"], "cache": COLORS["YELLOW"]}.get(obj_type, COLORS["GRAY"])

    def _format_objective(self, obj_type: str) -> str:
        return {"scan": "scan obelisk", "relay": "signal relay", "cache": "supply cache"}.get(obj_type, "objective")

    def _draw_death_overlay(self):
        self.overlay_surf.fill((0, 0, 0, 0))
        # Slow fade to dark red-black
        alpha = min(220, int((time.time() - self.status_time) * 100))
        self.overlay_surf.fill((10, 0, 0, alpha))
        self.screen.blit(self.overlay_surf, (0, 0))

        # Big dead title
        txt = "YOU DIED" if self.status_reason != "kicked" else "SESSION TERMINATED"
        lbl = self.giant_font.render(txt, True, COLORS["RED"])
        self.screen.blit(lbl, (self.width // 2 - lbl.get_width() // 2, self.height // 2 - 60))

        reason_str = "Eliminated by the Boss."
        if self.status_reason == "afk":
            reason_str = "Eliminated: Failed AFK check quota."
        elif self.status_reason == "kicked":
            reason_str = "Logged in from another location."

        sub = self.large_font.render(reason_str, True, COLORS["WHITE"])
        self.screen.blit(sub, (self.width // 2 - sub.get_width() // 2, self.height // 2 + 30))

        ext = self.medium_font.render("Press any key to return to Main Menu", True, COLORS["GRAY"])
        self.screen.blit(ext, (self.width // 2 - ext.get_width() // 2, self.height // 2 + 100))

    def _draw_victory_overlay(self):
        self.overlay_surf.fill((0, 0, 0, 0))
        # Slow fade to gold
        alpha = min(220, int((time.time() - self.status_time) * 100))
        self.overlay_surf.fill((10, 10, 0, alpha))
        self.screen.blit(self.overlay_surf, (0, 0))

        lbl = self.giant_font.render("VICTORY", True, COLORS["YELLOW"])
        self.screen.blit(lbl, (self.width // 2 - lbl.get_width() // 2, self.height // 2 - 80))

        sub = self.large_font.render("You are the Last Man Standing!", True, COLORS["WHITE"])
        self.screen.blit(sub, (self.width // 2 - sub.get_width() // 2, self.height // 2 + 10))

        pz = self.large_font.render(f"Prize Claimed: ${self.prize_won:.2f}", True, COLORS["GREEN"])
        self.screen.blit(pz, (self.width // 2 - pz.get_width() // 2, self.height // 2 + 50))

        ext = self.medium_font.render("Press any key to return to Main Menu", True, COLORS["GRAY"])
        self.screen.blit(ext, (self.width // 2 - ext.get_width() // 2, self.height // 2 + 120))

    def _draw_respawn_overlay(self):
        self.overlay_surf.fill((0, 0, 0, 0))
        # Fade to dark crimson red
        alpha = min(180, int((time.time() - self.status_time) * 150))
        self.overlay_surf.fill((15, 0, 0, alpha))
        self.screen.blit(self.overlay_surf, (0, 0))

        # Show respawn counter
        time_left = max(1, 3 - int(time.time() - self.status_time))
        lbl = self.giant_font.render(f"RESPAWNING IN {time_left}...", True, COLORS["RED"])
        self.screen.blit(lbl, (self.width // 2 - lbl.get_width() // 2, self.height // 2 - 50))
        
        sub = self.large_font.render("Prepare to fight back!", True, COLORS["WHITE"])
        self.screen.blit(sub, (self.width // 2 - sub.get_width() // 2, self.height // 2 + 20))

    def _draw_round_over_overlay(self):
        self.overlay_surf.fill((0, 0, 0, 0))
        # Fade to dark gray
        alpha = min(200, int((time.time() - self.status_time) * 100))
        self.overlay_surf.fill((10, 10, 12, alpha))
        self.screen.blit(self.overlay_surf, (0, 0))

        lbl = self.giant_font.render("ROUND OVER", True, COLORS["WHITE"])
        self.screen.blit(lbl, (self.width // 2 - lbl.get_width() // 2, self.height // 2 - 80))

        sub = self.large_font.render(f"Winner: {self.winner_username}", True, COLORS["YELLOW"])
        self.screen.blit(sub, (self.width // 2 - sub.get_width() // 2, self.height // 2 + 10))

        ext = self.medium_font.render("Press ESC to exit to Lobby, or wait for next round...", True, COLORS["GRAY"])
        self.screen.blit(ext, (self.width // 2 - ext.get_width() // 2, self.height // 2 + 80))
