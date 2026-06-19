"""
hud.py — In-game Head Up Display overlay.
Visualizes tournament prizes, live survivor counts, Boss countdown alerts,
daily AFK movement checklists, and sliding elimination banners.
"""

import pygame
import math
import time
from typing import Dict, Any, List

from config import COLORS, FONT_SIZES

class HUD:
    def __init__(self):
        self.medium_font = pygame.font.SysFont("Inter", 20, bold=True)
        self.large_font = pygame.font.SysFont("Outfit", 28, bold=True)
        self.alert_font = pygame.font.SysFont("Outfit", 40, bold=True)
        self.small_font = pygame.font.SysFont("Inter", 14)

        # Elimination banners queue: [{"username": str, "reason": str, "time_added": float}]
        self.eliminations_queue: List[Dict[str, Any]] = []
        self.banner_duration = 4.0
        
        # Pre-allocated surface to avoid per-frame allocations during alerts
        self.banner_surf = pygame.Surface((420, 55), pygame.SRCALPHA)

    def add_elimination_alert(self, username: str, reason: str):
        self.eliminations_queue.append({
            "username": username,
            "reason": reason,
            "time_added": time.time()
        })

    def draw(self, surface: pygame.Surface, game_state: Dict[str, Any]):
        width, height = surface.get_size()
        now = time.time()

        # ─── TOP LEFT: TIER INFO & PRIZE ─────────────────────────────────────────
        tier_id = game_state.get("tier_id", 1)
        prize = game_state.get("prize_pool", 0.0)

        # Draw a dark backing panel
        panel_w = 200
        panel_h = 70
        pygame.draw.rect(surface, COLORS["PANEL_BG"], (15, 15, panel_w, panel_h), border_radius=6)
        pygame.draw.rect(surface, COLORS["PANEL_BORDER"], (15, 15, panel_w, panel_h), 1, border_radius=6)

        t_lbl = self.small_font.render(f"TIER {tier_id} TOURNAMENT", True, COLORS["GRAY"])
        surface.blit(t_lbl, (25, 22))

        p_lbl = self.large_font.render(f"{prize:.2f} CR", True, COLORS["WHITE"])
        surface.blit(p_lbl, (25, 40))

        # ─── TOP RIGHT: LIVE SURVIVOR COUNT ──────────────────────────────────────
        alive_count = game_state.get("alive_count", 0)
        total_count = game_state.get("total_count", 0)

        pygame.draw.rect(surface, COLORS["PANEL_BG"], (width - 175, 15, 160, 45), border_radius=6)
        pygame.draw.rect(surface, COLORS["PANEL_BORDER"], (width - 175, 15, 160, 45), 1, border_radius=6)

        surv_lbl = self.medium_font.render(f"ALIVE: {alive_count} / {total_count}", True, COLORS["GREEN"])
        surface.blit(surv_lbl, (width - 160, 26))

        # ─── BOTTOM RIGHT: AFK RULES / DAILY MOVES ───────────────────────────────
        # Displays two circles. Circles are filled based on daily_moves count
        moves_count = game_state.get("daily_moves", 0)
        
        afk_w = 210
        afk_h = 65
        afk_x = width - afk_w - 15
        afk_y = height - afk_h - 15
        
        pygame.draw.rect(surface, COLORS["PANEL_BG"], (afk_x, afk_y, afk_w, afk_h), border_radius=6)
        pygame.draw.rect(surface, COLORS["PANEL_BORDER"], (afk_x, afk_y, afk_w, afk_h), 1, border_radius=6)

        m_lbl = self.small_font.render("DAILY ACTIVITY CHECK", True, COLORS["GRAY"])
        surface.blit(m_lbl, (afk_x + 12, afk_y + 10))

        # Draw 2 activity check dots
        dot_y = afk_y + 40
        for i in range(2):
            dot_x = afk_x + 20 + i * 22
            is_filled = (moves_count > i)
            dot_color = COLORS["WHITE"] if is_filled else COLORS["DARK_GRAY"]
            
            pygame.draw.circle(surface, dot_color, (dot_x, dot_y), 6)
            pygame.draw.circle(surface, COLORS["WHITE"], (dot_x, dot_y), 6, 1)

        quota_lbl = self.small_font.render(f"{min(moves_count, 2)}/2 moves done", True, COLORS["WHITE"] if moves_count >= 2 else COLORS["ORANGE"])
        surface.blit(quota_lbl, (afk_x + 75, afk_y + 32))

        # ─── CENTER TOP: BOSS ALERTS ─────────────────────────────────────────────
        boss = game_state.get("boss_state", {})
        boss_state = boss.get("state", "sleeping")
        time_rem = boss.get("time_remaining", 0)

        if boss_state == "warning":
            # Pulsing countdown text
            pulse = abs(math.sin(time.time() * 6.0))
            warning_color = (255, int(50 + 100 * pulse), int(50 + 100 * pulse))
            
            alert_text = f"BOSS IS WAKING IN {time_rem}s!"
            alert_surf = self.alert_font.render(alert_text, True, warning_color)
            surface.blit(alert_surf, (width // 2 - alert_surf.get_width() // 2, 85))

            action_sub = self.medium_font.render("PREPARE TO RUN / HIDE IMMEDIATELY", True, COLORS["WHITE"])
            surface.blit(action_sub, (width // 2 - action_sub.get_width() // 2, 135))

        elif boss_state == "hunting":
            # Flashing WARNING border text
            flash = (int(time.time() * 4) % 2 == 0)
            text_color = COLORS["RED"] if flash else COLORS["WHITE"]

            alert_text = "WARNING: BOSS ACTIVE & HUNTING!"
            alert_surf = self.alert_font.render(alert_text, True, text_color)
            surface.blit(alert_surf, (width // 2 - alert_surf.get_width() // 2, 85))

            tgt_user = boss.get("target_username")
            if tgt_user:
                action_sub = self.medium_font.render(f"CURRENT TARGET: {tgt_user.upper()}", True, COLORS["YELLOW"])
                surface.blit(action_sub, (width // 2 - action_sub.get_width() // 2, 135))

        # ─── SLIDING ELIMINATION BANNER ──────────────────────────────────────────
        # Filter active banners
        self.eliminations_queue = [
            b for b in self.eliminations_queue if now - b["time_added"] < self.banner_duration
        ]

        if self.eliminations_queue:
            # Draw the first active banner in the queue
            banner = self.eliminations_queue[0]
            age = now - banner["time_added"]
            
            # Slide in and slide out logic
            slide_in_time = 0.5
            slide_out_time = 0.5
            
            target_y = 15  # finished y pos
            start_y = -80  # hidden y pos
            
            if age < slide_in_time:
                # slide down
                t = age / slide_in_time
                y_pos = start_y + (target_y - start_y) * (1.0 - (1.0 - t) ** 2)
            elif age > self.banner_duration - slide_out_time:
                # slide up
                t = (self.banner_duration - age) / slide_out_time
                y_pos = start_y + (target_y - start_y) * (1.0 - (1.0 - t) ** 2)
            else:
                y_pos = target_y

            # Draw alert banner box
            b_w = 420
            b_h = 55
            b_x = (width - b_w) // 2

            self.banner_surf.fill((0, 0, 0, 0))
            self.banner_surf.fill((0, 0, 0, 220))
            pygame.draw.rect(self.banner_surf, COLORS["RED"], (0, 0, b_w, b_h), 1, border_radius=4)

            # Text
            elim_text = f"{banner['username'].upper()} ELIMINATED"
            reason_text = "Killed by the Boss" if banner["reason"] == "boss" else f"Eliminated: {banner['reason']}"
            
            elim_surf = self.medium_font.render(elim_text, True, COLORS["WHITE"])
            reason_surf = self.small_font.render(reason_text, True, COLORS["GRAY"])

            self.banner_surf.blit(elim_surf, ((b_w - elim_surf.get_width()) // 2, 8))
            self.banner_surf.blit(reason_surf, ((b_w - reason_surf.get_width()) // 2, 30))

            surface.blit(self.banner_surf, (b_x, int(y_pos)))
