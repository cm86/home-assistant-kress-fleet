# SPDX-License-Identifier: GPL-3.0-only
# This file is part of kress_fleet, a modified work derived in part from
# MTrab/landroid_cloud and MTrab/pyworxcloud (GPL-3.0).
# Kress Fleet modifications began on 2026-08-21; see NOTICE and LICENSE.

"""Lawn mower platform for Kress Fleet."""

from __future__ import annotations

from homeassistant.components.lawn_mower import (
    LawnMowerActivity,
    LawnMowerEntity,
    LawnMowerEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import KressFleetEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Fleet mower entities."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        KressFleetLawnMower(coordinator, mower_uuid)
        for mower_uuid in coordinator.data
    )


class KressFleetLawnMower(KressFleetEntity, LawnMowerEntity):
    """Native Home Assistant lawn mower entity."""

    _attr_name = None
    _attr_supported_features = (
        LawnMowerEntityFeature.START_MOWING
        | LawnMowerEntityFeature.PAUSE
        | LawnMowerEntityFeature.DOCK
    )

    def __init__(self, coordinator, mower_uuid: str) -> None:
        super().__init__(coordinator, mower_uuid, "mower")

    @property
    def activity(self):
        status = self.mower.status
        return {
            "docked": LawnMowerActivity.DOCKED,
            "mowing": LawnMowerActivity.MOWING,
            "returning": LawnMowerActivity.RETURNING,
            "paused": LawnMowerActivity.PAUSED,
            "error": LawnMowerActivity.ERROR,
        }.get(status, status)

    @property
    def available(self) -> bool:
        # This entity exposes commands, so only mark it available while the
        # Fleet MQTT command channel is actually connected. Read-only sensors,
        # tracker and coverage map remain available independently.
        return super().available and self.mower.mqtt_connected

    @property
    def extra_state_attributes(self):
        attrs = {}
        if self.mower.coordinates:
            attrs["latitude"], attrs["longitude"] = self.mower.coordinates
        if self.mower.battery_percent is not None:
            attrs["battery"] = self.mower.battery_percent
        if self.mower.zone is not None:
            attrs["zone"] = self.mower.zone

        # Temporary protocol diagnostics for discovering how the official Kress
        # app starts a mower in a selected RTK zone. The stored payload is
        # privacy-filtered before it ever reaches entity state attributes.
        attrs["diagnostic_command_capture_ready"] = (
            self.mower.command_capture_ready
        )
        if self.mower.last_command_in_at is not None:
            attrs["diagnostic_last_command_in_at"] = (
                self.mower.last_command_in_at.isoformat()
            )
        if self.mower.last_command_in is not None:
            attrs["diagnostic_last_command_in"] = self.mower.last_command_in
        return attrs or None

    async def async_start_mowing(self) -> None:
        await self._command({"cmd": 1})

    async def async_pause(self) -> None:
        await self._command({"cmd": 2})

    async def async_dock(self) -> None:
        await self._command({"cmd": 3})

    async def _command(self, payload: dict) -> None:
        mqtt_client = self.coordinator.mqtt
        if mqtt_client is None:
            raise HomeAssistantError("Kress Fleet MQTT is not ready")
        try:
            await mqtt_client.async_publish_command(self.mower, payload)
        except RuntimeError as err:
            raise HomeAssistantError(str(err)) from err
