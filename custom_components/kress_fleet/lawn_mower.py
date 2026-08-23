# SPDX-License-Identifier: GPL-3.0-only
# This file is part of kress_fleet, a modified work derived in part from
# MTrab/landroid_cloud and MTrab/pyworxcloud (GPL-3.0).
# Kress Fleet modifications began on 2026-08-21; see NOTICE and LICENSE.

"""Lawn mower platform for Kress Fleet."""

from __future__ import annotations

import re
from typing import Any

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

        # Temporary read-only protocol probe for discovering the RTK
        # zone-start payload without subscribing to Fleet commandIn. Only the
        # already received commandOut telemetry blocks that describe one-time
        # mowing/task state are exposed, after privacy filtering.
        sc = self.mower.dat.get("sc")
        if isinstance(sc, dict) and "once" in sc:
            attrs["diagnostic_zone_probe_sc_once"] = _sanitize_zone_probe(
                sc.get("once")
            )

        cut = self.mower.dat.get("cut")
        if isinstance(cut, dict) and "tsk" in cut:
            attrs["diagnostic_zone_probe_cut_task"] = _sanitize_zone_probe(
                cut.get("tsk")
            )
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


_ZONE_PROBE_REDACT_KEYS = frozenset(
    {
        "access_token",
        "client_id",
        "coo",
        "coordinates",
        "email",
        "gps",
        "lat",
        "latitude",
        "lng",
        "lon",
        "longitude",
        "mac",
        "password",
        "pos",
        "position",
        "serial",
        "serial_number",
        "signature",
        "sn",
        "token",
        "uuid",
    }
)
_ZONE_PROBE_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
_ZONE_PROBE_LONG_SECRET_RE = re.compile(r"^[A-Za-z0-9_+/=-]{48,}$")


def _sanitize_zone_probe(value: Any, key: str | None = None) -> Any:
    """Return small protocol telemetry while redacting private identifiers."""
    normalized_key = key.lower() if isinstance(key, str) else None
    if normalized_key in _ZONE_PROBE_REDACT_KEYS:
        return "<redacted>"

    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, str):
        stripped = value.strip()
        if (
            _ZONE_PROBE_UUID_RE.fullmatch(stripped)
            or _ZONE_PROBE_LONG_SECRET_RE.fullmatch(stripped)
        ):
            return f"<redacted:str:{len(value)}>"
        if "@" in stripped and "." in stripped:
            return "<redacted:email>"
        if len(value) <= 80:
            return value
        return f"<redacted:str:{len(value)}>"

    if isinstance(value, dict):
        return {
            str(child_key): _sanitize_zone_probe(child, str(child_key))
            for child_key, child in list(value.items())[:50]
        }

    if isinstance(value, list):
        return [_sanitize_zone_probe(child) for child in value[:50]]

    return f"<{type(value).__name__}>"
