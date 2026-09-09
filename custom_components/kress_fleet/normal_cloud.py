# SPDX-License-Identifier: GPL-3.0-only

"""Normal Kress cloud backend powered by pyworxcloud."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pyworxcloud import WorxCloud
from pyworxcloud.events import LandroidEvent
from pyworxcloud.utils.requests import AGET, HEADERS

from .const import DOMAIN

RTK_MAP_CACHE_TTL = timedelta(minutes=30)
PRODUCT_ITEM_CACHE_TTL = timedelta(minutes=15)
ACTIVITY_LOG_CACHE_TTL = timedelta(hours=1)
RTK_MOWING_TRAIL_MAX_POINTS = 5000
RTK_TRAIL_STORAGE_VERSION = 1
RTK_TRAIL_SAVE_DELAY = 60
MOWING_STATUS_IDS = frozenset({7, 12, 32})
MOWING_STATUS_HINTS = ("mow", "cut", "maeh", "mäh")

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


def _status_description(device: Any) -> str:
    """Return the upstream mower status description for diagnostics/fallback."""
    status = getattr(device, "status", None)
    value = (
        status.get("description")
        if isinstance(status, dict)
        else getattr(status, "description", None)
    )
    return str(value or "").strip()


def _is_cutting_status(device: Any) -> bool:
    """Return True when the mower is currently cutting grass."""
    if _status_id(device) in MOWING_STATUS_IDS:
        return True
    description = _status_description(device).casefold()
    return any(hint in description for hint in MOWING_STATUS_HINTS)


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
            update_interval=PRODUCT_ITEM_CACHE_TTL,
        )
        self.cloud = cloud
        self.data = normal_devices(cloud)
        self._rtk_map_cache: dict[str, tuple[datetime, dict[str, Any]]] = {}
        self._product_item_cache: dict[str, tuple[datetime, dict[str, Any]]] = {}
        self._activity_log_cache: dict[str, tuple[datetime, Any]] = {}
        self._rtk_mowing_trails: dict[
            str, deque[tuple[datetime, float, float]]
        ] = {}
        self._rtk_trail_days: dict[str, str] = {}
        self._rtk_trail_store = Store[dict[str, Any]](
            hass,
            RTK_TRAIL_STORAGE_VERSION,
            f"{DOMAIN}.{entry.entry_id}.normal_rtk_trail",
        )
        self._rtk_trail_save_pending = False

    async def async_prepare(self) -> None:
        """Restore today's RTK trail before Home Assistant creates entities."""
        await self._load_rtk_mowing_trails()
        await self._refresh_private_cloud_data(self.data, force_product=True)
        self._remember_mowing_positions(self.data)

    async def _async_update_data(self) -> dict[str, Any]:
        """Refresh private Kress REST data used for coverage diagnostics."""
        devices = normal_devices(self.cloud)
        await self._refresh_private_cloud_data(devices, force_product=True)
        self._remember_mowing_positions(devices)
        return devices

    async def _refresh_private_cloud_data(
        self, devices: dict[str, Any], *, force_product: bool = False
    ) -> None:
        """Refresh product statistics and activity log without blocking Fleet."""
        for serial in devices:
            await self.async_get_product_item(serial, force=force_product)
            await self.async_get_activity_log(serial)

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
        """Keep today's RTK positions while the mower is actually cutting."""
        now = datetime.now(UTC)
        today = datetime.now(self._local_timezone()).date().isoformat()
        for serial, device in devices.items():
            if self._rtk_trail_days.get(serial) != today:
                self._rtk_mowing_trails[serial] = deque(
                    maxlen=RTK_MOWING_TRAIL_MAX_POINTS
                )
                self._rtk_trail_days[serial] = today
            if not _is_cutting_status(device):
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
            self._schedule_rtk_trail_save()

    def _local_timezone(self):
        """Return the Home Assistant configured timezone."""
        try:
            return ZoneInfo(self.hass.config.time_zone)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            return UTC

    def rtk_coverage_period(self) -> tuple[datetime, datetime]:
        """Return today's local coverage period from midnight until now."""
        timezone = self._local_timezone()
        now = datetime.now(timezone)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, now

    def rtk_mowing_trail(
        self, serial: str, max_points: int = RTK_MOWING_TRAIL_MAX_POINTS
    ) -> list[tuple[datetime, float, float]]:
        """Return only RTK mowing trail points from the current local day."""
        today = datetime.now(self._local_timezone()).date().isoformat()
        if self._rtk_trail_days.get(serial) != today:
            return []
        trail = self._rtk_mowing_trails.get(serial)
        if trail is None:
            return []
        start_local, _ = self.rtk_coverage_period()
        start_utc = start_local.astimezone(UTC)
        while trail and trail[0][0] < start_utc:
            trail.popleft()
        return list(trail)[-max_points:]

    def _schedule_rtk_trail_save(self) -> None:
        """Debounce persistence of the current Mission RTK trail."""
        if self._rtk_trail_save_pending:
            return
        self._rtk_trail_save_pending = True
        self._rtk_trail_store.async_delay_save(
            self._rtk_trail_store_data_and_clear_pending,
            RTK_TRAIL_SAVE_DELAY,
        )

    def _rtk_trail_store_data_and_clear_pending(self) -> dict[str, Any]:
        """Return serializable trail data and allow the next delayed save."""
        self._rtk_trail_save_pending = False
        return self._rtk_trail_store_data()

    def _rtk_trail_store_data(self) -> dict[str, Any]:
        """Return daily Mission RTK trails in a JSON-serializable shape."""
        return {
            serial: {
                "day": self._rtk_trail_days.get(serial),
                "points": [
                    {"t": timestamp.isoformat(), "lat": latitude, "lon": longitude}
                    for timestamp, latitude, longitude in trail
                ],
            }
            for serial, trail in self._rtk_mowing_trails.items()
        }

    async def _load_rtk_mowing_trails(self) -> None:
        """Restore today's Mission RTK trail after a Home Assistant restart."""
        stored = await self._rtk_trail_store.async_load()
        if not isinstance(stored, dict):
            return
        today = datetime.now(self._local_timezone()).date().isoformat()
        for serial, entry in stored.items():
            if not isinstance(entry, dict) or entry.get("day") != today:
                continue
            trail: deque[tuple[datetime, float, float]] = deque(
                maxlen=RTK_MOWING_TRAIL_MAX_POINTS
            )
            for point in entry.get("points") or []:
                if not isinstance(point, dict):
                    continue
                try:
                    timestamp = datetime.fromisoformat(str(point["t"]))
                    latitude = float(point["lat"])
                    longitude = float(point["lon"])
                except (KeyError, TypeError, ValueError):
                    continue
                trail.append((timestamp, latitude, longitude))
            if trail:
                self._rtk_mowing_trails[str(serial)] = trail
                self._rtk_trail_days[str(serial)] = today

    async def async_save_mowing_trails(self) -> None:
        """Persist current Mission RTK trails immediately during unload."""
        self._rtk_trail_save_pending = False
        await self._rtk_trail_store.async_save(self._rtk_trail_store_data())

    async def _api_get(self, path: str) -> Any:
        """Fetch a private Kress API path using pyworxcloud's token/session."""
        api = getattr(self.cloud, "_api", None)
        if api is None:
            return None
        try:
            await api.check_token()
            endpoint = getattr(getattr(api, "cloud", None), "ENDPOINT", None)
            if endpoint is None:
                endpoint = getattr(
                    getattr(self.cloud, "_cloud", None), "ENDPOINT", None
                )
            if endpoint is None:
                return None
            return await AGET(
                f"https://{endpoint}{path}",
                HEADERS(api.access_token),
                session=await api._ensure_session(),
            )
        except Exception:  # noqa: BLE001 - private upstream API is best effort
            _LOGGER.debug("Could not fetch Kress API path %s", path, exc_info=True)
            return None

    async def async_get_product_item(
        self, serial: str, *, force: bool = False
    ) -> dict[str, Any] | None:
        """Fetch the private product-item record containing cloud statistics."""
        now = datetime.now(UTC)
        cached = self._product_item_cache.get(serial)
        if (
            cached is not None
            and not force
            and now - cached[0] < PRODUCT_ITEM_CACHE_TTL
        ):
            return cached[1]
        value = await self._api_get(f"/api/v2/product-items/{serial}")
        if isinstance(value, dict):
            self._product_item_cache[serial] = (now, value)
            return value
        return cached[1] if cached is not None else None

    async def async_get_activity_log(
        self, serial: str, *, force: bool = False
    ) -> Any:
        """Fetch the normal Kress activity log for coverage/API probing."""
        now = datetime.now(UTC)
        cached = self._activity_log_cache.get(serial)
        if (
            cached is not None
            and not force
            and now - cached[0] < ACTIVITY_LOG_CACHE_TTL
        ):
            return cached[1]
        value = await self._api_get(f"/api/v2/product-items/{serial}/activity-log")
        if isinstance(value, (dict, list)):
            self._activity_log_cache[serial] = (now, value)
            return value
        return cached[1] if cached is not None else None

    def product_item_data(self, serial: str) -> dict[str, Any] | None:
        """Return cached product-item data for one mower."""
        cached = self._product_item_cache.get(serial)
        return None if cached is None else cached[1]

    def product_item_updated_at(self, serial: str) -> datetime | None:
        """Return when private product statistics were last fetched."""
        cached = self._product_item_cache.get(serial)
        return None if cached is None else cached[0]

    def activity_log_data(self, serial: str) -> Any:
        """Return cached Kress activity-log data for one mower."""
        cached = self._activity_log_cache.get(serial)
        return None if cached is None else cached[1]

    def activity_log_updated_at(self, serial: str) -> datetime | None:
        """Return when the activity log was last fetched."""
        cached = self._activity_log_cache.get(serial)
        return None if cached is None else cached[0]

    @staticmethod
    def _probe_preview(value: Any) -> Any:
        """Return a compact, HA-safe preview without dumping large payloads."""
        if isinstance(value, dict):
            return f"dict[{len(value)}]"
        if isinstance(value, (list, tuple)):
            return f"list[{len(value)}]"
        if isinstance(value, str):
            return value if len(value) <= 80 else value[:77] + "..."
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return type(value).__name__

    @classmethod
    def _probe_candidates(cls, source: Any, prefix: str) -> list[str]:
        """Return candidate field paths that may describe mowing/coverage."""
        keywords = (
            "area",
            "cover",
            "mow",
            "work",
            "progress",
            "trail",
            "track",
            "route",
            "path",
            "rtk",
            "map",
            "task",
            "history",
        )
        sensitive = ("token", "password", "email", "user_id")
        results: list[str] = []

        def walk(value: Any, path: str, depth: int) -> None:
            if depth > 5 or len(results) >= 80:
                return
            if isinstance(value, dict):
                for key, child in value.items():
                    key_text = str(key)
                    child_path = f"{path}.{key_text}" if path else key_text
                    folded = child_path.casefold()
                    if any(secret in folded for secret in sensitive):
                        continue
                    if any(keyword in folded for keyword in keywords):
                        results.append(f"{child_path}={cls._probe_preview(child)}")
                    walk(child, child_path, depth + 1)
            elif isinstance(value, list) and depth < 3:
                for index, child in enumerate(value[:5]):
                    walk(child, f"{path}[{index}]", depth + 1)

        walk(source, prefix, 0)
        return results

    def coverage_probe(self, serial: str) -> dict[str, Any]:
        """Return compact cloud/MQTT diagnostics for coverage reverse engineering."""
        device = self.data.get(serial)
        product = self.product_item_data(serial) or {}
        activity = self.activity_log_data(serial)
        raw_cfg = getattr(device, "raw_cfg", {}) if device is not None else {}
        raw_dat = getattr(device, "raw_dat", {}) if device is not None else {}
        raw_data = getattr(device, "raw_data", {}) if device is not None else {}
        statistics = getattr(device, "statistics", {}) if device is not None else {}
        rtk_cfg = raw_cfg.get("rtk", {}) if isinstance(raw_cfg, dict) else {}
        rtk_dat = raw_dat.get("rtk", {}) if isinstance(raw_dat, dict) else {}

        activity_count = len(activity) if isinstance(activity, list) else None
        activity_top_keys: list[str] = []
        if isinstance(activity, dict):
            items = activity.get("items")
            activity_count = len(items) if isinstance(items, list) else len(activity)
            activity_top_keys = sorted(str(key) for key in activity)
        elif isinstance(activity, list) and activity and isinstance(activity[0], dict):
            activity_top_keys = sorted(str(key) for key in activity[0])

        candidates = []
        candidates.extend(self._probe_candidates(product, "product_item"))
        candidates.extend(self._probe_candidates(statistics, "statistics"))
        candidates.extend(self._probe_candidates(raw_cfg, "raw_cfg"))
        candidates.extend(self._probe_candidates(raw_dat, "raw_dat"))
        candidates.extend(self._probe_candidates(raw_data, "raw_data"))
        candidates.extend(self._probe_candidates(activity, "activity_log"))

        return {
            "cloud_area_mowed_total": product.get("area_mowed"),
            "cloud_lawn_size": product.get("lawn_size"),
            "cloud_lawn_perimeter": product.get("lawn_perimeter"),
            "cloud_product_item_updated_at": self.product_item_updated_at(serial),
            "cloud_product_item_keys": sorted(product),
            "activity_log_count": activity_count,
            "activity_log_updated_at": self.activity_log_updated_at(serial),
            "activity_log_top_keys": activity_top_keys,
            "statistics_keys": (
                sorted(statistics) if isinstance(statistics, dict) else []
            ),
            "raw_data_keys": sorted(raw_data) if isinstance(raw_data, dict) else [],
            "raw_cfg_keys": sorted(raw_cfg) if isinstance(raw_cfg, dict) else [],
            "raw_dat_keys": sorted(raw_dat) if isinstance(raw_dat, dict) else [],
            "raw_rtk_cfg_keys": sorted(rtk_cfg) if isinstance(rtk_cfg, dict) else [],
            "raw_rtk_dat_keys": sorted(rtk_dat) if isinstance(rtk_dat, dict) else [],
            "coverage_probe_candidates": candidates[:80],
        }

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
