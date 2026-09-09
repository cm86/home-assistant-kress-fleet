# SPDX-License-Identifier: GPL-3.0-only

"""RTK live-map camera for the normal Kress/Mission cloud backend."""

from __future__ import annotations

from typing import Any

from homeassistant.components.camera import Camera
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .normal_cloud import KressNormalCoordinator
from .normal_entity import normal_device_info, normal_rtk_map_id, normal_rtk_position
from .normal_map_renderer import (
    normal_cutting_width_m,
    normal_map_diagnostics,
    render_normal_rtk_map,
)


def _status_values(device: Any) -> tuple[int | None, str | None]:
    status = getattr(device, "status", None)
    if isinstance(status, dict):
        raw_id = status.get("id")
        raw_description = status.get("description")
    else:
        raw_id = getattr(status, "id", None)
        raw_description = getattr(status, "description", None)
    try:
        status_id = int(raw_id) if raw_id is not None else None
    except (TypeError, ValueError):
        status_id = None
    description = None if raw_description in (None, "") else str(raw_description)
    return status_id, description


def _battery_percent(device: Any) -> int | float | None:
    battery = getattr(device, "battery", None)
    if isinstance(battery, dict):
        value = battery.get("percent")
    else:
        value = getattr(battery, "percent", None)
    return value if isinstance(value, (int, float)) else None


class KressNormalMapCamera(CoordinatorEntity[KressNormalCoordinator], Camera):
    """Render a Kress Mission RTK map as an SVG camera image."""

    _attr_has_entity_name = True
    _attr_translation_key = "live_map"
    _attr_should_poll = False
    _attr_icon = "mdi:map"

    def __init__(self, coordinator: KressNormalCoordinator, serial: str) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self.serial = serial
        self._attr_unique_id = f"{serial}_live_map"
        self.content_type = "image/svg+xml"
        self._last_map_data: dict[str, Any] | None = None

    @property
    def device(self) -> Any:
        return self.coordinator.data[self.serial]

    @property
    def device_info(self):
        return normal_device_info(self.device, self.serial)

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.serial in self.coordinator.data
            and normal_rtk_map_id(self.device) is not None
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        trail = self.coordinator.rtk_mowing_trail(self.serial)
        status_id, status_description = _status_values(self.device)
        position = normal_rtk_position(self.device)
        coverage_from, coverage_to = self.coordinator.rtk_coverage_period()
        attrs = {
            "map_id": normal_rtk_map_id(self.device),
            "coverage_source": "local_rtk_daily_trail",
            "coverage_persisted": True,
            "coverage_points": len(trail),
            "coverage_days": 1,
            "coverage_from": coverage_from.isoformat(),
            "coverage_to": coverage_to.isoformat(),
            "cutting_width_cm": round(normal_cutting_width_m(self.device) * 100, 1),
            "coverage_status_id": status_id,
            "coverage_status": status_description,
            "coverage_position": list(position) if position is not None else None,
        }
        attrs.update(normal_map_diagnostics(self._last_map_data))
        return {key: value for key, value in attrs.items() if value is not None}

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        del width, height
        map_id = normal_rtk_map_id(self.device)
        map_data = await self.coordinator.async_get_rtk_map(map_id)
        if map_data is not None:
            self._last_map_data = map_data
        status_id, status_description = _status_values(self.device)
        return render_normal_rtk_map(
            self._last_map_data,
            normal_rtk_position(self.device),
            self.coordinator.rtk_mowing_trail(self.serial),
            normal_cutting_width_m(self.device),
            mower_name=str(getattr(self.device, "name", "Kress Mission")),
            status_text=status_description
            or (str(status_id) if status_id is not None else None),
            battery_percent=_battery_percent(self.device),
        ).encode()
