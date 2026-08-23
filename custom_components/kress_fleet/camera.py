# SPDX-License-Identifier: GPL-3.0-only
# This file is part of kress_fleet, a modified work derived in part from
# MTrab/landroid_cloud and MTrab/pyworxcloud (GPL-3.0).
# Kress Fleet modifications began on 2026-08-21; see NOTICE and LICENSE.

"""Coverage/live-position camera for Kress Fleet."""

from __future__ import annotations

import asyncio
from copy import copy
import re
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

        # Temporary, privacy-safe zone telemetry diagnostics. Fleet protocol
        # variants do not always expose the active zone in the same field.
        # Keep this deliberately small: no UUIDs, coordinates, tokens or full
        # MQTT payloads are exposed through the camera state.
        cut = mower.dat.get("cut")
        cut_keys = sorted(str(key) for key in cut) if isinstance(cut, dict) else []
        cut_zone = cut.get("z") if isinstance(cut, dict) else None
        cut_task = cut.get("tsk") if isinstance(cut, dict) else None
        telemetry_lz = mower.dat.get("lz")

        def diagnostic_scalar(value):
            """Return only non-sensitive primitive values for diagnostics."""
            if value is None or isinstance(value, (int, float, bool)):
                return value
            if isinstance(value, str):
                stripped = value.strip()
                if re.fullmatch(r"-?\d+(?:\.\d+)?", stripped):
                    return stripped
                return f"<str:{len(value)}>"
            return f"<{type(value).__name__}>"

        def diagnostic_shape(value):
            """Describe a telemetry block without exposing its raw contents."""
            if isinstance(value, dict):
                return {
                    "type": "dict",
                    "keys": sorted(str(key) for key in value),
                }
            if isinstance(value, list):
                item_keys: list[str] = []
                for item in value[:3]:
                    if isinstance(item, dict):
                        item_keys.extend(str(key) for key in item)
                return {
                    "type": "list",
                    "length": len(value),
                    "item_keys": sorted(set(item_keys)),
                }
            return diagnostic_scalar(value)

        zoneish_names = {
            "z",
            "zone",
            "zoneid",
            "zone_id",
            "currentzone",
            "current_zone",
            "lz",
            "task",
            "taskid",
            "task_id",
            "tsk",
            "area",
            "areaid",
            "area_id",
        }

        def find_zone_candidates(value, path="dat", depth=0):
            """Find only zone/task-like fields while redacting arbitrary strings."""
            if depth > 5:
                return []
            found = []
            if isinstance(value, dict):
                for key, child in value.items():
                    key_text = str(key)
                    child_path = f"{path}.{key_text}"
                    normalized = key_text.lower().replace("-", "_")
                    if normalized in zoneish_names:
                        found.append(
                            {
                                "path": child_path,
                                "value": diagnostic_scalar(child),
                                "shape": diagnostic_shape(child),
                            }
                        )
                    found.extend(find_zone_candidates(child, child_path, depth + 1))
            elif isinstance(value, list):
                for index, child in enumerate(value[:10]):
                    found.extend(
                        find_zone_candidates(child, f"{path}[{index}]", depth + 1)
                    )
            return found[:40]

        candidate_blocks = {}
        for key in ("act", "rtk", "sc", "sh", "st", "tm", "tr"):
            if key in mower.dat:
                candidate_blocks[key] = diagnostic_shape(mower.dat.get(key))

        task_details = None
        if isinstance(cut_task, list) and cut_task:
            task = cut_task[0]
            if isinstance(task, dict):
                task_details = {
                    key: diagnostic_scalar(task.get(key))
                    for key in ("id", "st", "tm", "tr")
                    if key in task
                }

                zones = task.get("z")
                if isinstance(zones, list):
                    task_details["zones"] = []
                    for index, zone in enumerate(zones[:20]):
                        if not isinstance(zone, dict):
                            task_details["zones"].append(
                                {
                                    "index": index,
                                    "value": diagnostic_shape(zone),
                                }
                            )
                            continue

                        zone_details = {"index": index}
                        for key in ("a", "id", "p", "rtg", "rtn"):
                            if key in zone:
                                value = zone.get(key)
                                if isinstance(value, (dict, list)):
                                    zone_details[key] = diagnostic_shape(value)
                                else:
                                    zone_details[key] = diagnostic_scalar(value)

                        task_details["zones"].append(zone_details)

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
            "telemetry_status_id": mower.status_id,
            "telemetry_status": mower.status,
            "telemetry_cut_zone": diagnostic_scalar(cut_zone),
            "telemetry_cut_task": diagnostic_shape(cut_task),
            "telemetry_lz": diagnostic_scalar(telemetry_lz),
            "telemetry_dat_keys": sorted(str(key) for key in mower.dat),
            "telemetry_cut_keys": cut_keys,
            "telemetry_candidate_blocks": candidate_blocks,
            "telemetry_zone_candidates": find_zone_candidates(mower.dat),
            "telemetry_cut_task_details": task_details,
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
        prefix = f"component.{DOMAIN}.common.map."
        self._map_translations = {
            key.removeprefix(prefix): value
            for key, value in resources.items()
            if key.startswith(prefix)
        }
        self._map_translation_language = language
        return self._map_translations
