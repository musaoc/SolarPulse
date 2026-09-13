"""
Configuration settings for SolarPulse local solar monitoring.
Reads settings from environment variables or .env file.
"""

import os
import socket
from pathlib import Path

# Try loading .env file if python-dotenv is installed
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = os.getenv("SOLAR_DB_PATH", str(BASE_DIR / "solar_history.db"))
FRONTEND_DIR = str(BASE_DIR / "frontend")

# Inverter & Dongle Hardware Configuration
DONGLE_IP = os.getenv("DONGLE_IP", "192.168.0.100")
DONGLE_UDP_PORT = int(os.getenv("DONGLE_UDP_PORT", "58899"))
DONGLE_PN = os.getenv("DONGLE_PN", "")
INVERTER_SN = os.getenv("INVERTER_SN", "00000000000000")
DEVCODE = int(os.getenv("DEVCODE", "2451"))  # 0x0993: Axpert / Voltronic PI30 family
DEVADDR = int(os.getenv("DEVADDR", "1"))     # RS-485 Bus Address

# Regional & UI Customization
GRID_LABEL = os.getenv("GRID_LABEL", "Utility Grid")
DEFAULT_CURRENCY = os.getenv("CURRENCY", "USD")
DEFAULT_TARIFF_RATE = float(os.getenv("TARIFF_RATE", "0.20"))


# Local Collector Server Configuration
def get_local_ip() -> str:
    """Find the best local IP that routes to the dongle's subnet."""
    override = os.getenv("HOST_IP")
    if override:
        return override
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Connect to dongle IP (UDP connect does not send packets)
        s.connect((DONGLE_IP, 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


HOST_IP = get_local_ip()
TCP_PORT = int(os.getenv("COLLECTOR_TCP_PORT", "8899"))
WEB_PORT = int(os.getenv("WEB_PORT", "8500"))

# Polling and System Timing
DEFAULT_POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "2.0"))  # Seconds (1.0 to 5.0)
HEARTBEAT_INTERVAL = 60.0                                          # Collector heartbeat (seconds)
UDP_REDIRECT_INTERVAL = 15.0                                       # Seconds to re-announce if disconnected
REQUEST_TIMEOUT = 4.0                                              # Per-query timeout in seconds

# Inverter Electrical Specs
RATED_POWER_W = int(os.getenv("RATED_POWER_W", "5000"))
BATTERY_RATED_VOLTAGE = float(os.getenv("BATTERY_RATED_VOLTAGE", "24.0"))  # 24V or 48V bank
