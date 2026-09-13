"""
Home Assistant MQTT Integration for SolarPulse with Auto-Discovery.
Publishes telemetry to standard topics and registers entities in Home Assistant.
"""

import json
import logging
import time
from typing import Any, Optional

try:
    import paho.mqtt.client as mqtt
    PAHO_AVAILABLE = True
except ImportError:
    PAHO_AVAILABLE = False

from .config import GRID_LABEL, INVERTER_SN

logger = logging.getLogger(__name__)


class MqttManager:
    """Manages MQTT connection, Home Assistant discovery registration, and metric broadcasting."""

    def __init__(self):
        self.enabled: bool = False
        self.broker: str = "127.0.0.1"
        self.port: int = 1883
        self.username: str = ""
        self.password: str = ""
        self.topic_prefix: str = "solarpulse"
        self.discovery_prefix: str = "homeassistant"

        self.connected: bool = False
        self._client: Optional[Any] = None
        self._discovery_sent: bool = False

    def configure(self, enabled: bool, broker: str, port: int = 1883,
                  username: str = "", password: str = "",
                  topic_prefix: str = "solarpulse"):
        """Update MQTT settings and connect/disconnect accordingly."""
        self.enabled = enabled
        self.broker = broker
        self.port = int(port)
        self.username = username
        self.password = password
        self.topic_prefix = topic_prefix.rstrip("/")

        if self.enabled:
            self.start()
        else:
            self.stop()

    def start(self):
        """Connect to the MQTT broker."""
        if not PAHO_AVAILABLE:
            logger.warning("paho-mqtt is not installed. MQTT publishing disabled.")
            return

        if not self.enabled or not self.broker:
            return

        self.stop()

        try:
            sn_suffix = INVERTER_SN[-6:] if len(INVERTER_SN) >= 6 else "main"
            self._client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=f"solarpulse_{sn_suffix}",
            )

            if self.username:
                self._client.username_pw_set(self.username, self.password)

            self._client.on_connect = self._on_connect
            self._client.on_disconnect = self._on_disconnect

            logger.info("Connecting to MQTT Broker at %s:%d...", self.broker, self.port)
            self._client.connect_async(self.broker, self.port, keepalive=60)
            self._client.loop_start()

        except Exception as e:
            logger.error("Failed to initialize MQTT client: %s", e)
            self.connected = False

    def stop(self):
        """Disconnect and stop client thread."""
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self.connected = False
        self._discovery_sent = False
        logger.info("MQTT Client stopped")

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        """Callback when connected to broker."""
        if reason_code == 0:
            logger.info("Connected to MQTT Broker successfully (%s:%d)", self.broker, self.port)
            self.connected = True
            self._publish_ha_discovery()
        else:
            logger.error("MQTT connection failed with code: %s", reason_code)
            self.connected = False

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties=None):
        logger.warning("MQTT disconnected (code: %s)", reason_code)
        self.connected = False
        self._discovery_sent = False

    def _publish_ha_discovery(self):
        """Register all inverter sensors in Home Assistant via MQTT Discovery."""
        if not self._client or not self.connected:
            return

        device_info = {
            "identifiers": [f"solarpulse_{INVERTER_SN}"],
            "name": "SolarPulse Inverter Monitor",
            "model": "Voltronic / Axpert (PI30 Protocol)",
            "manufacturer": "Voltronic Power / OEM",
            "sw_version": "SolarPulse v1.0.0",
        }

        telemetry_topic = f"{self.topic_prefix}/telemetry"

        sensors = [
            # Home Output Load
            {
                "id": "load_power",
                "name": "Home Load Power",
                "component": "sensor",
                "unit": "W",
                "device_class": "power",
                "state_class": "measurement",
                "icon": "mdi:home-lightning-bolt",
                "val_template": "{{ value_json.output_active_power }}",
            },
            {
                "id": "load_percent",
                "name": "Inverter Load Percent",
                "component": "sensor",
                "unit": "%",
                "icon": "mdi:gauge",
                "val_template": "{{ value_json.load_percent }}",
            },
            # Battery Storage
            {
                "id": "battery_soc",
                "name": "Battery State of Charge",
                "component": "sensor",
                "unit": "%",
                "device_class": "battery",
                "state_class": "measurement",
                "val_template": "{{ value_json.battery_soc }}",
            },
            {
                "id": "battery_voltage",
                "name": "Battery Voltage",
                "component": "sensor",
                "unit": "V",
                "device_class": "voltage",
                "state_class": "measurement",
                "val_template": "{{ value_json.battery_voltage }}",
            },
            {
                "id": "battery_net_power",
                "name": "Battery Net Power",
                "component": "sensor",
                "unit": "W",
                "device_class": "power",
                "state_class": "measurement",
                "val_template": "{{ value_json.battery_net_power }}",
            },
            {
                "id": "battery_charge_current",
                "name": "Battery Charge Current",
                "component": "sensor",
                "unit": "A",
                "device_class": "current",
                "state_class": "measurement",
                "val_template": "{{ value_json.battery_charge_current }}",
            },
            {
                "id": "battery_discharge_current",
                "name": "Battery Discharge Current",
                "component": "sensor",
                "unit": "A",
                "device_class": "current",
                "state_class": "measurement",
                "val_template": "{{ value_json.battery_discharge_current }}",
            },
            # Solar PV
            {
                "id": "solar_pv_power",
                "name": "Solar PV Power",
                "component": "sensor",
                "unit": "W",
                "device_class": "power",
                "state_class": "measurement",
                "icon": "mdi:solar-power-variant",
                "val_template": "{{ value_json.pv_power }}",
            },
            {
                "id": "solar_pv_voltage",
                "name": "Solar PV Voltage",
                "component": "sensor",
                "unit": "V",
                "device_class": "voltage",
                "state_class": "measurement",
                "val_template": "{{ value_json.pv_voltage }}",
            },
            # Utility Grid
            {
                "id": "grid_voltage",
                "name": f"{GRID_LABEL} Voltage",
                "component": "sensor",
                "unit": "V",
                "device_class": "voltage",
                "state_class": "measurement",
                "val_template": "{{ value_json.grid_voltage }}",
            },
            {
                "id": "grid_power",
                "name": f"{GRID_LABEL} Import Power",
                "component": "sensor",
                "unit": "W",
                "device_class": "power",
                "state_class": "measurement",
                "val_template": "{{ value_json.grid_power_w }}",
            },
            {
                "id": "grid_online",
                "name": f"{GRID_LABEL} Online",
                "component": "binary_sensor",
                "device_class": "power",
                "val_template": "{{ 'ON' if value_json.grid_available else 'OFF' }}",
            },
            # Inverter Status
            {
                "id": "inverter_temp",
                "name": "Inverter Temperature",
                "component": "sensor",
                "unit": "°C",
                "device_class": "temperature",
                "state_class": "measurement",
                "val_template": "{{ value_json.inverter_temperature }}",
            },
            {
                "id": "operating_mode",
                "name": "Inverter Operating Mode",
                "component": "sensor",
                "icon": "mdi:solar-panel",
                "val_template": "{{ value_json.mode_name }}",
            },
        ]

        for s in sensors:
            disc_topic = f"{self.discovery_prefix}/{s['component']}/solarpulse_{INVERTER_SN}/{s['id']}/config"
            payload = {
                "name": s["name"],
                "unique_id": f"solarpulse_{INVERTER_SN}_{s['id']}",
                "state_topic": telemetry_topic,
                "value_template": s["val_template"],
                "device": device_info,
            }
            if "unit" in s:
                payload["unit_of_measurement"] = s["unit"]
            if "device_class" in s:
                payload["device_class"] = s["device_class"]
            if "state_class" in s:
                payload["state_class"] = s["state_class"]
            if "icon" in s:
                payload["icon"] = s["icon"]

            self._client.publish(disc_topic, json.dumps(payload), retain=True)

        self._discovery_sent = True
        logger.info("Published %d Home Assistant MQTT discovery sensors", len(sensors))

    def publish_telemetry(self, data: dict[str, Any]):
        """Publish fresh telemetry frame to MQTT."""
        if not self.enabled or not self.connected or not self._client:
            return

        try:
            # 1. Unified JSON payload for Home Assistant discovery templates
            telemetry_topic = f"{self.topic_prefix}/telemetry"
            self._client.publish(telemetry_topic, json.dumps(data), qos=0)

            # 2. Individual sub-topics for easy Node-RED / custom automation consumption
            sub_topics = {
                "load_w": data.get("output_active_power", 0),
                "battery_soc": data.get("battery_soc", 0),
                "battery_voltage": data.get("battery_voltage", 0.0),
                "battery_net_w": data.get("battery_net_power", 0.0),
                "pv_w": data.get("pv_power", 0.0),
                "grid_v": data.get("grid_voltage", 0.0),
                "grid_w": data.get("grid_power_w", 0.0),
                "grid_online": 1 if data.get("grid_available") else 0,
                "inverter_temp": data.get("inverter_temperature", 0),
                "mode": data.get("mode_code", "L"),
            }

            for key, val in sub_topics.items():
                self._client.publish(f"{self.topic_prefix}/{key}", str(val), qos=0)

        except Exception as e:
            logger.debug("Failed to publish MQTT message: %s", e)


# Global MQTT Manager instance
mqtt_manager = MqttManager()
