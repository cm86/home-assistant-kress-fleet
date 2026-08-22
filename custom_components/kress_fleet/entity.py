# SPDX-License-Identifier: GPL-3.0-only
# This file is part of kress_fleet, a modified work derived in part from
# MTrab/landroid_cloud and MTrab/pyworxcloud (GPL-3.0).
# Kress Fleet modifications began on 2026-08-21; see NOTICE and LICENSE.

"""Base entity helpers for Kress Fleet."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import KressFleetCoordinator
from .models import FleetMower


def mower_device_info(mower: FleetMower) -> DeviceInfo:
    """Return common Home Assistant device information."""
    return DeviceInfo(
        identifiers={(DOMAIN, mower.uuid)},
        name=mower.name,
        manufacturer="Kress",
        model=mower.model,
        serial_number=mower.serial_number,
        configuration_url="https://fleet.kress.com/",
    )


class KressFleetEntity(CoordinatorEntity[KressFleetCoordinator]):
    """Base Kress Fleet entity."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: KressFleetCoordinator,
        mower_uuid: str,
        key: str,
    ) -> None:
        super().__init__(coordinator)
        self.mower_uuid = mower_uuid
        self._attr_unique_id = f"{mower_uuid}_{key}"
        self._attr_device_info = mower_device_info(self.mower)

    @property
    def mower(self) -> FleetMower:
        return self.coordinator.data[self.mower_uuid]
