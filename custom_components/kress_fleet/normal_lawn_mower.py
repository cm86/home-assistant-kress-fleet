# SPDX-License-Identifier: GPL-3.0-only

"""Lawn mower entity for the normal Kress cloud backend."""

from __future__ import annotations

from typing import Final

from homeassistant.components.lawn_mower import LawnMowerActivity, LawnMowerEntity, LawnMowerEntityFeature

from .normal_entity import KressNormalEntity, normal_coordinates

_STATUS_ACTIVITY: Final[dict[int, str]] = {
    0: "idle",
    1: LawnMowerActivity.DOCKED,
    2: "starting",
    3: "starting",
    4: LawnMowerActivity.RETURNING,
    5: LawnMowerActivity.RETURNING,
    6: LawnMowerActivity.RETURNING,
    7: LawnMowerActivity.MOWING,
    8: LawnMowerActivity.ERROR,
    9: LawnMowerActivity.ERROR,
    10: LawnMowerActivity.ERROR,
    11: LawnMowerActivity.ERROR,
    12: LawnMowerActivity.MOWING,
    13: "escaped_digital_fence",
    30: LawnMowerActivity.RETURNING,
    31: "zoning",
    32: "edge_cut",
    33: "starting",
    34: LawnMowerActivity.PAUSED,
    103: "searching_for_zone",
    104: LawnMowerActivity.RETURNING,
}


class KressNormalLawnMower(KressNormalEntity, LawnMowerEntity):
    """Native mower control for a mower from the normal Kress app."""

    _attr_name = None
    _attr_supported_features = (
        LawnMowerEntityFeature.START_MOWING
        | LawnMowerEntityFeature.PAUSE
        | LawnMowerEntityFeature.DOCK
    )

    def __init__(self, coordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "mower")

    @property
    def activity(self):
        status_id = int(getattr(getattr(self.device, "status", None), "id", -1))
        return _STATUS_ACTIVITY.get(status_id, LawnMowerActivity.ERROR)

    @property
    def available(self) -> bool:
        return (
            super().available
            and bool(getattr(self.device, "online", False))
            and bool(getattr(self.coordinator.cloud, "mqtt_connected", False))
        )

    @property
    def extra_state_attributes(self):
        attrs = {}
        if (coords := normal_coordinates(self.device)) is not None:
            attrs["latitude"], attrs["longitude"] = coords
        battery = getattr(self.device, "battery", {})
        if isinstance(battery, dict) and battery.get("percent") is not None:
            attrs["battery"] = battery["percent"]
        model = getattr(self.device, "model", None)
        if model:
            attrs["model"] = str(model)
        return attrs or None

    async def async_start_mowing(self) -> None:
        await self.coordinator.cloud.start(self.serial)

    async def async_pause(self) -> None:
        await self.coordinator.cloud.pause(self.serial)

    async def async_dock(self) -> None:
        await self.coordinator.cloud.home(self.serial)
