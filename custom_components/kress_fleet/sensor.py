# SPDX-License-Identifier: GPL-3.0-only
# This file is part of kress_fleet, a modified work derived in part from
# MTrab/landroid_cloud and MTrab/pyworxcloud (GPL-3.0).
# Kress Fleet modifications began on 2026-08-21; see NOTICE and LICENSE.

"""Sensor platform for Kress Fleet."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from copy import copy
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
from homeassistant.core import HomeAssistant, callback
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

    def __init__(
        self, coordinator, mower_uuid: str, description: FleetSensorDescription
    ) -> None:
        self.entity_description = description
        self._cached_zone_name: str | None = None
        self._zone_name_key: tuple[object, ...] | None = None
        self._zone_name_task: asyncio.Task[None] | None = None
        super().__init__(coordinator, mower_uuid, description.key)

    async def async_added_to_hass(self) -> None:
        """Register coordinator updates and prime the friendly zone-name cache."""
        await super().async_added_to_hass()
        if self.entity_description.key == "zone_name":
            self._schedule_zone_name_refresh()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel a pending zone-name refresh when the entity is removed."""
        if self._zone_name_task is not None:
            self._zone_name_task.cancel()
        await super().async_will_remove_from_hass()

    def _zone_name_source_key(self) -> tuple[object, ...]:
        """Return a cheap key for inputs used by friendly-zone resolution."""
        mower = self.mower
        cfg_fallback_key = (
            id(mower.cfg)
            if mower.map_detail is None and mower.product_detail is None
            else None
        )
        return (
            mower.zone,
            mower.map_id,
            mower.map_revision,
            id(mower.map_detail),
            id(mower.product_detail),
            cfg_fallback_key,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Keep expensive zone-name parsing off Home Assistant's event loop."""
        if self.entity_description.key == "zone_name":
            self._schedule_zone_name_refresh()
        super()._handle_coordinator_update()

    @callback
    def _schedule_zone_name_refresh(self) -> None:
        """Refresh the friendly zone name in an executor when inputs change."""
        if self.hass is None:
            return
        if self._zone_name_source_key() == self._zone_name_key:
            return
        if self._zone_name_task is not None and not self._zone_name_task.done():
            return

        self._zone_name_task = self.hass.async_create_task(
            self._async_refresh_zone_name(),
            name=f"Refresh Kress Fleet zone name {self.mower_uuid[:8]}",
        )

    async def _async_refresh_zone_name(self) -> None:
        """Resolve the friendly zone name without blocking the event loop."""
        try:
            while self.hass is not None:
                source_key = self._zone_name_source_key()
                if source_key == self._zone_name_key:
                    return

                mower_snapshot = copy(self.mower)
                zone_name = await self.hass.async_add_executor_job(
                    current_zone_name, mower_snapshot
                )

                if source_key != self._zone_name_source_key():
                    continue

                self._cached_zone_name = zone_name
                self._zone_name_key = source_key
                self.async_write_ha_state()
                return
        finally:
            self._zone_name_task = None

    @property
    def native_value(self):
        """Return the native sensor value without blocking map parsing."""
        if self.entity_description.key == "zone_name":
            return self._cached_zone_name
        return self.entity_description.value_fn(self.mower)
