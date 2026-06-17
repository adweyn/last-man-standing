"""
menu.py — Pygame menu state manager.
Renders elegant landing page, credentials panels, deposit systems, and live tier lobbies.
"""

import pygame
import time
import math
import random
from typing import Dict, Any, List, Optional

from config import COLORS, FONT_SIZES, TIER_INFO
from assets import generate_particle, update_particles, draw_particles
from network import NetworkManager


class TextInputField:
    def __init__(self, x: int, y: int, w: int, h: int, label: str = "", is_password: bool = False):
        self.rect = pygame.Rect(x, y, w, h)
        self.text = ""
        self.label = label
        self.is_password = is_password
        self.active = False
        self.color = COLORS["GRAY"]

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Returns True if text changed."""
        if event.type == pygame.MOUSEBUTTONDOWN:
            if self.rect.collidepoint(event.pos):
                self.active = True
                self.color = COLORS["WHITE"]
            else:
                self.active = False
                self.color = COLORS["GRAY"]
            return True

        if event.type == pygame.KEYDOWN and self.active:
            if event.key == pygame.K_BACKSPACE:
                self.text = self.text[:-1]
            elif event.key in (pygame.K_RETURN, pygame.K_TAB):
                self.active = False
                self.color = COLORS["GRAY"]
            else:
                # Add text (caps/symbols are handled by event.unicode)
                if event.unicode and ord(event.unicode) >= 32:
                    if len(self.text) < 20:
                        self.text += event.unicode
            return True
        return False

    def draw(self, surface: pygame.Surface, font: pygame.font.Font):
        # Draw label
        if self.label:
            lbl_surf = font.render(self.label, True, COLORS["ACCENT"])
            surface.blit(lbl_surf, (self.rect.x, self.rect.y - 22))

        # Box
        pygame.draw.rect(surface, COLORS["PANEL_BG"], self.rect)
        pygame.draw.rect(surface, self.color, self.rect, 2 if self.active else 1)

        # Text render
        display_text = "*" * len(self.text) if self.is_password else self.text
        # Cursor flash
        if self.active and (int(time.time() * 2) % 2 == 0):
            display_text += "|"

        txt_surf = font.render(display_text, True, COLORS["WHITE"])
        surface.blit(txt_surf, (self.rect.x + 8, self.rect.y + (self.rect.height - txt_surf.get_height()) // 2))


class MainMenu:
    def __init__(self, screen: pygame.Surface, network: NetworkManager):
        self.screen = screen
        self.network = network
        self.width, self.height = screen.get_size()

        # Fonts
        self.title_font = pygame.font.SysFont("Outfit", FONT_SIZES["TITLE"], bold=True)
        self.large_font = pygame.font.SysFont("Outfit", FONT_SIZES["LARGE"])
        self.medium_font = pygame.font.SysFont("Inter", FONT_SIZES["MEDIUM"])
        self.small_font = pygame.font.SysFont("Inter", FONT_SIZES["SMALL"])

        # States: "main", "login", "register", "tier_select", "loading"
        self.state = "main"

        # Form Fields
        self.fields: Dict[str, TextInputField] = {}
        self._init_forms()

        # Particles background
        self.particles: List[Dict[str, Any]] = []
        
        # User details cached
        self.player_profile: Optional[dict] = None
        self.tier_stats: Dict[int, dict] = {}

        # Messages
        self.status_msg = ""
        self.status_color = COLORS["WHITE"]

        # Selection state
        self.selected_tier: Optional[int] = None

    def _init_forms(self):
        cx = self.width // 2
        cy = self.height // 2
        self.fields = {
            "login_user": TextInputField(cx - 150, cy - 60, 300, 40, "Username"),
            "login_pass": TextInputField(cx - 150, cy + 20, 300, 40, "Password", is_password=True),
            "reg_user": TextInputField(cx - 150, cy - 100, 300, 40, "Username"),
            "reg_pass": TextInputField(cx - 150, cy - 20, 300, 40, "Password", is_password=True),
            "reg_email": TextInputField(cx - 150, cy + 60, 300, 40, "Email (Optional)"),
        }

    def update(self, dt: float):
        # Update particles
        if len(self.particles) < 40 and random.random() < 0.15:
            # Spawn at bottom/sides
            px = random.randint(0, self.width)
            py = self.height - 2
            self.particles.append(generate_particle(px, py, COLORS["GRAY"], speed=0.5))

        self.particles = update_particles(self.particles, dt)

        # Keep fetching stats on lobby tier selection screen every 5 seconds (non-blocking)
        if self.state == "tier_select":
            if not hasattr(self, "_lobby_fetch_timer"):
                self._lobby_fetch_timer = 0.0
            self._lobby_fetch_timer += dt
            if self._lobby_fetch_timer >= 5.0:
                self._lobby_fetch_timer = 0.0
                self.refresh_lobby_data()

    def refresh_lobby_data(self):
        """Fetches live balance and server player counts from REST server asynchronously in a background thread."""
        if getattr(self, "_fetching_lobby_data", False):
            return
        self._fetching_lobby_data = True

        def _fetch():
            try:
                prof = self.network.get_profile()
                if prof:
                    self.player_profile = prof
                for t in [1, 2, 3]:
                    stats = self.network.get_tier_stats(t)
                    if stats:
                        self.tier_stats[t] = stats
            except Exception:
                pass
            finally:
                self._fetching_lobby_data = False

        import threading
        threading.Thread(target=_fetch, daemon=True).start()

    def handle_event(self, event: pygame.event.Event) -> Optional[int]:
        """Handles menu input clicks. Returns selected tier ID if game starts, else None."""
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos

            # Handle form field focuses
            if self.state == "login":
                self.fields["login_user"].handle_event(event)
                self.fields["login_pass"].handle_event(event)
            elif self.state == "register":
                self.fields["reg_user"].handle_event(event)
                self.fields["reg_pass"].handle_event(event)
                self.fields["reg_email"].handle_event(event)

            # Button Actions
            act = self._check_button_clicks(mx, my)
            if act:
                return act

        # Keyboards on active fields
        if event.type == pygame.KEYDOWN:
            if self.state == "login":
                self.fields["login_user"].handle_event(event)
                self.fields["login_pass"].handle_event(event)
                if event.key == pygame.K_RETURN:
                    return self._check_button_clicks(0, 0, shortcut="login_submit")
            elif self.state == "register":
                self.fields["reg_user"].handle_event(event)
                self.fields["reg_pass"].handle_event(event)
                self.fields["reg_email"].handle_event(event)
                if event.key == pygame.K_RETURN:
                    return self._check_button_clicks(0, 0, shortcut="reg_submit")

        return None

    def _check_button_clicks(self, mx: int, my: int, shortcut: str = "") -> Optional[int]:
        cx = self.width // 2

        # ─── MAIN STATE BUTTONS ──────────────────────────────────────────────────
        if self.state == "main" or shortcut:
            # Login button
            if shortcut == "login_submit" or (cx - 120 <= mx <= cx + 120 and 340 <= my <= 390):
                self.state = "login"
                self.status_msg = ""
            # Register button
            elif cx - 120 <= mx <= cx + 120 and 410 <= my <= 460:
                self.state = "register"
                self.status_msg = ""

        # ─── LOGIN STATE BUTTONS ─────────────────────────────────────────────────
        elif self.state == "login":
            # Submit Login
            if shortcut == "login_submit" or (cx - 120 <= mx <= cx + 120 and 420 <= my <= 465):
                username = self.fields["login_user"].text.strip()
                password = self.fields["login_pass"].text.strip()
                if not username or not password:
                    self.status_msg = "Please enter all fields."
                    self.status_color = COLORS["RED"]
                    return None
                
                success, msg = self.network.login(username, password)
                if success:
                    self.state = "tier_select"
                    self.refresh_lobby_data()
                    self.status_msg = ""
                else:
                    self.status_msg = msg
                    self.status_color = COLORS["RED"]
            # Back to main
            elif cx - 120 <= mx <= cx + 120 and 485 <= my <= 530:
                self.state = "main"

        # ─── REGISTER STATE BUTTONS ──────────────────────────────────────────────
        elif self.state == "register":
            # Submit Register
            if shortcut == "reg_submit" or (cx - 120 <= mx <= cx + 120 and 480 <= my <= 525):
                username = self.fields["reg_user"].text.strip()
                password = self.fields["reg_pass"].text.strip()
                email = self.fields["reg_email"].text.strip()
                if not username or not password:
                    self.status_msg = "Username & Password required."
                    self.status_color = COLORS["RED"]
                    return None
                
                success, msg = self.network.register(username, password, email if email else None)
                if success:
                    self.state = "tier_select"
                    self.refresh_lobby_data()
                    self.status_msg = ""
                else:
                    self.status_msg = msg
                    self.status_color = COLORS["RED"]
            # Back
            elif cx - 120 <= mx <= cx + 120 and 545 <= my <= 590:
                self.state = "main"

        # ─── TIER SELECTION LOBBY ────────────────────────────────────────────────
        elif self.state == "tier_select":
            # Three cards horizontally
            card_w = 260
            card_h = 320
            gap = 40
            start_x = cx - (card_w * 3 + gap * 2) // 2

            for i, tier in enumerate([1, 2, 3]):
                tx = start_x + i * (card_w + gap)
                ty = 220
                if tx <= mx <= tx + card_w and ty <= my <= ty + card_h:
                    self.selected_tier = tier
                    self.status_msg = ""
                    # Check balance vs fee
                    fee = TIER_INFO[tier]["fee"]
                    bal = self.player_profile.get("balance", 0.0) if self.player_profile else 0.0
                    
                    if bal < fee:
                        self.status_msg = f"Insufficient funds. Need ${fee:.2f}."
                        self.status_color = COLORS["RED"]
                        return None

                    # Call join api
                    success, err = self.network.join_tier(tier)
                    if success:
                        # Success joining server lobby, start the websocket!
                        self.network.connect()
                        return tier
                    else:
                        self.status_msg = err
                        self.status_color = COLORS["RED"]

            # Deposit mock money button
            if 30 <= mx <= 180 and self.height - 70 <= my <= self.height - 30:
                success, new_bal, err = self.network.deposit(10.00)
                if success:
                    if self.player_profile:
                        self.player_profile["balance"] = new_bal
                    self.status_msg = "+$10.00 deposited (Mock mode)."
                    self.status_color = COLORS["GREEN"]
                else:
                    self.status_msg = err
                    self.status_color = COLORS["RED"]

            # Register mock FCM Push token
            if self.width - 250 <= mx <= self.width - 30 and self.height - 70 <= my <= self.height - 30:
                mock_token = f"mock_token_{random.randint(1000, 9999)}_{self.network.username}"
                success = self.network.update_fcm_token(mock_token)
                if success:
                    if self.player_profile:
                        self.player_profile["fcm_registered"] = True
                    self.status_msg = "Mobile Alert Registered successfully."
                    self.status_color = COLORS["GREEN"]
                else:
                    self.status_msg = "FCM registration failed."
                    self.status_color = COLORS["RED"]

            # Logout
            if cx - 60 <= mx <= cx + 60 and self.height - 65 <= my <= self.height - 30:
                self.state = "main"
                self.network.auth_token = None
                self.network.username = None
                self.player_profile = None

        return None

    def draw(self):
        self.screen.fill(COLORS["BG"])

        # Draw background particles
        draw_particles(self.screen, self.particles)

        cx = self.width // 2

        # ─── LOGO HEADER ────────────────────────────────────────────────────────
        # Render a sleek shadow offset for depth glow
        glow_val = abs(math.sin(pygame.time.get_ticks() / 400.0))
        glow_color = (int(80 * glow_val), int(80 * glow_val), int(80 * glow_val))
        
        logo_shadow = self.title_font.render("LAST MAN STANDING", True, glow_color)
        self.screen.blit(logo_shadow, (cx - logo_shadow.get_width() // 2 + 2, 72))

        logo = self.title_font.render("LAST MAN STANDING", True, COLORS["WHITE"])
        self.screen.blit(logo, (cx - logo.get_width() // 2, 70))

        tagline = self.small_font.render("PERMADEATH ELIMINATION  •  HIGH-STAKES TOURNAMENT", True, COLORS["GRAY"])
        self.screen.blit(tagline, (cx - tagline.get_width() // 2, 145))

        # ─── DRAW SCREENS ────────────────────────────────────────────────────────
        if self.state == "main":
            self._draw_main_menu()
        elif self.state == "login":
            self._draw_login_screen()
        elif self.state == "register":
            self._draw_register_screen()
        elif self.state == "tier_select":
            self._draw_tier_select()

        # Status text rendering (errors / successes)
        if self.status_msg:
            lbl = self.medium_font.render(self.status_msg, True, self.status_color)
            self.screen.blit(lbl, (cx - lbl.get_width() // 2, self.height - 110))

    def _draw_main_menu(self):
        cx = self.width // 2
        # Buttons
        self._draw_button("LOGIN", cx - 120, 340, 240, 50)
        self._draw_button("REGISTER", cx - 120, 410, 240, 50)

    def _draw_login_screen(self):
        self.fields["login_user"].draw(self.screen, self.medium_font)
        self.fields["login_pass"].draw(self.screen, self.medium_font)
        cx = self.width // 2
        self._draw_button("SUBMIT", cx - 120, 420, 240, 45)
        self._draw_button("BACK", cx - 120, 485, 240, 45)

    def _draw_register_screen(self):
        self.fields["reg_user"].draw(self.screen, self.medium_font)
        self.fields["reg_pass"].draw(self.screen, self.medium_font)
        self.fields["reg_email"].draw(self.screen, self.medium_font)
        cx = self.width // 2
        self._draw_button("CREATE ACCOUNT", cx - 120, 480, 240, 45)
        self._draw_button("BACK", cx - 120, 545, 240, 45)

    def _draw_tier_select(self):
        cx = self.width // 2
        mx, my = pygame.mouse.get_pos()

        # Balance display
        bal = 0.0
        if self.player_profile:
            bal = self.player_profile.get("balance", 0.0)
        
        bal_txt = self.large_font.render(f"ACCOUNT BALANCE: ${bal:.2f}", True, COLORS["WHITE"])
        self.screen.blit(bal_txt, (cx - bal_txt.get_width() // 2, 175))

        # Horizontal tier cards
        card_w = 260
        card_h = 320
        gap = 40
        start_x = cx - (card_w * 3 + gap * 2) // 2

        for i, tier in enumerate([1, 2, 3]):
            info = TIER_INFO[tier]
            tx = start_x + i * (card_w + gap)
            ty = 220

            # Get database stats
            stats = self.tier_stats.get(tier, {"total": 0, "alive": 0, "prize_pool": 0.0})
            
            # Hover check
            is_hover = (tx <= mx <= tx + card_w and ty <= my <= ty + card_h)
            border_col = COLORS["WHITE"] if is_hover else COLORS["PANEL_BORDER"]
            bg_col = (24, 24, 24) if is_hover else COLORS["PANEL_BG"]

            # Draw card outline
            pygame.draw.rect(self.screen, bg_col, (tx, ty, card_w, card_h), border_radius=8)
            pygame.draw.rect(self.screen, border_col, (tx, ty, card_w, card_h), 2 if is_hover else 1, border_radius=8)

            # Card text details
            lbl = self.large_font.render(info["label"], True, COLORS["WHITE"])
            self.screen.blit(lbl, (tx + (card_w - lbl.get_width()) // 2, ty + 24))

            desc = self.small_font.render(info["desc"], True, COLORS["GRAY"])
            self.screen.blit(desc, (tx + (card_w - desc.get_width()) // 2, ty + 60))

            # Entry fee
            fee_lbl = self.title_font.render(f"${info['fee']:.0f}", True, COLORS["WHITE"])
            # shrink font if needed
            self.screen.blit(fee_lbl, (tx + (card_w - fee_lbl.get_width()) // 2, ty + 95))
            
            # Line separator
            pygame.draw.line(self.screen, border_col, (tx + 30, ty + 180), (tx + card_w - 30, ty + 180), 1)

            # Live count / prize
            prize = stats.get("prize_pool", 0.0)
            prize_txt = self.medium_font.render(f"Prize: ${prize:.2f}", True, COLORS["YELLOW"])
            self.screen.blit(prize_txt, (tx + (card_w - prize_txt.get_width()) // 2, ty + 200))

            alive = stats.get("alive", 0)
            total = stats.get("total", 0)
            alive_txt = self.small_font.render(f"Survivors: {alive} / {total}", True, COLORS["GRAY"])
            self.screen.blit(alive_txt, (tx + (card_w - alive_txt.get_width()) // 2, ty + 235))

            # Boss state info
            b_state = stats.get("boss_status", {}).get("state", "sleeping")
            b_col = COLORS["RED"] if b_state != "sleeping" else COLORS["GRAY"]
            b_txt = self.small_font.render(f"Boss: {b_state.upper()}", True, b_col)
            self.screen.blit(b_txt, (tx + (card_w - b_txt.get_width()) // 2, ty + 270))

        # Bottom Utilities
        # Mock Deposit Button
        self._draw_button("+$10 DEPOSIT", 30, self.height - 70, 150, 40)

        # Mock Mobile Alert Registration
        fcm_reg = self.player_profile.get("fcm_registered", False) if self.player_profile else False
        btn_txt = "Alert Status: Connected" if fcm_reg else "LINK MOBILE ALERTS"
        self._draw_button(btn_txt, self.width - 250, self.height - 70, 220, 40, active=fcm_reg)

        # Logout
        self._draw_button("LOGOUT", cx - 60, self.height - 65, 120, 35)

    def _draw_button(self, label: str, x: int, y: int, w: int, h: int, active: bool = False):
        mx, my = pygame.mouse.get_pos()
        is_hover = (x <= mx <= x + w and y <= my <= y + h)

        col = COLORS["WHITE"] if is_hover else COLORS["GRAY"]
        if active:
            col = COLORS["GREEN"]

        bg = (24, 24, 24) if is_hover else COLORS["PANEL_BG"]

        pygame.draw.rect(self.screen, bg, (x, y, w, h), border_radius=4)
        pygame.draw.rect(self.screen, col, (x, y, w, h), 2 if is_hover else 1, border_radius=4)

        txt = self.medium_font.render(label, True, COLORS["WHITE"] if is_hover else COLORS["ACCENT"])
        self.screen.blit(txt, (x + (w - txt.get_width()) // 2, y + (h - txt.get_height()) // 2))
