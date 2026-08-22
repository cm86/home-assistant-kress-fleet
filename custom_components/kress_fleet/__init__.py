# SPDX-License-Identifier: GPL-3.0-only
# This file is part of kress_fleet, a modified work derived in part from
# MTrab/landroid_cloud and MTrab/pyworxcloud (GPL-3.0).
# Kress Fleet modifications began on 2026-08-21; see NOTICE and LICENSE.

"""Kress Fleet integration for Home Assistant."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from time import monotonic

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers import device_registry as dr

from .api import FleetAuthError, FleetConnectionError, FleetError, KressFleetApi
from .const import DOMAIN, PLATFORMS
from .coordinator import KressFleetCoordinator
from .mqtt import KressFleetMqtt

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class KressFleetRuntimeData:
    """Runtime objects for one config entry."""

    api: KressFleetApi
    coordinator: KressFleetCoordinator
    mqtt: KressFleetMqtt


def _is_placeholder_mower_name(name: str | None, mower_uuid: str) -> bool:
    """Return True when a mower still has a discovery-only fallback name."""
    if not name:
        return True
    normalized = name.strip()
    return normalized in {
        "Kress Mäher",
        "Kress Mower",
        f"Kress {mower_uuid[:8]}",
    }


def _restore_known_device_metadata(
    hass: HomeAssistant, entry: ConfigEntry, mowers: dict[str, object]
) -> None:
    """Reuse useful device-registry metadata while Fleet details load.

    Existing installations already know friendly mower names/model/serial. The
    fast startup discovery intentionally skips product-detail calls, so restore
    those fields locally before entities are forwarded. User-assigned device
    names remain untouched because they live separately as ``name_by_user``.

    Old UUID-based fallback names from pre-0.3.1 builds are deliberately *not*
    restored. This lets the initial-name bootstrap replace them with the real
    Fleet mower name on the next integration reload.
    """
    registry = dr.async_get(hass)
    for device in registry.devices.values():
        config_entries = getattr(device, "config_entries", set())
        if entry.entry_id not in config_entries:
            continue
        for identifier in getattr(device, "identifiers", set()):
            if len(identifier) != 2 or identifier[0] != DOMAIN:
                continue
            mower = mowers.get(identifier[1])
            if mower is None or getattr(mower, "metadata_ready", False):
                continue
            # ``device.name`` is the integration-provided/original name. A user
            # rename is held in name_by_user and is intentionally not copied.
            device_name = getattr(device, "name", None)
            if device_name and not _is_placeholder_mower_name(
                device_name, getattr(mower, "uuid")
            ):
                mower.name = device_name
            if getattr(device, "model", None):
                mower.model = device.model
            if getattr(device, "serial_number", None):
                mower.serial_number = device.serial_number


async def _async_prepare_initial_device_names(
    api: KressFleetApi, mowers: dict[str, object]
) -> None:
    """Resolve friendly Fleet names before Home Assistant creates devices.

    Product-detail requests remain outside the normal startup critical path once
    Home Assistant already knows a mower's friendly integration-provided name.
    For a newly added mower (or an old UUID-named device), however, we spend a
    short bounded amount of time fetching product details in parallel so HA's
    post-setup "rename and assign" dialog receives names such as "Mähgatron"
    instead of "Kress 99337e8f".
    """
    unresolved = [
        mower
        for mower in mowers.values()
        if _is_placeholder_mower_name(
            getattr(mower, "name", None), getattr(mower, "uuid")
        )
    ]
    if not unresolved:
        return

    _LOGGER.debug(
        "Resolving friendly Fleet device names for %s mower(s) before entity setup",
        len(unresolved),
    )

    async def resolve_one(mower: object) -> None:
        try:
            async with asyncio.timeout(8.0):
                await api.async_update_product_state(mower)
        except TimeoutError:
            _LOGGER.debug(
                "Initial Fleet name lookup timed out for mower %s",
                getattr(mower, "uuid")[:8],
            )
        except FleetAuthError:
            raise
        except (FleetConnectionError, FleetError):
            _LOGGER.debug(
                "Initial Fleet name lookup failed for mower %s",
                getattr(mower, "uuid")[:8],
                exc_info=True,
            )

    await asyncio.gather(*(resolve_one(mower) for mower in unresolved))

    # Never expose a UUID fragment as the suggested Home Assistant device name.
    # This branch is only a resilience fallback when Fleet product details are
    # temporarily unavailable during the bounded bootstrap above.
    still_unresolved = sorted(
        (
            mower
            for mower in unresolved
            if _is_placeholder_mower_name(
                getattr(mower, "name", None), getattr(mower, "uuid")
            )
        ),
        key=lambda mower: getattr(mower, "uuid"),
    )
    for index, mower in enumerate(still_unresolved, start=1):
        model = getattr(mower, "model", None)
        if model and len(still_unresolved) == 1:
            mower.name = f"Kress {model}"
        elif model:
            mower.name = f"Kress {model} {index}"
        elif len(still_unresolved) == 1:
            mower.name = "Kress Mäher"
        else:
            mower.name = f"Kress Mäher {index}"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Kress Fleet from a config entry."""
    started = monotonic()
    session = async_create_clientsession(
        hass,
        cookie_jar=aiohttp.CookieJar(unsafe=True, quote_cookie=False),
    )
    api = KressFleetApi(
        session,
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        hass.config.time_zone,
    )

    try:
        async with asyncio.timeout(120):
            await api.async_authenticate()
            mowers = await api.async_discover()
    except TimeoutError as err:
        raise ConfigEntryNotReady("Kress Fleet setup timed out") from err
    except FleetAuthError as err:
        await api.async_close()
        raise ConfigEntryAuthFailed(str(err)) from err
    except (FleetConnectionError, FleetError) as err:
        await api.async_close()
        raise ConfigEntryNotReady(str(err)) from err

    # Fast discovery deliberately skips mapped mower product details. Reuse
    # already-known HA metadata first. For newly added/legacy UUID-named devices,
    # resolve only the missing friendly names in parallel before platform setup so
    # Home Assistant's rename/area dialog starts with meaningful mower names.
    _restore_known_device_metadata(hass, entry, mowers)
    try:
        await _async_prepare_initial_device_names(api, mowers)
    except FleetAuthError as err:
        await api.async_close()
        raise ConfigEntryAuthFailed(str(err)) from err

    # Discovery already gives us enough data to create every entity.  Do not
    # block Home Assistant startup on product details, map detail or potentially
    # multi-megabyte coverage downloads.
    coordinator = KressFleetCoordinator(hass, entry, api, mowers)
    coordinator.async_set_updated_data(dict(mowers))

    mqtt_client = KressFleetMqtt(api, coordinator, entry)
    coordinator.mqtt = mqtt_client
    entry.runtime_data = KressFleetRuntimeData(api, coordinator, mqtt_client)

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await api.async_close()
        raise

    mqtt_client.async_start()
    entry.async_create_background_task(
        hass,
        coordinator.async_enrich_product_details(),
        name="Kress Fleet product enrichment",
        eager_start=True,
    )
    entry.async_create_background_task(
        hass,
        coordinator.async_enrich_zone_metadata(),
        name="Kress Fleet zone metadata enrichment",
        eager_start=True,
    )
    _LOGGER.info(
        "Kress Fleet startup setup ready in %.2fs; zone metadata enriches in background and coverage stays lazy",
        monotonic() - started,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Kress Fleet."""
    runtime: KressFleetRuntimeData = entry.runtime_data
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await runtime.mqtt.async_stop()
        await runtime.api.async_close()
    return unloaded
