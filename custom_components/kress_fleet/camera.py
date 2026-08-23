# SPDX-License-Identifier: GPL-3.0-only
# This file is part of kress_fleet, a modified work derived in part from
# MTrab/landroid_cloud and MTrab/pyworxcloud (GPL-3.0).
# Kress Fleet modifications began on 2026-08-21; see NOTICE and LICENSE.

"""Coverage/live-position camera for Kress Fleet."""

from __future__ import annotations

import asyncio
from copy import copy
from functools import partial
from typing import Any

from homeassistant.components.camera import Camera
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.translation import async_get_translations
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import KressFleetCoordinator
from .entity import mower_device_info
from .map_renderer import mower_map_diagnostics, render_mower_map, timestamp_local


_MAP_TRANSLATION_KEYS: dict[str, str] = {
    "map_coverage": "coverage",
    "map_no_coverage": "no_coverage",
    "map_no_map_data": "no_map_data",
    "map_not_mowed": "not_mowed",
    "map_mowed": "mowed",
    "map_no_go_active": "no_go_active",
    "map_no_go_inactive": "no_go_inactive",
    "map_battery": "battery",
    "map_status_label": "status_label",
    "map_zone": "zone",
    "map_signal": "signal",
    "map_from": "from",
    "map_to": "to",
    "map_position": "position",
    "map_live_map": "live_map",
    "map_period_today": "period.today",
    "map_period_last_days": "period.last_days",
    "map_status_idle": "status.idle",
    "map_status_docked": "status.docked",
    "map_status_starting": "status.starting",
    "map_status_returning": "status.returning",
    "map_status_mowing": "status.mowing",
    "map_status_error": "status.error",
    "map_status_escaped_digital_fence": "status.escaped_digital_fence",
    "map_status_zoning": "status.zoning",
    "map_status_edge_cut": "status.edge_cut",
    "map_status_paused": "status.paused",
    "map_status_searching_for_zone": "status.searching_for_zone",
    "map_status_unknown": "status.unknown",
}


_EMPTY_MAP_DIAGNOSTICS: dict[str, Any] = {
    "map_shapes": 0,
    "map_boundaries": 0,
    "no_go_zones": 0,
    "no_go_active": 0,
    "no_go_inactive": 0,
    "no_go_unknown": 0,
    "no_go_state_keys": [],
    "map_zones": 0,
    "zone_names": [],
    "zone_name_sources": [],
    "zone_id_map": {},
}


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

        # SVG rendering traverses large Fleet map/coverage payloads. Serialize
        # concurrent frontend requests and keep the result cached.
        self._cached_key: tuple[object, ...] | None = None
        self._cached_svg: bytes | None = None
        self._render_lock = asyncio.Lock()

        self._map_translation_language: str | None = None
        self._map_translations: dict[str, str] = {}

        # Home Assistant evaluates entity attributes synchronously while writing
        # state on the event loop. Never parse Fleet geometry in that property;
        # compute diagnostics in an executor and expose only this small cache.
        self._diagnostics_key: tuple[object, ...] | None = None
        self._cached_diagnostics: dict[str, Any] = dict(_EMPTY_MAP_DIAGNOSTICS)
        self._diagnostics_task: asyncio.Task[None] | None = None

    @property
    def mower(self):
        return self.coordinator.data[self.mower_uuid]

    @property
    def available(self) -> bool:
        mower = self.mower
        # A discovered map is enough to expose the camera. The expensive map
        # detail/coverage payloads are fetched on first image request.
        return super().available and bool(
            mower.map_id or mower.coverage or mower.coordinates or mower.map_detail
        )

    async def async_added_to_hass(self) -> None:
        """Register coordinator updates and prime cached diagnostics."""
        await super().async_added_to_hass()
        self._schedule_diagnostics_refresh()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel a pending diagnostics refresh when the entity is removed."""
        if self._diagnostics_task is not None:
            self._diagnostics_task.cancel()
        await super().async_will_remove_from_hass()

    def _diagnostics_source_key(self) -> tuple[object, ...]:
        """Return a cheap key for inputs that can change map diagnostics."""
        mower = self.mower

        # MQTT cfg is replaced frequently. Once richer map/product metadata is
        # available, avoid re-analysing a large map on every telemetry packet.
        cfg_fallback_key = (
            id(mower.cfg)
            if mower.map_detail is None and mower.product_detail is None
            else None
        )
        return (
            mower.map_id,
            mower.map_revision,
            id(mower.map_detail),
            id(mower.product_detail),
            cfg_fallback_key,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Schedule expensive diagnostics away from Home Assistant's event loop."""
        self._schedule_diagnostics_refresh()
        super()._handle_coordinator_update()

    @callback
    def _schedule_diagnostics_refresh(self) -> None:
        """Start one background diagnostics refresh when its inputs changed."""
        if self.hass is None:
            return
        if self._diagnostics_source_key() == self._diagnostics_key:
            return
        if self._diagnostics_task is not None and not self._diagnostics_task.done():
            return

        self._diagnostics_task = self.hass.async_create_task(
            self._async_refresh_diagnostics(),
            name=f"Refresh Kress Fleet map diagnostics {self.mower_uuid[:8]}",
        )

    async def _async_refresh_diagnostics(self) -> None:
        """Compute small camera diagnostics in an executor and cache them."""
        try:
            while self.hass is not None:
                source_key = self._diagnostics_source_key()
                if source_key == self._diagnostics_key:
                    return

                # FleetMower replaces map/cfg containers rather than mutating
                # their nested geometry in place. A shallow copy pins the input
                # references without copying multi-megabyte payloads.
                mower_snapshot = copy(self.mower)
                diagnostics = await self.hass.async_add_executor_job(
                    mower_map_diagnostics, mower_snapshot
                )

                # Metadata may have changed while the executor was running.
                if source_key != self._diagnostics_source_key():
                    continue

                self._cached_diagnostics = diagnostics
                self._diagnostics_key = source_key
                self.async_write_ha_state()
                return
        finally:
            self._diagnostics_task = None

    @property
    def extra_state_attributes(self):
        """Expose only cached/small diagnostics; never parse geometry here."""
        mower = self.mower
        timezone_name = self.hass.config.time_zone if self.hass is not None else "UTC"
        diagnostics = self._cached_diagnostics
        zone_id_map = diagnostics.get("zone_id_map", {})
        current_zone_name = (
            zone_id_map.get(str(mower.zone)) if mower.zone is not None else None
        )

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
            "map_shapes": diagnostics.get("map_shapes", 0),
            "map_boundaries": diagnostics.get("map_boundaries", 0),
            "no_go_zones": diagnostics.get("no_go_zones", 0),
            "no_go_active": diagnostics.get("no_go_active", 0),
            "no_go_inactive": diagnostics.get("no_go_inactive", 0),
            "no_go_unknown": diagnostics.get("no_go_unknown", 0),
            "no_go_state_keys": diagnostics.get("no_go_state_keys", []),
            "map_zones": diagnostics.get("map_zones", 0),
            "zone_names": diagnostics.get("zone_names", []),
            "zone_name_sources": diagnostics.get("zone_name_sources", []),
            "zone_id_map": zone_id_map,
            "current_zone_id": mower.zone,
            "current_zone_name": current_zone_name,
            "current_zone_source": mower.zone_source,
        }

    def _render_key(
        self,
        mower,
        peers,
        timezone_name: str,
        language: str,
    ) -> tuple[object, ...]:
        """Return a cheap cache key for the rendered SVG."""
        peer_key = tuple(
            sorted(
                (peer.uuid, peer.name, peer.coordinates, peer.last_update, peer.status_id)
                for peer in peers
            )
        )
        return (
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

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the live-map SVG without blocking Home Assistant's event loop."""
        await self.coordinator.async_ensure_map_data(self.mower_uuid)
        self._schedule_diagnostics_refresh()

        if self.hass is None:
            return None

        # Browsers/Home Assistant can request the same image concurrently.
        # Serialize them so one cache miss results in only one SVG render.
        async with self._render_lock:
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
            timezone_name = self.hass.config.time_zone
            language = self.hass.config.language
            translations = await self._async_get_map_translations(language)
            key = self._render_key(mower, peers, timezone_name, language)

            if key != self._cached_key or self._cached_svg is None:
                mower_snapshot = copy(mower)
                peer_snapshots = [copy(peer) for peer in peers]
                render = partial(
                    render_mower_map,
                    mower_snapshot,
                    peer_snapshots,
                    timezone_name=timezone_name,
                    translations=dict(translations),
                )
                self._cached_svg = await self.hass.async_add_executor_job(render)
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
        prefix = f"component.{DOMAIN}.common."
        self._map_translations = {}
        for ha_key, renderer_key in _MAP_TRANSLATION_KEYS.items():
            value = resources.get(f"{prefix}{ha_key}")
            if value is not None:
                self._map_translations[renderer_key] = value
        self._map_translation_language = language
        return self._map_translations
