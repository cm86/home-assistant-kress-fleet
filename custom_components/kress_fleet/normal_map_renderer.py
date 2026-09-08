# SPDX-License-Identifier: GPL-3.0-only

"""Small SVG renderer for Kress Mission RTK map geometry."""

from __future__ import annotations

from collections.abc import Iterable
from html import escape
from math import cos, radians
from typing import Any

SVG_WIDTH = 900
SVG_HEIGHT = 620
SVG_PADDING = 48


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


def _contour_points(contour: dict[str, Any]) -> list[tuple[float, float]]:
    points = contour.get("points") or []
    return [pair for pair in (_pair(point) for point in points) if pair is not None]


def _iter_contours(
    map_data: dict[str, Any],
) -> Iterable[tuple[str, dict[str, Any]]]:
    for boundary in _nested(map_data, "layers", "boundaries", default=[]) or []:
        if not isinstance(boundary, dict):
            continue
        for zone in boundary.get("zones") or []:
            if not isinstance(zone, dict):
                continue
            for contour in zone.get("contours") or []:
                if isinstance(contour, dict):
                    yield "zone", contour

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
    scale = min(
        (SVG_WIDTH - SVG_PADDING * 2) / width_m,
        (SVG_HEIGHT - SVG_PADDING * 2) / height_m,
    )
    drawn_width = width_m * scale
    drawn_height = height_m * scale
    offset_x = (SVG_WIDTH - drawn_width) / 2
    offset_y = (SVG_HEIGHT - drawn_height) / 2

    def project(point: tuple[float, float]) -> tuple[float, float]:
        lat, lon = point
        x_m = (lon - min_lon) * 111_320 * lon_scale
        y_m = (max_lat - lat) * 110_540
        return offset_x + x_m * scale, offset_y + y_m * scale

    return project


def _path(points: list[tuple[float, float]], project) -> str:
    if not points:
        return ""
    projected = [project(point) for point in points]
    first_x, first_y = projected[0]
    parts = [f"M {first_x:.2f} {first_y:.2f}"]
    parts.extend(f"L {x:.2f} {y:.2f}" for x, y in projected[1:])
    parts.append("Z")
    return " ".join(parts)


def _placeholder(message: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{SVG_WIDTH}" '
        f'height="{SVG_HEIGHT}" viewBox="0 0 {SVG_WIDTH} {SVG_HEIGHT}">'
        "<rect width='100%' height='100%' fill='#101412'/>"
        f"<text x='50%' y='50%' fill='#f8faf4' font-size='28' "
        f"font-family='Arial,sans-serif' text-anchor='middle'>{escape(message)}</text>"
        "</svg>"
    )


def normal_map_diagnostics(map_data: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(map_data, dict):
        return {}
    zones = 0
    for boundary in _nested(map_data, "layers", "boundaries", default=[]) or []:
        if isinstance(boundary, dict):
            zones += sum(isinstance(zone, dict) for zone in boundary.get("zones") or [])
    exclusions = _nested(map_data, "layers", "exclusions", default=[]) or []
    markers = _nested(map_data, "layers", "markers", default=[]) or []
    return {
        "map_status": map_data.get("status"),
        "map_type": map_data.get("type"),
        "active": map_data.get("active"),
        "rtk_provider": map_data.get("rtk_provider"),
        "zone_count": zones,
        "exclusion_count": len(exclusions) if isinstance(exclusions, list) else 0,
        "marker_count": len(markers) if isinstance(markers, list) else 0,
    }


def render_normal_rtk_map(
    map_data: dict[str, Any] | None,
    robot_position: tuple[float, float] | None,
) -> str:
    """Render the private Kress/Worx RTK geometry as an SVG."""
    if not isinstance(map_data, dict):
        return _placeholder("Keine RTK-Karte vom Kress Cloud API")

    points = _all_points(map_data, robot_position)
    if not points:
        return _placeholder("RTK-Karte enthaelt keine Geometrie")

    project = _projector(points)
    body: list[str] = []

    for layer, contour in _iter_contours(map_data):
        outer = _contour_points(contour)
        if not outer:
            continue
        css_class = "zone" if layer == "zone" else "exclusion"
        body.append(f'<path class="{css_class}" d="{_path(outer, project)}"/>')
        if layer == "zone":
            for child in contour.get("children") or []:
                if isinstance(child, dict):
                    child_points = _contour_points(child)
                    if child_points:
                        body.append(
                            f'<path class="hole" d="{_path(child_points, project)}"/>'
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
            body.append(
                f'<g class="station" transform="translate({x:.2f} {y:.2f})">'
                '<circle r="16"/><path d="M 3 -11 L -6 2 H 0 L -3 12 L 8 -4 H 2 Z"/>'
                "</g>"
            )

    if robot_position is not None:
        x, y = project(robot_position)
        body.append(
            f'<g class="robot" transform="translate({x:.2f} {y:.2f})">'
            '<circle class="halo" r="18"/><circle class="body" r="11"/>'
            '<circle class="dot" r="3"/></g>'
        )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{SVG_WIDTH}" '
        f'height="{SVG_HEIGHT}" viewBox="0 0 {SVG_WIDTH} {SVG_HEIGHT}" role="img">'
        "<style>"
        "svg{background:#050607}.grid{stroke:#202624;stroke-width:1;opacity:.45}"
        ".zone{fill:#087a37;stroke:#27c267;stroke-width:4;stroke-linejoin:round}"
        ".hole{fill:#050607;stroke:#d5dae0;stroke-width:3}"
        ".exclusion{fill:#a85f2c;stroke:#e59052;stroke-width:4;opacity:.95}"
        ".station circle{fill:#70380f;stroke:#f6a15f;stroke-width:2}.station path{fill:#fff}"
        ".robot .halo{fill:#f47b20;opacity:.28}.robot .body{fill:#f47b20;stroke:#fff;stroke-width:2}"
        ".robot .dot{fill:#111}"
        "</style>"
        '<defs><pattern id="grid" width="48" height="48" patternUnits="userSpaceOnUse">'
        '<path class="grid" d="M 48 0 L 0 0 0 48"/></pattern></defs>'
        f'<rect width="{SVG_WIDTH}" height="{SVG_HEIGHT}" fill="url(#grid)"/>'
        f"{''.join(body)}"
        "</svg>"
    )
