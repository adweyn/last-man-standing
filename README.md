# Last Man Standing — Multiplayer Survival Game

A high-stakes, permadeath standalone multiplayer game built on a "Last Man Standing" elimination tournament. Featuring tiered entry lobbies ($1, $5, $10), real-time player movement sync, in-game overlay chat, daily anti-AFK activity checks, and a roaming Boss that wakes up to hunt players.

When the Boss begins to wake up, the server fires real-time mobile push notifications to players' connected phones, giving them a brief window to log in on their PC, dodge, and survive the event.

---

## 🎮 Game Architecture

```
                                      ┌──────────────┐
                                      │  Mobile PWA  │
                                      │  (FCM Push)  │
                                      └──────┬───────┘
                                             │ Auth / Token sync
                                             ▼
┌──────────────┐   User/Lobby APIs    ┌──────────────┐
│  PC Client   ├─────────────────────►│ FastAPI Rest │
│   (Pygame)   │                      │  WebServer   │
│              │◄────────────────────►│   (SQLite)   │
└──────┬───────┘  Websockets Move/Chat └──────┬───────┘
       │                                     │
       ▼                                     ▼
   2D World                            BossAI & Loops
(4000x4000 grid)                       (Anti-AFK checker)
```

1. **PC Client (Pygame)**: Standalone 2D minimalist monochrome client. Draws players, scrolling camera grid, radar minimap, sliding elimination notifications, overlay chat, and custom inputs.
2. **REST API (FastAPI)**: Manages player creation, password hashing, JWT credentials generation, balance deposits, tier lobby sign-ups, and FCM token syncs. Uses SQLite (aiosqlite) database persistence.
3. **WS Game Loop (Websockets)**: Coordinates real-time position updates, speed validations, active chat broadcasts, and Boss state syncs.
4. **Boss AI**: Governs waking warning states, targets the nearest active player, moves towards them, and eliminates players inside the hazard zone who are stationary/AFK or caught on contact.
5. **AFK Checker**: Background loop verifying daily movement checkmarks. Eliminates inactive characters.

---

## 🚀 Getting Started

### Prerequisites
- Python 3.10+ installed.

### 1. Server Setup
Navigate to the `server/` directory, create a virtual environment, install dependencies, and start the backend:

```bash
cd server
python -m venv venv
# Windows activate:
venv\Scripts\activate
# Linux/macOS activate:
# source venv/bin/activate

pip install -r requirements.txt
```

Copy the `.env.example` to `.env`:
```bash
copy .env.example .env
```
*(If you have a Firebase Cloud Messaging account, fill in your `FCM_SERVER_KEY` in `.env`. Otherwise, the server will output warnings to the terminal and mock notifications will trigger on the mobile companion.)*

Launch the server orchestrator:
```bash
python main.py
```
This runs the REST API on `http://localhost:8000` and the WebSocket game loop on `ws://localhost:8765`.

---

## Telegram / Render Monetization Notes

The Telegram Mini App now separates real payments from the in-game tournament
economy:

- Real payments create Telegram Stars (`XTR`) invoices through `/shop/stars-invoice`.
- The Telegram bot validates `pre_checkout_query` updates and fulfills
  `successful_payment` updates.
- Paid products grant internal credits, Chaos tickets, or premium time.
- Tournament entry fees and prize pools remain internal credits (`CR`), not cash.

For Render production, set these environment variables:

```ini
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_MINI_APP_URL=https://your-render-app.onrender.com/telegram-app
SECRET_KEY=replace_with_a_long_random_secret
ALLOW_MOCK_PAYMENTS=false
DATABASE_URL=lms.db
```

During local testing only, set `ALLOW_MOCK_PAYMENTS=true` if you still want the
old mock deposit button to work.

The danger system also has tunable variables:

```ini
DIFFICULTY_MAX_LEVEL=8
DIFFICULTY_LEVEL_PER_SURVIVOR_DAY=0.75
HAZARD_MAX_PER_TIER=10
HAZARD_TICK_INTERVAL=0.5
HAZARD_DAMAGE_STILL_SECONDS=2.5
```

---

### 2. Client Setup
Navigate to the `client/` directory, activate the environment (or share the environment), install requirements, and run the client launcher:

```bash
cd client
python -m venv venv
# Windows activate:
venv\Scripts\activate

pip install -r requirements.txt
python main.py
```

- Register a new account. New accounts are granted **$20.00 mock play money** to test tier entries.
- Log in and select a tier lobby card ($1, $5, or $10).
- Standard controls: **WASD** or **Arrow keys** to move. Press **Enter** to open the in-game chat overlay. Press **Escape** to disconnect.

---

### 3. Mobile PWA Setup (Push Alerts)
To receive alerts on a mobile device:

1. Serve the `mobile/` directory using any HTTP server (e.g. `python -m http.server 3000`).
2. Open the page on your phone (using your PC's local IP address, e.g., `http://192.168.1.50:3000`).
3. Log in with the same username and password you created on the PC client.
4. Click **Link Account & Subscribe**.
5. Grant Notification permissions when prompted.
6. The app will associate your phone with your account (supporting both real FCM push payloads or local mock push triggers).

---

## 🛠️ Testing Guide

### Fast-Testing the Boss & Alerts
To test the Boss wake-up alert loop quickly without waiting 24 hours:

1. Open your `.env` file on the server.
2. Edit the timing variables to fast values:
   ```ini
   BOSS_MIN_SLEEP=30
   BOSS_MAX_SLEEP=60
   BOSS_WARNING_SEC=15
   BOSS_HUNT_SEC=20
   ```
3. Restart the server (`python main.py`).
4. Start the Pygame client and join a tier.
5. In 30-60 seconds, you will receive:
   - A push notification on your mobile companion.
   - A full-screen pulsing red alert on your PC client with a ticking countdown.
   - Screen shake when the Boss starts hunting.
6. **Survival**: Run away from the giant shifting black void (the Boss). If you stay still for 5 seconds inside the Boss radius, or if the Boss overlaps your core center, your screen fades red-black and you are eliminated (permadeath).

### Verifying AFK Quotas
The server runs an hourly checker. If a player is alive in a tournament tier but hasn't performed **2 distinct movement actions** (distance > 50px) by 20:00 UTC:
- A push warning is sent to their phone at 18:00 UTC.
- They are permanently killed and eliminated at 20:00 UTC.
- You can monitor daily moves in the bottom-right HUD indicator (two checkmark dots).
