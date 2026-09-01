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


# Stable, language-neutral mower error states. The numeric Fleet/Worx/Kress
# protocol code remains exposed separately by ``error_id`` for automations and
# diagnostics; Home Assistant translates these keys for human-facing UI.
#
# The known protocol table is community reverse-engineered and currently
# covers the classic 0-20 range plus the Vision/RTK 100-120 range.
ERROR_CODE_STATES: dict[int, str] = {
    0: "no_error",
    1: "trapped",
    2: "lifted",
    3: "wire_missing",
    4: "outside_boundary",
    5: "rain_delay",
    6: "close_door_to_cut_grass",
    7: "close_door_to_go_home",
    8: "blade_motor_fault",
    9: "wheel_motor_fault",
    10: "trapped_timeout_fault",
    11: "upside_down",
    12: "battery_low",
    13: "wire_reversed",
    14: "charge_error",
    15: "home_search_timeout",
    16: "wifi_locked",
    17: "battery_over_temperature",
    18: "dummy_model",
    19: "battery_trunk_open_timeout",
    20: "wire_signal_out_of_sync",
    100: "charging_station_docking_error",
    101: "hbi_error",
    102: "ota_upgrade_error",
    103: "map_error",
    104: "excessive_slope",
    105: "unreachable_zone",
    106: "unreachable_charging_station",
    107: "calibration_needed",
    108: "insufficient_sensor_data",
    109: "training_start_disallowed",
    110: "camera_error",
    111: "lawn_exploration_required",
    112: "mapping_exploration_failed",
    113: "rfid_reader_error",
    114: "headlight_error",
    115: "missing_charging_station",
    116: "blade_height_adjustment_blocked",
    117: "unsupported_blade_height",
    118: "manual_firmware_upgrade_required",
    119: "area_limit_exceeded",
    120: "charging_station_undocking_error",
}
ERROR_STATE_OPTIONS: tuple[str, ...] = tuple(
    dict.fromkeys((*ERROR_CODE_STATES.values(), "unknown_error"))
)


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

    # Cached, privacy-safe Fleet zone catalog. The expensive map geometry
    # parsing is done in Home Assistant's executor by the coordinator, not in
    # SelectEntity properties on the main event loop.
    zone_id_name_map: dict[int, str] = field(default_factory=dict)
    zone_catalog_map_id: str | None = None
    target_zone_id: int | None = None

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
            incoming_map_id = str(rtk_cfg["map"])
            if incoming_map_id != self.map_id:
                self.map_id = incoming_map_id
                self.zone_id_name_map = {}
                self.zone_catalog_map_id = None
                self.target_zone_id = None

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

            reported_zone, _reported_source = _reported_zone(dat)
            if reported_zone is not None:
                self._last_reported_zone = reported_zone
                self._last_reported_zone_map_id = self.map_id

            self._zone_context_active = zone_context_active
            self.dat = dat
            self.online = True

    @property
    def coordinates(self) -> tuple[float, float] | None:
        """Return the best current coordinates as latitude, longitude.

        RTK-capable Kress mowers expose their precise live position as
        ``dat.rtk.pos``. Prefer that over the cellular-module GPS fallback so
        live-map placement and position-based zone resolution use Fleet's precise
        RTK source whenever it is available.
        """
        rtk_position = _coordinate_pair(_nested(self.dat, "rtk", "pos"))
        if rtk_position is not None:
            return rtk_position

        return _coordinate_pair(_nested(self.dat, "modules", "4G", "gps", "coo"))

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

        Prefer a zone that is unambiguously present in the latest telemetry.
        Fleet protocol variants can expose it directly as ``dat.cut.z`` or, on
        RTK task payloads, as the single task-zone with live route counters. If
        a later packet temporarily omits both forms during the same active run,
        retain the last unambiguous zone from that run and map.
        """
        reported_zone, _source = _reported_zone(self.dat)
        if reported_zone is not None:
            return reported_zone
        if (
            self._zone_context_active
            and self._last_reported_zone_map_id == self.map_id
            and self._last_reported_zone is not None
        ):
            return self._last_reported_zone

        # Vision/RTK protocol-1 telemetry commonly leaves the legacy/current
        # zone field empty. When the mower is in an active mowing context,
        # resolve its precise RTK position against Fleet's structured map zone
        # contours. This is geometry-based, not an ID guess: if the position is
        # outside all configured zone polygons, keep the zone unknown.
        if self._zone_context_active:
            return _rtk_map_zone_at_position(self.map_detail, self.coordinates)
        return None

    @property
    def zone_source(self) -> str | None:
        """Return the source used for the current zone."""
        reported_zone, source = _reported_zone(self.dat)
        if reported_zone is not None:
            return source
        if (
            self._zone_context_active
            and self._last_reported_zone is not None
            and self._last_reported_zone_map_id == self.map_id
        ):
            return "last_reported"
        if (
            self._zone_context_active
            and _rtk_map_zone_at_position(self.map_detail, self.coordinates) is not None
        ):
            return "rtk_map"
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


def error_text(error_id: int | None) -> str | None:
    """Return a stable, translatable state key for a mower error code."""
    if error_id is None:
        return None
    return ERROR_CODE_STATES.get(error_id, "unknown_error")


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


def _coordinate_pair(value: Any) -> tuple[float, float] | None:
    """Normalize a coordinate pair to ``(latitude, longitude)``."""
    if isinstance(value, dict):
        latitude = value.get("latitude", value.get("lat"))
        longitude = value.get("longitude", value.get("lon", value.get("lng")))
    elif isinstance(value, (list, tuple)) and len(value) >= 2:
        latitude, longitude = value[0], value[1]
    else:
        return None

    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def _map_layers(map_detail: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the structured Fleet map layers when present."""
    if not isinstance(map_detail, dict):
        return None
    layers = map_detail.get("layers")
    return layers if isinstance(layers, dict) else None


def _contour_points(contour: Any) -> list[tuple[float, float]]:
    """Return normalized points from one Fleet RTK zone contour."""
    if not isinstance(contour, dict):
        return []
    points = contour.get("points")
    if not isinstance(points, list):
        return []
    return [pair for point in points if (pair := _coordinate_pair(point)) is not None]


def _point_in_ring(
    point: tuple[float, float], ring: list[tuple[float, float]]
) -> bool:
    """Return whether a latitude/longitude point is inside one polygon ring."""
    if len(ring) < 3:
        return False

    x, y = point[1], point[0]
    inside = False
    x1, y1 = ring[-1][1], ring[-1][0]
    for latitude, longitude in ring:
        x2, y2 = longitude, latitude
        if (y1 > y) != (y2 > y):
            x_intersection = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < x_intersection:
                inside = not inside
        x1, y1 = x2, y2
    return inside


def _point_in_contour(point: tuple[float, float], contour: Any) -> bool:
    """Return whether a point is in a zone contour and outside exclusion holes."""
    if not _point_in_ring(point, _contour_points(contour)):
        return False

    if not isinstance(contour, dict):
        return False
    children = contour.get("children")
    if not isinstance(children, list):
        return True
    for child in children:
        if _point_in_ring(point, _contour_points(child)):
            return False
    return True


def _rtk_map_zone_at_position(
    map_detail: dict[str, Any] | None,
    position: tuple[float, float] | None,
) -> int | None:
    """Return the Fleet RTK zone ID containing ``position``.

    Current Fleet map responses expose mowing regions under
    ``layers.boundaries[].zones[].contours[]``. The resolver is deliberately
    strict: only an explicit zone ID whose polygon contains the live mower
    position is returned.
    """
    if position is None:
        return None
    layers = _map_layers(map_detail)
    if layers is None:
        return None
    boundaries = layers.get("boundaries")
    if not isinstance(boundaries, list):
        return None

    for boundary in boundaries:
        if not isinstance(boundary, dict):
            continue
        zones = boundary.get("zones")
        if not isinstance(zones, list):
            continue
        for zone in zones:
            if not isinstance(zone, dict):
                continue
            zone_id = _as_int(zone.get("id"))
            if zone_id is None:
                continue
            contours = zone.get("contours")
            if not isinstance(contours, list):
                continue
            if any(_point_in_contour(position, contour) for contour in contours):
                return zone_id
    return None


def _reported_zone(dat: dict[str, Any]) -> tuple[int | None, str | None]:
    """Return an unambiguous active zone and its Fleet telemetry source.

    Known Fleet payload variants currently use either a direct ``dat.cut.z``
    value or RTK task telemetry under ``dat.cut.tsk[*].z[*]``. In observed RTK
    task payloads the zone currently being worked is the only task-zone with
    non-zero live route counters (``rtg`` / ``rtn``).

    The task fallback is intentionally conservative. If no task-zone or more
    than one task-zone looks active, return no zone instead of guessing.
    """
    direct = _as_int(_nested(dat, "cut", "z"))
    if direct is not None:
        return direct, "telemetry"

    cut = dat.get("cut")
    if not isinstance(cut, dict):
        return None, None
    tasks = cut.get("tsk")
    if not isinstance(tasks, list):
        return None, None

    active_zone_ids: set[int] = set()
    for task in tasks:
        if not isinstance(task, dict):
            continue
        zones = task.get("z")
        if not isinstance(zones, list):
            continue
        for zone in zones:
            if not isinstance(zone, dict):
                continue
            zone_id = _as_int(zone.get("id"))
            if zone_id is None:
                continue
            rtg = _as_float(zone.get("rtg")) or 0.0
            rtn = _as_float(zone.get("rtn")) or 0.0
            if rtg > 0 or rtn > 0:
                active_zone_ids.add(zone_id)

    if len(active_zone_ids) == 1:
        return next(iter(active_zone_ids)), "task"
    return None, None


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
