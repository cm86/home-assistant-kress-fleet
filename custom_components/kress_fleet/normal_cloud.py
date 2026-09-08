# SPDX-License-Identifier: GPL-3.0-only

"""Normal Kress cloud backend powered by pyworxcloud."""

from __future__ import annotations

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

_LOGGER = logging.getLogger(__name__)


def normal_devices(cloud: WorxCloud) -> dict[str, Any]:
    """Return pyworxcloud devices indexed by stable serial number."""
    devices: dict[str, Any] = {}
    for device in cloud.devices.values():
        serial = getattr(device, "serial_number", None)
        if serial is not None:
            devices[str(serial)] = device
    return devices


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

    def bind_callbacks(self) -> None:
        """Refresh Home Assistant entities whenever pyworxcloud receives data."""

        def _handle_event(**_kwargs: Any) -> None:
            self.hass.loop.call_soon_threadsafe(
                self.async_set_updated_data, normal_devices(self.cloud)
            )

        self.cloud.set_callback(LandroidEvent.DATA_RECEIVED, _handle_event)
        self.cloud.set_callback(LandroidEvent.API, _handle_event)
        self.cloud.set_callback(LandroidEvent.MQTT_CONNECTION, _handle_event)

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
