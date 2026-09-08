# SPDX-License-Identifier: GPL-3.0-only

"""Normal Kress cloud backend powered by pyworxcloud."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pyworxcloud import WorxCloud
from pyworxcloud.events import LandroidEvent

from .const import DOMAIN


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
            logger=__import__("logging").getLogger(__name__),
            name=f"{DOMAIN}_normal",
            config_entry=entry,
        )
        self.cloud = cloud
        self.data = normal_devices(cloud)

    def bind_callbacks(self) -> None:
        """Refresh Home Assistant entities whenever pyworxcloud receives data."""

        def _handle_event(**_kwargs: Any) -> None:
            self.hass.loop.call_soon_threadsafe(
                self.async_set_updated_data, normal_devices(self.cloud)
            )

        self.cloud.set_callback(LandroidEvent.DATA_RECEIVED, _handle_event)
        self.cloud.set_callback(LandroidEvent.API, _handle_event)
        self.cloud.set_callback(LandroidEvent.MQTT_CONNECTION, _handle_event)
