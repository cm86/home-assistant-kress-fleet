# SPDX-License-Identifier: GPL-3.0-only

"""GPS tracker for the normal Kress cloud backend."""

from homeassistant.components.device_tracker import SourceType, TrackerEntity

from .normal_entity import KressNormalEntity, normal_coordinates


class KressNormalLocation(KressNormalEntity, TrackerEntity):
    _attr_translation_key = "location"
    _attr_source_type = SourceType.GPS

    def __init__(self, coordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "location")

    @property
    def available(self) -> bool:
        return super().available and normal_coordinates(self.device) is not None

    @property
    def latitude(self):
        coords = normal_coordinates(self.device)
        return coords[0] if coords else None

    @property
    def longitude(self):
        coords = normal_coordinates(self.device)
        return coords[1] if coords else None
