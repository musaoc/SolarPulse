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
    """Compute today's aggregate statistics and energy totals (kWh) in user's local timezone."""
    now_local = datetime.now()
    today_start = datetime.combine(now_local.date(), datetime.min.time()).astimezone().timestamp()
    today_end = datetime.combine(now_local.date(), datetime.max.time()).astimezone().timestamp()

    with get_db_connection() as conn:
        cursor = conn.execute("""
            SELECT 
                COUNT(*) as count,
                MAX(pv_power) as peak_pv_w,
                MAX(load_w) as peak_load_w,
                MIN(bat_v) as min_bat_v,
                MAX(bat_v) as max_bat_v,
                MIN(bat_soc) as min_bat_soc,
                MAX(bat_soc) as max_bat_soc,
                AVG(pv_power) as avg_pv_w,
                AVG(load_w) as avg_load_w,
                AVG(grid_w) as avg_grid_w
            FROM telemetry_history
            WHERE epoch >= ? AND epoch <= ?
        """, (today_start, today_end))
        row = dict(cursor.fetchone() or {})
        
        count = row.get("count", 0)

        peak_pv_val = row.get("peak_pv_w") or 0.0
        peak_load_val = row.get("peak_load_w") or 0.0
        peak_pv_time = "--"
        peak_load_time = "--"

        if peak_pv_val > 0:
            p_row = conn.execute(
                "SELECT epoch FROM telemetry_history WHERE epoch >= ? AND epoch <= ? AND pv_power = ? ORDER BY epoch ASC LIMIT 1",
                (today_start, today_end, peak_pv_val)
            ).fetchone()
            if p_row and p_row["epoch"]:
                peak_pv_time = datetime.fromtimestamp(p_row["epoch"]).astimezone().strftime("%H:%M")

        if peak_load_val > 0:
            l_row = conn.execute(
                "SELECT epoch FROM telemetry_history WHERE epoch >= ? AND epoch <= ? AND load_w = ? ORDER BY epoch ASC LIMIT 1",
                (today_start, today_end, peak_load_val)
            ).fetchone()
            if l_row and l_row["epoch"]:
                peak_load_time = datetime.fromtimestamp(l_row["epoch"]).astimezone().strftime("%H:%M")
        
        # Energy numerical integration
        cursor = conn.execute("""
            SELECT 
                pv_power, load_w, grid_w, bat_net_w, epoch
            FROM telemetry_history
            WHERE epoch >= ? AND epoch <= ?
            ORDER BY epoch ASC
        """, (today_start, today_end))
        records = cursor.fetchall()

    pv_kwh = 0.0
    load_kwh = 0.0
    grid_kwh = 0.0
    bat_charge_kwh = 0.0
    bat_discharge_kwh = 0.0
    total_gap_seconds = 0.0

    if len(records) > 1:
        prev_r = records[0]
        for r in records[1:]:
            dt = r["epoch"] - prev_r["epoch"]
            if dt <= 0:
                continue

            p_pv1, p_pv2 = (prev_r["pv_power"] or 0.0), (r["pv_power"] or 0.0)
            p_load1, p_load2 = (prev_r["load_w"] or 0.0), (r["load_w"] or 0.0)
            p_grid1, p_grid2 = (prev_r["grid_w"] or 0.0), (r["grid_w"] or 0.0)
            p_bat1, p_bat2 = (prev_r["bat_net_w"] or 0.0), (r["bat_net_w"] or 0.0)

            avg_pv = (p_pv1 + p_pv2) / 2.0
            avg_load = (p_load1 + p_load2) / 2.0
            avg_grid = (p_grid1 + p_grid2) / 2.0
            avg_bat = (p_bat1 + p_bat2) / 2.0

            if dt > 90:  # Offline gap
                total_gap_seconds += dt
                load_kwh += avg_load * dt / 3600000.0
                grid_kwh += avg_grid * dt / 3600000.0

                start_hour = datetime.fromtimestamp(prev_r["epoch"]).astimezone().hour
                end_hour = datetime.fromtimestamp(r["epoch"]).astimezone().hour
                if p_pv1 > 0 or p_pv2 > 0 or (6 <= start_hour <= 18 or 6 <= end_hour <= 18):
                    pv_kwh += avg_pv * dt / 3600000.0

                if avg_bat > 0:
                    bat_charge_kwh += avg_bat * dt / 3600000.0
                else:
                    bat_discharge_kwh += abs(avg_bat) * dt / 3600000.0
            else:
                pv_kwh += avg_pv * dt / 3600000.0
                load_kwh += avg_load * dt / 3600000.0
                grid_kwh += avg_grid * dt / 3600000.0
                if avg_bat > 0:
                    bat_charge_kwh += avg_bat * dt / 3600000.0
                else:
                    bat_discharge_kwh += abs(avg_bat) * dt / 3600000.0

            prev_r = r

    return {
        "today_pv_energy_kwh": round(pv_kwh, 2),
        "today_load_energy_kwh": round(load_kwh, 2),
        "today_grid_energy_kwh": round(grid_kwh, 2),
        "today_battery_charge_kwh": round(bat_charge_kwh, 2),
        "today_battery_discharge_kwh": round(bat_discharge_kwh, 2),
        "peak_pv_w": round(peak_pv_val, 1),
        "peak_pv_time": peak_pv_time,
        "peak_load_w": round(peak_load_val, 1),
        "peak_load_time": peak_load_time,
        "min_bat_v": round(row.get("min_bat_v") or 0.0, 2),
        "max_bat_v": round(row.get("max_bat_v") or 0.0, 2),
        "min_bat_soc": row.get("min_bat_soc") or 0,
        "max_bat_soc": row.get("max_bat_soc") or 0,
        "sample_count": count,
        "offline_gap_minutes": round(total_gap_seconds / 60.0, 1),
    }
