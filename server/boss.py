"""
boss.py — Boss AI engine for Last Man Standing.
Manages the boss state machine (SLEEPING, WARNING, HUNTING, RESTING) and movement logic.
"""

import asyncio
import logging
import random
import time
from typing import Callable, Dict, Any, Optional

from config import (
    WORLD_WIDTH, WORLD_HEIGHT, BOSS_SPEED, BOSS_RADIUS,
    BOSS_WARNING_SEC, BOSS_HUNT_SEC, BOSS_MIN_SLEEP, BOSS_MAX_SLEEP,
    BOSS_AFK_STILL_SECONDS
)

logger = logging.getLogger(__name__)

class BossAI:
    def __init__(
        self,
        tier_id: int,
        get_players_callback: Callable[[], list],
        kill_player_callback: Callable[[int, str], Any],  # player_id, reason
        notify_callback: Callable[[int, int], Any],       # tier_id, seconds_remaining
        event_log_callback: Callable[[str, int], Any],     # event_type ('start'|'hunt'|'end'), extra
        difficulty_callback: Optional[Callable[[int], Any]] = None
    ):
        self.tier_id = tier_id
        self.get_players_callback = get_players_callback  # returns list of dicts: {"id": int, "username": str, "x": float, "y": float, "last_move_time": float, "is_alive": bool}
        self.kill_player_callback = kill_player_callback
        self.notify_callback = notify_callback
        self.event_log_callback = event_log_callback
        self.difficulty_callback = difficulty_callback

        # Initial boss position (spawn in center of world)
        self.x = float(WORLD_WIDTH // 2)
        self.y = float(WORLD_HEIGHT // 2)
        
        # State machine: 'sleeping' | 'warning' | 'hunting'
        self.state = "sleeping"
        self.time_remaining = 0.0
        self.target_player: Optional[Dict[str, Any]] = None
        self.kills_this_event = 0
        self.active_event_id: Optional[int] = None
        self.difficulty_level = 0

        # Task handle for the boss behavior loop
        self.loop_task: Optional[asyncio.Task] = None

    def start(self):
        """Start the background lifecycle loop for this boss."""
        self.loop_task = asyncio.create_task(self._lifecycle_loop())
        logger.info(f"Boss AI started for Tier {self.tier_id}")

    def stop(self):
        if self.loop_task:
            self.loop_task.cancel()

    def get_state(self) -> Dict[str, Any]:
        """Return serialization-friendly state dictionary."""
        return {
            "x": self.x,
            "y": self.y,
            "state": self.state,
            "time_remaining": int(max(0.0, self.time_remaining)),
            "target_username": self.target_player["username"] if self.target_player else None,
            "kills": self.kills_this_event,
            "difficulty": self.difficulty_level
        }

    async def _lifecycle_loop(self):
        """Infinite loop driving the daily/hourly sleeping, warning, and hunting states."""
        try:
            while True:
                # 1. SLEEPING PHASE
                self.state = "sleeping"
                sleep_duration = random.randint(BOSS_MIN_SLEEP, BOSS_MAX_SLEEP)
                logger.info(f"Tier {self.tier_id} Boss is sleeping for {sleep_duration} seconds.")
                
                # Sleep in small increments so we can observe time_remaining
                self.time_remaining = float(sleep_duration)
                while self.time_remaining > 0:
                    await asyncio.sleep(1.0)
                    self.time_remaining -= 1.0

                # 2. WARNING PHASE
                self.state = "warning"
                self.kills_this_event = 0
                self.difficulty_level = await self._load_difficulty_level()
                self.time_remaining = float(BOSS_WARNING_SEC)
                logger.info(f"Tier {self.tier_id} Boss is waking up at difficulty {self.difficulty_level}. Triggering warning notifications.")
                
                # Create event in DB (via callback)
                if asyncio.iscoroutinefunction(self.event_log_callback):
                    self.active_event_id = await self.event_log_callback("start", 0)
                else:
                    self.active_event_id = self.event_log_callback("start", 0)

                # Send FCM push notifications to all tier players
                await self.notify_callback(self.tier_id, BOSS_WARNING_SEC)

                while self.time_remaining > 0:
                    await asyncio.sleep(1.0)
                    self.time_remaining -= 1.0

                # 3. HUNTING PHASE
                self.state = "hunting"
                self.time_remaining = float(BOSS_HUNT_SEC)
                logger.info(f"Tier {self.tier_id} Boss started hunting!")
                
                if self.active_event_id:
                    if asyncio.iscoroutinefunction(self.event_log_callback):
                        await self.event_log_callback("hunt", self.active_event_id)
                    else:
                        self.event_log_callback("hunt", self.active_event_id)

                hunt_tick_rate = 0.1  # 10 Hz boss updates
                steps = int(BOSS_HUNT_SEC / hunt_tick_rate)

                for _ in range(steps):
                    if not asyncio.current_task().cancelled():
                        await self._hunt_step(hunt_tick_rate)
                        await asyncio.sleep(hunt_tick_rate)
                        self.time_remaining = max(0.0, self.time_remaining - hunt_tick_rate)
                    else:
                        break

                # 4. RETREAT / DONE
                logger.info(f"Tier {self.tier_id} Boss hunt ended. Retreating to center. Total Kills: {self.kills_this_event}")
                if self.active_event_id:
                    if asyncio.iscoroutinefunction(self.event_log_callback):
                        await self.event_log_callback("end", (self.active_event_id, self.kills_this_event))
                    else:
                        self.event_log_callback("end", (self.active_event_id, self.kills_this_event))
                
                # Smoothly retreat to center
                self.state = "sleeping"
                await self._retreat_to_center()

        except asyncio.CancelledError:
            logger.info(f"Boss AI for Tier {self.tier_id} cancelled.")
        except Exception as e:
            logger.error(f"Error in Boss AI loop for Tier {self.tier_id}: {e}", exc_info=True)

    async def _hunt_step(self, dt: float):
        """Single tick of the boss hunt AI."""
        # Get active players
        players = self.get_players_callback()
        alive_players = [p for p in players if p.get("is_alive", True)]

        if not alive_players:
            self.target_player = None
            return

        # Find nearest player
        nearest_player = None
        min_dist = float("inf")
        for p in alive_players:
            dx = p["x"] - self.x
            dy = p["y"] - self.y
            dist = (dx*dx + dy*dy) ** 0.5
            if dist < min_dist:
                min_dist = dist
                nearest_player = p

        self.target_player = nearest_player

        if nearest_player:
            speed = BOSS_SPEED * (1.0 + self.difficulty_level * 0.12)
            kill_radius = BOSS_RADIUS * (1.0 + self.difficulty_level * 0.08)
            still_seconds = max(1.5, BOSS_AFK_STILL_SECONDS - self.difficulty_level * 0.25)

            # Move towards target
            dx = nearest_player["x"] - self.x
            dy = nearest_player["y"] - self.y
            dist = (dx*dx + dy*dy) ** 0.5
            
            if dist > 0:
                self.x += (dx / dist) * speed * dt
                self.y += (dy / dist) * speed * dt

            # Check collision/kills for ALL players in range
            now = time.time()
            for p in alive_players:
                p_dx = p["x"] - self.x
                p_dy = p["y"] - self.y
                p_dist = (p_dx*p_dx + p_dy*p_dy) ** 0.5

                if p_dist < kill_radius:
                    # Player is inside boss touch zone.
                    # Condition: did they stay completely AFK / fails to react?
                    # "any player within 80px radius who hasn't moved in 5 seconds gets killed"
                    # Or if the boss touches them directly and they are static.
                    # If they are running and the boss catches them, they also die (permadeath collision check) OR
                    # let's be strict: if they haven't moved in the last BOSS_AFK_STILL_SECONDS seconds, OR if they are stationary.
                    time_since_last_move = now - p.get("last_move_time", 0)
                    
                    if time_since_last_move >= still_seconds:
                        logger.info(f"Boss killed player {p['username']} (ID: {p['id']}) for being still/AFK inside kill radius.")
                        self.kills_this_event += 1
                        await self.kill_player_callback(p["id"], "boss")
                    elif p_dist < (kill_radius / 2): # Caught completely (dead center collision)
                        logger.info(f"Boss caught and killed player {p['username']} (ID: {p['id']}) on contact.")
                        self.kills_this_event += 1
                        await self.kill_player_callback(p["id"], "boss")

    async def _retreat_to_center(self):
        """Move the boss back to center coordinates smoothly."""
        center_x = float(WORLD_WIDTH // 2)
        center_y = float(WORLD_HEIGHT // 2)
        
        while True:
            dx = center_x - self.x
            dy = center_y - self.y
            dist = (dx*dx + dy*dy) ** 0.5
            
            if dist < 10:
                self.x = center_x
                self.y = center_y
                break
                
            self.x += (dx / dist) * BOSS_SPEED * 0.1
            self.y += (dy / dist) * BOSS_SPEED * 0.1
            await asyncio.sleep(0.1)

    async def _load_difficulty_level(self) -> int:
        if not self.difficulty_callback:
            return 0
        try:
            if asyncio.iscoroutinefunction(self.difficulty_callback):
                return int(await self.difficulty_callback(self.tier_id))
            return int(self.difficulty_callback(self.tier_id))
        except Exception as e:
            logger.error(f"Failed to load difficulty for Tier {self.tier_id}: {e}")
            return 0
