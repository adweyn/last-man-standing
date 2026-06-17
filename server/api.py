"""
api.py — FastAPI REST API endpoints for user registration, authentication,
profile metadata, tier entry with fee deduction, and leaderboards.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
import jwt
from fastapi import FastAPI, Depends, HTTPException, status, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, EmailStr
import os

import re

from config import (
    SECRET_KEY, JWT_ALGORITHM, JWT_EXPIRE_HOURS, TIERS,
    TELEGRAM_BOT_TOKEN, SHOP_PRODUCTS, ALLOW_MOCK_PAYMENTS
)
import database

# ─────────────────────────────────────────────────────────────────────────────
# Input Sanitization
# ─────────────────────────────────────────────────────────────────────────────
_USERNAME_RE = re.compile(r'^[a-zA-Z0-9_-]+$')
_DANGEROUS_CHARS_RE = re.compile(r'[<>$(){}\[\]`\\;|&!#%^~\'"]')

def sanitize_username(name: str) -> str:
    """Strip dangerous characters and enforce safe username format."""
    # Remove any dangerous chars
    cleaned = _DANGEROUS_CHARS_RE.sub('', name).strip()
    # Replace spaces/dots with underscores
    cleaned = re.sub(r'[\s.]+', '_', cleaned)
    # Trim to max 20 characters
    cleaned = cleaned[:20]
    # If nothing usable remains, generate a fallback
    if not cleaned or len(cleaned) < 2:
        import uuid
        cleaned = f'player_{uuid.uuid4().hex[:8]}'
    return cleaned

def sanitize_chat_message(text: str) -> str:
    """Strip HTML tags and dangerous characters from chat messages."""
    # Remove HTML/script tags
    cleaned = re.sub(r'<[^>]*>', '', text)
    # Remove shell-like command patterns
    cleaned = _DANGEROUS_CHARS_RE.sub('', cleaned)
    return cleaned[:120].strip()

app = FastAPI(
    title="Last Man Standing API",
    description="Backend services for user registration, login, tier checkins, and leaderboards.",
    version="1.0.0"
)

# Enable CORS for cross-platform access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve Telegram Mini App static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/telegram-app/static", StaticFiles(directory=static_dir), name="static")

@app.get("/telegram-app")
async def get_telegram_app():
    return FileResponse(os.path.join(static_dir, "index.html"))

security = HTTPBearer()

# ─────────────────────────────────────────────────────────────────────────────
# Pydantic Schemas
# ─────────────────────────────────────────────────────────────────────────────

class RegisterSchema(BaseModel):
    username: str = Field(..., min_length=3, max_length=20, pattern=r"^[a-zA-Z0-9_-]+$")
    password: str = Field(..., min_length=6)
    email: Optional[EmailStr] = None

class LoginSchema(BaseModel):
    username: str
    password: str

class DepositSchema(BaseModel):
    amount: float = Field(..., gt=0.0)

class JoinTierSchema(BaseModel):
    tier_id: int = Field(..., ge=1, le=3)

class TokenUpdateSchema(BaseModel):
    fcm_token: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

class TelegramAuthSchema(BaseModel):
    init_data: Optional[str] = None
    username: Optional[str] = Field(None, max_length=30)

class ClaimQuestSchema(BaseModel):
    quest_type: str = Field(..., pattern="^(explorer|survivor|scavenger)$")

class ShopInvoiceSchema(BaseModel):
    product_id: str


# ─────────────────────────────────────────────────────────────────────────────
# Helpers & Dependencies
# ─────────────────────────────────────────────────────────────────────────────

def create_access_token(player_id: int, username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS)
    payload = {
        "sub": str(player_id),
        "username": username,
        "exp": expire
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=JWT_ALGORITHM)


async def get_current_player(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[JWT_ALGORITHM])
        player_id = int(payload["sub"])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, ValueError, KeyError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    player = await database.get_player_by_id(player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    return player


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health")
@app.get("/healthz")
async def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.post("/register", response_model=TokenResponse)
async def register(form: RegisterSchema):
    # Check if player exists
    existing = await database.get_player_by_username(form.username)
    if existing:
        raise HTTPException(status_code=400, detail="Username already taken")
    
    player_id = await database.create_player(form.username, form.password, form.email)
    if not player_id:
        raise HTTPException(status_code=500, detail="Failed to create user account")
    
    # Grant initial mock balance ($20) to new players for testing
    await database.update_balance(player_id, 20.00)

    token = create_access_token(player_id, form.username)
    return {"access_token": token, "token_type": "bearer"}


@app.post("/login", response_model=TokenResponse)
async def login(form: LoginSchema):
    player = await database.verify_password(form.username, form.password)
    if not player:
        raise HTTPException(status_code=400, detail="Invalid username or password")
    
    token = create_access_token(player["id"], player["username"])
    return {"access_token": token, "token_type": "bearer"}


import hmac
import hashlib
import urllib.parse
import json
import uuid
import aiohttp

def verify_telegram_init_data(init_data: str, bot_token: str) -> Optional[dict]:
    try:
        parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        if "hash" not in parsed:
            return None
        
        received_hash = parsed.pop("hash")
        
        # Sort and join all params
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        
        # Compute secret key
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        
        # Compute validation hash
        computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if hmac.compare_digest(computed_hash, received_hash):
            user_data = json.loads(parsed.get("user", "{}"))
            return user_data
    except Exception:
        pass
    return None


@app.post("/telegram-auth", response_model=TokenResponse)
async def telegram_auth(form: TelegramAuthSchema):
    telegram_id = None
    username = form.username

    # 1. Verify via Telegram initData if available
    from config import TELEGRAM_BOT_TOKEN
    if form.init_data and TELEGRAM_BOT_TOKEN and TELEGRAM_BOT_TOKEN != "CHANGE_ME" and TELEGRAM_BOT_TOKEN.strip():
        user_data = verify_telegram_init_data(form.init_data, TELEGRAM_BOT_TOKEN)
        if user_data:
            telegram_id = user_data.get("id")
            raw_name = user_data.get("username") or user_data.get("first_name") or f"tg_{telegram_id}"
            username = sanitize_username(raw_name)
        else:
            raise HTTPException(status_code=400, detail="Invalid Telegram authentication data")

    # 2. If Telegram ID is verified, fetch or create user
    if telegram_id:
        player = await database.get_player_by_telegram_id(telegram_id)
        if player:
            token = create_access_token(player["id"], player["username"])
            return {"access_token": token, "token_type": "bearer"}
        
        # Unique username check
        base_username = username
        existing = await database.get_player_by_username(username)
        counter = 1
        while existing:
            username = f"{base_username}_{counter}"
            existing = await database.get_player_by_username(username)
            counter += 1
            
        player_id = await database.create_telegram_player(telegram_id, username)
        if not player_id:
            raise HTTPException(status_code=500, detail="Failed to create Telegram user account")
            
        await database.update_balance(player_id, 20.00)
        token = create_access_token(player_id, username)
        return {"access_token": token, "token_type": "bearer"}

    # 3. Fallback: Passwordless login/auto-register by username
    if not username:
        raise HTTPException(status_code=400, detail="Username is required for passwordless login")

    # Sanitize and validate the username
    username = sanitize_username(username)
    if not _USERNAME_RE.match(username):
        raise HTTPException(status_code=400, detail="Username can only contain letters, numbers, underscores, and dashes")

    player = await database.get_player_by_username(username)
    if not player:
        import uuid
        dummy_password = str(uuid.uuid4())
        player_id = await database.create_player(username, dummy_password)
        if not player_id:
            raise HTTPException(status_code=500, detail="Failed to auto-register passwordless user")
        await database.update_balance(player_id, 20.00)
        player = await database.get_player_by_id(player_id)

    token = create_access_token(player["id"], player["username"])
    return {"access_token": token, "token_type": "bearer"}


@app.get("/profile")
async def get_profile(player: dict = Depends(get_current_player)):
    # Check if user has active session
    active_session = None
    async with database.aiosqlite.connect(database.DATABASE_URL) as db:
        db.row_factory = database.aiosqlite.Row
        async with db.execute(
            "SELECT * FROM tier_sessions WHERE player_id=? AND is_alive=1", (player["id"],)
        ) as cur:
            row = await cur.fetchone()
            if row:
                active_session = dict(row)

    daily_moves = await database.get_daily_moves(player["id"])

    return {
        "id": player["id"],
        "username": player["username"],
        "email": player["email"],
        "balance": round(player["balance"], 2),
        "premium_until": player.get("premium_until", 0),
        "is_premium": float(player.get("premium_until", 0) or 0) > datetime.now(timezone.utc).timestamp(),
        "chaos_tickets": player.get("chaos_tickets", 0),
        "fcm_registered": bool(player["fcm_token"]),
        "active_session": active_session,
        "daily_moves_today": daily_moves
    }


@app.post("/deposit")
async def deposit(deposit_form: DepositSchema, player: dict = Depends(get_current_player)):
    """Add mock funds to play tiered levels."""
    if not ALLOW_MOCK_PAYMENTS:
        raise HTTPException(
            status_code=403,
            detail="Mock deposits are disabled. Use the Telegram Stars shop."
        )
    await database.update_balance(player["id"], deposit_form.amount)
    return {"message": "Deposit successful", "new_balance": round(player["balance"] + deposit_form.amount, 2)}


@app.get("/shop")
async def get_shop(player: dict = Depends(get_current_player)):
    products = []
    for product_id, product in SHOP_PRODUCTS.items():
        products.append({
            "id": product_id,
            "title": product["title"],
            "description": product["description"],
            "stars": product["stars"],
            "grant_credits": product.get("grant_credits", 0.0),
            "grant_chaos_tickets": product.get("grant_chaos_tickets", 0),
            "premium_days": product.get("premium_days", 0),
        })
    return {"currency": "XTR", "products": products}


@app.post("/shop/stars-invoice")
async def create_stars_invoice(form: ShopInvoiceSchema, player: dict = Depends(get_current_player)):
    product = SHOP_PRODUCTS.get(form.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Unknown product")
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "CHANGE_ME" or not TELEGRAM_BOT_TOKEN.strip():
        raise HTTPException(status_code=503, detail="Telegram payments are not configured")

    payload = json.dumps({
        "order": uuid.uuid4().hex,
        "player_id": player["id"],
        "product_id": form.product_id,
    }, separators=(",", ":"))
    await database.create_payment_order(player["id"], form.product_id, int(product["stars"]), payload)

    invoice_payload = {
        "title": product["title"],
        "description": product["description"],
        "payload": payload,
        "currency": "XTR",
        "prices": [{"label": product["title"], "amount": int(product["stars"])}],
    }

    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/createInvoiceLink"
    async with aiohttp.ClientSession() as session:
        async with session.post(api_url, json=invoice_payload) as resp:
            data = await resp.json()
            if not data.get("ok"):
                raise HTTPException(status_code=502, detail=data.get("description", "Telegram invoice failed"))

    return {"invoice_link": data["result"], "product_id": form.product_id}


@app.post("/join-tier")
async def join_tier(join_form: JoinTierSchema, player: dict = Depends(get_current_player)):
    tier_id = join_form.tier_id
    player_id = player["id"]
    fee = TIERS[tier_id]["entry_fee"]

    # Check if they already have an active session
    active_session = None
    async with database.aiosqlite.connect(database.DATABASE_URL) as db:
        db.row_factory = database.aiosqlite.Row
        async with db.execute(
            "SELECT * FROM tier_sessions WHERE player_id=? AND is_alive=1", (player_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                active_session = dict(row)

    if active_session:
        if active_session["tier_id"] == tier_id:
            # Reconnecting to the same active tier. Permitted. No additional fee.
            return {
                "message": f"Reconnecting to Tier {tier_id}",
                "session_id": active_session["id"],
                "fee_deducted": 0.0,
                "new_balance": round(player["balance"], 2)
            }
        else:
            # Active in a different tier. Block.
            raise HTTPException(
                status_code=400,
                detail=f"You are already in an active tournament in Tier {active_session['tier_id']}. You must complete or die before joining a new one."
            )

    # Refresh player profile to check actual current balance
    refreshed_player = await database.get_player_by_id(player_id)
    if refreshed_player["balance"] < fee:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient funds. Entry fee is ${fee:.2f}, your balance is ${refreshed_player['balance']:.2f}."
        )

    # Try database transaction
    # First deduct fee
    await database.update_balance(player_id, -fee)

    # Insert tier session
    session_id = await database.join_tier(player_id, tier_id)
    if not session_id:
        # Refund fee since join failed (e.g. already in another game)
        await database.update_balance(player_id, fee)
        raise HTTPException(status_code=400, detail="You are already in an active tournament. You must complete or die before joining a new one.")

    return {
        "message": f"Successfully joined Tier {tier_id}",
        "session_id": session_id,
        "fee_deducted": fee,
        "new_balance": round(refreshed_player["balance"] - fee, 2)
    }


@app.put("/fcm-token")
async def update_fcm(token_form: TokenUpdateSchema, player: dict = Depends(get_current_player)):
    await database.update_fcm_token(player["id"], token_form.fcm_token)
    return {"message": "FCM token updated successfully"}


@app.get("/tier-stats/{tier_id}")
async def get_tier_statistics(tier_id: int):
    if tier_id not in TIERS:
        raise HTTPException(status_code=404, detail="Invalid tier ID")
    stats = await database.get_tier_stats(tier_id)
    
    # Get Boss Status
    # Avoid circular imports by fetching directly
    from game_server import boss_instances
    boss = boss_instances.get(tier_id)
    stats["boss_status"] = boss.get_state() if boss else {"state": "sleeping", "time_remaining": 0}

    return stats


@app.get("/leaderboard")
async def get_leaderboard():
    """Returns top historical survivors."""
    async with database.aiosqlite.connect(database.DATABASE_URL) as db:
        db.row_factory = database.aiosqlite.Row
        async with db.execute(
            """SELECT p.username, ts.tier_id, ts.entry_time, ts.death_time, ts.eliminated_by,
               (CASE WHEN ts.is_alive=1 THEN unixepoch() ELSE ts.death_time END - ts.entry_time) as survival_time
               FROM tier_sessions ts
               JOIN players p ON p.id = ts.player_id
               ORDER BY survival_time DESC LIMIT 20"""
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


@app.get("/quests")
async def get_quests(player: dict = Depends(get_current_player)):
    """Fetches daily quests and progress for the authenticated player."""
    quests = await database.get_daily_quests(player["id"])
    return quests


@app.post("/quests/claim")
async def claim_quest(form: ClaimQuestSchema, player: dict = Depends(get_current_player)):
    """Verifies completions, flags the quest as claimed, and pays the reward to the player's balance."""
    reward = await database.claim_quest_reward(player["id"], form.quest_type)
    if reward is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Quest is not complete or reward has already been claimed."
        )
    
    # Fetch updated profile for new balance
    refreshed = await database.get_player_by_id(player["id"])
    return {
        "message": f"Successfully claimed reward of {reward:.2f} CR.",
        "reward": reward,
        "new_balance": round(refreshed["balance"], 2)
    }


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket Endpoint (FastAPI Wrapper for game_server)
# ─────────────────────────────────────────────────────────────────────────────
from fastapi import WebSocket, WebSocketDisconnect
from websockets.exceptions import ConnectionClosed
import game_server

class FastAPIWebSocketWrapper:
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket

    async def send(self, message: str):
        try:
            await self.websocket.send_text(message)
        except Exception:
            raise ConnectionClosed(None, None)

    async def recv(self) -> str:
        try:
            return await self.websocket.receive_text()
        except WebSocketDisconnect:
            raise ConnectionClosed(None, None)
        except Exception:
            raise ConnectionClosed(None, None)

    async def close(self, code: int = 1000):
        try:
            await self.websocket.close(code)
        except Exception:
            pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await self.recv()
        except ConnectionClosed:
            raise StopAsyncIteration

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    wrapper = FastAPIWebSocketWrapper(websocket)
    await game_server.handle_connection(wrapper)
