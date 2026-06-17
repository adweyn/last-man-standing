"""
chat.py — In-game overlay chat.
Manages semi-transparent chat feeds, fading messages, text input boxes, and WebSocket broadcasting.
"""

import pygame
import time
from typing import List, Dict, Any
from config import COLORS, CHAT_MAX_CHARS, CHAT_VISIBLE_LINES, CHAT_FADE_SECONDS

class ChatSystem:
    def __init__(self):
        # List of message dicts: {"username": str, "message": str, "timestamp": float}
        self.messages: List[Dict[str, Any]] = []
        
        # Typing state variables
        self.typing = False
        self.input_text = ""
        
        # UI Coordinates
        self.x = 20
        self.y = 520  # relative layout from bottom
        self.width = 400
        self.height = 140

        self.font = pygame.font.SysFont("Inter", 16)
        self.medium_font = pygame.font.SysFont("Inter", 18)

    def add_message(self, username: str, msg: str):
        """Append incoming chat to the window queue."""
        self.messages.append({
            "username": username,
            "message": msg[:CHAT_MAX_CHARS],
            "timestamp": time.time()
        })
        # Prune to maximum size
        if len(self.messages) > 100:
            self.messages.pop(0)

    def handle_event(self, event: pygame.event.Event, send_callback: callable) -> bool:
        """
        Processes key presses for typing.
        Returns True if event is consumed (prevents player WASD moving while typing).
        """
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_RETURN:
                if self.typing:
                    # Submit message
                    trimmed = self.input_text.strip()
                    if trimmed:
                        send_callback(trimmed)
                    self.input_text = ""
                    self.typing = False
                else:
                    self.typing = True
                return True

            if self.typing:
                if event.key == pygame.K_BACKSPACE:
                    self.input_text = self.input_text[:-1]
                elif event.key == pygame.K_ESCAPE:
                    self.typing = False
                    self.input_text = ""
                else:
                    # Append printable unicode characters
                    if event.unicode and ord(event.unicode) >= 32:
                        if len(self.input_text) < CHAT_MAX_CHARS:
                            self.input_text += event.unicode
                return True

        return self.typing

    def draw(self, surface: pygame.Surface):
        now = time.time()
        scr_h = surface.get_size()[1]
        
        # Draw messages bottom-up above input field
        y_offset = scr_h - 100
        
        # Filter active messages (only show messages under fade duration or if typing)
        active_msgs = []
        for m in reversed(self.messages):
            age = now - m["timestamp"]
            if age < CHAT_FADE_SECONDS or self.typing:
                active_msgs.append((m, age))
            if len(active_msgs) >= CHAT_VISIBLE_LINES:
                break

        # Draw messages box backdrop (if typing or there are active messages)
        if self.typing or active_msgs:
            box_height = len(active_msgs) * 20 + 10
            # Semi transparent black backdrop
            box_surf = pygame.Surface((self.width, box_height), pygame.SRCALPHA)
            box_surf.fill((0, 0, 0, 100))
            surface.blit(box_surf, (self.x, y_offset - box_height))

            # Draw messages text
            curr_y = y_offset - 20
            for m, age in active_msgs:
                # Fade text if approaching age limit
                alpha = 255
                if not self.typing and age > (CHAT_FADE_SECONDS - 5):
                    alpha = int(255 * (1.0 - (age - (CHAT_FADE_SECONDS - 5)) / 5.0))
                    alpha = max(0, min(255, alpha))

                user_col = (COLORS["WHITE"][0], COLORS["WHITE"][1], COLORS["WHITE"][2], alpha)
                msg_col = (COLORS["ACCENT"][0], COLORS["ACCENT"][1], COLORS["ACCENT"][2], alpha)

                # Render user prefix
                prefix = f"{m['username']}: "
                pref_surf = self.font.render(prefix, True, user_col)
                
                # Render message body
                body_surf = self.font.render(m["message"], True, msg_col)
                
                # Blit line item
                line_surf = pygame.Surface((self.width, 20), pygame.SRCALPHA)
                line_surf.blit(pref_surf, (8, 0))
                line_surf.blit(body_surf, (8 + pref_surf.get_width(), 0))
                
                surface.blit(line_surf, (self.x, curr_y))
                curr_y -= 20

        # Draw input box if typing mode enabled
        if self.typing:
            in_rect = pygame.Rect(self.x, y_offset, self.width, 32)
            pygame.draw.rect(surface, COLORS["PANEL_BG"], in_rect)
            pygame.draw.rect(surface, COLORS["WHITE"], in_rect, 1)

            display_text = self.input_text
            # Cursor blink
            if int(time.time() * 2) % 2 == 0:
                display_text += "|"

            txt_surf = self.medium_font.render(display_text, True, COLORS["WHITE"])
            surface.blit(txt_surf, (self.x + 8, y_offset + (32 - txt_surf.get_height()) // 2))
            
            # Simple typing indicator text above
            ind = self.font.render("Press Enter to send / Escape to close", True, COLORS["GRAY"])
            surface.blit(ind, (self.x, y_offset - 20 - (len(active_msgs) * 20 if active_msgs else 0)))
