"""
SQLite Database module for high-frequency telemetry logging and efficient historical aggregation.
"""

import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Optional

from .config import DATABASE_PATH

logger = logging.getLogger(__name__)


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    # Enable WAL mode for high concurrency without blocking reads/writes
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_db():
    """Initialize database tables and indexes."""
    with get_db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS telemetry_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                epoch REAL NOT NULL,
                pv_power REAL,
                pv_voltage REAL,
                pv_current REAL,
                load_w REAL,
                load_va REAL,
                load_pct INTEGER,
                bat_v REAL,
                bat_charge_a REAL,
                bat_discharge_a REAL,
                bat_soc INTEGER,
                bat_net_w REAL,
                grid_v REAL,
                grid_freq REAL,
                grid_w REAL,
                inverter_temp INTEGER,
                mode TEXT
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_epoch ON telemetry_history(epoch);")
        conn.commit()
    logger.info("Initialized SQLite telemetry database at %s", DATABASE_PATH)


def insert_telemetry(data: dict[str, Any]):
    """Insert one telemetry snapshot."""
    now_epoch = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()

    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO telemetry_history (
                timestamp, epoch, pv_power, pv_voltage, pv_current,
                load_w, load_va, load_pct, bat_v, bat_charge_a,
                bat_discharge_a, bat_soc, bat_net_w, grid_v,
                grid_freq, grid_w, inverter_temp, mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now_iso,
            now_epoch,
            data.get("pv_power", 0.0),
            data.get("pv_voltage", 0.0),
            data.get("pv_current", 0.0),
            data.get("output_active_power", 0.0),
            data.get("output_apparent_power", 0.0),
            data.get("load_percent", 0),
            data.get("battery_voltage", 0.0),
            data.get("battery_charge_current", 0.0),
            data.get("battery_discharge_current", 0.0),
            data.get("battery_soc", 0),
            data.get("battery_net_power", 0.0),
            data.get("grid_voltage", 0.0),
            data.get("grid_frequency", 0.0),
            data.get("grid_power_w", 0.0),
            data.get("inverter_temperature", 0),
            data.get("mode_code", "L")
        ))
        conn.commit()


def get_historical_telemetry(time_range: str = "1h") -> list[dict[str, Any]]:
    """Retrieve historical telemetry with adaptive downsampling."""
    now = time.time()
    range_seconds_map = {
        "1h": 3600,
        "6h": 6 * 3600,
        "24h": 24 * 3600,
        "7d": 7 * 86400,
        "30d": 30 * 86400,
    }
    bucket_seconds_map = {
        "1h": 4,
        "6h": 20,
        "24h": 60,
        "7d": 300,
        "30d": 1800,
    }

    duration = range_seconds_map.get(time_range, 3600)
    bucket = bucket_seconds_map.get(time_range, 4)
    start_epoch = now - duration

    query = f"""
        SELECT 
            strftime('%Y-%m-%dT%H:%M:%SZ', datetime((CAST(epoch / {bucket} AS INT) * {bucket}), 'unixepoch')) as time,
            ROUND(AVG(pv_power), 1) as pv_power,
            ROUND(AVG(pv_voltage), 1) as pv_voltage,
            ROUND(AVG(pv_current), 1) as pv_current,
            ROUND(AVG(load_w), 1) as load_w,
            ROUND(AVG(load_va), 1) as load_va,
            ROUND(AVG(load_pct), 0) as load_pct,
            ROUND(AVG(bat_v), 2) as bat_v,
            ROUND(AVG(bat_charge_a), 1) as bat_charge_a,
            ROUND(AVG(bat_discharge_a), 1) as bat_discharge_a,
            ROUND(AVG(bat_soc), 0) as bat_soc,
            ROUND(AVG(bat_net_w), 1) as bat_net_w,
            ROUND(AVG(grid_v), 1) as grid_v,
            ROUND(AVG(grid_w), 1) as grid_w,
            ROUND(AVG(inverter_temp), 1) as inverter_temp
        FROM telemetry_history
        WHERE epoch >= ?
        GROUP BY (CAST(epoch / {bucket} AS INT))
        ORDER BY epoch ASC;
    """

    with get_db_connection() as conn:
        cursor = conn.execute(query, (start_epoch,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]


def get_today_summary() -> dict[str, Any]:
    """Compute today's aggregate statistics and energy totals (kWh) in user's local timezone.
    Delegates directly to the centralized analytics engine to ensure 100% mathematical consistency."""
    from .analytics import get_daily_analytics

    analytics = get_daily_analytics(range_type="today")
    today_metric = analytics["daily"][0] if analytics.get("daily") else {}

    return {
        "today_pv_energy_kwh": today_metric.get("pv_kwh", 0.0),
        "today_load_energy_kwh": today_metric.get("load_kwh", 0.0),
        "today_grid_energy_kwh": today_metric.get("grid_kwh", 0.0),
        "today_battery_charge_kwh": today_metric.get("bat_charge_kwh", 0.0),
        "today_battery_discharge_kwh": today_metric.get("bat_discharge_kwh", 0.0),
        "peak_pv_w": today_metric.get("peak_pv_w", 0.0),
        "peak_pv_time": today_metric.get("peak_pv_time", "--"),
        "peak_load_w": today_metric.get("peak_load_w", 0.0),
        "peak_load_time": today_metric.get("peak_load_time", "--"),
        "min_bat_v": today_metric.get("min_bat_v", 0.0),
        "max_bat_v": today_metric.get("max_bat_v", 0.0),
        "min_bat_soc": today_metric.get("min_soc", 0),
        "max_bat_soc": today_metric.get("max_soc", 0),
        "sample_count": today_metric.get("samples", 0),
        "offline_gap_minutes": today_metric.get("offline_gap_minutes", 0.0),
    }
