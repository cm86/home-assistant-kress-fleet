# SPDX-License-Identifier: GPL-3.0-only

"""Binary sensors for the normal Kress cloud backend."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.helpers.entity import EntityCategory

from .normal_entity import KressNormalEntity


def _mapping_value(value: Any, key: str) -> Any:
    """Return a value from a dict-like pyworxcloud attribute."""
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


class KressNormalOnlineBinarySensor(KressNormalEntity, BinarySensorEntity):
    _attr_translation_key = "online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "online")

    @property
    def is_on(self) -> bool | None:
        value = getattr(self.device, "online", None)
        return bool(value) if value is not None else None


class KressNormalMqttBinarySensor(KressNormalEntity, BinarySensorEntity):
    _attr_translation_key = "mqtt_connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "mqtt_connected")

    @property
    def is_on(self) -> bool | None:
        value = getattr(self.device, "mqtt_connected", None)
        return bool(value) if value is not None else None


class KressNormalRainBinarySensor(KressNormalEntity, BinarySensorEntity):
    _attr_translation_key = "rain"
    _attr_device_class = BinarySensorDeviceClass.MOISTURE
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "rain")

    @property
    def is_on(self) -> bool | None:
        value = _mapping_value(getattr(self.device, "rainsensor", None), "triggered")
        return bool(value) if value is not None else None


class KressNormalChargingBinarySensor(KressNormalEntity, BinarySensorEntity):
    _attr_translation_key = "charging"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:battery-charging"

    def __init__(self, coordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "charging")

    @property
    def is_on(self) -> bool | None:
        value = _mapping_value(getattr(self.device, "battery", None), "charging")
        return bool(value) if value is not None else None
