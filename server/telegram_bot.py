"""
telegram_bot.py — Lightweight, async Telegram Bot runner.
Polls for updates and replies with a Web App launcher button.
"""

import asyncio
import logging
import aiohttp
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_MINI_APP_URL

logger = logging.getLogger("LMS_TELEGRAM_BOT")

class TelegramBotRunner:
    def __init__(self):
        self.token = TELEGRAM_BOT_TOKEN
        self.app_url = TELEGRAM_MINI_APP_URL
        self.offset = 0
        self.session = None
        self.running = False
        self._task = None

    def start(self):
        """Starts the bot polling loop in the background."""
        if not self.token or self.token == "CHANGE_ME" or not self.token.strip():
            logger.warning("TELEGRAM_BOT_TOKEN is not configured. Telegram Bot features will be disabled.")
            return
        
        self.running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Telegram Bot service started.")

    def stop(self):
        """Stops the bot polling loop."""
        self.running = False
        if self._task:
            self._task.cancel()
        logger.info("Telegram Bot service stopped.")

    async def _poll_loop(self):
        self.session = aiohttp.ClientSession()
        api_url = f"https://api.telegram.org/bot{self.token}"

        # Programmatically configure/update the Chat Menu Button with the active URL
        try:
            menu_btn_payload = {
                "menu_button": {
                    "type": "web_app",
                    "text": "🎮 Play LMS",
                    "web_app": {"url": self.app_url}
                }
            }
            async with self.session.post(f"{api_url}/setChatMenuButton", json=menu_btn_payload) as r:
                if r.status == 200:
                    logger.info("Successfully configured bot Chat Menu Button.")
                else:
                    logger.error(f"Failed to configure Chat Menu Button: {await r.text()}")
        except Exception as e:
            logger.error(f"Error configuring Chat Menu Button: {e}")

        # Clean/clear old updates on startup by calling getUpdates with negative offset
        try:
            await self.session.get(f"{api_url}/getUpdates", params={"offset": -1, "timeout": 1})
        except Exception:
            pass

        while self.running:
            try:
                params = {
                    "offset": self.offset,
                    "timeout": 30,
                    "allowed_updates": ["message"]
                }
                async with self.session.get(f"{api_url}/getUpdates", params=params, timeout=35) as resp:
                    if resp.status != 200:
                        logger.error(f"Telegram API returned status {resp.status}")
                        await asyncio.sleep(5)
                        continue

                    data = await resp.json()
                    if not data.get("ok"):
                        logger.error(f"Telegram API error: {data.get('description', 'Unknown')}")
                        await asyncio.sleep(5)
                        continue

                    updates = data.get("result", [])
                    for update in updates:
                        self.offset = update["update_id"] + 1
                        message = update.get("message")
                        if message:
                            await self._handle_message(api_url, message)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in Telegram Bot poll loop: {e}")
                await asyncio.sleep(5)

        await self.session.close()

    async def _handle_message(self, api_url: str, message: dict):
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        text = message.get("text", "").strip()

        if not chat_id:
            return

        if text.startswith("/start"):
            is_https = self.app_url.lower().startswith("https://")
            
            welcome_text = (
                "⚔️ *LAST MAN STANDING* ⚔️\n\n"
                "Welcome to the ultimate permutation survival arena!\n\n"
                "💸 *Tier I* ($1.00) | *Tier II* ($5.00) | *Tier III* ($10.00)\n"
                "💀 Avoid the roaming Boss AI\n"
                "🏃 Complete your daily moves check\n"
                "🏆 Be the last survivor to take the prize pool!\n\n"
            )

            if is_https:
                welcome_text += "Click the button below to join the game directly inside Telegram:"
                reply_markup = {
                    "inline_keyboard": [
                        [
                            {
                                "text": "🎮 Play Last Man Standing",
                                "web_app": {"url": self.app_url}
                            }
                        ]
                    ]
                }
            else:
                # Fallback for local HTTP testing
                welcome_text += (
                    "🔗 *Play in Browser* (Local HTTP):\n"
                    f"[Click here to Play]({self.app_url})\n\n"
                    "⚠️ *Note*: Telegram requires an `https://` secure link to use the native Web App button inside the chat. "
                    "Use a tool like `ngrok` to map your local server to HTTPS for native mobile play!"
                )
                reply_markup = None

            payload = {
                "chat_id": chat_id,
                "text": welcome_text,
                "parse_mode": "Markdown"
            }
            if reply_markup:
                payload["reply_markup"] = reply_markup

            try:
                async with self.session.post(f"{api_url}/sendMessage", json=payload) as resp:
                    if resp.status == 200:
                        logger.info(f"Welcome message sent to chat {chat_id}")
                    else:
                        err_text = await resp.text()
                        logger.error(f"Failed to send welcome message: {err_text}")
            except Exception as e:
                logger.error(f"Error sending message: {e}")
