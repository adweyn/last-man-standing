# Desktop Release Guide

This project can ship as a downloadable Windows PC client while keeping the
online server on Render.

## 1. Deploy The Server

Render should run the Python backend from `server/`.

Recommended Render settings:

```text
Build command: pip install -r requirements.txt
Start command: python main.py
```

Production environment variables:

```text
SECRET_KEY=replace_with_a_long_random_secret
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_MINI_APP_URL=https://your-render-app.onrender.com/telegram-app
ALLOW_MOCK_PAYMENTS=false
DATABASE_URL=lms.db
SERVER_HOST=0.0.0.0
```

For long-term production, use a persistent disk or migrate from SQLite to
PostgreSQL. Without persistent storage, Render restarts can wipe SQLite data.

## 2. Configure The PC Client

Copy:

```text
client/client_settings.example.json
```

to:

```text
client/client_settings.json
```

Use your real Render URL:

```json
{
  "server_api_url": "https://your-render-app.onrender.com",
  "server_ws_url": "wss://your-render-app.onrender.com/ws"
}
```

The downloadable `.exe` also reads `client_settings.json` from the same folder
as the executable, so you can change server URLs without rebuilding.

## 3. Build Windows EXE

From PowerShell:

```powershell
cd client
.\build_windows.ps1
```

Output:

```text
client/dist/LastManStanding.exe
client/dist/client_settings.json
```

Before publishing, edit `client/dist/client_settings.json` with the production
Render URL.

## 4. Publish

Zip these files:

```text
LastManStanding.exe
client_settings.json
```

Upload the zip to itch.io, a landing page, or a private download link.

## 5. Player Flow

1. Player downloads the zip.
2. Player extracts it.
3. Player runs `LastManStanding.exe`.
4. Client connects to the online Render server.
5. Players see each other through WebSocket multiplayer.

## 6. Recommended Next Step

This Pygame build is the fastest downloadable MVP. For a more premium game feel,
keep the same backend and later rebuild only the client in Godot 4.
