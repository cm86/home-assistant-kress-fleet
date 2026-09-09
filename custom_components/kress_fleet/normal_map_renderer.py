# SPDX-License-Identifier: GPL-3.0-only

"""Small SVG renderer for Kress Mission RTK map geometry."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from html import escape
from math import cos, hypot, radians
from typing import Any

SVG_WIDTH = 1100
SVG_HEIGHT = 760
SVG_HEADER = 96
SVG_PADDING = 34
TRAIL_MAX_GAP = timedelta(minutes=5)
TRAIL_MAX_SEGMENT_DISTANCE_M = 35.0
TRAIL_MIN_POINT_DISTANCE_M = 0.25
TRAIL_MAP_MARGIN_M = 12.0
DEFAULT_CUTTING_WIDTH_M = 0.20
MOWED_SWATH_MIN_WIDTH_PX = 3.0
MOWED_SWATH_MAX_WIDTH_PX = 32.0

# Known Kress RTK cutting widths. Unknown models use the conservative 20 cm default.
CUTTING_WIDTH_BY_MODEL_M = {
    "KR171E": 0.20,
    "KR172E": 0.20,
    "KR173E": 0.20,
    "KR174E": 0.22,
    "KR230E": 0.22,
}


def _nested(value: Any, *keys: str, default: Any = None) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return default if current is None else current


def _pair(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None


def _normalize_model(value: Any) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def normal_cutting_width_m(device: Any) -> float:
    """Return the known cutting width for a normal Kress mower."""
    normalized = _normalize_model(getattr(device, "model", None))
    for model, width in CUTTING_WIDTH_BY_MODEL_M.items():
        if model in normalized:
            return width
    return DEFAULT_CUTTING_WIDTH_M


def _contour_points(contour: dict[str, Any]) -> list[tuple[float, float]]:
    points = contour.get("points") or []
    return [pair for pair in (_pair(point) for point in points) if pair is not None]


def _is_mowing_zone(zone: dict[str, Any]) -> bool:
    """Return True for zones that carry Kress cutting metadata.

    Kress uses map zones without cutting metadata as drive-through corridors.
    Those are the yellow/orange paths shown by the app and Fleet renderer.
    """
    metadata = zone.get("metadata") or {}
    if not isinstance(metadata, dict):
        return False
    return any(key in metadata for key in ("cut_type", "cut_direction"))


def _iter_contours(
    map_data: dict[str, Any],
) -> Iterable[tuple[str, dict[str, Any]]]:
    for boundary in _nested(map_data, "layers", "boundaries", default=[]) or []:
        if not isinstance(boundary, dict):
            continue
        for zone in boundary.get("zones") or []:
            if not isinstance(zone, dict):
                continue
            layer = "zone" if _is_mowing_zone(zone) else "path"
            for contour in zone.get("contours") or []:
                if isinstance(contour, dict):
                    yield layer, contour

    for exclusion in _nested(map_data, "layers", "exclusions", default=[]) or []:
        if not isinstance(exclusion, dict):
            continue
        for contour in exclusion.get("contours") or []:
            if isinstance(contour, dict):
                yield "exclusion", contour


def _all_points(
    map_data: dict[str, Any], robot_position: tuple[float, float] | None
) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for _, contour in _iter_contours(map_data):
        points.extend(_contour_points(contour))
        for child in contour.get("children") or []:
            if isinstance(child, dict):
                points.extend(_contour_points(child))

    for marker in _nested(map_data, "layers", "markers", default=[]) or []:
        if not isinstance(marker, dict):
            continue
        pair = _pair(
            [
                _nested(marker, "record", "latitude"),
                _nested(marker, "record", "longitude"),
            ]
        )
        if pair is not None:
            points.append(pair)

    if robot_position is not None:
        points.append(robot_position)
    return points


def _projector(points: list[tuple[float, float]]):
    lats = [point[0] for point in points]
    lons = [point[1] for point in points]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    mean_lat = (min_lat + max_lat) / 2
    lon_scale = max(cos(radians(mean_lat)), 0.1)
    width_m = max((max_lon - min_lon) * 111_320 * lon_scale, 1.0)
    height_m = max((max_lat - min_lat) * 110_540, 1.0)

    pad_x = max(width_m * 0.06, 1.0)
    pad_y = max(height_m * 0.06, 1.0)
    width_m += pad_x * 2
    height_m += pad_y * 2

    map_width = SVG_WIDTH - SVG_PADDING * 2
    map_height = SVG_HEIGHT - SVG_HEADER - SVG_PADDING
    scale = min(map_width / width_m, map_height / height_m)
    drawn_width = width_m * scale
    drawn_height = height_m * scale
    offset_x = SVG_PADDING + (map_width - drawn_width) / 2
    offset_y = SVG_HEADER + (map_height - drawn_height) / 2

    min_lon_padded = min_lon - pad_x / (111_320 * lon_scale)
    max_lat_padded = max_lat + pad_y / 110_540

    def project(point: tuple[float, float]) -> tuple[float, float]:
        lat, lon = point
        x_m = (lon - min_lon_padded) * 111_320 * lon_scale
        y_m = (max_lat_padded - lat) * 110_540
        return offset_x + x_m * scale, offset_y + y_m * scale

    return project, scale

def _path(points: list[tuple[float, float]], project) -> str:
    if not points:
        return ""
    projected = [project(point) for point in points]
    first_x, first_y = projected[0]
    parts = [f"M {first_x:.2f} {first_y:.2f}"]
    parts.extend(f"L {x:.2f} {y:.2f}" for x, y in projected[1:])
    parts.append("Z")
    return " ".join(parts)


def _open_path(points: list[tuple[float, float]], project) -> str:
    if not points:
        return ""
    projected = [project(point) for point in points]
    first_x, first_y = projected[0]
    parts = [f"M {first_x:.2f} {first_y:.2f}"]
    parts.extend(f"L {x:.2f} {y:.2f}" for x, y in projected[1:])
    return " ".join(parts)


def _compound_zone_path(contour: dict[str, Any], project) -> str:
    parts: list[str] = []
    outer = _contour_points(contour)
    if outer:
        parts.append(_path(outer, project))
    for child in contour.get("children") or []:
        if isinstance(child, dict):
            child_points = _contour_points(child)
            if child_points:
                parts.append(_path(child_points, project))
    return " ".join(part for part in parts if part)


def _mowed_clip_def(map_data: dict[str, Any], project) -> str:
    clip_paths: list[str] = []
    for layer, contour in _iter_contours(map_data):
        if layer != "zone":
            continue
        path = _compound_zone_path(contour, project)
        if path:
            clip_paths.append(f'<path d="{path}" fill-rule="evenodd"/>')
    if not clip_paths:
        return ""
    return '<clipPath id="mowed-clip">' + "".join(clip_paths) + "</clipPath>"


def _distance_m(first: tuple[float, float], second: tuple[float, float]) -> float:
    mean_lat = (first[0] + second[0]) / 2
    lon_scale = max(cos(radians(mean_lat)), 0.1)
    x_m = (second[1] - first[1]) * 111_320 * lon_scale
    y_m = (second[0] - first[0]) * 110_540
    return hypot(x_m, y_m)


def _coordinate_bounds(
    points: list[tuple[float, float]],
) -> tuple[float, float, float, float] | None:
    if not points:
        return None
    lats = [point[0] for point in points]
    lons = [point[1] for point in points]
    return min(lats), max(lats), min(lons), max(lons)


def _point_in_bounds(
    point: tuple[float, float],
    bounds: tuple[float, float, float, float],
    margin_m: float,
) -> bool:
    min_lat, max_lat, min_lon, max_lon = bounds
    mean_lat = (min_lat + max_lat) / 2
    lon_scale = max(cos(radians(mean_lat)), 0.1)
    lat_margin = margin_m / 110_540
    lon_margin = margin_m / (111_320 * lon_scale)
    lat, lon = point
    return (
        min_lat - lat_margin <= lat <= max_lat + lat_margin
        and min_lon - lon_margin <= lon <= max_lon + lon_margin
    )


def _trail_segments(
    map_data: dict[str, Any],
    trail: list[tuple[datetime, float, float]] | None,
) -> list[list[tuple[datetime, float, float]]]:
    if not trail:
        return []

    bounds = _coordinate_bounds(_all_points(map_data, None))
    segments: list[list[tuple[datetime, float, float]]] = []
    current: list[tuple[datetime, float, float]] = []
    previous_time: datetime | None = None
    previous_point: tuple[float, float] | None = None

    def flush() -> None:
        if len(current) > 1:
            segments.append(list(current))
        current.clear()

    for timestamp, latitude, longitude in trail:
        point = (latitude, longitude)
        if bounds is not None and not _point_in_bounds(point, bounds, TRAIL_MAP_MARGIN_M):
            flush()
            previous_time = None
            previous_point = None
            continue
        if previous_point is not None:
            distance = _distance_m(previous_point, point)
            if distance < TRAIL_MIN_POINT_DISTANCE_M:
                continue
            if (
                previous_time is not None
                and timestamp - previous_time > TRAIL_MAX_GAP
            ) or distance > TRAIL_MAX_SEGMENT_DISTANCE_M:
                flush()
        current.append((timestamp, latitude, longitude))
        previous_time = timestamp
        previous_point = point

    flush()
    return segments


def _mowed_segments_svg(
    segments: list[list[tuple[datetime, float, float]]],
    project,
    swath_width_px: float,
) -> list[str]:
    paths: list[str] = []
    for segment in segments:
        points = [(latitude, longitude) for _, latitude, longitude in segment]
        path = _open_path(points, project)
        if path:
            paths.append(
                f'<path class="mowed" d="{path}" stroke-width="{swath_width_px:.2f}"/>'
            )
    if not paths:
        return []
    return ['<g class="mowed-area" clip-path="url(#mowed-clip)">', *paths, "</g>"]


def _placeholder(message: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{SVG_WIDTH}" '
        f'height="{SVG_HEIGHT}" viewBox="0 0 {SVG_WIDTH} {SVG_HEIGHT}">'
        '<rect width="100%" height="100%" fill="#f4faf6"/>'
        '<rect x="0" y="0" width="100%" height="96" fill="#1f2923"/>'
        '<text x="30" y="58" font-family="sans-serif" font-size="25" '
        'font-weight="700" fill="white">Kress / Mission Live-Karte</text>'
        f'<text x="50%" y="52%" fill="#66736a" font-size="22" '
        f'font-family="sans-serif" text-anchor="middle">{escape(message)}</text>'
        '</svg>'
    )


def normal_map_diagnostics(map_data: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(map_data, dict):
        return {}
    zones = 0
    mowing_zones = 0
    path_zones = 0
    for boundary in _nested(map_data, "layers", "boundaries", default=[]) or []:
        if not isinstance(boundary, dict):
            continue
        for zone in boundary.get("zones") or []:
            if not isinstance(zone, dict):
                continue
            zones += 1
            if _is_mowing_zone(zone):
                mowing_zones += 1
            else:
                path_zones += 1
    exclusions = _nested(map_data, "layers", "exclusions", default=[]) or []
    markers = _nested(map_data, "layers", "markers", default=[]) or []
    layers = map_data.get("layers")
    return {
        "map_status": map_data.get("status"),
        "map_type": map_data.get("type"),
        "active": map_data.get("active"),
        "rtk_provider": map_data.get("rtk_provider"),
        "zone_count": zones,
        "mowing_zone_count": mowing_zones,
        "path_zone_count": path_zones,
        "exclusion_count": len(exclusions) if isinstance(exclusions, list) else 0,
        "marker_count": len(markers) if isinstance(markers, list) else 0,
        "map_layers": sorted(layers) if isinstance(layers, dict) else [],
    }


def _fleet_mower_marker_svg(x: float, y: float) -> str:
    """Return the same black/white mower marker style used by Fleet."""
    radius = 15
    left = x - 14
    top = y - 14
    return "".join(
        [
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{radius + 2}" '
            'fill="#ffffff" fill-opacity="0.96"/>',
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{radius}" '
            'fill="#111614" stroke="#28322d" stroke-width="1.3"/>',
            f'<g transform="translate({left:.2f} {top:.2f})">',
            '<rect x="5.2" y="9.1" width="17.6" height="10.2" rx="4.8" fill="#ffffff"/>',
            '<rect x="3.8" y="10.7" width="2.4" height="3.2" rx="1.1" fill="#ffffff"/>',
            '<rect x="3.8" y="15.0" width="2.4" height="3.2" rx="1.1" fill="#ffffff"/>',
            '<rect x="21.8" y="10.7" width="2.4" height="3.2" rx="1.1" fill="#ffffff"/>',
            '<rect x="21.8" y="15.0" width="2.4" height="3.2" rx="1.1" fill="#ffffff"/>',
            '<path d="M8.2 12.2h11.6M8.2 16.2h7.8" stroke="#111614" '
            'stroke-width="1.35" stroke-linecap="round"/>',
            '<circle cx="18.4" cy="16.2" r="1.55" fill="#111614"/>',
            '</g>',
        ]
    )


def _station_marker_svg(x: float, y: float) -> str:
    """Return a subtle Fleet-palette charging-station marker."""
    return (
        f'<g transform="translate({x:.2f} {y:.2f})">'
        '<circle r="14" fill="#1f2923" stroke="#ffffff" stroke-width="3"/>'
        '<path d="M3 -10 L-6 2 H0 L-3 11 L8 -4 H2 Z" fill="#ffffff"/>'
        '</g>'
    )


def render_normal_rtk_map(
    map_data: dict[str, Any] | None,
    robot_position: tuple[float, float] | None,
    mowing_trail: list[tuple[datetime, float, float]] | None = None,
    cutting_width_m: float = DEFAULT_CUTTING_WIDTH_M,
    *,
    mower_name: str = "Kress Mission",
    status_text: str | None = None,
    battery_percent: int | float | None = None,
) -> str:
    """Render Kress/Mission RTK geometry in the Fleet Live-Map visual style."""
    if not isinstance(map_data, dict):
        return _placeholder("Keine RTK-Karte vom Kress Cloud API")

    points = _all_points(map_data, robot_position)
    if not points:
        return _placeholder("RTK-Karte enthaelt keine Geometrie")

    project, meters_to_pixels = _projector(points)
    swath_width_px = max(
        MOWED_SWATH_MIN_WIDTH_PX,
        min(MOWED_SWATH_MAX_WIDTH_PX, cutting_width_m * meters_to_pixels),
    )
    trail_segments = _trail_segments(map_data, mowing_trail)
    clip_def = _mowed_clip_def(map_data, project)

    parts: list[str] = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{SVG_WIDTH}" '
            f'height="{SVG_HEIGHT}" viewBox="0 0 {SVG_WIDTH} {SVG_HEIGHT}" role="img">'
        ),
        '<rect width="100%" height="100%" fill="#f4faf6"/>',
        '<rect x="0" y="0" width="100%" height="96" fill="#1f2923"/>',
        (
            '<text x="30" y="35" font-family="sans-serif" font-size="25" '
            f'font-weight="700" fill="white">{escape(mower_name)}</text>'
        ),
    ]

    status_chunks: list[str] = []
    if battery_percent is not None:
        status_chunks.append(f"Akku {battery_percent:g}%")
    if status_text:
        status_chunks.append(status_text)
    status_line = " · ".join(status_chunks) or "Kress / Mission RTK"
    parts.append(
        '<text x="30" y="65" font-family="sans-serif" font-size="16" '
        f'fill="#dce7df">{escape(status_line)}</text>'
    )
    parts.append(
        '<text x="30" y="87" font-family="sans-serif" font-size="13" '
        f'fill="#aac0b1">Coverage: Heute · {len(mowing_trail or [])} Punkte</text>'
    )

    if clip_def:
        parts.append(f"<defs>{clip_def}</defs>")

    # Base work map: mowing zones green, drive-through corridors yellow/orange,
    # exactly matching the Fleet renderer palette.
    for layer, contour in _iter_contours(map_data):
        if layer not in {"zone", "path"}:
            continue
        path = _compound_zone_path(contour, project)
        if not path:
            continue
        if layer == "zone":
            parts.append(
                f'<path d="{path}" fill="#9BE2B9" fill-opacity="0.93" '
                'stroke="#159657" stroke-width="2.4" fill-rule="evenodd"/>'
            )
        else:
            parts.append(
                f'<path d="{path}" fill="#FFB33B" fill-opacity="0.70" '
                'stroke="#E58A00" stroke-width="1.5" fill-rule="evenodd"/>'
            )

    # Today's local RTK coverage: same darker green used by Fleet coverage.
    if clip_def:
        for segment in trail_segments:
            segment_points = [
                (latitude, longitude) for _, latitude, longitude in segment
            ]
            path = _open_path(segment_points, project)
            if path:
                parts.append(
                    '<g clip-path="url(#mowed-clip)">'
                    f'<path d="{path}" fill="none" stroke="#08AA57" '
                    f'stroke-opacity="0.88" stroke-width="{swath_width_px:.2f}" '
                    'stroke-linecap="round" stroke-linejoin="round"/>'
                    '</g>'
                )

    # Exclusions/No-Go areas: same red palette as Fleet.
    for layer, contour in _iter_contours(map_data):
        if layer != "exclusion":
            continue
        path = _compound_zone_path(contour, project)
        if path:
            parts.append(
                f'<path d="{path}" fill="#FF3344" fill-opacity="0.90" '
                'stroke="#D9152B" stroke-width="2.0" fill-rule="evenodd"/>'
            )

    for marker in _nested(map_data, "layers", "markers", default=[]) or []:
        if not isinstance(marker, dict):
            continue
        pair = _pair(
            [
                _nested(marker, "record", "latitude"),
                _nested(marker, "record", "longitude"),
            ]
        )
        if pair is not None:
            x, y = project(pair)
            parts.append(_station_marker_svg(x, y))

    if robot_position is not None:
        x, y = project(robot_position)
        parts.append(_fleet_mower_marker_svg(x, y))

    # Same small legend layout/palette as Fleet.
    legend_x = 44
    legend_y = SVG_HEIGHT - 67
    parts.extend(
        [
            f'<rect x="{legend_x - 10}" y="{legend_y - 20}" width="445" height="42" '
            'rx="7" fill="white" fill-opacity="0.82"/>',
            f'<rect x="{legend_x}" y="{legend_y - 9}" width="20" height="12" '
            'fill="#9BE2B9" stroke="#159657"/>',
            f'<text x="{legend_x + 27}" y="{legend_y + 2}" font-family="sans-serif" '
            'font-size="12" fill="#344039">Nicht gemäht</text>',
            f'<rect x="{legend_x + 140}" y="{legend_y - 9}" width="20" height="12" '
            'fill="#FF3344" stroke="#D9152B"/>',
            f'<text x="{legend_x + 167}" y="{legend_y + 2}" font-family="sans-serif" '
            'font-size="12" fill="#344039">No-Go aktiv</text>',
            f'<rect x="{legend_x + 263}" y="{legend_y - 9}" width="20" height="12" '
            'fill="#08AA57" fill-opacity="0.88" stroke="#078648"/>',
            f'<text x="{legend_x + 290}" y="{legend_y + 2}" font-family="sans-serif" '
            'font-size="12" fill="#344039">Gemäht</text>',
        ]
    )

    parts.append(
        f'<text x="{SVG_WIDTH - 25}" y="{SVG_HEIGHT - 18}" text-anchor="end" '
        'font-family="sans-serif" font-size="13" fill="#69746d">'
        f'Kress / Mission · RTK · {len(mowing_trail or [])} Coverage-Punkte</text>'
    )
    parts.append("</svg>")
    return "".join(parts)
