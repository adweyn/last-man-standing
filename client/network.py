"""
network.py — Threaded WebSockets gameplay client and requests REST API wrapper.
Communicates real-time events to Pygame state thread-safely.
"""

import asyncio
import json
import logging
import threading
import queue
import requests
from typing import Callable, Optional, Dict, Any

import websockets
from websockets.exceptions import ConnectionClosed

from config import SERVER_WS_URL, SERVER_API_URL, HTTP_TIMEOUT

logger = logging.getLogger(__name__)

class NetworkManager:
    def __init__(self):
        # REST variables
        self.auth_token: Optional[str] = None
        self.username: Optional[str] = None

        # Websocket thread control
        self.ws_thread: Optional[threading.Thread] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.ws_conn = None
        self.send_queue = queue.Queue()
        self.running = False

        # Callback storage
        self.callbacks: Dict[str, Callable] = {
            "welcome": None,
            "player_list": None,
            "boss_update": None,
            "chat_message": None,
            "player_eliminated": None,
            "game_won": None,
            "you_died": None,
            "snap_back": None,
            "disconnect": None,
            "kicked": None,
            "crystal_list": None,
            "hazard_list": None,
            "objective_list": None,
            "crystal_collected": None,
            "objective_completed": None
        }

    # ─────────────────────────────────────────────────────────────────────────────
    # REST API wrappers
    # ─────────────────────────────────────────────────────────────────────────────

    def register(self, username, password, email=None) -> tuple[bool, str]:
        """Returns (success, token_or_error_msg)."""
        payload = {"username": username, "password": password}
        if email:
            payload["email"] = email
        try:
            resp = requests.post(f"{SERVER_API_URL}/register", json=payload, timeout=HTTP_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                self.auth_token = data["access_token"]
                self.username = username
                return True, self.auth_token
            else:
                err = resp.json().get("detail", "Registration failed")
                return False, str(err)
        except requests.RequestException as e:
            return False, f"Connection error: {str(e)}"

    def login(self, username, password) -> tuple[bool, str]:
        """Returns (success, token_or_error_msg)."""
        payload = {"username": username, "password": password}
        try:
            resp = requests.post(f"{SERVER_API_URL}/login", json=payload, timeout=HTTP_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                self.auth_token = data["access_token"]
                self.username = username
                return True, self.auth_token
            else:
                err = resp.json().get("detail", "Login failed")
                return False, str(err)
        except requests.RequestException as e:
            return False, f"Connection error: {str(e)}"

    def join_tier(self, tier_id: int) -> tuple[bool, str]:
        """Returns (success, error_msg_if_failed)."""
        if not self.auth_token:
            return False, "Not authenticated"
        headers = {"Authorization": f"Bearer {self.auth_token}"}
        payload = {"tier_id": tier_id}
        try:
            resp = requests.post(f"{SERVER_API_URL}/join-tier", json=payload, headers=headers, timeout=HTTP_TIMEOUT)
            if resp.status_code == 200:
                return True, ""
            else:
                err = resp.json().get("detail", "Could not join tier")
                return False, str(err)
        except requests.RequestException as e:
            return False, f"Connection error: {str(e)}"

    def deposit(self, amount: float) -> tuple[bool, float, str]:
        """Deposits mock play money. Returns (success, new_balance, error_msg)."""
        if not self.auth_token:
            return False, 0.0, "Not authenticated"
        headers = {"Authorization": f"Bearer {self.auth_token}"}
        payload = {"amount": amount}
        try:
            resp = requests.post(f"{SERVER_API_URL}/deposit", json=payload, headers=headers, timeout=HTTP_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                return True, data.get("new_balance", 0.0), ""
            else:
                err = resp.json().get("detail", "Deposit failed")
                return False, 0.0, str(err)
        except requests.RequestException as e:
            return False, 0.0, f"Connection error: {str(e)}"

    def get_profile(self) -> Optional[dict]:
        if not self.auth_token:
            return None
        headers = {"Authorization": f"Bearer {self.auth_token}"}
        try:
            resp = requests.get(f"{SERVER_API_URL}/profile", headers=headers, timeout=HTTP_TIMEOUT)
            return resp.json() if resp.status_code == 200 else None
        except requests.RequestException:
            return None

    def get_tier_stats(self, tier_id: int) -> Optional[dict]:
        try:
            resp = requests.get(f"{SERVER_API_URL}/tier-stats/{tier_id}", timeout=HTTP_TIMEOUT)
            return resp.json() if resp.status_code == 200 else None
        except requests.RequestException:
            return None

    def get_quests(self) -> list[dict]:
        if not self.auth_token:
            return []
        headers = {"Authorization": f"Bearer {self.auth_token}"}
        try:
            resp = requests.get(f"{SERVER_API_URL}/quests", headers=headers, timeout=HTTP_TIMEOUT)
            return resp.json() if resp.status_code == 200 else []
        except requests.RequestException:
            return []

    def claim_quest(self, quest_type: str) -> tuple[bool, str]:
        if not self.auth_token:
            return False, "Not authenticated"
        headers = {"Authorization": f"Bearer {self.auth_token}"}
        try:
            resp = requests.post(
                f"{SERVER_API_URL}/quests/claim",
                json={"quest_type": quest_type},
                headers=headers,
                timeout=HTTP_TIMEOUT
            )
            if resp.status_code == 200:
                data = resp.json()
                return True, data.get("message", "Quest claimed")
            return False, str(resp.json().get("detail", "Quest not ready"))
        except requests.RequestException as e:
            return False, f"Connection error: {str(e)}"

    def get_leaderboard(self) -> list[dict]:
        try:
            resp = requests.get(f"{SERVER_API_URL}/leaderboard", timeout=HTTP_TIMEOUT)
            return resp.json() if resp.status_code == 200 else []
        except requests.RequestException:
            return []

    def update_fcm_token(self, fcm_token: str) -> bool:
        if not self.auth_token:
            return False
        headers = {"Authorization": f"Bearer {self.auth_token}"}
        try:
            resp = requests.put(
                f"{SERVER_API_URL}/fcm-token",
                json={"fcm_token": fcm_token},
                headers=headers,
                timeout=HTTP_TIMEOUT
            )
            return resp.status_code == 200
        except requests.RequestException:
            return False

    # ─────────────────────────────────────────────────────────────────────────────
    # WebSockets gameplay loops
    # ─────────────────────────────────────────────────────────────────────────────

    def connect(self):
        """Spawns background WebSocket loops on a dedicated thread."""
        if self.running:
            return
        self.running = True
        self.ws_thread = threading.Thread(target=self._run_async_thread, daemon=True)
        self.ws_thread.start()

    def disconnect(self):
        """Stops background threads and cleans connections."""
        self.running = False
        if self.loop and self.loop.is_running():
            try:
                self.loop.call_soon_threadsafe(self.loop.stop)
            except RuntimeError:
                pass
        self.ws_conn = None

    def send_move(self, x: float, y: float):
        """Enqueues a movement packet for immediate delivery."""
        self.send_queue.put({"type": "move", "x": x, "y": y})

    def send_chat(self, msg: str):
        """Enqueues chat message."""
        self.send_queue.put({"type": "chat", "message": msg})

    def register_callback(self, event_name: str, callback: Callable):
        if event_name in self.callbacks:
            self.callbacks[event_name] = callback

    def _run_async_thread(self):
        """WebSocket thread entry point."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._socket_lifecycle())
        except RuntimeError:
            # Handle clean shutdown where loop is stopped before future completes
            pass
        finally:
            try:
                self.loop.close()
            except Exception:
                pass

    async def _socket_lifecycle(self):
        """Handles websocket connection establishment, auth, and read/write task splits."""
        if not self.auth_token:
            logger.error("No auth token configured prior to WebSocket start.")
            self._trigger_cb("disconnect", "No auth token")
            return

        try:
            async with websockets.connect(SERVER_WS_URL) as ws:
                self.ws_conn = ws
                logger.info(f"WebSocket client connected to {SERVER_WS_URL}")

                # Send authentication handshake instantly
                auth_payload = {"type": "auth", "token": self.auth_token}
                await ws.send(json.dumps(auth_payload))

                # Launch read and write runners concurrently
                done, pending = await asyncio.wait(
                    [
                        asyncio.create_task(self._socket_read_loop()),
                        asyncio.create_task(self._socket_write_loop()),
                        asyncio.create_task(self._socket_ping_loop())
                    ],
                    return_when=asyncio.FIRST_COMPLETED
                )

                # Terminate any remaining tasks on failure/disconnect
                for task in pending:
                    task.cancel()

        except Exception as e:
            logger.error(f"WebSocket exception: {e}")
            self._trigger_cb("disconnect", str(e))
        finally:
            self.ws_conn = None
            self.running = False
            self._trigger_cb("disconnect", "Disconnected")

    async def _socket_read_loop(self):
        """Listens for server broadcasts and triggers appropriate state callbacks."""
        try:
            async for raw in self.ws_conn:
                data = json.loads(raw)
                mtype = data.get("type")
                if mtype in self.callbacks and self.callbacks[mtype]:
                    self.callbacks[mtype](data)
        except ConnectionClosed:
            logger.info("WS connection closed by peer.")
        except Exception as e:
            logger.error(f"WS read loop exception: {e}")

    async def _socket_write_loop(self):
        """Sends user moves and chats from queue to server websocket."""
        try:
            while self.running and self.ws_conn:
                # Poll queue inside loop using short timeouts
                try:
                    msg = self.send_queue.get_nowait()
                    await self.ws_conn.send(json.dumps(msg))
                    self.send_queue.task_done()
                except queue.Empty:
                    await asyncio.sleep(0.01)
        except Exception as e:
            logger.error(f"WS write loop exception: {e}")

    async def _socket_ping_loop(self):
        """Sends periodic pings to keep connections alive through proxies."""
        try:
            while self.running and self.ws_conn:
                await asyncio.sleep(20.0)
                await self.ws_conn.send(json.dumps({"type": "ping"}))
        except Exception:
            pass

    def _trigger_cb(self, event_name: str, *args):
        """Triggers local registered callback if present."""
        cb = self.callbacks.get(event_name)
        if cb:
            # We can execute directly since callbacks are simple
            cb(*args)
