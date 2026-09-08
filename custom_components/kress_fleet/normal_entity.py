# SPDX-License-Identifier: GPL-3.0-only

"""Entity helpers for the normal Kress cloud backend."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .normal_cloud import KressNormalCoordinator


def normal_coordinates(device: Any) -> tuple[float, float] | None:
    """Return GPS coordinates exposed by pyworxcloud, when available."""
    gps = getattr(device, "gps", None)
    if isinstance(gps, dict):
        latitude = gps.get("latitude")
        longitude = gps.get("longitude")
    else:
        latitude = getattr(gps, "latitude", None)
        longitude = getattr(gps, "longitude", None)
    if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
        return None
    return float(latitude), float(longitude)


def normal_rtk_map_id(device: Any) -> str | None:
    """Return the RTK map identifier reported by pyworxcloud."""
    cfg = getattr(device, "raw_cfg", {}) or {}
    if not isinstance(cfg, dict):
        return None
    rtk = cfg.get("rtk") or {}
    if not isinstance(rtk, dict):
        return None
    value = rtk.get("map")
    return None if value in (None, "") else str(value)


def normal_rtk_position(device: Any) -> tuple[float, float] | None:
    """Return live RTK coordinates, falling back to the normal GPS value."""
    dat = getattr(device, "raw_dat", {}) or {}
    if isinstance(dat, dict):
        rtk = dat.get("rtk") or {}
        if isinstance(rtk, dict):
            pos = rtk.get("pos")
            if isinstance(pos, (list, tuple)) and len(pos) >= 2:
                try:
                    return float(pos[0]), float(pos[1])
                except (TypeError, ValueError):
                    pass
    return normal_coordinates(device)


def normal_device_info(device: Any, serial: str) -> DeviceInfo:
    """Build shared Home Assistant device information for normal Kress entities."""
    info: dict[str, Any] = {
        "identifiers": {(DOMAIN, serial)},
        "name": str(getattr(device, "name", f"Kress {serial}")),
        "manufacturer": "Kress",
        "model": str(getattr(device, "model", "Kress")),
        "serial_number": serial,
    }
    firmware = getattr(device, "firmware", None)
    if isinstance(firmware, dict) and firmware.get("version") is not None:
        info["sw_version"] = str(firmware["version"])
    mac = getattr(device, "mac_address", None)
    if mac and mac != "__UUID__":
        info["connections"] = {(CONNECTION_NETWORK_MAC, str(mac))}
    return DeviceInfo(**info)


class KressNormalEntity(CoordinatorEntity[KressNormalCoordinator]):
    """Base entity for a mower from the normal Kress cloud."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: KressNormalCoordinator, serial: str, key: str) -> None:
        super().__init__(coordinator)
        self.serial = serial
        self._attr_unique_id = f"{serial}_{key}"

    @property
    def device(self) -> Any:
        return self.coordinator.data[self.serial]

    @property
    def available(self) -> bool:
        return super().available and self.serial in self.coordinator.data

    @property
    def device_info(self) -> DeviceInfo:
        return normal_device_info(self.device, self.serial)
