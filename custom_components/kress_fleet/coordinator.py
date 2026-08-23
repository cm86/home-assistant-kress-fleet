# SPDX-License-Identifier: GPL-3.0-only
# This file is part of kress_fleet, a modified work derived in part from
# MTrab/landroid_cloud and MTrab/pyworxcloud (GPL-3.0).
# Kress Fleet modifications began on 2026-08-21; see NOTICE and LICENSE.

"""Coordinator for Kress Fleet."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import json
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import FleetAuthError, FleetConnectionError, FleetError, KressFleetApi
from .const import DEFAULT_COVERAGE_INTERVAL, DOMAIN
from .models import FleetMower

_LOGGER = logging.getLogger(__name__)


class KressFleetCoordinator(DataUpdateCoordinator[dict[str, FleetMower]]):
    """Coordinate REST refreshes and MQTT push data."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: KressFleetApi,
        mowers: dict[str, FleetMower],
    ) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=DEFAULT_COVERAGE_INTERVAL),
        )
        self.api = api
        self.data = mowers
        self.mqtt = None
        self._refresh_counter = 0
        self._map_load_locks: dict[tuple[int, int, str, int], asyncio.Lock] = {}

    async def _async_update_data(self) -> dict[str, FleetMower]:
        try:
            self._refresh_counter += 1
            mowers = list(self.data.values())

            # Product REST is only a fallback. A single slow/offline mower must
            # never stall the whole coordinator update, so each request gets a
            # short independent timeout and failures stay local to that mower.
            if self._refresh_counter % 5 == 0:
                await self._async_refresh_product_states(mowers, timeout_seconds=8.0)

            # Map metadata is slow-changing. Refresh once per location/map
            # instead of issuing duplicate /maps requests for every mower.
            if self._refresh_counter % 10 == 0:
                await self.api.async_update_map_metadata_group(mowers)

            # Coverage is a property of /maps/{map_id}/coverage. Multiple mowers
            # may share the same map, so download each unique map only once and
            # share that in-memory snapshot across those mower objects.
            active_statuses = {2, 3, 7, 12, 31, 32, 33, 103}
            coverage_groups: dict[tuple[int, int, str, int], list[FleetMower]] = {}
            for mower in mowers:
                if not mower.map_id:
                    continue
                active = mower.status_id in active_statuses
                if mower.coverage_days <= 1:
                    # Today's coverage is small enough to keep genuinely live
                    # while mowing.
                    should_refresh = mower.coverage_revision > 0 and (
                        active or self._refresh_counter % 10 == 0
                    )
                else:
                    # Multi-day coverage can be many megabytes. Refresh it
                    # immediately on select changes, then at a lower rate while
                    # mowing to avoid repeatedly downloading the static history.
                    should_refresh = mower.coverage_revision > 0 and (
                        (active and self._refresh_counter % 5 == 0)
                        or self._refresh_counter % 10 == 0
                    )
                if should_refresh:
                    key = (
                        mower.user_id,
                        mower.location_id,
                        mower.map_id,
                        mower.coverage_days,
                    )
                    coverage_groups.setdefault(key, []).append(mower)

            async def refresh_group(group: list[FleetMower]) -> None:
                representative = group[0]
                coverage, coverage_from, coverage_to = await self.api.async_get_coverage(
                    representative, representative.coverage_days
                )
                for mower in group:
                    mower.coverage = coverage
                    mower.coverage_from = coverage_from
                    mower.coverage_to = coverage_to
                    mower.coverage_revision += 1

            if coverage_groups:
                await asyncio.gather(
                    *(refresh_group(group) for group in coverage_groups.values())
                )

            return self.data
        except FleetAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (FleetConnectionError, FleetError) as err:
            raise UpdateFailed(str(err)) from err


    async def _async_refresh_product_states(
        self, mowers: list[FleetMower], *, timeout_seconds: float
    ) -> None:
        """Refresh product data independently without a slow mower blocking peers."""

        async def refresh_one(mower: FleetMower) -> None:
            try:
                async with asyncio.timeout(timeout_seconds):
                    await self.api.async_update_product_state(mower)
            except TimeoutError:
                _LOGGER.debug(
                    "Fleet product refresh timed out for mower %s", mower.uuid[:8]
                )
            except FleetAuthError:
                raise
            except (FleetConnectionError, FleetError):
                _LOGGER.debug(
                    "Fleet product refresh failed for mower %s",
                    mower.uuid[:8],
                    exc_info=True,
                )

        await asyncio.gather(*(refresh_one(mower) for mower in mowers))

    async def async_enrich_product_details(self) -> None:
        """Load product names/model/last status after HA startup is already ready."""
        mowers = list(self.data.values())
        if not mowers:
            return
        started_missing = sum(not mower.metadata_ready for mower in mowers)
        if not started_missing:
            return
        _LOGGER.debug(
            "Background Fleet product enrichment started for %s mower(s)",
            started_missing,
        )
        try:
            await self._async_refresh_product_states(mowers, timeout_seconds=12.0)
        except FleetAuthError:
            _LOGGER.debug("Background Fleet product enrichment lost authentication")
            return
        self.async_set_updated_data(dict(self.data))
        remaining = sum(not mower.metadata_ready for mower in mowers)
        _LOGGER.debug(
            "Background Fleet product enrichment finished; %s mower(s) still pending",
            remaining,
        )

    async def async_enrich_zone_metadata(self) -> None:
        """Load active map detail in background so zone names work immediately.

        This intentionally does *not* request coverage.  Map detail is much
        smaller and contains the Fleet zone metadata discovered in v0.2.7;
        coverage remains lazy and is still fetched only when a live map is
        opened.
        """
        groups: dict[tuple[int, int, str], list[FleetMower]] = {}
        for mower in self.data.values():
            if mower.map_id and mower.map_detail is None:
                groups.setdefault(
                    (mower.user_id, mower.location_id, mower.map_id), []
                ).append(mower)

        if not groups:
            return

        async def load_one(group: list[FleetMower]) -> None:
            sample = group[0]
            try:
                async with asyncio.timeout(12.0):
                    detail = await self.api.async_get_map_detail(
                        sample.user_id, sample.location_id, sample.map_id or ""
                    )
            except TimeoutError:
                _LOGGER.debug(
                    "Fleet zone metadata timed out for map %s",
                    (sample.map_id or "")[:8],
                )
                return
            except FleetAuthError:
                raise
            except (FleetConnectionError, FleetError):
                _LOGGER.debug(
                    "Fleet zone metadata refresh failed for map %s",
                    (sample.map_id or "")[:8],
                    exc_info=True,
                )
                return

            for mower in group:
                if detail != mower.map_detail:
                    mower.map_detail = detail
                    mower.map_revision += 1

        try:
            await asyncio.gather(*(load_one(group) for group in groups.values()))
        except FleetAuthError:
            _LOGGER.debug("Background Fleet zone metadata lost authentication")
            return

        self.async_set_updated_data(dict(self.data))
        _LOGGER.debug(
            "Background Fleet zone metadata loaded for %s active map(s)",
            len(groups),
        )

    async def async_ensure_map_data(self, mower_uuid: str) -> None:
        """Load map detail and coverage only when a camera actually needs it.

        The Fleet coverage response can be several megabytes per map.  Loading
        all maps during config-entry setup made Home Assistant startup wait for
        data that may never be viewed.  This method deduplicates concurrent
        camera requests per map/range and shares the result with peer mowers.
        """
        mower = self.data[mower_uuid]
        if not mower.map_id:
            return

        key = (mower.user_id, mower.location_id, mower.map_id, mower.coverage_days)
        lock = self._map_load_locks.setdefault(key, asyncio.Lock())
        async with lock:
            # Another camera request may have completed while we waited.
            need_detail = mower.map_detail is None
            need_coverage = mower.coverage_revision == 0
            if not need_detail and not need_coverage:
                return

            if need_detail:
                detail = await self.api.async_get_map_detail(
                    mower.user_id, mower.location_id, mower.map_id
                )
                for candidate in self.data.values():
                    if (
                        candidate.user_id == mower.user_id
                        and candidate.location_id == mower.location_id
                        and candidate.map_id == mower.map_id
                    ):
                        candidate.map_detail = detail
                        candidate.map_revision += 1

            if need_coverage:
                coverage, coverage_from, coverage_to = await self.api.async_get_coverage(
                    mower, mower.coverage_days
                )
                for candidate in self.data.values():
                    if (
                        candidate.user_id == mower.user_id
                        and candidate.location_id == mower.location_id
                        and candidate.map_id == mower.map_id
                        and candidate.coverage_days == mower.coverage_days
                    ):
                        candidate.coverage = coverage
                        candidate.coverage_from = coverage_from
                        candidate.coverage_to = coverage_to
                        candidate.coverage_revision += 1

            self.async_set_updated_data(dict(self.data))

    async def async_set_coverage_days(self, mower_uuid: str, days: int) -> None:
        """Change a mower's history window and refresh its map immediately."""
        mower = self.data[mower_uuid]
        period_days = max(1, min(7, int(days)))
        if mower.coverage_days == period_days and mower.coverage:
            return

        previous_days = mower.coverage_days
        mower.coverage_days = period_days
        if not mower.map_id:
            self.async_set_updated_data(dict(self.data))
            return

        try:
            coverage, coverage_from, coverage_to = await self.api.async_get_coverage(
                mower, period_days
            )
        except Exception:
            mower.coverage_days = previous_days
            raise

        # Share the response only with peers using the exact same map AND the
        # same selected time window. This keeps per-mower select choices
        # independent while still avoiding needless duplicate data in memory.
        for candidate in self.data.values():
            if (
                candidate.user_id == mower.user_id
                and candidate.location_id == mower.location_id
                and candidate.map_id == mower.map_id
                and candidate.coverage_days == period_days
            ):
                candidate.coverage = coverage
                candidate.coverage_from = coverage_from
                candidate.coverage_to = coverage_to
                candidate.coverage_revision += 1

        self.async_set_updated_data(dict(self.data))

    @callback
    def async_handle_mqtt_payload(
        self, mower_uuid: str | None, topic: str, payload_text: str
    ) -> None:
        """Decode a commandOut message delivered by paho's worker thread."""
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            _LOGGER.debug("Ignoring malformed Fleet MQTT JSON")
            return
        if not isinstance(payload, dict):
            return

        dat = payload.get("dat")
        # Prefer the mower UUID carried by the payload when it maps to a real
        # product item. Fleet can expose companion MQTT-only identifiers in the
        # credential block, so the topic UUID is not always an HA mower UUID.
        if isinstance(dat, dict) and dat.get("uuid"):
            payload_uuid = str(dat["uuid"])
            if payload_uuid in self.data:
                mower_uuid = payload_uuid
        if not mower_uuid:
            for candidate in self.data.values():
                if candidate.command_out == topic:
                    mower_uuid = candidate.uuid
                    break
        if not mower_uuid or mower_uuid not in self.data:
            _LOGGER.debug("Could not match Fleet MQTT payload to a product mower")
            return

        mower = self.data[mower_uuid]
        mower.update_payload(payload)
        mower.mqtt_connected = True
        self.async_set_updated_data(dict(self.data))

    @callback
    def async_push_update(self) -> None:
        """Push in-memory changes to entities without a REST request."""
        self.async_set_updated_data(dict(self.data))
