# SPDX-License-Identifier: GPL-3.0-only
# This file is part of kress_fleet, a modified work derived in part from
# MTrab/landroid_cloud and MTrab/pyworxcloud (GPL-3.0).
# Kress Fleet modifications began on 2026-08-21; see NOTICE and LICENSE.

"""Binary sensor platform for Kress Fleet."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import KressFleetEntity
from .models import FleetMower


@dataclass(frozen=True, kw_only=True)
class FleetBinaryDescription(BinarySensorEntityDescription):
    """Description of a Fleet binary sensor."""

    value_fn: Callable[[FleetMower], bool | None]


BINARY_SENSORS: tuple[FleetBinaryDescription, ...] = (
    FleetBinaryDescription(
        key="online",
        translation_key="online",
        value_fn=lambda mower: mower.online,
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
    ),
    FleetBinaryDescription(
        key="mqtt_connected",
        translation_key="mqtt_connected",
        value_fn=lambda mower: mower.mqtt_connected,
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FleetBinaryDescription(
        key="rtk_ok",
        translation_key="rtk_ok",
        value_fn=lambda mower: mower.rtk_ok,
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FleetBinaryDescription(
        key="rain",
        translation_key="rain",
        value_fn=lambda mower: mower.rain,
        device_class=BinarySensorDeviceClass.MOISTURE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    FleetBinaryDescription(
        key="charging",
        translation_key="charging",
        value_fn=lambda mower: mower.battery_charging,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:battery-charging",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Fleet binary sensors."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        KressFleetBinarySensor(coordinator, mower_uuid, description)
        for mower_uuid in coordinator.data
        for description in BINARY_SENSORS
    )


class KressFleetBinarySensor(KressFleetEntity, BinarySensorEntity):
    """A Fleet binary sensor."""

    entity_description: FleetBinaryDescription

    def __init__(self, coordinator, mower_uuid: str, description: FleetBinaryDescription) -> None:
        self.entity_description = description
        super().__init__(coordinator, mower_uuid, description.key)

    @property
    def is_on(self) -> bool | None:
        """Return the binary state."""
        return self.entity_description.value_fn(self.mower)
