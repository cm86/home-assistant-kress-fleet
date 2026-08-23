# SPDX-License-Identifier: GPL-3.0-only
# This file is part of kress_fleet, a modified work derived in part from
# MTrab/landroid_cloud and MTrab/pyworxcloud (GPL-3.0).
# Kress Fleet modifications began on 2026-08-21; see NOTICE and LICENSE.

"""Data models and payload decoding for Kress Fleet."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


# Statuses where a mowing-zone context is meaningful. Fleet telemetry can
# temporarily omit ``dat.cut.z`` while the mower remains in the same active
# run. Keep the last explicitly reported zone only for the duration of such a
# run; never carry it into a later mowing session or a different map.
_ZONE_CONTEXT_STATUS_IDS = frozenset({2, 3, 7, 12, 31, 32, 33, 34, 103})


@dataclass(slots=True)
class FleetMower:
    """A Kress Fleet mower."""

    uuid: str
    user_id: int
    location_id: int
    name: str
    model: str | None = None
    serial_number: str | None = None
    map_id: str | None = None
    map_name: str | None = None
    command_in: str | None = None
    command_out: str | None = None
    online: bool = False
    metadata_ready: bool = False
    mqtt_connected: bool = False
    cfg: dict[str, Any] = field(default_factory=dict)
    dat: dict[str, Any] = field(default_factory=dict)
    coverage: list[dict[str, Any]] = field(default_factory=list)
    coverage_from: str | None = None
    coverage_to: str | None = None
    coverage_revision: int = 0
    coverage_days: int = 1
    map_detail: dict[str, Any] | None = None
    map_revision: int = 0
    # Cached REST product detail.  This remains in RAM only and lets the zone
    # parser use Fleet metadata that is not part of the MQTT payload/map JSON.
    product_detail: dict[str, Any] | None = None

    # Kress protocol-1 telemetry normally reports the active zone in
    # ``dat.cut.z``. Some commandOut snapshots omit that value temporarily even
    # though the mower is still mowing. Remember the last explicitly reported
    # zone only within the current active run so the HA zone entities do not
    # flap to ``unknown`` between otherwise valid telemetry packets.
    _last_reported_zone: int | None = field(default=None, init=False, repr=False)
    _last_reported_zone_map_id: str | None = field(
        default=None, init=False, repr=False
    )
    _zone_context_active: bool = field(default=False, init=False, repr=False)

    def update_payload(self, payload: dict[str, Any]) -> None:
        """Update the mower from a commandOut payload.

        Fleet commandOut data is authoritative, but not every snapshot carries
        ``dat.cut.z``. Track the last *explicitly* reported zone during one
        active mowing/zoning session instead of turning the zone sensors into
        ``unknown`` whenever that optional field is absent.
        """
        cfg = payload.get("cfg")
        dat = payload.get("dat")

        if isinstance(cfg, dict):
            self.cfg = cfg

        # Resolve a map assignment before remembering a zone so the fallback is
        # automatically scoped to the map on which it was observed.
        rtk_cfg = self.cfg.get("rtk")
        if isinstance(rtk_cfg, dict) and rtk_cfg.get("map"):
            self.map_id = str(rtk_cfg["map"])

        if isinstance(dat, dict):
            previous_status_id = self.status_id
            incoming_status_id = _as_int(dat.get("ls"))
            effective_status_id = (
                incoming_status_id
                if incoming_status_id is not None
                else previous_status_id
            )
            zone_context_active = effective_status_id in _ZONE_CONTEXT_STATUS_IDS

            # A transition into a new active run must not reuse the zone from a
            # previous mowing session. The next explicit ``cut.z`` starts the
            # fallback context for this run.
            if zone_context_active and not self._zone_context_active:
                self._last_reported_zone = None
                self._last_reported_zone_map_id = None

            reported_zone = _reported_zone(dat)
            if reported_zone is not None:
                self._last_reported_zone = reported_zone
                self._last_reported_zone_map_id = self.map_id

            self._zone_context_active = zone_context_active
            self.dat = dat
            self.online = True

    @property
    def coordinates(self) -> tuple[float, float] | None:
        """Return current coordinates as latitude, longitude."""
        modules = self.dat.get("modules")
        if not isinstance(modules, dict):
            return None
        modem = modules.get("4G")
        if not isinstance(modem, dict):
            return None
        gps = modem.get("gps")
        if not isinstance(gps, dict):
            return None
        coo = gps.get("coo")
        if not isinstance(coo, (list, tuple)) or len(coo) < 2:
            return None
        try:
            lat = float(coo[0])
            lon = float(coo[1])
        except (TypeError, ValueError):
            return None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        return lat, lon

    @property
    def battery_percent(self) -> int | None:
        return _as_int(_nested(self.dat, "bt", "p"))

    @property
    def battery_voltage(self) -> float | None:
        return _as_float(_nested(self.dat, "bt", "v"))

    @property
    def battery_temperature(self) -> float | None:
        return _as_float(_nested(self.dat, "bt", "t"))

    @property
    def battery_charging(self) -> bool | None:
        value = _as_int(_nested(self.dat, "bt", "c"))
        return None if value is None else bool(value)

    @property
    def rssi(self) -> int | None:
        return _as_int(self.dat.get("rsi"))

    @property
    def connection(self) -> str | None:
        value = self.dat.get("conn")
        return str(value) if value is not None else None

    @property
    def zone(self) -> int | None:
        """Return the current Fleet zone without transient MQTT drop-outs.

        Prefer the zone in the latest telemetry packet. If that packet omits
        ``dat.cut.z`` during the same active mowing/zoning run, fall back to the
        most recently reported zone from that run and map.
        """
        reported_zone = _reported_zone(self.dat)
        if reported_zone is not None:
            return reported_zone
        if (
            self._zone_context_active
            and self._last_reported_zone_map_id == self.map_id
        ):
            return self._last_reported_zone
        return None

    @property
    def zone_source(self) -> str | None:
        """Return whether ``zone`` comes from current or retained telemetry."""
        if _reported_zone(self.dat) is not None:
            return "telemetry"
        if (
            self._zone_context_active
            and self._last_reported_zone is not None
            and self._last_reported_zone_map_id == self.map_id
        ):
            return "last_reported"
        return None

    @property
    def firmware(self) -> str | None:
        value = self.dat.get("fw")
        return str(value) if value is not None else None

    @property
    def status_id(self) -> int | None:
        return _as_int(self.dat.get("ls"))

    @property
    def error_id(self) -> int | None:
        return _as_int(self.dat.get("le"))

    @property
    def rain(self) -> bool | None:
        value = _as_int(_nested(self.dat, "rain", "s"))
        return None if value is None else bool(value)

    @property
    def rtk_ok(self) -> bool | None:
        rtk = self.dat.get("rtk")
        if not isinstance(rtk, dict):
            return None
        values: list[int] = []
        for section_name in ("network", "gps", "imu"):
            section = rtk.get(section_name)
            if not isinstance(section, dict):
                continue
            for key in ("status", "error"):
                if key in section:
                    val = _as_int(section.get(key))
                    if val is not None:
                        values.append(val)
        if not values:
            return None
        return all(value == 0 for value in values)

    @property
    def last_update(self) -> datetime | None:
        value = self.dat.get("tm")
        if not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            return None

    @property
    def status(self) -> str:
        return status_text(self.status_id)


def status_text(status_id: int | None) -> str:
    """Return a stable text state for a Fleet status id."""
    return {
        0: "idle",
        1: "docked",
        2: "starting",
        3: "starting",
        4: "returning",
        5: "returning",
        6: "returning",
        7: "mowing",
        8: "error",
        9: "error",
        10: "error",
        11: "error",
        12: "mowing",
        13: "escaped_digital_fence",
        30: "returning",
        31: "zoning",
        32: "edge_cut",
        33: "starting",
        34: "paused",
        103: "searching_for_zone",
        104: "returning",
    }.get(status_id, "unknown")


def iter_coverage_rings(nodes: list[dict[str, Any]]):
    """Yield all valid coverage rings recursively."""
    for node in nodes:
        if not isinstance(node, dict):
            continue
        points = node.get("points")
        if isinstance(points, list) and len(points) >= 3:
            ring: list[tuple[float, float]] = []
            for point in points:
                if not isinstance(point, (list, tuple)) or len(point) < 2:
                    continue
                try:
                    ring.append((float(point[0]), float(point[1])))
                except (TypeError, ValueError):
                    continue
            if len(ring) >= 3:
                yield ring
        children = node.get("children")
        if isinstance(children, list):
            yield from iter_coverage_rings(children)


def _reported_zone(dat: dict[str, Any]) -> int | None:
    """Return a zone explicitly present in protocol-1 Fleet telemetry."""
    return _as_int(_nested(dat, "cut", "z"))


def _nested(data: dict[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
