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
    async_add_entities(
        KressFleetCoveragePeriodSelect(coordinator, mower_uuid)
        for mower_uuid in coordinator.data
    )


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
