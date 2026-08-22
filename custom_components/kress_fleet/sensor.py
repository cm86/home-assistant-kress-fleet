# SPDX-License-Identifier: GPL-3.0-only
# This file is part of kress_fleet, a modified work derived in part from
# MTrab/landroid_cloud and MTrab/pyworxcloud (GPL-3.0).
# Kress Fleet modifications began on 2026-08-21; see NOTICE and LICENSE.

"""Sensor platform for Kress Fleet."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfElectricPotential,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import KressFleetEntity
from .models import FleetMower
from .map_renderer import current_zone_name


@dataclass(frozen=True, kw_only=True)
class FleetSensorDescription(SensorEntityDescription):
    """Description of a Kress Fleet sensor."""

    value_fn: Callable[[FleetMower], Any]


SENSORS: tuple[FleetSensorDescription, ...] = (
    FleetSensorDescription(
        key="battery",
        translation_key="battery",
        value_fn=lambda mower: mower.battery_percent,
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    FleetSensorDescription(
        key="status",
        translation_key="status",
        value_fn=lambda mower: mower.status,
        device_class=SensorDeviceClass.ENUM,
        options=[
            "idle",
            "docked",
            "starting",
            "returning",
            "mowing",
            "error",
            "escaped_digital_fence",
            "zoning",
            "edge_cut",
            "paused",
            "searching_for_zone",
            "unknown",
        ],
        icon="mdi:robot-mower",
    ),
    FleetSensorDescription(
        key="zone",
        translation_key="zone",
        value_fn=lambda mower: mower.zone,
        icon="mdi:map-marker-radius",
    ),
    FleetSensorDescription(
        key="zone_name",
        translation_key="zone_name",
        value_fn=current_zone_name,
        icon="mdi:map-marker-star",
    ),
    FleetSensorDescription(
        key="error",
        translation_key="error",
        value_fn=lambda mower: mower.error_id,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:alert-circle-outline",
    ),
    FleetSensorDescription(
        key="rssi",
        translation_key="rssi",
        value_fn=lambda mower: mower.rssi,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    FleetSensorDescription(
        key="connection",
        translation_key="connection",
        value_fn=lambda mower: mower.connection,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:access-point-network",
    ),
    FleetSensorDescription(
        key="battery_temperature",
        translation_key="battery_temperature",
        value_fn=lambda mower: mower.battery_temperature,
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    FleetSensorDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        value_fn=lambda mower: mower.battery_voltage,
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    FleetSensorDescription(
        key="firmware",
        translation_key="firmware",
        value_fn=lambda mower: mower.firmware,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:chip",
    ),
    FleetSensorDescription(
        key="last_update",
        translation_key="last_update",
        value_fn=lambda mower: mower.last_update,
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:clock-check",
    ),
    FleetSensorDescription(
        key="map",
        translation_key="map",
        value_fn=lambda mower: mower.map_name or mower.map_id,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:map",
    ),
    FleetSensorDescription(
        key="coverage_polygons",
        translation_key="coverage_polygons",
        value_fn=lambda mower: len(mower.coverage),
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:vector-polygon",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Kress Fleet sensors."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        KressFleetSensor(coordinator, mower_uuid, description)
        for mower_uuid in coordinator.data
        for description in SENSORS
    )


class KressFleetSensor(KressFleetEntity, SensorEntity):
    """A sensor backed by Fleet data already cached in memory."""

    entity_description: FleetSensorDescription

    def __init__(self, coordinator, mower_uuid: str, description: FleetSensorDescription) -> None:
        self.entity_description = description
        super().__init__(coordinator, mower_uuid, description.key)

    @property
    def native_value(self):
        """Return the native sensor value."""
        return self.entity_description.value_fn(self.mower)
