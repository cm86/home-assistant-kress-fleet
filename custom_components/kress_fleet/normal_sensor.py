# SPDX-License-Identifier: GPL-3.0-only

"""Basic sensors for the normal Kress cloud backend."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE

from .normal_entity import KressNormalEntity


class KressNormalBatterySensor(KressNormalEntity, SensorEntity):
    _attr_translation_key = "battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "battery")

    @property
    def native_value(self):
        battery = getattr(self.device, "battery", {})
        return battery.get("percent") if isinstance(battery, dict) else None


class KressNormalStatusSensor(KressNormalEntity, SensorEntity):
    _attr_translation_key = "status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [
        "idle", "docked", "starting", "returning", "mowing", "error",
        "escaped_digital_fence", "zoning", "edge_cut", "paused",
        "searching_for_zone", "unknown",
    ]
    _attr_icon = "mdi:robot-mower"

    def __init__(self, coordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "status")

    @property
    def native_value(self):
        status_id = int(getattr(getattr(self.device, "status", None), "id", -1))
        return {
            0: "idle", 1: "docked", 2: "starting", 3: "starting",
            4: "returning", 5: "returning", 6: "returning", 7: "mowing",
            8: "error", 9: "error", 10: "error", 11: "error", 12: "mowing",
            13: "escaped_digital_fence", 30: "returning", 31: "zoning",
            32: "edge_cut", 33: "starting", 34: "paused",
            103: "searching_for_zone", 104: "returning",
        }.get(status_id, "unknown")
