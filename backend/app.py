"""
FastAPI Application serving real-time WebSocket telemetry, historical REST APIs,
Home Assistant MQTT configuration, and the local glassmorphic dashboard.
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .analytics import (
    get_daily_analytics,
    get_day_hourly_profile,
    get_monthly_analytics,
    get_power_timeseries,
    export_csv_data,
    load_tariff_config,
    save_tariff_config,
)
from .cloud_sync import load_cloud_config, save_cloud_config, sync_cloud_offline_gap
from .collector_server import CollectorServer
from .config import BASE_DIR, DEFAULT_CURRENCY, FRONTEND_DIR, WEB_PORT
from .database import get_historical_telemetry, get_today_summary, init_db
from .mqtt_publisher import mqtt_manager
from .poller import TelemetryPoller

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("solarpulse")

MQTT_CONFIG_FILE = BASE_DIR / "mqtt_config.json"

collector = CollectorServer()
poller = TelemetryPoller(collector)


def load_persisted_mqtt_config():
    """Load MQTT configuration from JSON file if present."""
    if MQTT_CONFIG_FILE.exists():
        try:
            with open(MQTT_CONFIG_FILE, "r") as f:
                cfg = json.load(f)
                mqtt_manager.configure(
                    enabled=cfg.get("enabled", False),
                    broker=cfg.get("broker", "127.0.0.1"),
                    port=cfg.get("port", 1883),
                    username=cfg.get("username", ""),
                    password=cfg.get("password", ""),
                    topic_prefix=cfg.get("topic_prefix", "solarpulse"),
                )
                logger.info("Loaded persisted MQTT configuration (enabled=%s, broker=%s)",
                            mqtt_manager.enabled, mqtt_manager.broker)
        except Exception as e:
            logger.error("Failed to load MQTT config: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown orchestration."""
    logger.info("Starting SolarPulse Backend...")
    init_db()

    # Start collector and poller
    await collector.start()
    await poller.start()

    # Load and initialize MQTT if configured
    load_persisted_mqtt_config()

    # Synchronize offline gap from cloud in background thread if configured
    asyncio.create_task(asyncio.to_thread(sync_cloud_offline_gap))

    yield

    logger.info("Shutting down SolarPulse Backend...")
    mqtt_manager.stop()
    await poller.stop()
    await collector.stop()


app = FastAPI(
    title="SolarPulse Real-Time Solar Monitor",
    description="High-frequency local telemetry for Voltronic / Axpert / Knox solar inverters",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class PollIntervalRequest(BaseModel):
    interval: float


class MqttConfigRequest(BaseModel):
    enabled: bool
    broker: str
    port: int = 1883
    username: str = ""
    password: str = ""
    topic_prefix: str = "solarpulse"


@app.get("/api/live")
async def get_live_data() -> dict[str, Any]:
    """Return the most recent telemetry snapshot."""
    return poller.latest_telemetry


@app.get("/api/history")
async def get_history(range: str = "1h") -> list[dict[str, Any]]:
    """Retrieve historical telemetry with adaptive downsampling."""
    valid_ranges = {"1h", "6h", "24h", "7d", "30d"}
    if range not in valid_ranges:
        range = "1h"
    return get_historical_telemetry(range)


@app.get("/api/summary")
async def get_summary() -> dict[str, Any]:
    """Retrieve today's aggregate generation, consumption, and battery metrics."""
    return get_today_summary()


@app.get("/api/status")
async def get_system_status() -> dict[str, Any]:
    """Retrieve overall communication health and connectivity parameters."""
    return {
        "collector_connected": collector.connected,
        "collector_ip": collector.remote_ip,
        "collector_pn": collector.collector_pn,
        "poll_interval_seconds": poller.poll_interval,
        "active_subscribers": len(poller._subscribers),
        "last_seen_epoch": collector.last_seen,
        "mqtt_connected": mqtt_manager.connected,
        "mqtt_enabled": mqtt_manager.enabled,
    }


@app.post("/api/settings/poll_interval")
async def set_poll_rate(req: PollIntervalRequest) -> dict[str, Any]:
    """Dynamically set the polling frequency between 1.0s and 10.0s."""
    if not (1.0 <= req.interval <= 10.0):
        raise HTTPException(status_code=400, detail="Interval must be between 1.0 and 10.0 seconds")
    poller.set_poll_interval(req.interval)
    return {"status": "ok", "poll_interval": poller.poll_interval}


@app.get("/api/mqtt/status")
async def get_mqtt_status() -> dict[str, Any]:
    """Get current MQTT integration status and config."""
    return {
        "enabled": mqtt_manager.enabled,
        "connected": mqtt_manager.connected,
        "broker": mqtt_manager.broker,
        "port": mqtt_manager.port,
        "username": mqtt_manager.username,
        "topic_prefix": mqtt_manager.topic_prefix,
    }


@app.post("/api/mqtt/config")
async def save_mqtt_config(req: MqttConfigRequest) -> dict[str, Any]:
    """Configure MQTT connection and Home Assistant auto-discovery."""
    cfg_data = req.model_dump()
    try:
        with open(MQTT_CONFIG_FILE, "w") as f:
            json.dump(cfg_data, f, indent=2)
    except Exception as e:
        logger.error("Failed to save MQTT config file: %s", e)

    mqtt_manager.configure(
        enabled=req.enabled,
        broker=req.broker,
        port=req.port,
        username=req.username,
        password=req.password,
        topic_prefix=req.topic_prefix,
    )

    return {
        "status": "ok",
        "enabled": mqtt_manager.enabled,
        "connected": mqtt_manager.connected,
        "broker": mqtt_manager.broker,
    }


class CloudConfigRequest(BaseModel):
    enabled: bool = True
    url: str
    auth: str
    token: str
    sign: str
    project: str = "DESS"


@app.get("/api/cmd")
async def send_custom_command(cmd: str = "QPIGS") -> dict[str, Any]:
    """Send a custom PI30 query command directly to the inverter and return response."""
    try:
        raw = await collector.send_inverter_command(cmd.strip(), timeout=3.0)
        return {"cmd": cmd, "response": raw, "status": "ok"}
    except Exception as e:
        return {"cmd": cmd, "error": str(e), "status": "error"}


@app.get("/api/cloud/status")
async def get_cloud_status() -> dict[str, Any]:
    """Get current cloud synchronization status and configuration."""
    cfg = load_cloud_config()
    return {
        "enabled": cfg.get("enabled", False),
        "last_sync_timestamp": cfg.get("last_sync_timestamp"),
        "last_sync_status": cfg.get("last_sync_status"),
        "cloud_last_gts": cfg.get("cloud_last_gts"),
        "url": cfg.get("url"),
    }


@app.post("/api/cloud/sync")
async def trigger_cloud_sync() -> dict[str, Any]:
    """Manually trigger cloud offline gap recovery and data backfill."""
    result = await asyncio.to_thread(sync_cloud_offline_gap)
    return result


@app.post("/api/cloud/config")
async def update_cloud_config(req: CloudConfigRequest) -> dict[str, Any]:
    """Update cloud credentials / token for offline data sync."""
    cfg = load_cloud_config()
    cfg.update(req.model_dump())
    save_cloud_config(cfg)
    return {"status": "ok", "config": cfg}


@app.websocket("/ws/live")
async def websocket_live_stream(websocket: WebSocket):
    """Real-time streaming push: broadcasts every new frame immediately to connected clients."""
    await websocket.accept()
    queue = poller.subscribe()

    await websocket.send_json(poller.latest_telemetry)

    try:
        while True:
            data = await queue.get()
            await websocket.send_json(data)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as e:
        logger.debug("WebSocket client error: %s", e)
    finally:
        poller.unsubscribe(queue)


# -----------------------------------------------------------------------------
# Historical Analytics Endpoints
# -----------------------------------------------------------------------------
class TariffRequest(BaseModel):
    rate_per_kwh: float
    currency: Optional[str] = DEFAULT_CURRENCY


@app.get("/api/analytics/daily")
async def api_analytics_daily(
    range: str = "today",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    start_time: Optional[str] = "00:00",
    end_time: Optional[str] = "23:59",
    tariff: Optional[float] = None,
) -> dict[str, Any]:
    """Retrieve daily energy aggregations, peaks with timestamps, autarky %, and savings."""
    return await asyncio.to_thread(
        get_daily_analytics,
        range_type=range,
        start_date=start_date,
        end_date=end_date,
        start_time=start_time,
        end_time=end_time,
        tariff_pkr=tariff,
    )


@app.get("/api/analytics/day")
async def api_analytics_day(date: str) -> dict[str, Any]:
    """Retrieve 24-hour profile (00:00 to 23:00) comparing Solar, Home Load, and Grid for a date."""
    return await asyncio.to_thread(get_day_hourly_profile, day_str=date)


@app.get("/api/analytics/power_timeseries")
async def api_analytics_power_timeseries(
    range: str = "today",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    start_time: Optional[str] = "00:00",
    end_time: Optional[str] = "23:59",
) -> dict[str, Any]:
    """Retrieve timeline of Solar (W), Load (W), Grid (W), Battery Charge (W), and Battery Discharge (W)
    for any filtered range and time interval.
    """
    return await asyncio.to_thread(
        get_power_timeseries,
        range_type=range,
        start_date=start_date,
        end_date=end_date,
        start_time=start_time,
        end_time=end_time,
    )


@app.get("/api/analytics/monthly")
async def api_analytics_monthly(year: Optional[int] = None, tariff: Optional[float] = None) -> list[dict[str, Any]]:
    """Retrieve monthly energy rollups and bill savings."""
    return await asyncio.to_thread(get_monthly_analytics, year=year, tariff_pkr=tariff)


@app.get("/api/analytics/export_csv")
async def api_analytics_export_csv(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    start_time: Optional[str] = "00:00",
    end_time: Optional[str] = "23:59",
):
    """Download daily historical records as a CSV spreadsheet."""
    csv_text = await asyncio.to_thread(
        export_csv_data,
        start_date=start_date,
        end_date=end_date,
        start_time=start_time,
        end_time=end_time,
    )
    filename = f"solarpulse_analytics_{datetime.now().strftime('%Y%m%d')}.csv"
    return PlainTextResponse(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/api/analytics/tariff")
async def api_get_tariff() -> dict[str, Any]:
    """Get electricity tariff setting."""
    return load_tariff_config()


@app.post("/api/analytics/tariff")
async def api_save_tariff(req: TariffRequest) -> dict[str, Any]:
    """Update electricity tariff setting."""
    cfg = {"rate_per_kwh": req.rate_per_kwh, "currency": req.currency or DEFAULT_CURRENCY}
    save_tariff_config(cfg)
    return {"status": "ok", "tariff": cfg}


frontend_path = Path(FRONTEND_DIR)
if frontend_path.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_path)), name="static")

    @app.get("/")
    async def serve_index():
        return FileResponse(str(frontend_path / "index.html"))

    @app.get("/analytics")
    async def serve_analytics():
        return FileResponse(str(frontend_path / "analytics.html"))
