# SolarPulse ⚡

> **Live local dashboard for your Voltronic & Axpert solar inverters — 1-second real-time power flow over your home Wi-Fi, with zero cloud delay.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-MQTT%20Discovery-41BDF5.svg)](https://www.home-assistant.io/)
[![Docker Ready](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](https://www.docker.com/)

---

## ☀️ The Problem SolarPulse Solves

Most off-grid and hybrid solar inverters ship with an external Wi-Fi data logger (such as the **Eybond Wi-Fi Plug Pro**) that transmits readings outward to manufacturer cloud servers (e.g. *SmartESS*, *DessMonitor*, or *ValueClouds*).

While functional, cloud monitoring has major drawbacks:
- ⏳ **5-Minute Cloud Delay**: The mobile apps update only once every 5 minutes. You cannot see sudden appliance spikes, cloud shading, or voltage dips in real time.
- 🌐 **Internet Dependency**: If your home broadband goes down or the manufacturer cloud server has an outage, you lose all visibility into your solar system.
- 🔒 **Zero Native Local Access**: Port scanners report 0 open TCP ports on the Wi-Fi dongle because it operates strictly as an outbound client.

**SolarPulse bypasses the cloud entirely.** By sending a local UDP handshake on port `58899`, SolarPulse redirects the dongle to stream telemetry directly to your local PC, Raspberry Pi, or home server over your LAN, delivering **1 to 2 second real-time updates with 100% local privacy**.

---

## 🔌 Hardware & Inverter Compatibility

SolarPulse works with inverters speaking the **Voltronic PI30 protocol** paired with an **Eybond / SmartESS Wi-Fi Plug Pro** data logger. 

Because **Voltronic Power** is the primary ODM/OEM manufacturer behind dozens of globally rebadged brands, this software is compatible with:

### Compatible Inverter Brands & Series

| Manufacturer / Brand | Supported Models & Platforms |
|---|---|
| **Knox** | Knox ECO 5000, Knox Krypton, Knox Xenon, Knox Oxygen series |
| **Axpert (Voltronic Native)** | Axpert VM, VM II, VM III, VM IV (4.2kW / 5kW / 6kW), King, King II, MKS, MKS II/III/IV, MAX, MAX II |
| **Inverex** | Aerox, Veyron, Yukon, Nitrox off-grid & hybrid series |
| **MPP Solar** | PIP-GK, PIP-MK, PIP-MS, PIP-MAX, PIP-LV series |
| **Fronus** | Infini, Platinum, Solar King, Eco-Green series |
| **Growatt** | Off-grid SPF series using PI30 / Axpert command set (e.g. SPF 3000TL / 5000TL HVM/ES) |
| **Phocos** | Any-Grid PSW-H series |
| **EASun Power** | BA-ISolar, SMG, IGrid SV/II/III series |
| **Crown Micro** | Elego, Elegance series |
| **Homage** | Vertex, Octane series |
| **Kodak / RCT Power** | OG 3.24, OG 5.48, OG-Plus off-grid series |

### Compatible Wi-Fi Data Loggers
- **Eybond Wi-Fi Plug Pro** (Serial / PN prefix `E5000...`, `E5...`, `WIFI-PLUG-PRO`)
- **SmartESS / DessMonitor / ValueClouds** Wi-Fi dongles (using UDP discovery port `58899` and TCP port `8899`)
- **Shinemonitor** OEM plugs running Eybond firmware

> For a deep technical breakdown of the reverse-engineered communication frames and byte escaping, see [HOW_IT_WORKS.md](HOW_IT_WORKS.md).

---

## ✨ Features

- ⚡ **1–2 Second Live Telemetry**: High-frequency sub-second querying over your local Wi-Fi.
- 🔄 **Live Animated Energy Flow**: Dynamic glassmorphic SVG power routing diagram showing Solar PV ➔ Inverter ➔ Home Load / Battery Bank / Utility Grid with animated particles proportional to wattage.
- 📊 **Local SQLite Logging**: Automated WAL-mode logging with adaptive downsampling rollups (`1h`, `6h`, `24h`, `7d`, `30d`).
- 📈 **Interactive Historical Analytics**:
  - Daily & monthly solar generation, grid import, and battery throughput.
  - Peak solar and load timestamps.
  - Solar independence (autarky %) and self-consumption calculations.
  - Configurable electricity tariff and bill savings tracking in any currency (`USD`, `EUR`, `PKR`, `GBP`, etc.).
  - 1-click CSV spreadsheet export.
- 🏠 **Home Assistant Auto-Discovery via MQTT**: Automatically registers all sensors in Home Assistant without editing YAML.
- 🛡️ **Self-Healing Connection Watchdog**: Periodic UDP keepalives and automatic reconnection keep the dongle locked to your local server without falling back to cloud mode.

---

## 🚀 Quick Start

### Option 1: Windows 1-Click Launch (Easiest)

1. [Download the repository](https://github.com/musaoc/SolarPulse/archive/refs/heads/main.zip) (or `git clone https://github.com/musaoc/SolarPulse.git`).
2. Double-click:
   ```bat
   start_solarpulse.bat
   ```
   *That's it!* It automatically checks Python, installs needed packages, starts the local server, and pops open your browser at **http://localhost:8500**.

---

### Option 2: Docker & Docker Compose (Raspberry Pi / Linux / NAS)

1. Clone the repository:
   ```bash
   git clone https://github.com/musaoc/SolarPulse.git
   cd SolarPulse
   ```
2. Copy the example configuration:
   ```bash
   cp .env.example .env
   ```
3. Edit `.env` to set your dongle's IP (e.g. `DONGLE_IP=192.168.0.100`).
4. Start the container:
   ```bash
   docker compose up -d
   ```
5. Open your browser:
   👉 **http://localhost:8500**

> **Note on Docker Networking**: The compose file uses `network_mode: host` so the container can communicate directly with your Wi-Fi dongle over UDP/TCP on your home network without NAT restrictions.

---

### Option 3: Manual Python Setup (macOS / Linux / Developers)

```bash
# 1. Clone repository
git clone https://github.com/musaoc/SolarPulse.git
cd SolarPulse

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy and configure .env
cp .env.example .env

# 5. Launch server
python -m uvicorn backend.app:app --host 0.0.0.0 --port 8500
```

---

## ⚙️ Configuration (.env)

| Variable | Default | Description |
|---|---|---|
| `DONGLE_IP` | `192.168.0.100` | IP address of your Wi-Fi dongle on your local network |
| `DONGLE_UDP_PORT` | `58899` | Port used by dongle firmware for broadcast redirect packets |
| `COLLECTOR_TCP_PORT`| `8899` | TCP port on your local server that the dongle connects to |
| `WEB_PORT` | `8500` | HTTP port for the web dashboard and REST API |
| `POLL_INTERVAL` | `2.0` | Cadence in seconds between inverter queries (1.0s to 5.0s) |
| `GRID_LABEL` | `Utility Grid` | Display label for your grid in UI & MQTT (e.g. `Utility Grid`, `WAPDA`, `Eskom`) |
| `CURRENCY` | `USD` | Default currency symbol / code (e.g. `USD`, `EUR`, `PKR`, `GBP`) |
| `TARIFF_RATE` | `0.20` | Default cost per kWh for bill savings estimation |
| `INVERTER_SN` | `00000000000000`| (Optional) Inverter serial number for MQTT unique IDs |

---

## 🏠 Home Assistant Integration (MQTT Discovery)

SolarPulse includes native **Home Assistant MQTT Auto-Discovery**. Once enabled, all inverter sensors appear automatically in Home Assistant under **Settings ➔ Devices & Services ➔ MQTT**.

1. In the SolarPulse web dashboard, open the **Settings** modal.
2. Enter your MQTT Broker details (e.g., `192.168.0.50`, port `1883`, username, and password).
3. Click **Save & Connect**.
4. SolarPulse publishes MQTT discovery payloads for:
   - Solar PV Power (W) and Voltage (V)
   - Home Output Active Power (W) and Load (%)
   - Battery Voltage (V), SOC (%), Net Power (W), Charge Current (A), Discharge Current (A)
   - Grid Voltage (V), Grid Import Power (W), and Online Binary Status
   - Inverter Heat Sink Temperature (°C) and Operating Mode

---

## 📡 REST & WebSocket API

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/live` | Current real-time telemetry snapshot |
| `WS` | `/ws/live` | Streaming WebSocket push for instant live UI updates |
| `GET` | `/api/history?range=1h\|6h\|24h\|7d\|30d` | Downsampled time-series data for interactive charts |
| `GET` | `/api/summary` | Today's aggregate generation, consumption, and battery kWh |
| `GET` | `/api/analytics/daily?range=today\|yesterday\|7d\|30d` | Daily KPI breakdown, autarky %, and savings |
| `GET` | `/api/analytics/power_timeseries` | Multi-series power flow timeline |
| `GET` | `/api/analytics/export_csv` | Download filtered history as a CSV file |
| `POST`| `/api/settings/poll_interval` | Dynamically update poll rate: `{"interval": 1.5}` |
| `GET` | `/api/status` | Inverter connection health and link diagnostics |

---

## 🤝 Contributing

Contributions are warmly welcome! If you have tested SolarPulse with an inverter brand or dongle model not listed above, please submit a PR to update the compatibility table or share your feedback in GitHub Discussions.

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/NewInverterSupport`)
3. Commit your Changes (`git commit -m 'Add support for Inverter XYZ'`)
4. Push to the Branch (`git push origin feature/NewInverterSupport`)
5. Open a Pull Request

---

## 📄 License

Distributed under the **MIT License**. See [LICENSE](LICENSE) for more information.
