"""
database.py — Async SQLite database layer using aiosqlite.
Tables: players, tier_sessions, daily_activity, boss_events
"""

import asyncio
import hashlib
import logging
import sqlite3
import time
from datetime import date, datetime, timezone
from typing import Any, Optional

import aiosqlite
import bcrypt

from config import DATABASE_URL

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────
SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS players (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT  NOT NULL,
    email       TEXT    UNIQUE,
    fcm_token   TEXT,
    created_at  REAL    NOT NULL DEFAULT (unixepoch()),
    balance     REAL    NOT NULL DEFAULT 0.0,
    telegram_id INTEGER UNIQUE,
    premium_until REAL NOT NULL DEFAULT 0,
    chaos_tickets INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tier_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tier_id     INTEGER NOT NULL,
    player_id   INTEGER NOT NULL REFERENCES players(id),
    entry_time  REAL    NOT NULL DEFAULT (unixepoch()),
    is_alive    INTEGER NOT NULL DEFAULT 1,
    death_time  REAL,
    eliminated_by TEXT  -- 'boss' | 'afk' | 'admin'
);

CREATE TABLE IF NOT EXISTS daily_activity (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id   INTEGER NOT NULL REFERENCES players(id),
    date        TEXT    NOT NULL,  -- ISO date YYYY-MM-DD
    move_count  INTEGER NOT NULL DEFAULT 0,
    last_move_x REAL    NOT NULL DEFAULT 0,
    last_move_y REAL    NOT NULL DEFAULT 0,
    last_move_time REAL NOT NULL DEFAULT 0,
    UNIQUE(player_id, date)
);

CREATE TABLE IF NOT EXISTS boss_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tier_id     INTEGER NOT NULL,
    start_time  REAL    NOT NULL DEFAULT (unixepoch()),
    end_time    REAL,
    kills_count INTEGER NOT NULL DEFAULT 0,
    status      TEXT    NOT NULL DEFAULT 'warning'  -- warning | hunting | done
);

CREATE TABLE IF NOT EXISTS player_quests (
    player_id   INTEGER NOT NULL REFERENCES players(id),
    date        TEXT    NOT NULL,  -- YYYY-MM-DD
    quest_type  TEXT    NOT NULL,  -- 'explorer' | 'survivor' | 'scavenger'
    progress    REAL    NOT NULL DEFAULT 0.0,
    target      REAL    NOT NULL,
    reward      REAL    NOT NULL,
    is_claimed  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (player_id, date, quest_type)
);

CREATE TABLE IF NOT EXISTS payment_orders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id   INTEGER NOT NULL REFERENCES players(id),
    product_id  TEXT    NOT NULL,
    stars       INTEGER NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'pending',
    payload     TEXT    NOT NULL UNIQUE,
    telegram_payment_charge_id TEXT,
    created_at  REAL    NOT NULL DEFAULT (unixepoch()),
    paid_at     REAL
);
"""


async def init_db() -> None:
    """Create all tables if they do not exist."""
    async with aiosqlite.connect(DATABASE_URL) as db:
        await db.executescript(SCHEMA)
        await db.commit()
        # Add telegram_id column if it doesn't exist
        try:
            await db.execute("ALTER TABLE players ADD COLUMN telegram_id INTEGER UNIQUE")
            await db.commit()
        except sqlite3.OperationalError:
            pass
        for sql in (
            "ALTER TABLE players ADD COLUMN premium_until REAL NOT NULL DEFAULT 0",
            "ALTER TABLE players ADD COLUMN chaos_tickets INTEGER NOT NULL DEFAULT 0",
        ):
            try:
                await db.execute(sql)
                await db.commit()
            except sqlite3.OperationalError:
                pass
    logger.info("Database initialised at %s", DATABASE_URL)


# ─────────────────────────────────────────────────────────────────────────────
# Players
# ─────────────────────────────────────────────────────────────────────────────

async def create_player(username: str, password: str, email: Optional[str] = None) -> Optional[int]:
    """Hash password and insert a new player. Returns new player id or None on conflict."""
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    try:
        async with aiosqlite.connect(DATABASE_URL) as db:
            cur = await db.execute(
                "INSERT INTO players (username, password_hash, email) VALUES (?,?,?)",
                (username, hashed, email),
            )
            await db.commit()
            return cur.lastrowid
    except aiosqlite.IntegrityError:
        return None


async def create_telegram_player(telegram_id: int, username: str, email: Optional[str] = None) -> Optional[int]:
    """Creates a player linked to a Telegram ID, using a dummy password hash."""
    import uuid
    dummy_password = str(uuid.uuid4())
    hashed = bcrypt.hashpw(dummy_password.encode(), bcrypt.gensalt()).decode()
    try:
        async with aiosqlite.connect(DATABASE_URL) as db:
            cur = await db.execute(
                "INSERT INTO players (username, password_hash, email, telegram_id) VALUES (?,?,?,?)",
                (username, hashed, email, telegram_id),
            )
            await db.commit()
            return cur.lastrowid
    except aiosqlite.IntegrityError:
        return None


async def get_player_by_telegram_id(telegram_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM players WHERE telegram_id = ?",
            (telegram_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_player_by_username(username: str) -> Optional[dict]:
    async with aiosqlite.connect(DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM players WHERE username=? COLLATE NOCASE", (username,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_player_by_id(player_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM players WHERE id=?", (player_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def verify_password(username: str, password: str) -> Optional[dict]:
    """Return player dict if credentials are valid, else None."""
    player = await get_player_by_username(username)
    if not player:
        return None
    if bcrypt.checkpw(password.encode(), player["password_hash"].encode()):
        return player
    return None


async def update_fcm_token(player_id: int, fcm_token: str) -> None:
    async with aiosqlite.connect(DATABASE_URL) as db:
        await db.execute(
            "UPDATE players SET fcm_token=? WHERE id=?", (fcm_token, player_id)
        )
        await db.commit()


async def update_balance(player_id: int, delta: float) -> None:
    async with aiosqlite.connect(DATABASE_URL) as db:
        await db.execute(
            "UPDATE players SET balance=balance+? WHERE id=?", (delta, player_id)
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Payments / digital goods
# ---------------------------------------------------------------------------

async def create_payment_order(player_id: int, product_id: str, stars: int, payload: str) -> int:
    async with aiosqlite.connect(DATABASE_URL) as db:
        cur = await db.execute(
            """INSERT INTO payment_orders (player_id, product_id, stars, payload)
               VALUES (?, ?, ?, ?)""",
            (player_id, product_id, stars, payload),
        )
        await db.commit()
        return cur.lastrowid


async def get_payment_order_by_payload(payload: str) -> Optional[dict]:
    async with aiosqlite.connect(DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM payment_orders WHERE payload=?", (payload,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def fulfill_payment_order(payload: str, telegram_charge_id: str, product: dict) -> Optional[dict]:
    """
    Mark a Stars order as paid and grant the configured digital goods exactly once.
    Returns the updated player row, or None if the payload is unknown.
    """
    now = time.time()
    async with aiosqlite.connect(DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM payment_orders WHERE payload=?", (payload,)
        ) as cur:
            order = await cur.fetchone()
            if not order:
                return None

        order = dict(order)
        if order["status"] == "paid":
            async with db.execute("SELECT * FROM players WHERE id=?", (order["player_id"],)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

        premium_days = int(product.get("premium_days", 0))
        premium_until = 0.0
        if premium_days > 0:
            async with db.execute("SELECT premium_until FROM players WHERE id=?", (order["player_id"],)) as cur:
                row = await cur.fetchone()
                current_until = float(row[0] or 0) if row else 0.0
            premium_until = max(current_until, now) + premium_days * 86400

        await db.execute(
            """UPDATE payment_orders
               SET status='paid', telegram_payment_charge_id=?, paid_at=?
               WHERE id=? AND status='pending'""",
            (telegram_charge_id, now, order["id"]),
        )
        await db.execute(
            """UPDATE players
               SET balance=balance+?,
                   chaos_tickets=chaos_tickets+?,
                   premium_until=CASE WHEN ? > 0 THEN ? ELSE premium_until END
               WHERE id=?""",
            (
                float(product.get("grant_credits", 0.0)),
                int(product.get("grant_chaos_tickets", 0)),
                premium_days,
                premium_until,
                order["player_id"],
            ),
        )
        await db.commit()

        async with db.execute("SELECT * FROM players WHERE id=?", (order["player_id"],)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


# ─────────────────────────────────────────────────────────────────────────────
# Tier sessions
# ─────────────────────────────────────────────────────────────────────────────

async def join_tier(player_id: int, tier_id: int) -> Optional[int]:
    """Add player to tier. Returns session id."""
    try:
        async with aiosqlite.connect(DATABASE_URL) as db:
            # Enforce one active session per player
            async with db.execute(
                "SELECT id FROM tier_sessions WHERE player_id=? AND is_alive=1", (player_id,)
            ) as cur:
                if await cur.fetchone():
                    return None  # already in a game
            cur = await db.execute(
                "INSERT INTO tier_sessions (tier_id, player_id) VALUES (?,?)",
                (tier_id, player_id),
            )
            await db.commit()
            return cur.lastrowid
    except Exception as exc:
        logger.error("join_tier error: %s", exc)
        return None


async def get_alive_players_in_tier(tier_id: int) -> list[dict]:
    async with aiosqlite.connect(DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT p.id, p.username, p.fcm_token, ts.entry_time
               FROM tier_sessions ts
               JOIN players p ON p.id = ts.player_id
               WHERE ts.tier_id=? AND ts.is_alive=1""",
            (tier_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def eliminate_player(player_id: int, tier_id: int, reason: str = "boss") -> None:
    async with aiosqlite.connect(DATABASE_URL) as db:
        await db.execute(
            """UPDATE tier_sessions SET is_alive=0, death_time=unixepoch(), eliminated_by=?
               WHERE player_id=? AND tier_id=? AND is_alive=1""",
            (reason, player_id, tier_id),
        )
        await db.commit()


async def get_tier_prize_pool(tier_id: int) -> float:
    """Sum all entry fees for alive + dead players in this tier."""
    from config import TIERS, PRIZE_RAKE
    async with aiosqlite.connect(DATABASE_URL) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM tier_sessions WHERE tier_id=?", (tier_id,)
        ) as cur:
            row = await cur.fetchone()
            count = row[0] if row else 0
    entry_fee = TIERS[tier_id]["entry_fee"]
    gross = count * entry_fee
    return round(gross * (1.0 - PRIZE_RAKE), 2)


async def get_tier_stats(tier_id: int) -> dict:
    async with aiosqlite.connect(DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT COUNT(*) as total, SUM(is_alive) as alive FROM tier_sessions WHERE tier_id=?",
            (tier_id,),
        ) as cur:
            row = dict(await cur.fetchone())
    prize_pool = await get_tier_prize_pool(tier_id)
    return {
        "tier_id": tier_id,
        "total": row["total"] or 0,
        "alive": row["alive"] or 0,
        "prize_pool": prize_pool,
    }


async def get_tier_difficulty_level(tier_id: int) -> int:
    """Calculate a persistent pressure level from tier and survivor age."""
    from config import DIFFICULTY_MAX_LEVEL, DIFFICULTY_LEVEL_PER_SURVIVOR_DAY, DIFFICULTY_TIER_BONUS
    now = time.time()
    async with aiosqlite.connect(DATABASE_URL) as db:
        async with db.execute(
            "SELECT entry_time FROM tier_sessions WHERE tier_id=? AND is_alive=1",
            (tier_id,),
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        return int(DIFFICULTY_TIER_BONUS.get(tier_id, 0))

    oldest_days = max((now - float(row[0])) / 86400.0 for row in rows)
    level = int(DIFFICULTY_TIER_BONUS.get(tier_id, 0) + oldest_days * DIFFICULTY_LEVEL_PER_SURVIVOR_DAY)
    return max(0, min(DIFFICULTY_MAX_LEVEL, level))


# ─────────────────────────────────────────────────────────────────────────────
# Daily Activity
# ─────────────────────────────────────────────────────────────────────────────

async def log_movement(player_id: int, x: float, y: float) -> int:
    """
    Record a movement. Returns new move_count for today.
    Movement is 'distinct' if >AFK_DISTINCT_MOVE_PX from last recorded position.
    """
    from config import AFK_DISTINCT_MOVE_PX
    today = date.today().isoformat()
    now   = time.time()

    async with aiosqlite.connect(DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        # Fetch existing record
        async with db.execute(
            "SELECT * FROM daily_activity WHERE player_id=? AND date=?",
            (player_id, today),
        ) as cur:
            row = await cur.fetchone()

        if row is None:
            # First move today
            await db.execute(
                """INSERT INTO daily_activity (player_id, date, move_count, last_move_x, last_move_y, last_move_time)
                   VALUES (?,?,1,?,?,?)""",
                (player_id, today, x, y, now),
            )
            await db.commit()
            return 1

        row = dict(row)
        dx = x - row["last_move_x"]
        dy = y - row["last_move_y"]
        dist = (dx * dx + dy * dy) ** 0.5

        if dist >= AFK_DISTINCT_MOVE_PX:
            new_count = row["move_count"] + 1
            await db.execute(
                """UPDATE daily_activity
                   SET move_count=?, last_move_x=?, last_move_y=?, last_move_time=?
                   WHERE player_id=? AND date=?""",
                (new_count, x, y, now, player_id, today),
            )
            await db.commit()
            return new_count
        # Not distinct enough
        await db.execute(
            "UPDATE daily_activity SET last_move_time=? WHERE player_id=? AND date=?",
            (now, player_id, today),
        )
        await db.commit()
        return row["move_count"]


async def get_daily_moves(player_id: int) -> int:
    today = date.today().isoformat()
    async with aiosqlite.connect(DATABASE_URL) as db:
        async with db.execute(
            "SELECT move_count FROM daily_activity WHERE player_id=? AND date=?",
            (player_id, today),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def get_last_move_time(player_id: int) -> float:
    today = date.today().isoformat()
    async with aiosqlite.connect(DATABASE_URL) as db:
        async with db.execute(
            "SELECT last_move_time FROM daily_activity WHERE player_id=? AND date=?",
            (player_id, today),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Boss events
# ─────────────────────────────────────────────────────────────────────────────

async def create_boss_event(tier_id: int) -> int:
    async with aiosqlite.connect(DATABASE_URL) as db:
        cur = await db.execute(
            "INSERT INTO boss_events (tier_id, status) VALUES (?,?)", (tier_id, "warning")
        )
        await db.commit()
        return cur.lastrowid


async def update_boss_event(event_id: int, status: str, kills: int = 0) -> None:
    async with aiosqlite.connect(DATABASE_URL) as db:
        await db.execute(
            "UPDATE boss_events SET status=?, kills_count=kills_count+? WHERE id=?",
            (status, kills, event_id),
        )
        await db.commit()


async def end_boss_event(event_id: int, total_kills: int) -> None:
    async with aiosqlite.connect(DATABASE_URL) as db:
        await db.execute(
            "UPDATE boss_events SET status='done', end_time=unixepoch(), kills_count=? WHERE id=?",
            (total_kills, event_id),
        )
        await db.commit()


async def get_active_boss_event(tier_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM boss_events WHERE tier_id=? AND status != 'done' ORDER BY start_time DESC LIMIT 1",
            (tier_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


# ─────────────────────────────────────────────────────────────────────────────
# Daily Quests Play-to-Earn logic
# ─────────────────────────────────────────────────────────────────────────────

async def init_daily_quests(player_id: int) -> None:
    """Initialize 3 daily quests for a player on the current day if not present."""
    from config import (
        QUEST_EXPLORER_TARGET, QUEST_EXPLORER_REWARD,
        QUEST_SURVIVOR_TARGET, QUEST_SURVIVOR_REWARD,
        QUEST_SCAVENGER_TARGET, QUEST_SCAVENGER_REWARD
    )
    today = date.today().isoformat()
    quests = [
        ("explorer", QUEST_EXPLORER_TARGET, QUEST_EXPLORER_REWARD),
        ("survivor", QUEST_SURVIVOR_TARGET, QUEST_SURVIVOR_REWARD),
        ("scavenger", QUEST_SCAVENGER_TARGET, QUEST_SCAVENGER_REWARD)
    ]
    async with aiosqlite.connect(DATABASE_URL) as db:
        for q_type, target, reward in quests:
            try:
                await db.execute(
                    """INSERT INTO player_quests (player_id, date, quest_type, progress, target, reward, is_claimed)
                       VALUES (?, ?, ?, 0.0, ?, ?, 0)""",
                    (player_id, today, q_type, target, reward)
                )
            except aiosqlite.IntegrityError:
                # Already exists, skip
                pass
        await db.commit()


async def get_daily_quests(player_id: int) -> list[dict]:
    """Fetches daily quests and progress for a player. Auto-initializes if not present."""
    await init_daily_quests(player_id)
    today = date.today().isoformat()
    async with aiosqlite.connect(DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM player_quests WHERE player_id=? AND date=?", (player_id, today)
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def increment_quest_progress(player_id: int, quest_type: str, amount: float) -> None:
    """Increments a quest's progress. Safely handles floating-point math."""
    await init_daily_quests(player_id)
    today = date.today().isoformat()
    async with aiosqlite.connect(DATABASE_URL) as db:
        await db.execute(
            """UPDATE player_quests
               SET progress = MIN(target, progress + ?)
               WHERE player_id=? AND date=? AND quest_type=? AND is_claimed=0""",
            (amount, player_id, today, quest_type)
        )
        await db.commit()


async def claim_quest_reward(player_id: int, quest_type: str) -> Optional[float]:
    """
    Checks if a quest is complete. If so, updates status to claimed and adds the
    reward directly to player balance. Returns reward amount or None.
    """
    today = date.today().isoformat()
    async with aiosqlite.connect(DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM player_quests WHERE player_id=? AND date=? AND quest_type=?",
            (player_id, today, quest_type)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            quest = dict(row)

        if quest["progress"] >= quest["target"] and not quest["is_claimed"]:
            # Perform atomic update
            await db.execute(
                "UPDATE player_quests SET is_claimed=1 WHERE player_id=? AND date=? AND quest_type=?",
                (player_id, today, quest_type)
            )
            # Add money to player balance
            await db.execute(
                "UPDATE players SET balance=balance+? WHERE id=?",
                (quest["reward"], player_id)
            )
            await db.commit()
            return quest["reward"]

    return None
