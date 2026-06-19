# Desktop Release Guide

This project can ship as a downloadable Windows PC client while keeping the
online server on Render.

There are two desktop options:

- `desktop/` is the recommended PC release. It opens the current web/Telegram
  game in a clean desktop app window, so it uses the same updated map, quests,
  lobby, and HUD.
- `client/` is the older Pygame client. Keep it for experiments only unless you
  want to manually port every UI/gameplay change into Pygame.

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

## 2. Configure The Recommended PC App

Copy:

```text
desktop/app_settings.json
```

Use your real Render URL:

```json
{
  "game_url": "https://your-render-app.onrender.com/telegram-app",
  "width": 1280,
  "height": 760,
  "fullscreen": false
}
```

The downloadable `.exe` also reads `app_settings.json` from the same folder as
the executable, so you can change the URL without rebuilding.

## 3. Build Windows EXE

From PowerShell:

```powershell
cd desktop
.\build_windows.ps1
```

Output:

```text
desktop/dist/LastManStanding.exe
desktop/dist/app_settings.json
desktop/release/LastManStanding-PC-Web.zip
```

Before publishing, make sure `desktop/dist/app_settings.json` points at the
production Render URL.

## 4. Publish

Zip these files:

```text
LastManStanding.exe
app_settings.json
```

Upload the zip to itch.io, a landing page, or a private download link.

## 5. Player Flow

1. Player downloads the zip.
2. Player extracts it.
3. Player runs `LastManStanding.exe`.
4. Client opens the current online game from Render.
5. Players see each other through WebSocket multiplayer.

## 6. Recommended Next Step

This desktop web build is the fastest downloadable MVP. For a more premium
native game feel, keep the same backend and later rebuild only the client in
Godot 4.
