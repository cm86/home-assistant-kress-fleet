# SPDX-License-Identifier: GPL-3.0-only
# This file is part of kress_fleet, a modified work derived in part from
# MTrab/landroid_cloud and MTrab/pyworxcloud (GPL-3.0).
# Kress Fleet modifications began on 2026-08-21; see NOTICE and LICENSE.

"""Select entities for Kress Fleet."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import COVERAGE_PERIOD_OPTIONS
from .entity import KressFleetEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Fleet select entities."""
    coordinator = entry.runtime_data.coordinator
    entities = []
    for mower_uuid in coordinator.data:
        entities.append(KressFleetCoveragePeriodSelect(coordinator, mower_uuid))
        entities.append(KressFleetTargetZoneSelect(coordinator, mower_uuid))
    async_add_entities(entities)


class KressFleetCoveragePeriodSelect(KressFleetEntity, SelectEntity):
    """Choose the calendar range shown by the coverage/live map."""

    _attr_has_entity_name = True
    _attr_translation_key = "coverage_period"
    _attr_icon = "mdi:calendar-range"
    _attr_options = list(COVERAGE_PERIOD_OPTIONS)

    def __init__(self, coordinator, mower_uuid: str) -> None:
        super().__init__(coordinator, mower_uuid, "coverage_period")

    @property
    def current_option(self) -> str:
        """Return the currently selected Fleet history window."""
        days = self.mower.coverage_days
        for option, option_days in COVERAGE_PERIOD_OPTIONS.items():
            if option_days == days:
                return option
        return "today"

    async def async_select_option(self, option: str) -> None:
        """Apply a new history window and refresh coverage immediately."""
        if option not in COVERAGE_PERIOD_OPTIONS:
            raise ValueError(f"Unsupported coverage period: {option}")
        await self.coordinator.async_set_coverage_days(
            self.mower_uuid, COVERAGE_PERIOD_OPTIONS[option]
        )


def _target_zone_options(mower) -> list[tuple[str, int]]:
    """Return stable display option -> Fleet zone ID pairs for one mower."""
    if not mower.map_id or mower.zone_catalog_map_id != mower.map_id:
        return []

    labels = list(mower.zone_id_name_map.values())
    counts = {label: labels.count(label) for label in set(labels)}
    result: list[tuple[str, int]] = []
    for zone_id, label in mower.zone_id_name_map.items():
        option = label if counts.get(label, 0) == 1 else f"{label} (Zone {zone_id})"
        result.append((option, zone_id))
    return result


class KressFleetTargetZoneSelect(KressFleetEntity, SelectEntity):
    """Choose a named Fleet RTK zone for the next targeted mowing command."""

    _attr_has_entity_name = True
    _attr_translation_key = "target_zone"
    _attr_icon = "mdi:map-marker-path"

    def __init__(self, coordinator, mower_uuid: str) -> None:
        super().__init__(coordinator, mower_uuid, "target_zone")

    @property
    def options(self) -> list[str]:
        """Return friendly zone names from the cached Fleet map catalog."""
        return [option for option, _zone_id in _target_zone_options(self.mower)]

    @property
    def current_option(self) -> str | None:
        """Return the currently armed target zone, if the user selected one."""
        target = self.mower.target_zone_id
        for option, zone_id in _target_zone_options(self.mower):
            if zone_id == target:
                return option
        return None

    async def async_select_option(self, option: str) -> None:
        """Arm one exact Fleet zone ID for the targeted start button."""
        for candidate, zone_id in _target_zone_options(self.mower):
            if candidate == option:
                self.mower.target_zone_id = zone_id
                self.coordinator.async_push_update()
                return
        raise ValueError(f"Unsupported Fleet target zone: {option}")
