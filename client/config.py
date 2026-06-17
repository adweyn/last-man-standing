# config.py — Last Man Standing Client Configuration

import pygame

# ─── Network ──────────────────────────────────────────────────────────────────
SERVER_WS_URL  = "ws://localhost:8765"
SERVER_API_URL = "http://localhost:8000"

# ─── Display ──────────────────────────────────────────────────────────────────
SCREEN_WIDTH  = 1280
SCREEN_HEIGHT = 720
FPS           = 60

# ─── World ────────────────────────────────────────────────────────────────────
WORLD_WIDTH  = 4000
WORLD_HEIGHT = 4000

# ─── Colors (hex → parsed at import time) ─────────────────────────────────────
def _hex(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

COLORS: dict = {
    "BG":        _hex("#0a0a0a"),
    "WHITE":     _hex("#f0f0f0"),
    "GRAY":      _hex("#888888"),
    "DARK_GRAY": _hex("#333333"),
    "RED":       _hex("#cc0000"),
    "GREEN":     _hex("#00cc44"),
    "ACCENT":    _hex("#e0e0e0"),
    "BOSS_COLOR":_hex("#1a0000"),
    "BOSS_GLOW": _hex("#ff2200"),
    # Extras used across the client
    "BLACK":     (0,   0,   0),
    "YELLOW":    _hex("#ccaa00"),
    "ORANGE":    _hex("#cc5500"),
    "PANEL_BG":  (18,  18,  18),
    "PANEL_BORDER": (55, 55, 55),
}

# ─── Font sizes ───────────────────────────────────────────────────────────────
FONT_SIZES = {
    "SMALL":  14,
    "MEDIUM": 20,
    "LARGE":  32,
    "TITLE":  72,
}

# ─── Gameplay geometry ────────────────────────────────────────────────────────
PLAYER_RADIUS  = 12
BOSS_RADIUS    = 40
CAMERA_SPEED   = 8

# ─── Boss / game logic ────────────────────────────────────────────────────────
BOSS_WARNING_SECONDS  = 10   # countdown from WARNING to HUNTING
BOSS_HUNT_SECONDS     = 30   # how long boss hunts before retreating
DAILY_MOVES_LIMIT     = 2    # max free moves per 24 h period
CHAT_MAX_CHARS        = 120
CHAT_VISIBLE_LINES    = 8
CHAT_FADE_SECONDS     = 30

# ─── Tier fees / prize multipliers ────────────────────────────────────────────
TIER_INFO = {
    1: {"fee": 1.00,  "label": "TIER I",   "desc": "Entry Level"},
    2: {"fee": 5.00,  "label": "TIER II",  "desc": "Standard"},
    3: {"fee": 10.00, "label": "TIER III", "desc": "High Stakes"},
}

# ─── Animation / UI constants ────────────────────────────────────────────────
SCREEN_SHAKE_FRAMES    = 20
SCREEN_SHAKE_MAGNITUDE = 8
PARTICLE_POOL_MAX      = 512
MINIMAP_WIDTH          = 200
MINIMAP_HEIGHT         = 200
MINIMAP_MARGIN         = 16

# ─── Network timeouts ─────────────────────────────────────────────────────────
HTTP_TIMEOUT           = 8   # seconds
WS_PING_INTERVAL       = 20  # seconds
WS_RECONNECT_DELAY     = 3   # seconds between reconnect attempts
WS_MAX_RETRIES         = 5
