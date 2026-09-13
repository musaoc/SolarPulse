"""
High-frequency poller for SolarPulse telemetry and WebSocket distribution.
Includes automatic watchdog and MQTT publishing.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from .collector_server import CollectorServer
from .config import DEFAULT_POLL_INTERVAL
from .database import insert_telemetry
from .mqtt_publisher import mqtt_manager
from .protocol_pi30 import parse_qmod, parse_qpigs

logger = logging.getLogger(__name__)


class TelemetryPoller:
    """Orchestrates periodic querying, data enrichment, database storage, and real-time streaming."""

    def __init__(self, collector: CollectorServer):
        self.collector = collector
        self.poll_interval = DEFAULT_POLL_INTERVAL
        self.running = False
        self._task: Optional[asyncio.Task] = None

        self.latest_telemetry: dict[str, Any] = {
            "status": "initializing",
            "collector_connected": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        self.current_mode_code = "L"
        self.current_mode_name = "Line Mode (Grid Passthrough)"
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        """Register a WebSocket subscriber queue."""
        q = asyncio.Queue(maxsize=20)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        """Deregister a WebSocket subscriber queue."""
        self._subscribers.discard(q)

    def set_poll_interval(self, interval: float):
        """Update polling frequency dynamically (1.0s to 10.0s)."""
        self.poll_interval = max(1.0, min(10.0, float(interval)))
        logger.info("Updated poll interval to %.1fs", self.poll_interval)

    async def start(self):
        """Start polling loop."""
        self.running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Telemetry Poller started (interval: %.1fs)", self.poll_interval)

    async def stop(self):
        """Stop polling loop."""
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Telemetry Poller stopped")

    async def _poll_loop(self):
        """Continuous polling worker with timeout recovery watchdog."""
        cycle = 0
        consecutive_timeouts = 0

        while self.running:
            start_time = time.time()

            # Ensure collector is connected AND has had >= 1.5s to settle UART
            if self.collector.connected and (time.time() - self.collector.connected_time >= 1.5):
                try:
                    # Periodically refresh inverter operating mode every 10 cycles
                    if cycle % 10 == 0:
                        try:
                            raw_qmod = await self.collector.send_inverter_command("QMOD", timeout=2.0)
                            parsed_mode = parse_qmod(raw_qmod)
                            self.current_mode_code = parsed_mode["mode_code"]
                            self.current_mode_name = parsed_mode["mode_name"]
                            await asyncio.sleep(0.2)  # Half-duplex RS485 turnaround
                        except Exception as e:
                            logger.debug("QMOD non-critical: %s", e)

                    # Poll core metrics with QPIGS
                    raw_qpigs = await self.collector.send_inverter_command("QPIGS", timeout=3.0)
                    telemetry = parse_qpigs(raw_qpigs, current_mode=self.current_mode_code)

                    # Query succeeded: reset consecutive timeout counter
                    consecutive_timeouts = 0

                    telemetry.update({
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "epoch": time.time(),
                        "collector_connected": True,
                        "collector_ip": self.collector.remote_ip,
                        "collector_pn": self.collector.collector_pn,
                        "mode_code": self.current_mode_code,
                        "mode_name": self.current_mode_name,
                        "status": "online",
                    })

                    self.latest_telemetry = telemetry

                    # 1. Persist to SQLite
                    try:
                        insert_telemetry(telemetry)
                    except Exception as e:
                        logger.error("Database insert error: %s", e)

                    # 2. Publish to MQTT (Home Assistant & individual topics)
                    try:
                        mqtt_manager.publish_telemetry(telemetry)
                    except Exception as e:
                        logger.debug("MQTT publish error: %s", e)

                    # 3. Broadcast to WebSockets
                    self._broadcast(telemetry)

                    # 4. Periodic terminal log
                    if cycle % 5 == 0:
                        bat_chg = telemetry.get("battery_charge_current", 0.0) or 0.0
                        bat_dis = telemetry.get("battery_discharge_current", 0.0) or 0.0
                        bat_curr_str = f"+{bat_chg:.1f}A" if bat_chg > 0 else (f"-{bat_dis:.1f}A" if bat_dis > 0 else "0.0A")
                        logger.info(
                            "[TELEMETRY] PV: %.0fW | Load: %.0fW | Grid: %.0fW (%.0fV) | Bat: %.1fV (%d%%, %s) | Mode: %s | DB: Logged",
                            telemetry.get("pv_power", 0.0) or 0.0,
                            telemetry.get("output_active_power", 0.0) or 0.0,
                            telemetry.get("grid_power_w", 0.0) or 0.0,
                            telemetry.get("grid_voltage", 0.0) or 0.0,
                            telemetry.get("battery_voltage", 0.0) or 0.0,
                            telemetry.get("battery_soc", 0) or 0,
                            bat_curr_str,
                            self.current_mode_name,
                        )

                except Exception as e:
                    consecutive_timeouts += 1
                    logger.warning("Inverter poll error (%d consecutive): %s", consecutive_timeouts, e)
                    self.latest_telemetry["status"] = "poll_error"
                    self.latest_telemetry["error"] = str(e)

                    # Self-healing watchdog: if 2 consecutive timeouts occur, force-reconnect
                    if consecutive_timeouts >= 2:
                        logger.warning("2 consecutive timeouts encountered. Triggering watchdog auto-reconnect...")
                        await self.collector.force_reconnect()
                        consecutive_timeouts = 0
                        await asyncio.sleep(2.5)
            else:
                self.latest_telemetry["collector_connected"] = False
                self.latest_telemetry["status"] = "waiting_for_collector"
                self.latest_telemetry["timestamp"] = datetime.now(timezone.utc).isoformat()
                self._broadcast(self.latest_telemetry)

            cycle += 1

            # Sleep remaining interval to maintain steady cadence
            elapsed = time.time() - start_time
            sleep_time = max(0.2, self.poll_interval - elapsed)
            try:
                await asyncio.sleep(sleep_time)
            except asyncio.CancelledError:
                break

    def _broadcast(self, data: dict[str, Any]):
        """Push data frame to all connected WebSocket subscriber queues."""
        dead = []
        for q in self._subscribers:
            try:
                if q.full():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                q.put_nowait(data)
            except Exception:
                dead.append(q)
        for d in dead:
            self._subscribers.discard(d)
