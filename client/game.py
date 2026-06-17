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
    draw_boss_warning_overlay, draw_minimap
)
from network import NetworkManager
from hud import HUD
from chat import ChatSystem


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

        # Local movement parameters
        self.move_speed = 160.0  # px/s
        self.last_network_update = 0.0
        self.net_update_rate = 1.0 / 30.0  # 30 Hz position update rate

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
        self._generate_obstacles()

        # Setup callbacks
        self._register_network_callbacks()

    def _generate_obstacles(self):
        """Generates visual ruins/rocks to hide behind."""
        random.seed(self.tier_id * 100)
        # Spawn some grey geometric circles acting as columns
        for _ in range(40):
            ox = random.uniform(100, WORLD_WIDTH - 100)
            oy = random.uniform(100, WORLD_HEIGHT - 100)
            rad = random.uniform(20, 50)
            # Ensure not spawning directly in the center spawning room
            cx, cy = WORLD_WIDTH // 2, WORLD_HEIGHT // 2
            if ((ox - cx)**2 + (oy - cy)**2) ** 0.5 > 200:
                self.obstacles.append((ox, oy, rad))

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
            elif curr_state == "hunting":
                self.shake_remaining_frames = SCREEN_SHAKE_FRAMES * 2
                self.chat.add_message("ALERT", "THE HUNT IS ON!")

    def _on_chat_message(self, data):
        self.chat.add_message(data.get("username", "Anon"), data.get("message", ""))

    def _on_player_eliminated(self, data):
        username = data.get("username", "Someone")
        reason = data.get("reason", "boss")
        self.hud.add_elimination_alert(username, reason)
        self.shake_remaining_frames = SCREEN_SHAKE_FRAMES // 2
        
        # Add red explosion particles at their death coordinates
        pid = data.get("player_id")
        if pid in self.players:
            px = self.players[pid]["x"]
            py = self.players[pid]["y"]
            for _ in range(15):
                self.particles.append(generate_particle(px, py, COLORS["RED"], speed=2.5))

    def _on_game_won(self, data):
        self.status = "victory"
        self.status_time = time.time()
        self.prize_won = data.get("prize", 0.0)

    def _on_you_died(self, data):
        self.status = "game_over"
        self.status_time = time.time()
        self.status_reason = data.get("reason", "boss")

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

    # ─────────────────────────────────────────────────────────────────────────────
    # Updates & inputs
    # ─────────────────────────────────────────────────────────────────────────────

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Processes keystrokes. Returns True if we should quit back to menu."""
        # Check game over exits
        if self.status != "playing":
            if event.type == pygame.KEYDOWN or event.type == pygame.MOUSEBUTTONDOWN:
                if time.time() - self.status_time > 2.0:
                    self.network.disconnect()
                    return True
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

        return False

    def update(self, dt: float):
        if self.status != "playing":
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
                    dx = (self.self_x - ox) / p_dist
                    dy = (self.self_y - oy) / p_dist
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

        # Smooth camera following
        # target camera is self coordinates
        self.camera_x += (self.self_x - self.camera_x) * CAMERA_SPEED * dt
        self.camera_y += (self.self_y - self.camera_y) * CAMERA_SPEED * dt

        # Periodic non-blocking REST fetch for HUD data (every 5 seconds)
        now = time.time()
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

        # Draw dynamic grid inside the visible screen viewport (super fast 60FPS)
        self.screen.fill(COLORS["BG"])
        
        # Grid line spacing
        grid_size = 80
        
        # Start coordinates aligned to the camera scroll offset
        start_x = int(offset_x) % grid_size
        start_y = int(offset_y) % grid_size
        
        # Draw vertical lines that are within screen bounds
        for x in range(start_x, self.width + grid_size, grid_size):
            pygame.draw.line(self.screen, COLORS["DARK_GRAY"], (x, 0), (x, self.height), 1)
            
        # Draw horizontal lines that are within screen bounds
        for y in range(start_y, self.height + grid_size, grid_size):
            pygame.draw.line(self.screen, COLORS["DARK_GRAY"], (0, y), (self.width, y), 1)

        # Draw intersection markers/crosses
        for x in range(start_x, self.width + grid_size, grid_size):
            for y in range(start_y, self.height + grid_size, grid_size):
                pygame.draw.line(self.screen, COLORS["GRAY"], (x - 3, y), (x + 3, y), 1)
                pygame.draw.line(self.screen, COLORS["GRAY"], (x, y - 3), (x, y + 3), 1)

        # Render obstacles
        for ox, oy, orad in self.obstacles:
            pygame.draw.circle(self.screen, COLORS["DARK_GRAY"], (int(ox + offset_x), int(oy + offset_y)), int(orad))
            pygame.draw.circle(self.screen, COLORS["GRAY"], (int(ox + offset_x), int(oy + offset_y)), int(orad), 1)

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

        # Draw other players
        for pid, p in self.players.items():
            if pid == self.self_player_id:
                continue
            draw_player(
                self.screen,
                p["x"] + offset_x,
                p["y"] + offset_y,
                COLORS["GRAY"],
                is_self=False,
                username=p["username"],
                is_alive=p.get("is_alive", True),
                font=self.medium_font
            )

        # Draw self player
        draw_player(
            self.screen,
            self.self_x + offset_x,
            self.self_y + offset_y,
            COLORS["WHITE"],
            is_self=True,
            username=self.network.username,
            is_alive=(self.status != "game_over"),
            font=self.medium_font
        )

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
