"""
afk_checker.py — Daily activity checking background loop.
Monitors alive players in each tier and enforces movement rules.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Any

from config import (
    AFK_DAILY_MOVES_REQUIRED, AFK_WARNING_HOUR_UTC, AFK_DEADLINE_HOUR_UTC
)
import database
import notifications

logger = logging.getLogger(__name__)

class AFKChecker:
    def __init__(
        self,
        eliminate_player_callback: Callable[[int, int, str], Any],  # player_id, tier_id, reason
        active_tiers: list[int]
    ):
        self.eliminate_player_callback = eliminate_player_callback
        self.active_tiers = active_tiers
        self.loop_task: Optional[asyncio.Task] = None
        self._last_checked_hour = -1

    def start(self):
        self.loop_task = asyncio.create_task(self._run_loop())
        logger.info("AFK Checker task started.")

    def stop(self):
        if self.loop_task:
            self.loop_task.cancel()

    async def _run_loop(self):
        try:
            while True:
                # Run checker once an hour
                now_utc = datetime.now(timezone.utc)
                current_hour = now_utc.hour

                if current_hour != self._last_checked_hour:
                    self._last_checked_hour = current_hour
                    await self._check_activity(current_hour)

                # Sleep 60 seconds before checking time again
                await asyncio.sleep(60.0)
        except asyncio.CancelledError:
            logger.info("AFK Checker loop stopped.")
        except Exception as e:
            logger.error(f"Error in AFK Checker: {e}", exc_info=True)

    async def _check_activity(self, current_hour: int):
        """Enforces warnings and eliminations based on UTC hour."""
        logger.info(f"Running AFK activity check for UTC hour: {current_hour}")

        is_warning_hour = (current_hour == AFK_WARNING_HOUR_UTC)
        is_elimination_hour = (current_hour == AFK_DEADLINE_HOUR_UTC)

        if not (is_warning_hour or is_elimination_hour):
            return

        for tier_id in self.active_tiers:
            alive_players = await database.get_alive_players_in_tier(tier_id)
            for p in alive_players:
                player_id = p["id"]
                username = p["username"]
                fcm_token = p["fcm_token"]

                moves_today = await database.get_daily_moves(player_id)
                logger.debug(f"Player {username} has {moves_today}/{AFK_DAILY_MOVES_REQUIRED} moves today.")

                if moves_today < AFK_DAILY_MOVES_REQUIRED:
                    if is_warning_hour:
                        # Send warning
                        logger.warning(f"Player {username} warned for inactivity ({moves_today}/{AFK_DAILY_MOVES_REQUIRED} moves).")
                        if fcm_token:
                            asyncio.create_task(
                                notifications.send_afk_warning(
                                    fcm_token, moves_today, AFK_DAILY_MOVES_REQUIRED
                                )
                            )
                    elif is_elimination_hour:
                        # Eliminate player
                        logger.error(f"Eliminating player {username} for AFK inactivity ({moves_today}/{AFK_DAILY_MOVES_REQUIRED} moves).")
                        await self.eliminate_player_callback(player_id, tier_id, "afk")
                        if fcm_token:
                            asyncio.create_task(notifications.send_elimination(fcm_token, "afk"))
