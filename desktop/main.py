import json
import os
import subprocess
import sys
import webbrowser
from pathlib import Path


APP_NAME = "Last Man Standing"
DEFAULT_SETTINGS = {
    "game_url": "https://last-man-standing-koo9.onrender.com/telegram-app",
    "width": 1280,
    "height": 760,
    "fullscreen": False,
}


def runtime_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def load_settings() -> dict:
    settings_path = runtime_dir() / "app_settings.json"
    settings = DEFAULT_SETTINGS.copy()
    if settings_path.exists():
        try:
            settings.update(json.loads(settings_path.read_text(encoding="utf-8-sig")))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Could not read app_settings.json: {exc}")
    return settings


def main() -> None:
    settings = load_settings()
    url = str(settings.get("game_url") or DEFAULT_SETTINGS["game_url"])
    width = int(settings.get("width") or DEFAULT_SETTINGS["width"])
    height = int(settings.get("height") or DEFAULT_SETTINGS["height"])

    browser = find_app_browser()
    if browser:
        args = [
            str(browser),
            f"--app={url}",
            f"--window-size={width},{height}",
            "--disable-features=Translate",
            "--no-first-run",
        ]
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return

    webbrowser.open(url)


def find_app_browser() -> Path | None:
    candidates = [
        os.getenv("LMS_BROWSER_PATH"),
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return Path(candidate)
    return None


if __name__ == "__main__":
    main()
