# SPDX-License-Identifier: GPL-3.0-only

"""Normal Kress cloud backend powered by pyworxcloud."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pyworxcloud import WorxCloud
from pyworxcloud.events import LandroidEvent
from pyworxcloud.utils.requests import AGET, HEADERS

from .const import DOMAIN

RTK_MAP_CACHE_TTL = timedelta(minutes=30)
RTK_MOWING_TRAIL_MAX_POINTS = 5000
MOWING_STATUS_IDS = frozenset({7, 12, 32})

_LOGGER = logging.getLogger(__name__)


def normal_devices(cloud: WorxCloud) -> dict[str, Any]:
    """Return pyworxcloud devices indexed by stable serial number."""
    devices: dict[str, Any] = {}
    for device in cloud.devices.values():
        serial = getattr(device, "serial_number", None)
        if serial is not None:
            devices[str(serial)] = device
    return devices


def _status_id(device: Any) -> int | None:
    """Return the numeric mower status reported by pyworxcloud."""
    status = getattr(device, "status", None)
    value = status.get("id") if isinstance(status, dict) else getattr(status, "id", None)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _rtk_position(device: Any) -> tuple[float, float] | None:
    """Return the live RTK position without importing entity helpers."""
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

    gps = getattr(device, "gps", None)
    if isinstance(gps, dict):
        latitude = gps.get("latitude")
        longitude = gps.get("longitude")
    else:
        latitude = getattr(gps, "latitude", None)
        longitude = getattr(gps, "longitude", None)
    if isinstance(latitude, (int, float)) and isinstance(longitude, (int, float)):
        return float(latitude), float(longitude)
    return None


class KressNormalCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Bridge pyworxcloud push/API updates into Home Assistant entities."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, cloud: WorxCloud) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=f"{DOMAIN}_normal",
            config_entry=entry,
        )
        self.cloud = cloud
        self.data = normal_devices(cloud)
        self._rtk_map_cache: dict[str, tuple[datetime, dict[str, Any]]] = {}
        self._rtk_mowing_trails: dict[
            str, deque[tuple[datetime, float, float]]
        ] = {}
        self._remember_mowing_positions(self.data)

    def bind_callbacks(self) -> None:
        """Refresh Home Assistant entities whenever pyworxcloud receives data."""

        def _handle_event(**_kwargs: Any) -> None:
            def _apply_update() -> None:
                devices = normal_devices(self.cloud)
                self._remember_mowing_positions(devices)
                self.async_set_updated_data(devices)

            self.hass.loop.call_soon_threadsafe(_apply_update)

        self.cloud.set_callback(LandroidEvent.DATA_RECEIVED, _handle_event)
        self.cloud.set_callback(LandroidEvent.API, _handle_event)
        self.cloud.set_callback(LandroidEvent.MQTT_CONNECTION, _handle_event)

    def _remember_mowing_positions(self, devices: dict[str, Any]) -> None:
        """Keep recent RTK positions while the mower is actually cutting."""
        now = datetime.now(UTC)
        for serial, device in devices.items():
            if _status_id(device) not in MOWING_STATUS_IDS:
                continue
            position = _rtk_position(device)
            if position is None:
                continue
            latitude, longitude = position
            trail = self._rtk_mowing_trails.setdefault(
                serial, deque(maxlen=RTK_MOWING_TRAIL_MAX_POINTS)
            )
            if trail:
                _, previous_latitude, previous_longitude = trail[-1]
                if (
                    round(previous_latitude, 7) == round(latitude, 7)
                    and round(previous_longitude, 7) == round(longitude, 7)
                ):
                    continue
            trail.append((now, latitude, longitude))

    def rtk_mowing_trail(
        self, serial: str, max_points: int = RTK_MOWING_TRAIL_MAX_POINTS
    ) -> list[tuple[datetime, float, float]]:
        """Return the recent in-memory RTK mowing trail."""
        trail = self._rtk_mowing_trails.get(serial)
        if trail is None:
            return []
        return list(trail)[-max_points:]

    async def async_get_rtk_map(
        self, map_id: str | None, *, force: bool = False
    ) -> dict[str, Any] | None:
        """Fetch RTK map geometry from the private Kress/Worx map endpoint."""
        if not map_id:
            return None

        now = datetime.now(UTC)
        cached = self._rtk_map_cache.get(map_id)
        if (
            cached is not None
            and not force
            and now - cached[0] < RTK_MAP_CACHE_TTL
        ):
            return cached[1]

        api = getattr(self.cloud, "_api", None)
        if api is None:
            return cached[1] if cached is not None else None

        try:
            await api.check_token()
            endpoint = getattr(getattr(api, "cloud", None), "ENDPOINT", None)
            if endpoint is None:
                endpoint = getattr(getattr(self.cloud, "_cloud", None), "ENDPOINT", None)
            if endpoint is None:
                return cached[1] if cached is not None else None

            map_data = await AGET(
                f"https://{endpoint}/api/v2/maps/{map_id}",
                HEADERS(api.access_token),
                session=await api._ensure_session(),
            )
        except Exception:  # noqa: BLE001 - private upstream endpoint is best effort
            _LOGGER.debug(
                "Could not fetch Kress RTK map %s", map_id, exc_info=True
            )
            return cached[1] if cached is not None else None

        if isinstance(map_data, dict):
            self._rtk_map_cache[map_id] = (now, map_data)
            return map_data
        return cached[1] if cached is not None else None
