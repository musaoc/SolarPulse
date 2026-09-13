"""
Cloud Synchronization module for SolarPulse / EyBond DESS platform.
Enables fetching data snapshots recorded on the cloud server while the local server was turned off.
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
import requests

from .config import BASE_DIR
from .database import get_db_connection

logger = logging.getLogger(__name__)

CLOUD_CONFIG_FILE = BASE_DIR / "cloud_config.json"

DEFAULT_CLOUD_CONFIG = {
    "enabled": False,
    "url": "https://api.valueclouds.com/ppe/api/auth/web/querySPDeviceLastData?devcode=2451&pn=&devaddr=1&sn=&i18n=en_US",
    "auth": "",
    "token": "",
    "sign": "",
    "project": "DESS",
    "origin": "https://www.dessmonitor.com",
    "referer": "https://www.dessmonitor.com/",
    "last_sync_timestamp": None,
    "last_sync_status": "disabled",
}


def load_cloud_config() -> dict[str, Any]:
    """Load cloud configuration from disk or create default."""
    if CLOUD_CONFIG_FILE.exists():
        try:
            with open(CLOUD_CONFIG_FILE, "r") as f:
                cfg = json.load(f)
                return {**DEFAULT_CLOUD_CONFIG, **cfg}
        except Exception as e:
            logger.error("Failed to read cloud config: %s", e)
    return DEFAULT_CLOUD_CONFIG


def save_cloud_config(cfg: dict[str, Any]):
    """Persist cloud config to JSON file."""
    try:
        with open(CLOUD_CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        logger.error("Failed to save cloud config: %s", e)


def fetch_cloud_last_data(config: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    """Fetch the latest snapshot recorded on the ValueClouds/DESS cloud server."""
    cfg = config or load_cloud_config()
    if not cfg.get("enabled", False) or not cfg.get("auth"):
        return None

    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "en-US,en;q=0.9",
        "auth": cfg.get("auth", ""),
        "origin": cfg.get("origin", "https://www.dessmonitor.com"),
        "project": cfg.get("project", "DESS"),
        "referer": cfg.get("referer", "https://www.dessmonitor.com/"),
        "sec-ch-ua": '"Not;A=Brand";v="8", "Chromium";v="150"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "cross-site",
        "sign": cfg.get("sign", ""),
        "token": cfg.get("token", ""),
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    try:
        url = cfg.get("url")
        if not url:
            return None
        res = requests.get(url, headers=headers, timeout=12)
        if res.status_code != 200:
            logger.warning("Cloud server returned HTTP %d", res.status_code)
            return None

        body = res.json()
        if body.get("code") != 0 or "data" not in body:
            logger.warning("Cloud server error response: %s", body.get("msg") or body)
            return None

        data = body["data"]
        gts = data.get("gts")  # Millisecond epoch
        if not gts:
            return None

        epoch_sec = float(gts) / 1000.0
        pars = data.get("pars", {})

        # Extract parameters from categories
        pv_v = 0.0
        pv_w = 0.0
        pv_i = 0.0
        grid_v = 0.0
        grid_freq = 50.0
        bat_v = 0.0
        bat_soc = 0
        bat_chg_a = 0.0
        bat_dischg_a = 0.0
        ac_w = 0.0
        ac_va = 0.0
        load_pct = 0
        mode_code = "L"

        def _float_val(item: dict) -> float:
            try:
                v = item.get("val", "0")
                return float(v) if v and v != "-" else 0.0
            except Exception:
                return 0.0

        def _int_val(item: dict) -> int:
            try:
                v = item.get("val", "0")
                return int(float(v)) if v and v != "-" else 0
            except Exception:
                return 0

        for item in pars.get("pv_", []):
            par_name = item.get("par", "").lower()
            if "pv1 input voltage" in par_name:
                pv_v = _float_val(item)
            elif "pv1 charging power" in par_name:
                pv_w = _float_val(item)
            elif "pv1 input current" in par_name:
                pv_i = _float_val(item)

        for item in pars.get("gd_", []):
            par_name = item.get("par", "").lower()
            if "grid voltage" in par_name:
                grid_v = _float_val(item)
            elif "grid frequency" in par_name:
                grid_freq = _float_val(item)

        for item in pars.get("bt_", []):
            par_name = item.get("par", "").lower()
            if "battery voltage" in par_name:
                bat_v = _float_val(item)
            elif "battery capacity" in par_name:
                bat_soc = _int_val(item)
            elif "battery charging current" in par_name:
                bat_chg_a = _float_val(item)
            elif "battery discharge current" in par_name:
                bat_dischg_a = _float_val(item)

        for item in pars.get("bc_", []):
            par_name = item.get("par", "").lower()
            if "ac output active power" in par_name:
                ac_w = _float_val(item)
            elif "ac1 output apparent power" in par_name:
                ac_va = _float_val(item)
            elif "output load percent" in par_name:
                load_pct = _int_val(item)

        for item in pars.get("sy_", []):
            par_name = item.get("par", "").lower()
            if "model" in par_name:
                val = item.get("val", "")
                if "battery" in val.lower():
                    mode_code = "B"
                elif "line" in val.lower():
                    mode_code = "L"

        if pv_w == 0.0 and pv_v > 0 and pv_i > 0:
            pv_w = round(pv_v * pv_i, 1)

        bat_net_w = round(bat_v * (bat_chg_a - bat_dischg_a), 1)

        is_grid_available = grid_v >= 90.0
        if is_grid_available and mode_code == "L":
            chg_w = max(0.0, bat_v * bat_chg_a / 0.90) if bat_chg_a > 0 else 0.0
            grid_w = round(ac_w + chg_w, 1)
        else:
            grid_w = 0.0

        iso_time = datetime.fromtimestamp(epoch_sec, tz=timezone.utc).isoformat()

        snapshot = {
            "source": "cloud_server",
            "timestamp": iso_time,
            "epoch": epoch_sec,
            "pv_power": pv_w,
            "pv_voltage": pv_v,
            "pv_current": pv_i,
            "load_w": ac_w,
            "load_va": ac_va,
            "load_pct": load_pct,
            "bat_v": bat_v,
            "bat_charge_a": bat_chg_a,
            "bat_discharge_a": bat_dischg_a,
            "bat_soc": bat_soc,
            "bat_net_w": bat_net_w,
            "grid_v": grid_v,
            "grid_freq": grid_freq,
            "grid_w": grid_w,
            "mode": mode_code,
            "inverter_temp": 35,
        }
        return snapshot

    except Exception as e:
        logger.error("Cloud fetch exception: %s", e)
        return None


def sync_cloud_offline_gap() -> dict[str, Any]:
    """Check for time gaps between local telemetry and cloud recordings, and backfill."""
    cfg = load_cloud_config()
    if not cfg.get("enabled", False) or not cfg.get("auth"):
        return {"status": "disabled"}

    with get_db_connection() as conn:
        cursor = conn.execute("SELECT MAX(epoch) as latest_epoch, COUNT(*) as total FROM telemetry_history;")
        row = cursor.fetchone()
        latest_local_epoch = row["latest_epoch"] if row and row["latest_epoch"] else 0.0

    cloud_snap = fetch_cloud_last_data(cfg)
    if not cloud_snap:
        cfg["last_sync_status"] = "fetch_failed"
        save_cloud_config(cfg)
        return {"status": "error", "message": "Failed to fetch cloud data"}

    cloud_epoch = cloud_snap["epoch"]
    gap_seconds = cloud_epoch - latest_local_epoch

    cfg["last_sync_timestamp"] = datetime.now(timezone.utc).isoformat()
    cfg["cloud_last_gts"] = cloud_snap["timestamp"]

    backfilled = 0
    if gap_seconds > 90:
        logger.info(
            "Detected offline gap of %.0f seconds. Backfilling cloud snapshot recorded at %s...",
            gap_seconds, cloud_snap["timestamp"]
        )
        with get_db_connection() as conn:
            existing = conn.execute("SELECT id FROM telemetry_history WHERE epoch = ?", (cloud_epoch,)).fetchone()
            if not existing:
                conn.execute("""
                    INSERT INTO telemetry_history (
                        timestamp, epoch, pv_power, pv_voltage, pv_current,
                        load_w, load_va, load_pct, bat_v, bat_charge_a,
                        bat_discharge_a, bat_soc, bat_net_w, grid_v,
                        grid_freq, grid_w, inverter_temp, mode
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    cloud_snap["timestamp"],
                    cloud_snap["epoch"],
                    cloud_snap["pv_power"],
                    cloud_snap["pv_voltage"],
                    cloud_snap["pv_current"],
                    cloud_snap["load_w"],
                    cloud_snap["load_va"],
                    cloud_snap["load_pct"],
                    cloud_snap["bat_v"],
                    cloud_snap["bat_charge_a"],
                    cloud_snap["bat_discharge_a"],
                    cloud_snap["bat_soc"],
                    cloud_snap["bat_net_w"],
                    cloud_snap["grid_v"],
                    cloud_snap["grid_freq"],
                    cloud_snap["grid_w"],
                    cloud_snap["inverter_temp"],
                    cloud_snap["mode"],
                ))
                conn.commit()
                backfilled += 1
                logger.info("Successfully backfilled cloud snapshot into SQLite!")

        cfg["last_sync_status"] = "synced_gap"
    else:
        cfg["last_sync_status"] = "up_to_date"

    save_cloud_config(cfg)

    return {
        "status": "ok",
        "gap_seconds": max(0, round(gap_seconds, 1)),
        "cloud_epoch": cloud_epoch,
        "cloud_time": cloud_snap["timestamp"],
        "backfilled": backfilled,
        "snapshot": cloud_snap,
    }
