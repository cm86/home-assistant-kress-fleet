# SPDX-License-Identifier: GPL-3.0-only
# This file is part of kress_fleet, a modified work derived in part from
# MTrab/landroid_cloud and MTrab/pyworxcloud (GPL-3.0).
# Kress Fleet modifications began on 2026-08-21; see NOTICE and LICENSE.

"""Button entities for Kress Fleet."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import KressFleetEntity
from .mqtt import zone_start_command


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Fleet button entities."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        KressFleetMowSelectedZoneButton(coordinator, mower_uuid)
        for mower_uuid in coordinator.data
    )


class KressFleetMowSelectedZoneButton(KressFleetEntity, ButtonEntity):
    """Start mowing in the zone armed by the target-zone select entity."""

    _attr_has_entity_name = True
    _attr_translation_key = "mow_selected_zone"
    _attr_icon = "mdi:play-circle-outline"

    def __init__(self, coordinator, mower_uuid: str) -> None:
        super().__init__(coordinator, mower_uuid, "mow_selected_zone")

    @property
    def available(self) -> bool:
        """Only enable the button for a current, explicitly selected zone."""
        mower = self.mower
        return (
            super().available
            and mower.mqtt_connected
            and mower.map_id is not None
            and mower.zone_catalog_map_id == mower.map_id
            and mower.target_zone_id is not None
            and mower.target_zone_id in mower.zone_id_name_map
        )

    async def async_press(self) -> None:
        """Send the exact single-zone RTK start command observed from Fleet."""
        mower = self.mower
        zone_id = mower.target_zone_id
        if (
            zone_id is None
            or mower.zone_catalog_map_id != mower.map_id
            or zone_id not in mower.zone_id_name_map
        ):
            raise HomeAssistantError("Select a valid Kress Fleet mowing zone first")

        mqtt_client = self.coordinator.mqtt
        if mqtt_client is None:
            raise HomeAssistantError("Kress Fleet MQTT is not ready")

        try:
            await mqtt_client.async_publish_command(
                mower, zone_start_command(zone_id)
            )
        except (RuntimeError, ValueError) as err:
            raise HomeAssistantError(str(err)) from err
