# SPDX-License-Identifier: GPL-3.0-only
# This file is part of kress_fleet, a modified work derived in part from
# MTrab/landroid_cloud and MTrab/pyworxcloud (GPL-3.0).
# Kress Fleet modifications began on 2026-08-21; see NOTICE and LICENSE.

"""GPS device tracker for Kress Fleet."""

from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import KressFleetEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        KressFleetLocation(coordinator, mower_uuid) for mower_uuid in coordinator.data
    )


class KressFleetLocation(KressFleetEntity, TrackerEntity):
    """Live GPS position from dat.modules['4G'].gps.coo."""

    _attr_name = "Location"
    _attr_source_type = SourceType.GPS

    def __init__(self, coordinator, mower_uuid: str) -> None:
        super().__init__(coordinator, mower_uuid, "location")

    @property
    def available(self) -> bool:
        return super().available and self.mower.coordinates is not None

    @property
    def latitude(self) -> float | None:
        return self.mower.coordinates[0] if self.mower.coordinates else None

    @property
    def longitude(self) -> float | None:
        return self.mower.coordinates[1] if self.mower.coordinates else None
