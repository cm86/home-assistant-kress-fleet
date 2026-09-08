# SPDX-License-Identifier: GPL-3.0-only

"""RTK live-map camera for the normal Kress/Mission cloud backend."""

from __future__ import annotations

from typing import Any

from homeassistant.components.camera import Camera
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .normal_cloud import KressNormalCoordinator
from .normal_entity import normal_device_info, normal_rtk_map_id, normal_rtk_position
from .normal_map_renderer import normal_map_diagnostics, render_normal_rtk_map


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
        attrs = {"map_id": normal_rtk_map_id(self.device)}
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
        return render_normal_rtk_map(
            self._last_map_data, normal_rtk_position(self.device)
        ).encode()
