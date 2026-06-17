"""
main.py — Main orchestrator. Runs FastAPI, WebSockets game server,
Boss AI state machines, and the hourly AFK checking daemon.
"""

import asyncio
import logging
import os
import uvicorn
from fastapi import FastAPI

import database
import game_server
import notifications
from boss import BossAI
from afk_checker import AFKChecker
from telegram_bot import TelegramBotRunner
from config import API_PORT, WS_PORT, SERVER_HOST

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("LMS_MAIN")


async def boss_notify_callback(tier_id: int, warning_sec: int):
    """Fetches all alive players in a tier and sends them a push warning."""
    try:
        players = await database.get_alive_players_in_tier(tier_id)
        tokens = [p["fcm_token"] for p in players if p.get("fcm_token")]
        if tokens:
            sent_count = await notifications.send_boss_warning(tokens, tier_id, warning_sec)
            logger.info(f"FCM Boss Warning sent to {sent_count} players in Tier {tier_id}")
        else:
            logger.debug(f"No FCM tokens found for alive players in Tier {tier_id}")
    except Exception as e:
        logger.error(f"Error in boss_notify_callback: {e}")


async def boss_event_log_callback(event_type: str, extra: int) -> int:
    """Handles writing boss wake events, hunt progression, and kills to database."""
    try:
        if event_type == "start":
            tier_id = extra
            event_id = await database.create_boss_event(tier_id)
            return event_id
        elif event_type == "hunt":
            event_id = extra
            await database.update_boss_event(event_id, "hunting")
            return event_id
        elif event_type == "end":
            event_id, kills = extra
            await database.end_boss_event(event_id, kills)
            return event_id
    except Exception as e:
        logger.error(f"Error in boss_event_log_callback ({event_type}): {e}")
    return 0


async def main():
    logger.info("Starting Last Man Standing Server...")

    # 1. Initialize SQLite database & tables
    await database.init_db()

    # 2. Instantiate Boss AI for each tier
    tiers = [1, 2, 3]
    for tier in tiers:
        # Create callbacks for this tier
        get_players = lambda t=tier: game_server.get_tier_players_list(t)
        
        # We wrap the async call in a thread-safe / task-safe future
        async def kill_player(pid, reason, t=tier):
            await game_server.kill_player_in_game(pid, t, reason)

        boss_instances = game_server.boss_instances
        boss_instances[tier] = BossAI(
            tier_id=tier,
            get_players_callback=get_players,
            kill_player_callback=kill_player,
            notify_callback=boss_notify_callback,
            event_log_callback=boss_event_log_callback,
            difficulty_callback=database.get_tier_difficulty_level
        )
        # Start the individual boss cycle
        boss_instances[tier].start()

    # 3. Instantiate AFK Checker daemon
    async def eliminate_afk_player(pid, tid, reason):
        await game_server.kill_player_in_game(pid, tid, reason)

    afk_checker = AFKChecker(
        eliminate_player_callback=eliminate_afk_player,
        active_tiers=tiers
    )
    afk_checker.start()

    # 4. Start Telegram Bot service
    telegram_bot = TelegramBotRunner()
    telegram_bot.start()

    # 4. Run WebSocket Game Server and FastAPI REST server concurrently
    # FastAPI uvicorn runner configuration
    from api import app
    config = uvicorn.Config(app, host=SERVER_HOST, port=API_PORT, log_level="warning")
    server = uvicorn.Server(config)

    logger.info("Initializing WebSocket and API server loops...")
    
    try:
        await asyncio.gather(
            game_server.start_websocket_server(),
            server.serve()
        )
    except asyncio.CancelledError:
        logger.info("Shutting down background tasks...")
    finally:
        # Cleanup
        if 'telegram_bot' in locals():
            telegram_bot.stop()
        for tier, boss in boss_instances.items():
            boss.stop()
        afk_checker.stop()
        logger.info("Servers stopped successfully.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server terminated by user.")
