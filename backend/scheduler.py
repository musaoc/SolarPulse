"""
SolarPulse Inverter Mode & Daily Scheduler Module
-------------------------------------------------
Manages output source priority (SUB / SBU / UTI) and handles daily scheduled
transitions directly within the FastAPI application event loop.

Key Design Principles:
  1. Default is OFF for new users (enabled: False).
  2. Single-trigger transition: Fires once when crossing into 18:00 (SUB) or 06:00 (SBU).
     Preserves manual adjustments in between transitions.
  3. Ground-truth priority detection: Queries QPIRI (Field 16) to verify inverter state.
  4. Local system timezone: Strictly uses local wall-clock time.
"""

import asyncio
import datetime
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from .config import BASE_DIR

logger = logging.getLogger("solarpulse.scheduler")

CONFIG_FILE = BASE_DIR / "scheduler_config.json"

DEFAULT_CONFIG = {
    "enabled": False,  # Default is OFF for new users
    "evening_time": "18:00",
    "evening_mode": "SUB",
    "evening_cmd": "POP01",  # POP01 = SUB / Solar First
    "morning_time": "06:00",
    "morning_mode": "SBU",
    "morning_cmd": "POP02",  # POP02 = SBU Priority
    "last_executed_event": None,
    "last_executed_at": None,
}

PRIORITY_MAP = {
    "0": "UTI",
    "00": "UTI",
    "1": "SUB",
    "01": "SUB",
    "2": "SBU",
    "02": "SBU",
}

PRIORITY_NAMES = {
    "UTI": "Utility First (UTI)",
    "SUB": "Solar-Utility-Battery (SUB)",
    "SBU": "Solar-Battery-Utility (SBU)",
}


class InverterScheduler:
    def __init__(self, collector):
        self.collector = collector
        self.config: dict[str, Any] = dict(DEFAULT_CONFIG)
        self.current_priority_code: str = "Unknown"
        self.current_priority_name: str = "Unknown"
        self.running: bool = False
        self._task: Optional[asyncio.Task] = None
        self._load_config()

    def _load_config(self):
        """Load configuration from scheduler_config.json if it exists."""
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    self.config.update(saved)
                logger.info(
                    "Loaded scheduler configuration (enabled=%s, evening=%s %s, morning=%s %s)",
                    self.config.get("enabled", False),
                    self.config.get("evening_time"),
                    self.config.get("evening_mode"),
                    self.config.get("morning_time"),
                    self.config.get("morning_mode"),
                )
            except Exception as e:
                logger.error("Failed to load scheduler config: %s. Using defaults.", e)
        else:
            # Save defaults on first run (with enabled: False)
            self._save_config()

    def _save_config(self):
        """Persist current configuration to scheduler_config.json."""
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            logger.error("Failed to save scheduler config: %s", e)

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", False))

    @property
    def evening_time(self) -> str:
        return self.config.get("evening_time", "18:00")

    @property
    def morning_time(self) -> str:
        return self.config.get("morning_time", "06:00")

    async def start(self):
        """Start the scheduler background task."""
        self.running = True
        self._task = asyncio.create_task(self._scheduler_loop())
        logger.info("Inverter Scheduler background worker started (enabled=%s)", self.enabled)

    async def stop(self):
        """Stop the scheduler background task."""
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Inverter Scheduler stopped")

    async def update_config(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Update scheduler settings and persist to disk."""
        if "enabled" in updates:
            self.config["enabled"] = bool(updates["enabled"])
        if "evening_time" in updates:
            # Validate HH:MM
            try:
                datetime.time.fromisoformat(updates["evening_time"])
                self.config["evening_time"] = updates["evening_time"]
            except ValueError:
                raise ValueError(f"Invalid evening_time format '{updates['evening_time']}', expected HH:MM")
        if "morning_time" in updates:
            try:
                datetime.time.fromisoformat(updates["morning_time"])
                self.config["morning_time"] = updates["morning_time"]
            except ValueError:
                raise ValueError(f"Invalid morning_time format '{updates['morning_time']}', expected HH:MM")

        self._save_config()
        logger.info("Updated scheduler config: enabled=%s, evening=%s, morning=%s",
                    self.enabled, self.evening_time, self.morning_time)
        return self.get_status()

    async def query_output_priority(self) -> str:
        """Query QPIRI from inverter to read active output source priority (Field 16)."""
        if not self.collector.connected:
            return self.current_priority_code

        try:
            raw = await self.collector.send_inverter_command("QPIRI", timeout=2.5)
            fields = [f for f in raw.strip().split(" ") if f]
            if len(fields) > 16:
                raw_code = fields[16].strip()
                code = PRIORITY_MAP.get(raw_code, raw_code)
                self.current_priority_code = code
                self.current_priority_name = PRIORITY_NAMES.get(code, f"Mode ({code})")
                return code
        except Exception as e:
            logger.debug("QPIRI priority check error: %s", e)

        return self.current_priority_code

    async def set_mode(self, mode: str) -> dict[str, Any]:
        """
        Manually switch the inverter mode (e.g. SUB, SBU, UTI).
        Sends the corresponding PI30 POP command and verifies response.
        """
        target = mode.strip().upper()
        if target == "SUB":
            cmd = "POP01"
        elif target == "SBU":
            cmd = "POP02"
        elif target == "UTI":
            cmd = "POP00"
        else:
            raise ValueError(f"Unsupported mode '{mode}'. Supported: SUB, SBU, UTI")

        if not self.collector.connected:
            raise ConnectionError("Inverter Wi-Fi dongle is currently disconnected")

        logger.info("Setting inverter mode to %s (cmd=%s)", target, cmd)
        raw_resp = await self.collector.send_inverter_command(cmd, timeout=3.0)

        # Brief pause for inverter register update, then re-query QPIRI
        await asyncio.sleep(0.4)
        verified_code = await self.query_output_priority()

        now_str = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        self.config["last_executed_at"] = now_str
        self.config["last_executed_event"] = f"MANUAL_{target}"
        self._save_config()

        return {
            "status": "ok",
            "mode": target,
            "command": cmd,
            "raw_response": raw_resp,
            "verified_priority": verified_code,
            "verified_name": PRIORITY_NAMES.get(verified_code, verified_code),
        }

    def _get_schedule_calculation(self) -> dict[str, Any]:
        """Calculate active window and next scheduled transition using local time."""
        now = datetime.datetime.now().astimezone()
        t_evening = datetime.time.fromisoformat(self.evening_time)
        t_morning = datetime.time.fromisoformat(self.morning_time)
        cur_time = now.time()

        if cur_time >= t_evening:
            # Evening (after 18:00)
            active_mode = self.config.get("evening_mode", "SUB")
            active_cmd = self.config.get("evening_cmd", "POP01")
            active_event_id = f"{now.date().isoformat()}_{self.evening_time}_{active_mode}"
            next_mode = self.config.get("morning_mode", "SBU")
            next_dt = datetime.datetime.combine(now.date() + datetime.timedelta(days=1), t_morning, tzinfo=now.tzinfo)
        elif cur_time < t_morning:
            # Past midnight but before 06:00
            active_mode = self.config.get("evening_mode", "SUB")
            active_cmd = self.config.get("evening_cmd", "POP01")
            prev_date = now.date() - datetime.timedelta(days=1)
            active_event_id = f"{prev_date.isoformat()}_{self.evening_time}_{active_mode}"
            next_mode = self.config.get("morning_mode", "SBU")
            next_dt = datetime.datetime.combine(now.date(), t_morning, tzinfo=now.tzinfo)
        else:
            # Daytime (between 06:00 and 18:00)
            active_mode = self.config.get("morning_mode", "SBU")
            active_cmd = self.config.get("morning_cmd", "POP02")
            active_event_id = f"{now.date().isoformat()}_{self.morning_time}_{active_mode}"
            next_mode = self.config.get("evening_mode", "SUB")
            next_dt = datetime.datetime.combine(now.date(), t_evening, tzinfo=now.tzinfo)

        countdown = max(0, int((next_dt - now).total_seconds()))

        return {
            "active_mode": active_mode,
            "active_cmd": active_cmd,
            "active_event_id": active_event_id,
            "next_mode": next_mode,
            "next_dt": next_dt,
            "countdown_seconds": countdown,
        }

    def get_status(self) -> dict[str, Any]:
        """Return full scheduler state for REST API / dashboard."""
        calc = self._get_schedule_calculation()
        hours, rem = divmod(calc["countdown_seconds"], 3600)
        minutes, _ = divmod(rem, 60)

        return {
            "enabled": self.enabled,
            "current_priority_code": self.current_priority_code,
            "current_priority_name": self.current_priority_name,
            "evening_time": self.evening_time,
            "evening_mode": self.config.get("evening_mode", "SUB"),
            "morning_time": self.morning_time,
            "morning_mode": self.config.get("morning_mode", "SBU"),
            "next_event_mode": calc["next_mode"],
            "next_event_time": calc["next_dt"].strftime("%H:%M"),
            "countdown_seconds": calc["countdown_seconds"],
            "countdown_human": f"{hours}h {minutes}m",
            "last_executed_event": self.config.get("last_executed_event"),
            "last_executed_at": self.config.get("last_executed_at"),
        }

    async def _scheduler_loop(self):
        """Background monitoring loop running every 5 seconds."""
        cycle = 0

        # Wait 3 seconds after boot for UART connection to stabilize
        await asyncio.sleep(3.0)

        # Initial QPIRI check
        await self.query_output_priority()

        while self.running:
            try:
                cycle += 1

                # Every 6 cycles (~30 seconds), refresh priority from inverter
                if cycle % 6 == 0:
                    await self.query_output_priority()

                # If scheduler is enabled, check transition trigger
                if self.enabled:
                    calc = self._get_schedule_calculation()
                    active_id = calc["active_event_id"]

                    # If this window has not been executed yet:
                    if self.config.get("last_executed_event") != active_id:
                        # Check if we are connected before attempting
                        if self.collector.connected:
                            mode_to_set = calc["active_mode"]
                            cmd_to_set = calc["active_cmd"]
                            logger.info(
                                "Auto-Scheduler trigger: Switching inverter to %s (%s) [Event: %s]",
                                mode_to_set, cmd_to_set, active_id
                            )
                            try:
                                resp = await self.collector.send_inverter_command(cmd_to_set, timeout=3.0)
                                logger.info("Auto-Scheduler success for %s. Response: %r", active_id, resp)
                                now_str = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
                                self.config["last_executed_event"] = active_id
                                self.config["last_executed_at"] = now_str
                                self._save_config()

                                # Brief pause and re-query
                                await asyncio.sleep(0.5)
                                await self.query_output_priority()
                            except Exception as e:
                                logger.warning("Auto-Scheduler command failed (%s). Will retry in 5s: %s", cmd_to_set, e)

                await asyncio.sleep(5.0)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in scheduler loop: %s", e, exc_info=True)
                await asyncio.sleep(10.0)
