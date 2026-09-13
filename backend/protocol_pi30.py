"""
PI30 Protocol Encoder, Decoder, and Parsers for Voltronic / Axpert / Knox inverters.
"""

import logging
from typing import Any, Optional

from .config import GRID_LABEL

logger = logging.getLogger(__name__)


def crc16_xmodem(data: bytes) -> int:
    """Compute the standard PI30 CRC16/XMODEM checksum."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def _escape_crc_byte(val: int) -> int:
    """PI30 character escaping for delimiters '(', '\\r', and '\\n'."""
    return val + 1 if val in {0x28, 0x0D, 0x0A} else val


def encode_crc(payload: bytes) -> bytes:
    """Calculate and escape the 2-byte CRC for a command payload."""
    crc = crc16_xmodem(payload)
    high = _escape_crc_byte((crc >> 8) & 0xFF)
    low = _escape_crc_byte(crc & 0xFF)
    return bytes((high, low))


def build_request(cmd: str) -> bytes:
    """Build a complete PI30 command frame with escaped CRC and trailing CR."""
    payload = cmd.encode("ascii")
    return payload + encode_crc(payload) + b"\r"


def parse_response_frame(frame: bytes) -> str:
    """Validate and unwrap a PI30 response frame.
    
    Expected format: b'(<ASCII_DATA><CRC:2>\\r'
    Returns decoded ASCII string without leading '(' and trailing CRC+CR.
    """
    if len(frame) < 4:
        raise ValueError(f"PI30 frame too short: {len(frame)} bytes")
    if frame[-1] != 0x0D:
        raise ValueError(f"PI30 frame missing trailing CR: {frame[-1]:#x}")
    if not frame.startswith(b"("):
        raise ValueError(f"PI30 frame missing leading '(': {frame[:1]}")

    body = frame[:-3]  # includes '(' and payload
    crc_received = frame[-3:-1]

    # Verify CRC
    expected_crc = encode_crc(body)
    if crc_received != expected_crc:
        logger.debug("PI30 CRC mismatch: rx=%s expected=%s (proceeding with payload)",
                     crc_received.hex(), expected_crc.hex())

    # Return ASCII payload stripped of leading '('
    return body[1:].decode("ascii", errors="replace").strip()


def parse_qpigs(payload: str, current_mode: str = "L") -> dict[str, Any]:
    """Parse space-separated QPIGS response fields into structured telemetry dictionary."""
    fields = [f for f in payload.strip().split(" ") if f]
    if len(fields) < 17:
        raise ValueError(f"QPIGS response had only {len(fields)} fields, expected >= 17")

    def _float(val: str, default: float = 0.0) -> float:
        try:
            return float(val)
        except Exception:
            return default

    def _int(val: str, default: int = 0) -> int:
        try:
            return int(val)
        except Exception:
            return default

    grid_v = _float(fields[0])
    grid_freq = _float(fields[1])
    ac_out_v = _float(fields[2])
    ac_out_freq = _float(fields[3])
    ac_out_va = _int(fields[4])
    ac_out_w = _int(fields[5])
    load_pct = _int(fields[6])
    bus_v = _int(fields[7])
    bat_v = _float(fields[8])
    bat_charge_a = _int(fields[9])
    bat_soc = _int(fields[10])
    inverter_temp = _int(fields[11])
    pv_i = _float(fields[12])
    pv_v = _float(fields[13])
    bat_scc_v = _float(fields[14])
    bat_discharge_a = _int(fields[15])
    raw_status = fields[16] if len(fields) > 16 else ""

    # Extra fields in 21+ layout
    pv_charge_w = _int(fields[19]) if len(fields) > 19 else 0

    # Calculated metrics
    pv_power = round(pv_v * pv_i, 1)
    if pv_power == 0.0 and pv_charge_w > 0:
        pv_power = float(pv_charge_w)

    # Battery net calculation
    bat_net_a = round(bat_charge_a - bat_discharge_a, 1)
    bat_net_w = round(bat_v * bat_net_a, 1)

    if bat_charge_a > 1:
        bat_status = "Charging"
    elif bat_discharge_a > 1:
        bat_status = "Discharging"
    elif bat_soc >= 98:
        bat_status = "Full / Float"
    else:
        bat_status = "Idle"

    # Utility Grid status
    is_grid_available = grid_v >= 90.0
    grid_status = f"{GRID_LABEL} Online" if is_grid_available else "Power Outage / Disconnected"

    # Approximate imported Grid Power
    # When in Line mode (grid bypass), Grid powers the home load plus battery charger
    if is_grid_available and current_mode in ("L", "Line", "LINE"):
        charger_w = max(0.0, bat_v * bat_charge_a / 0.90) if bat_charge_a > 0 else 0.0
        grid_w = round(ac_out_w + charger_w, 1)
    else:
        grid_w = 0.0

    return {
        "grid_voltage": grid_v,
        "grid_frequency": grid_freq,
        "grid_status": grid_status,
        "grid_available": is_grid_available,
        "grid_power_w": grid_w,
        "output_voltage": ac_out_v,
        "output_frequency": ac_out_freq,
        "output_apparent_power": ac_out_va,
        "output_active_power": ac_out_w,
        "load_percent": load_pct,
        "bus_voltage": bus_v,
        "battery_voltage": bat_v,
        "battery_charge_current": bat_charge_a,
        "battery_discharge_current": bat_discharge_a,
        "battery_net_current": bat_net_a,
        "battery_net_power": bat_net_w,
        "battery_soc": bat_soc,
        "battery_status": bat_status,
        "inverter_temperature": inverter_temp,
        "pv_current": pv_i,
        "pv_voltage": pv_v,
        "pv_power": pv_power,
        "battery_scc_voltage": bat_scc_v,
        "raw_status": raw_status,
    }


def parse_qmod(payload: str) -> dict[str, str]:
    """Parse QMOD (operating mode inquiry) response.
    
    P: Power On
    S: Standby
    L: Line Mode (Grid Passthrough)
    B: Battery Mode (Inverter / Off-grid)
    F: Fault Mode
    H: Power Saving Mode
    """
    mode_char = payload.strip().upper()[:1]
    mode_map = {
        "P": "Power On",
        "S": "Standby",
        "L": "Line Mode (Grid Passthrough)",
        "B": "Battery Mode (Inverter Running)",
        "F": "Fault Mode",
        "H": "Power Saving",
    }
    return {
        "mode_code": mode_char,
        "mode_name": mode_map.get(mode_char, f"Unknown ({mode_char})"),
    }
