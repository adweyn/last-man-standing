"""
config.py - Central configuration for Last Man Standing server.
All tunable constants, environment variables, and tier definitions live here.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Tier definitions
# ---------------------------------------------------------------------------
# Each tier has an entry fee (in coins/credits) and a prize multiplier that
# determines what fraction of the total prize pool goes to the winner.
TIERS: dict[int, dict] = {
    1: {
        "entry_fee": 1,
        "prize_multiplier": 0.90,
        "name": "Tier I – Bronze",
    },
    2: {
        "entry_fee": 5,
        "prize_multiplier": 0.90,
        "name": "Tier II – Silver",
    },
    3: {
        "entry_fee": 10,
        "prize_multiplier": 0.90,
        "name": "Tier III – Gold",
    },
}

# ---------------------------------------------------------------------------
# Boss behaviour
# ---------------------------------------------------------------------------
BOSS_MIN_INTERVAL: int = 3600 * 20        # 20 hours in seconds
BOSS_MAX_INTERVAL: int = 3600 * 24        # 24 hours in seconds
BOSS_WARNING_SECONDS: int = 90            # Grace period before hunt begins
BOSS_HUNT_SECONDS: int = 60              # How long the boss actively hunts
BOSS_KILL_RADIUS: int = 80               # Pixels: boss kill radius
BOSS_IDLE_SECONDS: float = 5.0           # Seconds without movement = AFK kill

# Difficulty director
DIFFICULTY_MAX_LEVEL: int = int(os.getenv("DIFFICULTY_MAX_LEVEL", "8"))
DIFFICULTY_LEVEL_PER_SURVIVOR_DAY: float = float(os.getenv("DIFFICULTY_LEVEL_PER_SURVIVOR_DAY", "0.75"))
DIFFICULTY_TIER_BONUS: dict[int, int] = {1: 0, 2: 1, 3: 2}
HAZARD_MAX_PER_TIER: int = int(os.getenv("HAZARD_MAX_PER_TIER", "10"))
HAZARD_TICK_INTERVAL: float = float(os.getenv("HAZARD_TICK_INTERVAL", "0.5"))
HAZARD_DAMAGE_STILL_SECONDS: float = float(os.getenv("HAZARD_DAMAGE_STILL_SECONDS", "2.5"))

# ---------------------------------------------------------------------------
# Movement / world
# ---------------------------------------------------------------------------
WORLD_WIDTH: int = 4000
WORLD_HEIGHT: int = 4000
BOSS_SPEED: float = 120.0    # px / second
PLAYER_SPEED: float = 160.0  # px / second (used for validation)

# ---------------------------------------------------------------------------
# AFK enforcement
# ---------------------------------------------------------------------------
AFK_DAILY_MOVES_REQUIRED: int = 2        # Minimum distinct moves per day
AFK_WARNING_HOUR_UTC: int = 18           # 18:00 UTC → send warning
AFK_DEADLINE_HOUR_UTC: int = 20          # 20:00 UTC → eliminate if not met
AFK_DISTINCT_MOVE_PX: float = 50.0       # px threshold for "distinct" movement

# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------
SERVER_HOST: str = os.getenv("SERVER_HOST", "0.0.0.0")
WS_PORT: int = int(os.getenv("SERVER_PORT", "8765"))
API_PORT: int = int(os.getenv("PORT", os.getenv("API_PORT", "8000")))

# ---------------------------------------------------------------------------
# Database / cache
# ---------------------------------------------------------------------------
DATABASE_URL: str = os.getenv("DATABASE_URL", "lms.db")
REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ---------------------------------------------------------------------------
# Firebase Cloud Messaging
# ---------------------------------------------------------------------------
FCM_SERVER_KEY: str | None = os.getenv("FCM_SERVER_KEY")  # None → notifications skipped
FCM_API_URL: str = "https://fcm.googleapis.com/fcm/send"

# ---------------------------------------------------------------------------
# Telegram Bot
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN: str | None = os.getenv("TELEGRAM_BOT_TOKEN")  # None -> Bot disabled
TELEGRAM_MINI_APP_URL: str = os.getenv("TELEGRAM_MINI_APP_URL", "http://localhost:8000/telegram-app")
ALLOW_MOCK_PAYMENTS: bool = os.getenv("ALLOW_MOCK_PAYMENTS", "false").lower() == "true"

# ---------------------------------------------------------------------------
# Monetization
# ---------------------------------------------------------------------------
# Real-money payments should buy digital goods only. Tournament entries and
# rewards remain internal credits to avoid mixing player-funded cash prizes with
# random elimination mechanics.
SHOP_PRODUCTS: dict[str, dict] = {
    "credits_25": {
        "title": "25 Credits",
        "description": "Adds 25 internal tournament credits.",
        "stars": 25,
        "grant_credits": 25.0,
        "grant_chaos_tickets": 0,
        "premium_days": 0,
    },
    "credits_120": {
        "title": "120 Credits",
        "description": "Adds 120 internal tournament credits.",
        "stars": 99,
        "grant_credits": 120.0,
        "grant_chaos_tickets": 0,
        "premium_days": 0,
    },
    "chaos_ticket_3": {
        "title": "3 Chaos Tickets",
        "description": "Unlocks optional No Mercy rooms when available.",
        "stars": 39,
        "grant_credits": 0.0,
        "grant_chaos_tickets": 3,
        "premium_days": 0,
    },
    "season_pass_30": {
        "title": "30 Day Season Pass",
        "description": "Premium status, event access, and starter credits.",
        "stars": 149,
        "grant_credits": 30.0,
        "grant_chaos_tickets": 2,
        "premium_days": 30,
    },
}

# ---------------------------------------------------------------------------
# Auth / security
# ---------------------------------------------------------------------------
SECRET_KEY: str = os.getenv("SECRET_KEY", "CHANGE_ME_IN_PRODUCTION_pleaseSetEnvVar")
JWT_ALGORITHM: str = "HS256"
JWT_EXPIRE_DAYS: int = int(os.getenv("JWT_EXPIRE_DAYS", "30"))
JWT_EXPIRE_HOURS = 24 * JWT_EXPIRE_DAYS

# ---------------------------------------------------------------------------
# Broadcast cadence
# ---------------------------------------------------------------------------
PLAYER_LIST_INTERVAL: float = 0.1   # seconds between full player-list broadcasts
BOSS_UPDATE_INTERVAL: float = 0.1   # seconds between boss-state broadcasts
BOSS_MOVE_INTERVAL: float = 0.1     # seconds between boss AI position updates

# Aliases for boss AI
BOSS_RADIUS = BOSS_KILL_RADIUS
BOSS_WARNING_SEC = BOSS_WARNING_SECONDS
BOSS_HUNT_SEC = BOSS_HUNT_SECONDS
BOSS_MIN_SLEEP = BOSS_MIN_INTERVAL
BOSS_MAX_SLEEP = BOSS_MAX_INTERVAL
BOSS_AFK_STILL_SECONDS = BOSS_IDLE_SECONDS
# ---------------------------------------------------------------------------
# Prize / rake logic
# ---------------------------------------------------------------------------
PRIZE_RAKE: float = 0.10  # 10% host fee rake

# ---------------------------------------------------------------------------
# Crystals and Quests Play-to-Earn Config
# ---------------------------------------------------------------------------
CRYSTAL_SPAWN_INTERVAL: float = 30.0   # Attempt to spawn a crystal every 30s
CRYSTAL_MAX_PER_TIER: int = 8          # Maximum concurrent crystals in arena
CRYSTAL_MIN_VALUE: float = 0.10        # Minimum mock $ value of a crystal
CRYSTAL_MAX_VALUE: float = 0.50        # Maximum mock $ value of a crystal
CRYSTAL_SPAWN_RADIUS: int = 15        # Radius of a crystal for drawing/collisions

MAP_OBJECTIVE_SPAWN_INTERVAL: float = float(os.getenv("MAP_OBJECTIVE_SPAWN_INTERVAL", "18.0"))
MAP_OBJECTIVE_MAX_PER_TIER: int = int(os.getenv("MAP_OBJECTIVE_MAX_PER_TIER", "5"))
MAP_OBJECTIVE_RADIUS: int = int(os.getenv("MAP_OBJECTIVE_RADIUS", "28"))
MAP_OBJECTIVE_MIN_REWARD: float = float(os.getenv("MAP_OBJECTIVE_MIN_REWARD", "0.35"))
MAP_OBJECTIVE_MAX_REWARD: float = float(os.getenv("MAP_OBJECTIVE_MAX_REWARD", "1.25"))

QUEST_EXPLORER_TARGET: float = 3000.0  # Pixels of distance needed
QUEST_EXPLORER_REWARD: float = 0.30    # Reward in mock $

QUEST_SURVIVOR_TARGET: float = 120.0   # Seconds of survival needed
QUEST_SURVIVOR_REWARD: float = 0.40    # Reward in mock $

QUEST_SCAVENGER_TARGET: float = 5.0    # Number of crystals collected needed
QUEST_SCAVENGER_REWARD: float = 0.50   # Reward in mock $
