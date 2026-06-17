"""
notifications.py — Firebase Cloud Messaging push notification dispatcher.

All functions are fire-and-forget async coroutines.
If FCM_SERVER_KEY is not configured, notifications are silently skipped.
"""

import logging
from typing import Optional

import aiohttp

from config import FCM_API_URL, FCM_SERVER_KEY

logger = logging.getLogger(__name__)


async def _send_fcm(token: str, title: str, body: str, data: Optional[dict] = None) -> bool:
    """Low-level FCM send. Returns True on success."""
    if not FCM_SERVER_KEY:
        logger.debug("FCM_SERVER_KEY not set — notification skipped: %s", title)
        return False

    payload: dict = {
        "to": token,
        "notification": {
            "title": title,
            "body": body,
            "sound": "default",
        },
        "priority": "high",
        "android": {"priority": "high"},
        "apns": {
            "headers": {"apns-priority": "10"},
            "payload": {"aps": {"sound": "default", "badge": 1}},
        },
    }
    if data:
        payload["data"] = {str(k): str(v) for k, v in data.items()}

    headers = {
        "Authorization": f"key={FCM_SERVER_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(FCM_API_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    logger.info("FCM sent OK → token=...%s | %s", token[-6:], title)
                    return True
                text = await resp.text()
                logger.warning("FCM error %d: %s", resp.status, text)
                return False
    except Exception as exc:
        logger.error("FCM exception: %s", exc)
        return False


async def _send_fcm_multicast(tokens: list[str], title: str, body: str, data: Optional[dict] = None) -> int:
    """Send to multiple tokens. Returns number of successes."""
    if not FCM_SERVER_KEY or not tokens:
        return 0

    payload: dict = {
        "registration_ids": tokens,
        "notification": {
            "title": title,
            "body": body,
            "sound": "default",
        },
        "priority": "high",
        "android": {"priority": "high"},
    }
    if data:
        payload["data"] = {str(k): str(v) for k, v in data.items()}

    headers = {
        "Authorization": f"key={FCM_SERVER_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(FCM_API_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    success = result.get("success", 0)
                    logger.info("FCM multicast: %d/%d OK | %s", success, len(tokens), title)
                    return success
                logger.warning("FCM multicast error %d", resp.status)
                return 0
    except Exception as exc:
        logger.error("FCM multicast exception: %s", exc)
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

async def send_boss_warning(fcm_tokens: list[str], tier_id: int, seconds_remaining: int) -> int:
    """
    Alert all players in a tier that the Boss is waking up.
    Returns number of successful deliveries.
    """
    clean_tokens = [t for t in fcm_tokens if t]
    if not clean_tokens:
        return 0
    title = "⚠️ BOSS IS WAKING!"
    body  = f"You have {seconds_remaining}s to reach your PC and move! [Tier {tier_id}]"
    return await _send_fcm_multicast(
        clean_tokens, title, body,
        data={"event": "boss_warning", "tier_id": str(tier_id), "seconds": str(seconds_remaining)},
    )


async def send_afk_warning(fcm_token: str, moves_done: int, moves_needed: int) -> bool:
    """Warn a player they haven't met their daily move quota."""
    if not fcm_token:
        return False
    title = "🏃 Move or be eliminated!"
    body  = f"You've made {moves_done}/{moves_needed} required moves today. Log in NOW!"
    return await _send_fcm(
        fcm_token, title, body,
        data={"event": "afk_warning", "moves_done": str(moves_done), "moves_needed": str(moves_needed)},
    )


async def send_elimination(fcm_token: str, reason: str = "boss") -> bool:
    """Notify a player they have been eliminated."""
    if not fcm_token:
        return False
    if reason == "boss":
        title = "💀 You have been eliminated"
        body  = "The Boss killed you. Your run is over."
    elif reason == "afk":
        title = "💀 Eliminated — AFK"
        body  = "You didn't move enough today. You've been eliminated."
    else:
        title = "💀 Eliminated"
        body  = "Your run has ended."
    return await _send_fcm(fcm_token, title, body, data={"event": "eliminated", "reason": reason})


async def send_victory(fcm_token: str, prize: float, tier_id: int) -> bool:
    """Congratulate the winner."""
    if not fcm_token:
        return False
    title = "🏆 YOU WIN!"
    body  = f"You survived Tier {tier_id} and won ${prize:.2f}!"
    return await _send_fcm(
        fcm_token, title, body,
        data={"event": "victory", "prize": str(prize), "tier_id": str(tier_id)},
    )
