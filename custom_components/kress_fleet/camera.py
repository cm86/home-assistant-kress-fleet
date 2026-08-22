# SPDX-License-Identifier: GPL-3.0-only
# This file is part of kress_fleet, a modified work derived in part from
# MTrab/landroid_cloud and MTrab/pyworxcloud (GPL-3.0).
# Kress Fleet modifications began on 2026-08-21; see NOTICE and LICENSE.

"""Coverage/live-position camera for Kress Fleet."""

from __future__ import annotations

from homeassistant.components.camera import Camera
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.translation import async_get_translations
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import KressFleetCoordinator
from .entity import mower_device_info
from .map_renderer import (
    current_zone_name,
    map_no_go_state_counts,
    map_no_go_state_keys,
    map_shape_counts,
    mower_zone_id_name_map,
    mower_zone_name_sources,
    mower_zone_names,
    render_mower_map,
    timestamp_local,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Fleet live-map cameras."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        KressFleetMapCamera(coordinator, mower_uuid) for mower_uuid in coordinator.data
    )


class KressFleetMapCamera(CoordinatorEntity[KressFleetCoordinator], Camera):
    """Render Fleet work map, selected coverage range and live mower position."""

    _attr_has_entity_name = True
    _attr_translation_key = "live_map"
    _attr_should_poll = False

    def __init__(self, coordinator: KressFleetCoordinator, mower_uuid: str) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self.mower_uuid = mower_uuid
        mower = coordinator.data[mower_uuid]
        self._attr_unique_id = f"{mower_uuid}_live_map"
        self._attr_device_info = mower_device_info(mower)
        self.content_type = "image/svg+xml"
        self._cached_key: tuple[object, ...] | None = None
        self._cached_svg: bytes | None = None
        self._map_translation_language: str | None = None
        self._map_translations: dict[str, str] = {}

    @property
    def mower(self):
        return self.coordinator.data[self.mower_uuid]

    @property
    def available(self) -> bool:
        mower = self.mower
        # A discovered map is enough to expose the camera.  The expensive map
        # detail/coverage payloads are fetched on first image request.
        return super().available and bool(
            mower.map_id or mower.coverage or mower.coordinates or mower.map_detail
        )

    @property
    def extra_state_attributes(self):
        """Expose only small map diagnostics, never raw geometry."""
        mower = self.mower
        counts = map_shape_counts(mower.map_detail, reference=mower.coordinates)
        zone_names = mower_zone_names(mower)
        no_go_states = map_no_go_state_counts(
            mower.map_detail, reference=mower.coordinates
        )
        timezone_name = self.hass.config.time_zone if self.hass is not None else "UTC"
        return {
            "coverage_days": mower.coverage_days,
            "coverage_from": (
                timestamp_local(mower.coverage_from, timezone_name)
                if mower.coverage_from
                else None
            ),
            "coverage_to": (
                timestamp_local(mower.coverage_to, timezone_name)
                if mower.coverage_to
                else None
            ),
            "map_name": mower.map_name,
            "map_shapes": sum(counts.values()),
            "map_boundaries": counts.get("boundary", 0),
            "no_go_zones": counts.get("no_go", 0),
            "no_go_active": no_go_states["active"],
            "no_go_inactive": no_go_states["inactive"],
            "no_go_unknown": no_go_states["unknown"],
            "no_go_state_keys": map_no_go_state_keys(mower.map_detail),
            "map_zones": counts.get("zone", 0),
            "zone_names": zone_names,
            "zone_name_sources": mower_zone_name_sources(mower),
            "zone_id_map": {
                str(key): value
                for key, value in mower_zone_id_name_map(mower).items()
            },
            "current_zone_id": mower.zone,
            "current_zone_name": current_zone_name(mower),
        }

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the live-map SVG, lazily loading Fleet geometry if needed."""
        await self.coordinator.async_ensure_map_data(self.mower_uuid)
        mower = self.mower
        peers = [
            candidate
            for candidate in self.coordinator.data.values()
            if candidate.uuid != mower.uuid
            and mower.map_id is not None
            and candidate.user_id == mower.user_id
            and candidate.location_id == mower.location_id
            and candidate.map_id == mower.map_id
        ]
        peer_key = tuple(
            sorted(
                (peer.uuid, peer.name, peer.coordinates, peer.last_update, peer.status_id)
                for peer in peers
            )
        )
        timezone_name = self.hass.config.time_zone if self.hass is not None else "UTC"
        language = self.hass.config.language if self.hass is not None else "en"
        translations = await self._async_get_map_translations(language)
        key = (
            timezone_name,
            language,
            mower.coverage_revision,
            mower.coverage_days,
            mower.coverage_from,
            mower.coverage_to,
            mower.map_revision,
            mower.last_update,
            mower.coordinates,
            mower.battery_percent,
            mower.status_id,
            mower.zone,
            mower.rssi,
            mower.map_id,
            len(mower.coverage),
            peer_key,
        )
        if key != self._cached_key or self._cached_svg is None:
            self._cached_svg = render_mower_map(
                mower,
                peers,
                timezone_name=timezone_name,
                translations=translations,
            )
            self._cached_key = key
        return self._cached_svg

    async def _async_get_map_translations(self, language: str) -> dict[str, str]:
        """Return localized live-map captions for the HA instance language."""
        if self.hass is None:
            return {}
        if self._map_translation_language == language and self._map_translations:
            return self._map_translations

        resources = await async_get_translations(
            self.hass, language, "common", {DOMAIN}
        )
        prefix = f"component.{DOMAIN}.common.map."
        self._map_translations = {
            key.removeprefix(prefix): value
            for key, value in resources.items()
            if key.startswith(prefix)
        }
        self._map_translation_language = language
        return self._map_translations
