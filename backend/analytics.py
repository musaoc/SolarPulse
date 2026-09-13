"""
Historical Analytics and Energy Aggregation Engine for SolarPulse.
Computes daily and monthly kWh totals, peak usages with timestamps, solar autarky,
financial savings, 24-hour hourly curves, and CSV exports.
"""

import csv
import io
import json
import logging
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .config import (
    BASE_DIR,
    DEFAULT_CURRENCY,
    DEFAULT_TARIFF_RATE,
    GRID_LABEL,
)
from .database import get_db_connection

logger = logging.getLogger(__name__)

TARIFF_CONFIG_FILE = BASE_DIR / "tariff_config.json"


def load_tariff_config() -> dict[str, Any]:
    """Load electricity tariff settings from disk or return defaults."""
    if TARIFF_CONFIG_FILE.exists():
        try:
            with open(TARIFF_CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Failed to read tariff config: %s", e)
    return {"rate_per_kwh": DEFAULT_TARIFF_RATE, "currency": DEFAULT_CURRENCY}


def save_tariff_config(cfg: dict[str, Any]):
    """Persist electricity tariff settings."""
    try:
        with open(TARIFF_CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        logger.error("Failed to save tariff config: %s", e)


def _format_time_str(iso_or_ts: Any, epoch_val: Optional[float] = None) -> str:
    """Extract and format time string (HH:MM 24-hour format) converted to user's local timezone."""
    if epoch_val is not None and epoch_val > 0:
        try:
            return datetime.fromtimestamp(epoch_val).astimezone().strftime("%H:%M")
        except Exception:
            pass

    if not iso_or_ts:
        return "--"

    try:
        if isinstance(iso_or_ts, (int, float)):
            return datetime.fromtimestamp(float(iso_or_ts)).astimezone().strftime("%H:%M")

        if isinstance(iso_or_ts, str):
            if "T" in iso_or_ts:
                dt = datetime.fromisoformat(iso_or_ts).astimezone()
                return dt.strftime("%H:%M")
            elif iso_or_ts.replace(".", "", 1).isdigit():
                return datetime.fromtimestamp(float(iso_or_ts)).astimezone().strftime("%H:%M")

        return str(iso_or_ts)
    except Exception:
        return str(iso_or_ts)


def compute_day_metrics(records: list[sqlite3.Row], day_str: str, tariff_rate: float) -> dict[str, Any]:
    """Compute exact energy totals, peak timestamps, autarky %, and savings for a single day."""
    if not records:
        return {
            "date": day_str,
            "samples": 0,
            "pv_kwh": 0.0,
            "load_kwh": 0.0,
            "grid_kwh": 0.0,
            "bat_charge_kwh": 0.0,
            "bat_discharge_kwh": 0.0,
            "peak_pv_w": 0.0,
            "peak_pv_time": "--",
            "peak_pv_epoch": 0.0,
            "peak_load_w": 0.0,
            "peak_load_time": "--",
            "peak_load_epoch": 0.0,
            "peak_grid_w": 0.0,
            "peak_grid_time": "--",
            "peak_grid_epoch": 0.0,
            "autarky_pct": 100.0,
            "self_consumption_pct": 100.0,
            "savings_pkr": 0.0,
            "grid_cost_pkr": 0.0,
            "min_soc": 0,
            "max_soc": 0,
        }

    pv_kwh = 0.0
    load_kwh = 0.0
    grid_kwh = 0.0
    bat_charge_kwh = 0.0
    bat_discharge_kwh = 0.0

    peak_pv = 0.0
    peak_pv_ts = ""
    peak_pv_epoch = 0.0
    peak_load = 0.0
    peak_load_ts = ""
    peak_load_epoch = 0.0
    peak_grid = 0.0
    peak_grid_ts = ""
    peak_grid_epoch = 0.0

    min_soc = 100
    max_soc = 0

    prev = records[0]

    for r in records:
        pv_w = r["pv_power"] or 0.0
        load_w = r["load_w"] or 0.0
        grid_w = r["grid_w"] or 0.0
        bat_net = r["bat_net_w"] or 0.0
        soc = r["bat_soc"] or 0
        ts = r["timestamp"]
        ep = r["epoch"] or 0.0

        if pv_w > peak_pv:
            peak_pv = pv_w
            peak_pv_ts = ts
            peak_pv_epoch = ep
        if load_w > peak_load:
            peak_load = load_w
            peak_load_ts = ts
            peak_load_epoch = ep
        if grid_w > peak_grid:
            peak_grid = grid_w
            peak_grid_ts = ts
            peak_grid_epoch = ep

        if soc > 0:
            if soc < min_soc:
                min_soc = soc
            if soc > max_soc:
                max_soc = soc

        dt = r["epoch"] - prev["epoch"]
        if 0 < dt < 600:
            avg_pv = ((prev["pv_power"] or 0.0) + pv_w) / 2.0
            avg_load = ((prev["load_w"] or 0.0) + load_w) / 2.0
            avg_grid = ((prev["grid_w"] or 0.0) + grid_w) / 2.0
            avg_bat = ((prev["bat_net_w"] or 0.0) + bat_net) / 2.0

            pv_kwh += avg_pv * dt / 3600000.0
            load_kwh += avg_load * dt / 3600000.0
            grid_kwh += avg_grid * dt / 3600000.0

            if avg_bat > 0:
                bat_charge_kwh += avg_bat * dt / 3600000.0
            else:
                bat_discharge_kwh += abs(avg_bat) * dt / 3600000.0

        prev = r

    if load_kwh > 0.01:
        autarky = max(0.0, min(100.0, ((load_kwh - grid_kwh) / load_kwh) * 100.0))
    else:
        autarky = 100.0 if grid_kwh == 0.0 else 0.0

    if pv_kwh > 0.01:
        consumed_solar = min(pv_kwh, load_kwh + bat_charge_kwh)
        self_consumption = max(0.0, min(100.0, (consumed_solar / pv_kwh) * 100.0))
    else:
        self_consumption = 100.0

    savings = round(pv_kwh * tariff_rate, 2)
    grid_cost = round(grid_kwh * tariff_rate, 2)

    return {
        "date": day_str,
        "samples": len(records),
        "pv_kwh": round(pv_kwh, 2),
        "load_kwh": round(load_kwh, 2),
        "grid_kwh": round(grid_kwh, 2),
        "bat_charge_kwh": round(bat_charge_kwh, 2),
        "bat_discharge_kwh": round(bat_discharge_kwh, 2),
        "peak_pv_w": round(peak_pv, 1),
        "peak_pv_time": _format_time_str(peak_pv_ts, peak_pv_epoch),
        "peak_pv_epoch": peak_pv_epoch,
        "peak_load_w": round(peak_load, 1),
        "peak_load_time": _format_time_str(peak_load_ts, peak_load_epoch),
        "peak_load_epoch": peak_load_epoch,
        "peak_grid_w": round(peak_grid, 1),
        "peak_grid_time": _format_time_str(peak_grid_ts, peak_grid_epoch),
        "peak_grid_epoch": peak_grid_epoch,
        "autarky_pct": round(autarky, 1),
        "self_consumption_pct": round(self_consumption, 1),
        "savings_pkr": savings,  # Preserved key for backward compatibility
        "grid_cost_pkr": grid_cost,
        "min_soc": min_soc if min_soc <= 100 else 0,
        "max_soc": max_soc,
    }


def get_daily_analytics(
    range_type: str = "today",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    start_time: Optional[str] = "00:00",
    end_time: Optional[str] = "23:59",
    tariff_pkr: Optional[float] = None
) -> dict[str, Any]:
    """Retrieve daily summaries and aggregate KPIs for the specified date and time range."""
    cfg = load_tariff_config()
    tariff = tariff_pkr if tariff_pkr is not None else cfg.get("rate_per_kwh", DEFAULT_TARIFF_RATE)
    currency = cfg.get("currency", DEFAULT_CURRENCY)

    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    s_time = (start_time or "00:00").strip()
    e_time = (end_time or "23:59").strip()

    if range_type == "today":
        start_day = today_str
        end_day = today_str
    elif range_type == "yesterday":
        yest = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        start_day = yest
        end_day = yest
    elif range_type == "7d":
        start_day = (now - timedelta(days=6)).strftime("%Y-%m-%d")
        end_day = today_str
        end_day = today_str
    elif range_type == "30d":
        start_day = (now - timedelta(days=29)).strftime("%Y-%m-%d")
        end_day = today_str
    elif range_type == "this_month":
        start_day = now.strftime("%Y-%m-01")
        end_day = today_str
    elif (range_type == "custom" or start_date or end_date) and start_date and end_date:
        range_type = "custom"
        start_day = start_date
        end_day = end_date
    else:
        start_day = "2020-01-01"
        end_day = today_str

    try:
        s_dt = datetime.strptime(f"{start_day} {s_time}", "%Y-%m-%d %H:%M").astimezone()
        start_epoch = s_dt.timestamp()
    except Exception:
        s_dt = datetime.strptime(f"{start_day} 00:00", "%Y-%m-%d %H:%M").astimezone()
        start_epoch = s_dt.timestamp()

    try:
        if len(e_time) == 5:
            e_dt = datetime.strptime(f"{end_day} {e_time}:59", "%Y-%m-%d %H:%M:%S").astimezone()
        else:
            e_dt = datetime.strptime(f"{end_day} {e_time}", "%Y-%m-%d %H:%M:%S").astimezone()
        end_epoch = e_dt.timestamp()
    except Exception:
        e_dt = datetime.strptime(f"{end_day} 23:59:59", "%Y-%m-%d %H:%M:%S").astimezone()
        end_epoch = e_dt.timestamp()

    with get_db_connection() as conn:
        records = conn.execute("""
            SELECT 
                timestamp, epoch, pv_power, load_w, grid_w, bat_net_w, bat_soc,
                date(epoch, 'unixepoch', 'localtime') as local_day
            FROM telemetry_history
            WHERE epoch >= ? AND epoch <= ?
            ORDER BY epoch ASC
        """, (start_epoch, end_epoch)).fetchall()

    day_records_map = defaultdict(list)
    for r in records:
        day_records_map[r["local_day"]].append(r)

    daily_list = []
    for d in sorted(day_records_map.keys(), reverse=True):
        daily_list.append(compute_day_metrics(day_records_map[d], d, tariff))

    if not daily_list and start_day == end_day:
        daily_list.append(compute_day_metrics([], start_day, tariff))

    total_pv_kwh = sum(d["pv_kwh"] for d in daily_list)
    total_load_kwh = sum(d["load_kwh"] for d in daily_list)
    total_grid_kwh = sum(d["grid_kwh"] for d in daily_list)
    total_savings = sum(d["savings_pkr"] for d in daily_list)
    total_grid_cost = sum(d["grid_cost_pkr"] for d in daily_list)

    peak_pv_all = max([d["peak_pv_w"] for d in daily_list], default=0.0)
    peak_load_all = max([d["peak_load_w"] for d in daily_list], default=0.0)
    peak_grid_all = max([d["peak_grid_w"] for d in daily_list], default=0.0)

    if total_load_kwh > 0.01:
        overall_autarky = max(0.0, min(100.0, ((total_load_kwh - total_grid_kwh) / total_load_kwh) * 100.0))
    else:
        overall_autarky = 100.0

    return {
        "range_type": range_type,
        "start_date": start_day,
        "end_date": end_day,
        "start_time": s_time,
        "end_time": e_time,
        "tariff_pkr": tariff,
        "currency": currency,
        "totals": {
            "solar_kwh": round(total_pv_kwh, 2),
            "load_kwh": round(total_load_kwh, 2),
            "grid_kwh": round(total_grid_kwh, 2),
            "savings_pkr": round(total_savings, 2),
            "grid_cost_pkr": round(total_grid_cost, 2),
            "autarky_pct": round(overall_autarky, 1),
            "peak_pv_w": peak_pv_all,
            "peak_load_w": peak_load_all,
            "peak_grid_w": peak_grid_all,
            "active_days": len(daily_list),
        },
        "daily": daily_list,
    }


def get_power_timeseries(
    range_type: str = "today",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    start_time: Optional[str] = "00:00",
    end_time: Optional[str] = "23:59",
) -> dict[str, Any]:
    """Retrieve high-resolution time series of Solar (W), Load (W), Grid (W),
    Battery Charge (W), Battery Discharge (W), and Battery SOC (%) for any filtered period.
    Dynamically applies adaptive downsampling to keep charts smooth and responsive.
    """
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    s_time = (start_time or "00:00").strip()
    e_time = (end_time or "23:59").strip()

    if range_type == "today":
        start_day = today_str
        end_day = today_str
    elif range_type == "yesterday":
        yest = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        start_day = yest
        end_day = yest
    elif range_type == "7d":
        start_day = (now - timedelta(days=6)).strftime("%Y-%m-%d")
        end_day = today_str
    elif range_type == "30d":
        start_day = (now - timedelta(days=29)).strftime("%Y-%m-%d")
        end_day = today_str
    elif range_type == "this_month":
        start_day = now.strftime("%Y-%m-01")
        end_day = today_str
    elif (range_type == "custom" or start_date or end_date) and start_date and end_date:
        range_type = "custom"
        start_day = start_date
        end_day = end_date
    else:
        start_day = "2026-01-01"
        end_day = today_str

    try:
        s_dt = datetime.strptime(f"{start_day} {s_time}", "%Y-%m-%d %H:%M").astimezone()
        start_epoch = s_dt.timestamp()
    except Exception:
        s_dt = datetime.strptime(f"{start_day} 00:00", "%Y-%m-%d %H:%M").astimezone()
        start_epoch = s_dt.timestamp()

    try:
        if len(e_time) == 5:
            e_dt = datetime.strptime(f"{end_day} {e_time}:59", "%Y-%m-%d %H:%M:%S").astimezone()
        else:
            e_dt = datetime.strptime(f"{end_day} {e_time}", "%Y-%m-%d %H:%M:%S").astimezone()
        end_epoch = e_dt.timestamp()
    except Exception:
        e_dt = datetime.strptime(f"{end_day} 23:59:59", "%Y-%m-%d %H:%M:%S").astimezone()
        end_epoch = e_dt.timestamp()

    if end_epoch < start_epoch:
        start_epoch, end_epoch = end_epoch, start_epoch

    duration_sec = max(1.0, end_epoch - start_epoch)
    if duration_sec <= 900:  # <= 15 minutes
        bucket = 5
    elif duration_sec <= 1800:  # <= 30 minutes
        bucket = 15
    elif duration_sec <= 3600 * 2:  # <= 2 hours
        bucket = 30
    elif duration_sec <= 24 * 3600:  # <= 24 hours (1m cadence)
        bucket = 60
    elif duration_sec <= 3 * 86400:  # <= 3 days (2m cadence)
        bucket = 120
    elif duration_sec <= 7 * 86400:  # <= 7 days (5m cadence)
        bucket = 300
    elif duration_sec <= 31 * 86400:  # <= 1 month (30m cadence)
        bucket = 1800
    else:
        bucket = 3600

    with get_db_connection() as conn:
        records = conn.execute("""
            SELECT 
                (CAST(epoch / ? AS INT) * ?) as bucket_epoch,
                ROUND(AVG(pv_power), 1) as solar_w,
                ROUND(AVG(load_w), 1) as load_w,
                ROUND(AVG(grid_w), 1) as wapda_w,
                ROUND(AVG(CASE WHEN bat_v > 0 AND bat_charge_a > 0 THEN bat_v * bat_charge_a ELSE 0.0 END), 1) as bat_charge_w,
                ROUND(AVG(CASE WHEN bat_v > 0 AND bat_discharge_a > 0 THEN bat_v * bat_discharge_a ELSE 0.0 END), 1) as bat_discharge_w,
                ROUND(AVG(bat_soc), 0) as bat_soc
            FROM telemetry_history
            WHERE epoch >= ? AND epoch <= ?
            GROUP BY (CAST(epoch / ? AS INT))
            ORDER BY bucket_epoch ASC
        """, (bucket, bucket, start_epoch, end_epoch, bucket)).fetchall()

    labels: list[str] = []
    solar_series: list[float] = []
    load_series: list[float] = []
    grid_series: list[float] = []
    bat_charge_series: list[float] = []
    bat_discharge_series: list[float] = []
    bat_soc_series: list[int] = []

    for r in records:
        ep = r["bucket_epoch"]
        if bucket < 60:
            dt_label = datetime.fromtimestamp(ep).astimezone().strftime("%H:%M:%S")
        elif duration_sec <= 86400 * 1.5:
            dt_label = datetime.fromtimestamp(ep).astimezone().strftime("%H:%M")
        elif duration_sec <= 86400 * 7:
            dt_label = datetime.fromtimestamp(ep).astimezone().strftime("%b %d, %H:%M")
        else:
            dt_label = datetime.fromtimestamp(ep).astimezone().strftime("%b %d, %H:%M")

        labels.append(dt_label)
        solar_series.append(float(r["solar_w"] or 0.0))
        load_series.append(float(r["load_w"] or 0.0))
        grid_series.append(float(r["wapda_w"] or 0.0))
        bat_charge_series.append(float(r["bat_charge_w"] or 0.0))
        bat_discharge_series.append(float(r["bat_discharge_w"] or 0.0))
        bat_soc_series.append(int(r["bat_soc"] or 0))

    peak_solar = max(solar_series, default=0.0)
    peak_load = max(load_series, default=0.0)
    peak_grid = max(grid_series, default=0.0)
    peak_bat_charge = max(bat_charge_series, default=0.0)
    peak_bat_discharge = max(bat_discharge_series, default=0.0)

    return {
        "range_type": range_type,
        "start_date": start_day,
        "end_date": end_day,
        "start_time": s_time,
        "end_time": e_time,
        "start_epoch": start_epoch,
        "end_epoch": end_epoch,
        "bucket_seconds": bucket,
        "point_count": len(records),
        "labels": labels,
        "solar_w": solar_series,
        "load_w": load_series,
        "wapda_w": grid_series,
        "bat_charge_w": bat_charge_series,
        "bat_discharge_w": bat_discharge_series,
        "bat_soc": bat_soc_series,
        "summary": {
            "peak_solar_w": peak_solar,
            "peak_load_w": peak_load,
            "peak_wapda_w": peak_grid,
            "peak_bat_charge_w": peak_bat_charge,
            "peak_bat_discharge_w": peak_bat_discharge,
        },
    }


def get_day_hourly_profile(day_str: str) -> dict[str, Any]:
    """Calculate 24-hour profile (00:00 to 23:00) comparing Solar, Load, and Grid for a given date in local time."""
    try:
        d_start = datetime.strptime(f"{day_str} 00:00:00", "%Y-%m-%d %H:%M:%S").astimezone().timestamp()
        d_end = datetime.strptime(f"{day_str} 23:59:59", "%Y-%m-%d %H:%M:%S").astimezone().timestamp()
    except Exception:
        d_start, d_end = 0, time.time() + 86400

    with get_db_connection() as conn:
        records = conn.execute("""
            SELECT 
                CAST(strftime('%H', datetime(epoch, 'unixepoch', 'localtime')) AS INT) as hour,
                ROUND(AVG(pv_power), 1) as avg_pv_w,
                ROUND(MAX(pv_power), 1) as max_pv_w,
                ROUND(AVG(load_w), 1) as avg_load_w,
                ROUND(MAX(load_w), 1) as max_load_w,
                ROUND(AVG(grid_w), 1) as avg_grid_w,
                ROUND(AVG(bat_soc), 0) as avg_soc,
                COUNT(*) as count
            FROM telemetry_history
            WHERE epoch >= ? AND epoch <= ?
            GROUP BY CAST(strftime('%H', datetime(epoch, 'unixepoch', 'localtime')) AS INT)
            ORDER BY hour ASC
        """, (d_start, d_end)).fetchall()

    hours_data = {r["hour"]: dict(r) for r in records}

    labels = []
    pv_series = []
    load_series = []
    grid_series = []
    soc_series = []

    for h in range(24):
        label = f"{h:02d}:00"
        labels.append(label)
        if h in hours_data:
            pv_series.append(hours_data[h]["avg_pv_w"] or 0.0)
            load_series.append(hours_data[h]["avg_load_w"] or 0.0)
            grid_series.append(hours_data[h]["avg_grid_w"] or 0.0)
            soc_series.append(hours_data[h]["avg_soc"] or 0)
        else:
            pv_series.append(0.0)
            load_series.append(0.0)
            grid_series.append(0.0)
            soc_series.append(0)

    return {
        "date": day_str,
        "labels": labels,
        "pv_series": pv_series,
        "load_series": load_series,
        "grid_series": grid_series,
        "soc_series": soc_series,
    }


def get_monthly_analytics(year: Optional[int] = None, tariff_pkr: Optional[float] = None) -> list[dict[str, Any]]:
    """Aggregate history by month (e.g. 2026-09) with monthly totals and savings."""
    cfg = load_tariff_config()
    tariff = tariff_pkr if tariff_pkr is not None else cfg.get("rate_per_kwh", DEFAULT_TARIFF_RATE)

    daily_data = get_daily_analytics(range_type="all", tariff_pkr=tariff)
    monthly_map: dict[str, dict[str, Any]] = {}

    for d in daily_data["daily"]:
        month_str = d["date"][:7]
        if month_str not in monthly_map:
            monthly_map[month_str] = {
                "month": month_str,
                "solar_kwh": 0.0,
                "load_kwh": 0.0,
                "grid_kwh": 0.0,
                "savings_pkr": 0.0,
                "grid_cost_pkr": 0.0,
                "days_count": 0,
                "peak_pv_w": 0.0,
                "peak_load_w": 0.0,
            }
        m = monthly_map[month_str]
        m["solar_kwh"] += d["pv_kwh"]
        m["load_kwh"] += d["load_kwh"]
        m["grid_kwh"] += d["grid_kwh"]
        m["savings_pkr"] += d["savings_pkr"]
        m["grid_cost_pkr"] += d["grid_cost_pkr"]
        m["days_count"] += 1
        m["peak_pv_w"] = max(m["peak_pv_w"], d["peak_pv_w"])
        m["peak_load_w"] = max(m["peak_load_w"], d["peak_load_w"])

    result = []
    for m_key in sorted(monthly_map.keys(), reverse=True):
        m = monthly_map[m_key]
        load_kwh = m["load_kwh"]
        grid_kwh = m["grid_kwh"]
        autarky = round(((load_kwh - grid_kwh) / load_kwh) * 100.0, 1) if load_kwh > 0.01 else 100.0
        result.append({
            "month": m["month"],
            "solar_kwh": round(m["solar_kwh"], 2),
            "load_kwh": round(m["load_kwh"], 2),
            "grid_kwh": round(m["grid_kwh"], 2),
            "savings_pkr": round(m["savings_pkr"], 2),
            "grid_cost_pkr": round(m["grid_cost_pkr"], 2),
            "autarky_pct": autarky,
            "days_count": m["days_count"],
            "peak_pv_w": m["peak_pv_w"],
            "peak_load_w": m["peak_load_w"],
        })
    return result


def export_csv_data(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    start_time: Optional[str] = "00:00",
    end_time: Optional[str] = "23:59",
) -> str:
    """Generate a clean CSV string of daily analytics for export."""
    cfg = load_tariff_config()
    currency = cfg.get("currency", DEFAULT_CURRENCY)

    data = get_daily_analytics(
        range_type="custom" if (start_date and end_date) else "all",
        start_date=start_date,
        end_date=end_date,
        start_time=start_time,
        end_time=end_time,
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Date",
        "Solar Generated (kWh)",
        "Home Load (kWh)",
        f"{GRID_LABEL} Imported (kWh)",
        "Battery Charged (kWh)",
        "Battery Discharged (kWh)",
        "Solar Independence (%)",
        f"Estimated Savings ({currency})",
        "Peak Solar (W)",
        "Peak Solar Time",
        "Peak Load (W)",
        "Peak Load Time",
        f"Peak {GRID_LABEL} (W)",
        f"Peak {GRID_LABEL} Time",
        "Min Battery SOC (%)",
        "Max Battery SOC (%)"
    ])

    for d in data["daily"]:
        writer.writerow([
            d["date"],
            d["pv_kwh"],
            d["load_kwh"],
            d["grid_kwh"],
            d["bat_charge_kwh"],
            d["bat_discharge_kwh"],
            d["autarky_pct"],
            d["savings_pkr"],
            d["peak_pv_w"],
            d["peak_pv_time"],
            d["peak_load_w"],
            d["peak_load_time"],
            d["peak_grid_w"],
            d["peak_grid_time"],
            d["min_soc"],
            d["max_soc"]
        ])

    return output.getvalue()
