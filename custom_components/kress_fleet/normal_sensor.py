# SPDX-License-Identifier: GPL-3.0-only

"""Sensors for the normal Kress cloud backend."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfArea,
)
from homeassistant.helpers.entity import EntityCategory

from .normal_entity import KressNormalEntity


def _mapping_value(value: Any, key: str) -> Any:
    """Return a value from a dict-like pyworxcloud attribute."""
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


class KressNormalBatterySensor(KressNormalEntity, SensorEntity):
    _attr_translation_key = "battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "battery")

    @property
    def native_value(self):
        return _mapping_value(getattr(self.device, "battery", None), "percent")


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


class KressNormalErrorSensor(KressNormalEntity, SensorEntity):
    _attr_translation_key = "error"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:alert-circle-outline"

    def __init__(self, coordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "error")

    @property
    def native_value(self):
        return _mapping_value(getattr(self.device, "error", None), "id")


class KressNormalErrorDescriptionSensor(KressNormalEntity, SensorEntity):
    _attr_translation_key = "error_description"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:alert-circle"

    def __init__(self, coordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "error_description")

    @property
    def native_value(self):
        return _mapping_value(getattr(self.device, "error", None), "description")


class KressNormalRssiSensor(KressNormalEntity, SensorEntity):
    _attr_translation_key = "rssi"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "rssi")

    @property
    def native_value(self):
        return getattr(self.device, "rssi", None)


class KressNormalFirmwareSensor(KressNormalEntity, SensorEntity):
    _attr_translation_key = "firmware"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:chip"

    def __init__(self, coordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "firmware")

    @property
    def native_value(self):
        return _mapping_value(getattr(self.device, "firmware", None), "version")


class KressNormalLastUpdateSensor(KressNormalEntity, SensorEntity):
    _attr_translation_key = "last_update"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:clock-check"

    def __init__(self, coordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "last_update")

    @property
    def native_value(self):
        return getattr(self.device, "updated", None)


class KressNormalAreaMowedTotalSensor(KressNormalEntity, SensorEntity):
    """Cumulative covered area reported by the normal Kress cloud."""

    _attr_translation_key = "area_mowed_total"
    _attr_device_class = SensorDeviceClass.AREA
    _attr_native_unit_of_measurement = UnitOfArea.SQUARE_METERS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:grass"

    def __init__(self, coordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "area_mowed_total")

    @property
    def native_value(self):
        product = self.coordinator.product_item_data(self.serial) or {}
        value = product.get("area_mowed")
        try:
            return round(float(value), 2)
        except (TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self):
        return {
            "source": "kress_cloud_product_item",
            "cloud_data_updated_at": self.coordinator.product_item_updated_at(
                self.serial
            ),
        }


class KressNormalLawnSizeSensor(KressNormalEntity, SensorEntity):
    """Configured lawn size reported by the normal Kress cloud."""

    _attr_translation_key = "lawn_size"
    _attr_device_class = SensorDeviceClass.AREA
    _attr_native_unit_of_measurement = UnitOfArea.SQUARE_METERS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:set-square"

    def __init__(self, coordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "lawn_size")

    @property
    def native_value(self):
        product = self.coordinator.product_item_data(self.serial) or {}
        value = product.get("lawn_size")
        try:
            return round(float(value), 2)
        except (TypeError, ValueError):
            return None


class KressNormalCoverageProbeSensor(KressNormalEntity, SensorEntity):
    """Diagnostic inventory of Kress cloud/MQTT coverage-related fields."""

    _attr_translation_key = "coverage_probe"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:database-search"

    def __init__(self, coordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "coverage_probe")

    @property
    def native_value(self):
        return len(self.coordinator.coverage_probe(self.serial).get(
            "coverage_probe_candidates", []
        ))

    @property
    def extra_state_attributes(self):
        return self.coordinator.coverage_probe(self.serial)
