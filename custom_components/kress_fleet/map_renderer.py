# SPDX-License-Identifier: GPL-3.0-only
# This file is part of kress_fleet, a modified work derived in part from
# MTrab/landroid_cloud and MTrab/pyworxcloud (GPL-3.0).
# Kress Fleet modifications began on 2026-08-21; see NOTICE and LICENSE.

"""Render Kress Fleet map geometry, coverage and live position as SVG."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
import json
import math
from statistics import median
from collections.abc import Mapping
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import FleetMower

WIDTH = 1100
HEIGHT = 760
HEADER = 96
MARGIN = 34


@dataclass(frozen=True, slots=True)
class MapShape:
    """One polygon-like geometry discovered in Fleet map detail JSON."""

    kind: str
    points: tuple[tuple[float, float], ...]
    source: str
    label: str | None = None
    zone_id: int | None = None
    enabled: bool | None = None


_NO_GO_HINTS = (
    "no-go",
    "no_go",
    "nogo",
    "no-mow",
    "no_mow",
    "nomow",
    "exclusion",
    "exclude",
    "excluded",
    "forbidden",
    "restricted",
    "restriction",
    "keepout",
    "keep_out",
    "obstacle",
    "island",
)
_BOUNDARY_HINTS = (
    "boundary",
    "boundaries",
    "border",
    "perimeter",
    "outline",
    "contour",
    "work_area",
    "working_area",
    "mowing_area",
    "workarea",
)
_ZONE_HINTS = (
    "work_zone",
    "working_zone",
    "mowing_zone",
    "cut_zone",
    "cutting_zone",
    "zone",
    "zones",
    "sector",
    "sectors",
    "subarea",
    "sub_area",
    "subareas",
    "sub_areas",
    # Fleet currently stores named mowing regions below fairly generic area
    # containers.  These are intentionally lower-priority than no-go/boundary
    # hints in _classify_context().
    "area",
    "areas",
    "region",
    "regions",
    "section",
    "sections",
    "partition",
    "partitions",
    "plot",
    "plots",
    "field",
    "fields",
)
_PATH_HINTS = (
    "route",
    "path",
    "corridor",
    "passage",
    "channel",
    "connection",
)
_SEMANTIC_KEYS = {
    "type",
    "kind",
    "name",
    "role",
    "category",
    "feature",
    "feature_type",
    "featuretype",
    "zone_type",
    "zonetype",
    "area_type",
    "areatype",
    "geometry_type",
    "geometrytype",
    "class",
}
_LABEL_KEYS = {
    "name",
    "label",
    "title",
    "display_name",
    "displayname",
    "zone_name",
    "zonename",
    "area_name",
    "areaname",
    "section_name",
    "sectionname",
    "sector_name",
    "sectorname",
    "alias",
}
_ZONE_ID_KEYS = (
    "zone_id",
    "zoneid",
    "zone_number",
    "zonenumber",
    "zone_no",
    "zoneno",
    "zone_index",
    "zoneindex",
    "area_id",
    "areaid",
    "area_no",
    "areano",
    "area_index",
    "areaindex",
    "sector_id",
    "sectorid",
    "section_id",
    "sectionid",
    "number",
    "nr",
    "index",
    "zone",
    "z",
    "id",
)

_NO_GO_ENABLED_KEYS = (
    "enabled",
    "enable",
    "active",
    "activated",
    "is_active",
    "isactive",
    "is_enabled",
    "isenabled",
    "selected",
    "is_selected",
    "isselected",
)
_NO_GO_DISABLED_KEYS = (
    "disabled",
    "disable",
    "inactive",
    "deactivated",
    "is_disabled",
    "isdisabled",
    "is_inactive",
    "isinactive",
)
_NO_GO_STATE_KEYS = ("status", "state")
_TRUE_STATE_WORDS = {"1", "true", "on", "yes", "active", "enabled", "enable", "activated"}
_FALSE_STATE_WORDS = {"0", "false", "off", "no", "inactive", "disabled", "disable", "deactivated"}
_STATUS_KEY_HINTS = ("enable", "disable", "active", "inactive", "status", "state", "switch", "select")


def render_mower_map(
    mower: FleetMower,
    peers: list[FleetMower] | None = None,
    *,
    timezone_name: str = "UTC",
    translations: Mapping[str, str] | None = None,
) -> bytes:
    """Return an SVG with Fleet work map below selected-period coverage."""
    peers = [peer for peer in (peers or []) if peer.uuid != mower.uuid]

    top_nodes = [node for node in mower.coverage if isinstance(node, dict)]
    rings_by_top = [_rings_for_node(node) for node in top_nodes]
    rings_by_top = [rings for rings in rings_by_top if rings]

    reference = _reference_point(mower)
    raw_map_shapes = extract_map_shapes(mower.map_detail, reference=reference)
    map_shapes = _promote_main_boundary(raw_map_shapes)

    points = [
        point
        for shape in map_shapes
        for point in shape.points
    ]
    points.extend(
        point for rings in rings_by_top for ring in rings for point in ring
    )
    if mower.coordinates:
        points.append(mower.coordinates)
    for peer in peers:
        if peer.coordinates:
            points.append(peer.coordinates)

    if points:
        center_lat = sum(point[0] for point in points) / len(points)
        center_lon = sum(point[1] for point in points) / len(points)
    else:
        center_lat = 0.0
        center_lon = 0.0

    cos_lat = max(0.01, math.cos(math.radians(center_lat)))

    def project(point: tuple[float, float]) -> tuple[float, float]:
        lat, lon = point
        return ((lon - center_lon) * cos_lat, lat - center_lat)

    projected = [project(point) for point in points]
    if projected:
        xs = [point[0] for point in projected]
        ys = [point[1] for point in projected]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
    else:
        min_x, max_x, min_y, max_y = -1.0, 1.0, -1.0, 1.0

    if max_x - min_x < 1e-8:
        min_x -= 5e-5
        max_x += 5e-5
    if max_y - min_y < 1e-8:
        min_y -= 5e-5
        max_y += 5e-5

    map_width = WIDTH - 2 * MARGIN
    map_height = HEIGHT - HEADER - MARGIN
    pad_x = (max_x - min_x) * 0.06
    pad_y = (max_y - min_y) * 0.06
    min_x -= pad_x
    max_x += pad_x
    min_y -= pad_y
    max_y += pad_y
    scale = min(map_width / (max_x - min_x), map_height / (max_y - min_y))

    used_w = (max_x - min_x) * scale
    used_h = (max_y - min_y) * scale
    offset_x = MARGIN + (map_width - used_w) / 2
    offset_y = HEADER + (map_height - used_h) / 2

    def screen(point: tuple[float, float]) -> tuple[float, float]:
        x, y = project(point)
        sx = offset_x + (x - min_x) * scale
        sy = offset_y + (max_y - y) * scale
        return sx, sy

    parts = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" '
            f'height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">'
        ),
        '<rect width="100%" height="100%" fill="#f4faf6"/>',
        '<rect x="0" y="0" width="100%" height="96" fill="#1f2923"/>',
        (
            '<text x="30" y="35" font-family="sans-serif" font-size="25" '
            f'font-weight="700" fill="white">{escape(mower.name)}</text>'
        ),
        (
            '<text x="30" y="65" font-family="sans-serif" font-size="16" '
            f'fill="#dce7df">{escape(_header_line(mower, translations))}</text>'
        ),
        (
            '<text x="30" y="87" font-family="sans-serif" font-size="13" '
            f'fill="#aac0b1">{escape(_tr(translations, "coverage", "Coverage"))}: {escape(_period_label(mower.coverage_days, translations))}</text>'
        ),
    ]

    # 1) Full Fleet work map is the base layer.
    for shape in sorted(map_shapes, key=_shape_draw_priority):
        # Keep No-Go areas visible even if a noisy coverage polygon happens
        # to touch it; No-Go is drawn again after coverage below.
        if shape.kind == "no_go":
            continue
        d = _svg_path(shape.points, screen)
        if not d:
            continue
        if shape.kind == "boundary":
            parts.append(
                f'<path d="{d}" fill="#9BE2B9" fill-opacity="0.93" '
                'stroke="#159657" stroke-width="2.4" fill-rule="evenodd"/>'
            )
        elif shape.kind == "zone":
            parts.append(
                f'<path d="{d}" fill="none" stroke="#159657" '
                'stroke-opacity="0.75" stroke-width="1.4"/>'
            )
        elif shape.kind == "path":
            parts.append(
                f'<path d="{d}" fill="#FFB33B" fill-opacity="0.70" '
                'stroke="#E58A00" stroke-width="1.5"/>'
            )
        else:
            parts.append(
                f'<path d="{d}" fill="none" stroke="#9aa69d" '
                'stroke-width="1.1" stroke-dasharray="3 4"/>'
            )

    # 2) Coverage is deliberately drawn above the map/no-go background.
    if rings_by_top:
        for rings in rings_by_top:
            commands: list[str] = []
            for ring in rings:
                screen_ring = [screen(point) for point in ring]
                if len(screen_ring) < 3:
                    continue
                first = screen_ring[0]
                commands.append(f"M {first[0]:.2f} {first[1]:.2f}")
                commands.extend(
                    f"L {point[0]:.2f} {point[1]:.2f}" for point in screen_ring[1:]
                )
                commands.append("Z")
            if commands:
                path = " ".join(commands)
                parts.append(
                    f'<path d="{path}" fill="#08AA57" fill-opacity="0.88" '
                    'stroke="#078648" stroke-width="1.0" fill-rule="evenodd"/>'
                )
    elif map_shapes:
        parts.append(
            '<text x="550" y="390" text-anchor="middle" font-family="sans-serif" '
            f'font-size="20" fill="#66736a">{escape(_tr(translations, "no_coverage", "No coverage in the selected period"))}</text>'
        )
    else:
        parts.append(
            '<text x="550" y="390" text-anchor="middle" font-family="sans-serif" '
            f'font-size="20" fill="#66736a">{escape(_tr(translations, "no_map_data", "No map/coverage data yet"))}</text>'
        )

    # 3) No-Go zones stay visible above coverage.  Fleet can disable an
    # exclusion temporarily; inactive exclusions remain visible but no longer
    # look like a currently enforced red keep-out area.  Unknown state stays
    # red deliberately: failing safe is preferable to visually hiding a real
    # restriction when Kress changes its private schema.
    inactive_no_go_present = False
    for shape in map_shapes:
        if shape.kind != "no_go":
            continue
        d = _svg_path(shape.points, screen)
        if not d:
            continue
        if shape.enabled is False:
            inactive_no_go_present = True
            parts.append(
                f'<path d="{d}" fill="#D9DEDC" fill-opacity="0.46" stroke="#78827D" '
                'stroke-width="1.8" stroke-dasharray="7 5" fill-rule="evenodd"/>'
            )
        else:
            opacity = "0.90" if shape.enabled is True else "0.78"
            dash = "" if shape.enabled is True else ' stroke-dasharray="3 2"'
            parts.append(
                f'<path d="{d}" fill="#FF3344" fill-opacity="{opacity}" stroke="#D9152B" '
                f'stroke-width="2.0"{dash} fill-rule="evenodd"/>'
            )

    # Small legend. It stays subtle so it does not cover much of the lawn.
    legend_x = 44
    legend_y = HEIGHT - 67
    if map_shapes:
        legend_width = 545 if inactive_no_go_present else 445
        parts.extend(
            [
                f'<rect x="{legend_x - 10}" y="{legend_y - 20}" width="{legend_width}" height="42" '
                'rx="7" fill="white" fill-opacity="0.82"/>',
                f'<rect x="{legend_x}" y="{legend_y - 9}" width="20" height="12" fill="#9BE2B9" stroke="#159657"/>',
                f'<text x="{legend_x + 27}" y="{legend_y + 2}" font-family="sans-serif" font-size="12" fill="#344039">{escape(_tr(translations, "not_mowed", "Not mowed"))}</text>',
                f'<rect x="{legend_x + 140}" y="{legend_y - 9}" width="20" height="12" fill="#FF3344" stroke="#D9152B"/>',
                f'<text x="{legend_x + 167}" y="{legend_y + 2}" font-family="sans-serif" font-size="12" fill="#344039">{escape(_tr(translations, "no_go_active", "No-Go active"))}</text>',
                f'<rect x="{legend_x + 263}" y="{legend_y - 9}" width="20" height="12" fill="#08AA57" fill-opacity="0.88" stroke="#078648"/>',
                f'<text x="{legend_x + 290}" y="{legend_y + 2}" font-family="sans-serif" font-size="12" fill="#344039">{escape(_tr(translations, "mowed", "Mowed"))}</text>',
            ]
        )
        if inactive_no_go_present:
            parts.extend(
                [
                    f'<rect x="{legend_x + 365}" y="{legend_y - 9}" width="20" height="12" fill="#D9DEDC" fill-opacity="0.7" stroke="#78827D" stroke-dasharray="4 2"/>',
                    f'<text x="{legend_x + 392}" y="{legend_y + 2}" font-family="sans-serif" font-size="12" fill="#344039">{escape(_tr(translations, "no_go_inactive", "No-Go off"))}</text>',
                ]
            )

    # Zone names are intentionally not drawn inside map polygons.
    # They remain available through the Zone name sensor and header status.

    # Fleet-style mower markers.  A single mower on a map does not need a
    # label; when several mowers share the same map, label every visible
    # marker so users can distinguish them.
    show_mower_names = bool(peers)

    # Secondary mower markers first so the primary mower remains visually on top.
    for peer in peers:
        if not peer.coordinates:
            continue
        x, y = screen(peer.coordinates)
        parts.append(
            _mower_marker_svg(
                x,
                y,
                peer.name,
                show_name=show_mower_names,
                primary=False,
            )
        )

    if mower.coordinates:
        x, y = screen(mower.coordinates)
        parts.append(
            _mower_marker_svg(
                x,
                y,
                mower.name,
                show_name=show_mower_names,
                primary=True,
            )
        )

    parts.append(
        f'<text x="{WIDTH - 25}" y="{HEIGHT - 18}" text-anchor="end" '
        'font-family="sans-serif" font-size="13" fill="#69746d">'
        f'{escape(_footer_line(mower, timezone_name, translations))}</text>'
    )
    parts.append("</svg>")
    return "".join(parts).encode("utf-8")


def _mower_marker_svg(
    x: float,
    y: float,
    name: str,
    *,
    show_name: bool,
    primary: bool,
) -> str:
    """Return a compact Fleet-style robotic mower marker.

    The marker is intentionally drawn from simple SVG primitives rather than
    embedding an upstream icon asset.  This keeps it sharp at any dashboard
    size and avoids shipping another vendor artwork file.
    """
    radius = 15 if primary else 13
    scale = 1.0 if primary else 0.88
    left = x - 14 * scale
    top = y - 14 * scale

    # A black roundel with a small white top-down robotic mower silhouette.
    # The four side tabs read as wheels even when the camera card is small.
    marker = [
        f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{radius + 2}" fill="#ffffff" fill-opacity="0.96"/>',
        f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{radius}" fill="#111614" stroke="#28322d" stroke-width="1.3"/>',
        f'<g transform="translate({left:.2f} {top:.2f}) scale({scale:.3f})">',
        '<rect x="5.2" y="9.1" width="17.6" height="10.2" rx="4.8" fill="#ffffff"/>',
        '<rect x="3.8" y="10.7" width="2.4" height="3.2" rx="1.1" fill="#ffffff"/>',
        '<rect x="3.8" y="15.0" width="2.4" height="3.2" rx="1.1" fill="#ffffff"/>',
        '<rect x="21.8" y="10.7" width="2.4" height="3.2" rx="1.1" fill="#ffffff"/>',
        '<rect x="21.8" y="15.0" width="2.4" height="3.2" rx="1.1" fill="#ffffff"/>',
        '<path d="M8.2 12.2h11.6M8.2 16.2h7.8" stroke="#111614" stroke-width="1.35" stroke-linecap="round"/>',
        '<circle cx="18.4" cy="16.2" r="1.55" fill="#111614"/>',
        '</g>',
    ]

    if show_name:
        marker.append(
            f'<text x="{x + radius + 7:.2f}" y="{y - radius + 1:.2f}" '
            'font-family="sans-serif" font-size="13" font-weight="700" '
            'fill="#24352b" stroke="#f4faf6" stroke-width="4" '
            'paint-order="stroke fill" stroke-linejoin="round">'
            f'{escape(name)}</text>'
        )

    return "".join(marker)


def extract_map_shapes(
    map_detail: dict[str, Any] | None,
    *,
    reference: tuple[float, float] | None = None,
) -> list[MapShape]:
    """Discover geographic polygon data, zone labels and zone IDs from Fleet.

    Fleet's map endpoint is private and field names have changed between
    frontend revisions.  The parser therefore accepts several point formats
    and carries a nearby user-visible zone name/number down into nested
    geometry objects when one is present.
    """
    if not isinstance(map_detail, dict):
        return []

    found: list[MapShape] = []
    seen: dict[tuple[str, tuple[tuple[float, float], ...]], int] = {}

    def add_sequences(
        value: Any,
        context: list[str],
        *,
        label: str | None = None,
        zone_id: int | None = None,
        no_go_enabled: bool | None = None,
        kind_override: str | None = None,
    ) -> bool:
        text = " ".join(context).casefold()
        prefer_lonlat = "geojson" in text
        sequences = _coordinate_sequences(
            value,
            reference=reference,
            prefer_lonlat=prefer_lonlat,
        )
        if not sequences:
            return False
        kind = kind_override or _classify_context(text)
        source = "/".join(context[-5:])[-180:]
        shape_label = label if kind in {"zone", "no_go"} else None
        shape_zone_id = zone_id if kind == "zone" else None
        shape_enabled = no_go_enabled if kind == "no_go" else None
        for sequence in sequences:
            if len(sequence) < 3:
                continue
            rounded = tuple((round(lat, 7), round(lon, 7)) for lat, lon in sequence)
            reverse = tuple(reversed(rounded))
            canonical = min(rounded, reverse)
            key = (kind, canonical)
            existing_index = seen.get(key)
            if existing_index is not None:
                existing = found[existing_index]
                # The same geometry can occur in both a generic geometry block
                # and a metadata-rich No-Go block. Upgrade an unknown state or
                # missing label instead of rendering/counting it twice.
                if kind == "no_go" and (
                    (existing.enabled is None and shape_enabled is not None)
                    or (existing.label is None and shape_label is not None)
                ):
                    found[existing_index] = MapShape(
                        existing.kind,
                        existing.points,
                        existing.source,
                        existing.label or shape_label,
                        existing.zone_id,
                        shape_enabled if existing.enabled is None else existing.enabled,
                    )
                continue
            seen[key] = len(found)
            found.append(
                MapShape(
                    kind,
                    tuple(sequence),
                    source,
                    shape_label,
                    shape_zone_id,
                    shape_enabled,
                )
            )
        return bool(sequences)

    def walk(
        value: Any,
        path: list[str],
        semantic: list[str],
        inherited_label: str | None = None,
        inherited_zone_id: int | None = None,
        inherited_no_go_enabled: bool | None = None,
    ) -> None:
        # Some Fleet/frontend revisions serialize GeoJSON-like geometry into a
        # JSON string. Parse only strings that clearly look like JSON; ordinary
        # names/IDs are left untouched.
        if isinstance(value, str):
            stripped = value.strip()
            if len(stripped) <= 2_000_000 and stripped[:1] in {"[", "{"}:
                try:
                    decoded = json.loads(stripped)
                except json.JSONDecodeError:
                    return
                walk(
                    decoded,
                    path + ["json"],
                    semantic,
                    inherited_label,
                    inherited_zone_id,
                    inherited_no_go_enabled,
                )
            return

        if isinstance(value, dict):
            current_text = " ".join(path + semantic).casefold()
            current_kind = _classify_context(current_text)
            local_label = inherited_label if current_kind in {"zone", "no_go"} else None
            local_zone_id = inherited_zone_id if current_kind == "zone" else None
            local_no_go_enabled = (
                inherited_no_go_enabled if current_kind == "no_go" else None
            )

            if current_kind == "no_go":
                explicit_no_go_enabled = _no_go_enabled_from_dict(value)
                if explicit_no_go_enabled is not None:
                    local_no_go_enabled = explicit_no_go_enabled

            # Only use an explicit name as a zone label when this dictionary is
            # already inside a zone/no-go context. This prevents the map's own
            # name from being copied onto every child polygon.
            if current_kind in {"zone", "no_go"}:
                explicit_label = _label_from_dict(value)
                if explicit_label:
                    local_label = explicit_label
            if current_kind == "zone":
                explicit_zone_id = _zone_id_from_dict(value)
                if explicit_zone_id is not None:
                    local_zone_id = explicit_zone_id

            local_semantic = list(semantic)
            for key, child in value.items():
                if key.casefold() in _SEMANTIC_KEYS and isinstance(
                    child, (str, int, float)
                ):
                    local_semantic.append(str(child))

            for key, child in value.items():
                context = path + [key] + local_semantic
                child_kind = _classify_context(" ".join(context).casefold())
                # A named Fleet area commonly stores its polygon below a key
                # called `boundary`.  The old parser therefore lost the area
                # semantics and treated it as another anonymous map boundary.
                # Preserve a named/numbered parent area as a zone unless the
                # child is explicitly a No-Go object.
                effective_kind = child_kind
                if (
                    current_kind == "zone"
                    and child_kind in {"boundary", "unknown"}
                    and (local_label is not None or local_zone_id is not None)
                ):
                    effective_kind = "zone"
                child_label = local_label if effective_kind in {"zone", "no_go"} else None
                child_zone_id = local_zone_id if effective_kind == "zone" else None
                # A coordinate container can be nested several levels deep;
                # consume it once here to avoid rendering duplicates.
                if isinstance(child, (list, tuple)) and add_sequences(
                    child,
                    context,
                    label=child_label,
                    zone_id=child_zone_id,
                    no_go_enabled=(
                        local_no_go_enabled if effective_kind == "no_go" else None
                    ),
                    kind_override=effective_kind,
                ):
                    continue
                walk(
                    child,
                    path + [key],
                    local_semantic,
                    child_label,
                    child_zone_id,
                    local_no_go_enabled if child_kind == "no_go" else None,
                )
            return

        if isinstance(value, (list, tuple)):
            context = path + semantic
            kind = _classify_context(" ".join(context).casefold())
            effective_kind = kind
            if (
                kind in {"boundary", "unknown"}
                and (inherited_label is not None or inherited_zone_id is not None)
            ):
                effective_kind = "zone"
            label = inherited_label if effective_kind in {"zone", "no_go"} else None
            zone_id = inherited_zone_id if effective_kind == "zone" else None
            no_go_enabled = inherited_no_go_enabled if effective_kind == "no_go" else None
            if add_sequences(
                value,
                context,
                label=label,
                zone_id=zone_id,
                no_go_enabled=no_go_enabled,
                kind_override=effective_kind,
            ):
                return
            for index, child in enumerate(value):
                walk(
                    child,
                    path + [str(index)],
                    semantic,
                    label,
                    zone_id,
                    no_go_enabled,
                )

    walk(map_detail, [], [])
    return found


def _zone_context(text: str) -> bool:
    """Return True for likely named mowing-zone containers, not No-Go objects."""
    normalized = text.casefold().replace(" ", "_")
    if any(hint in normalized for hint in _NO_GO_HINTS):
        return False
    return any(hint in normalized for hint in _ZONE_HINTS)


def _is_meaningful_zone_label(label: str | None) -> bool:
    """Return True only for user-visible zone names, not numeric IDs.

    Fleet map metadata contains several numeric fields that may also be exposed
    through generic label/title slots.  Those values are useful as identifiers
    but must not appear as friendly names in Home Assistant.
    """
    if not isinstance(label, str):
        return False
    normalized = " ".join(label.strip().split())
    if not normalized:
        return False
    return not normalized.isdecimal()


def _zone_catalog_from_source(
    source: dict[str, Any] | None,
    *,
    source_name: str,
) -> list[tuple[int | None, str, str]]:
    """Find small (zone id, user label, source) records without exposing raw JSON.

    Zone labels are metadata, not necessarily attached to the polygon object.
    Fleet revisions have placed them in map detail, product detail and MQTT cfg,
    so this deliberately scans all nested metadata while requiring a zone-like
    path or an explicit zone/area identifier.
    """
    if not isinstance(source, dict):
        return []

    found: list[tuple[int | None, str, str]] = []
    seen: set[tuple[int | None, str]] = set()

    def walk(value: Any, path: list[str]) -> None:
        if isinstance(value, dict):
            context = "/".join(path)
            label = _label_from_dict(value)
            zone_id = _zone_id_from_dict(value)
            likely_zone = _zone_context(context)

            # An explicit zone-ish numeric key is strong evidence even when
            # Fleet chose a generic parent container name such as `items`.
            folded_keys = {str(key).casefold() for key in value}
            explicit_zone_key = any(
                key in folded_keys
                for key in (
                    "zone_id", "zoneid", "zone_no", "zoneno", "zone_index",
                    "zoneindex", "area_id", "areaid", "area_no", "areano",
                    "sector_id", "sectorid", "section_id", "sectionid",
                )
            )
            if _is_meaningful_zone_label(label) and (likely_zone or explicit_zone_key):
                key = (zone_id, label.casefold())
                if key not in seen:
                    seen.add(key)
                    found.append((zone_id, label, source_name))

            for key, child in value.items():
                walk(child, path + [str(key)])
            return

        if isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                walk(child, path + [str(index)])

    walk(source, [])
    return found


def mower_zone_catalog(mower: FleetMower) -> list[tuple[int | None, str, str]]:
    """Return unique zone metadata from map REST, product REST and MQTT cfg."""
    result: list[tuple[int | None, str, str]] = []
    seen: set[tuple[int | None, str]] = set()
    for source_name, source in (
        ("map", mower.map_detail),
        ("product", mower.product_detail),
        ("mqtt_cfg", mower.cfg),
    ):
        for zone_id, label, origin in _zone_catalog_from_source(
            source, source_name=source_name
        ):
            key = (zone_id, label.casefold())
            if key in seen:
                continue
            seen.add(key)
            result.append((zone_id, label, origin))
    return result


def mower_zone_names(mower: FleetMower) -> list[str]:
    """Return unique user-assigned zone names known for a mower."""
    result: list[str] = []
    seen: set[str] = set()

    # Geometry labels first, then metadata-only labels from the other sources.
    for name in map_zone_names(mower.map_detail, reference=mower.coordinates):
        if not _is_meaningful_zone_label(name):
            continue
        folded = name.casefold()
        if folded not in seen:
            seen.add(folded)
            result.append(name)
    for _zone_id, name, _source in mower_zone_catalog(mower):
        if not _is_meaningful_zone_label(name):
            continue
        folded = name.casefold()
        if folded not in seen:
            seen.add(folded)
            result.append(name)
    return result


def mower_zone_name_sources(mower: FleetMower) -> list[str]:
    """Return diagnostic source names that contributed named-zone metadata."""
    return sorted({origin for _zone_id, _label, origin in mower_zone_catalog(mower)})


def mower_zone_id_name_map(mower: FleetMower) -> dict[int, str]:
    """Return explicit Fleet zone-number -> user-label mappings from all sources."""
    result = map_zone_name_by_id(mower.map_detail, reference=mower.coordinates)
    for zone_id, label, _source in mower_zone_catalog(mower):
        if zone_id is not None and _is_meaningful_zone_label(label):
            result.setdefault(zone_id, label)
    return result


def map_zone_names(
    map_detail: dict[str, Any] | None,
    *,
    reference: tuple[float, float] | None = None,
) -> list[str]:
    """Return unique user-visible zone names found in the active Fleet map."""
    result: list[str] = []
    seen: set[str] = set()
    for shape in extract_map_shapes(map_detail, reference=reference):
        if (
            shape.kind != "zone"
            or not shape.label
            or not _is_meaningful_zone_label(shape.label)
        ):
            continue
        folded = shape.label.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        result.append(shape.label)
    return result


def map_zone_name_by_id(
    map_detail: dict[str, Any] | None,
    *,
    reference: tuple[float, float] | None = None,
) -> dict[int, str]:
    """Return the zone-number to user-visible name mapping Fleet exposes."""
    result: dict[int, str] = {}
    for shape in extract_map_shapes(map_detail, reference=reference):
        if (
            shape.kind == "zone"
            and shape.zone_id is not None
            and shape.label
            and _is_meaningful_zone_label(shape.label)
        ):
            result.setdefault(shape.zone_id, shape.label)
    return result


def current_zone_name(mower: FleetMower) -> str | None:
    """Resolve the live MQTT ``dat.cut.z`` value to a Fleet zone name.

    The mapping is intentionally exact.  We do not guess offsets between the
    MQTT zone number and map metadata because that could silently display the
    wrong work area.
    """
    zone_id = mower.zone
    if zone_id is None:
        return None
    label = mower_zone_id_name_map(mower).get(zone_id)
    return label if _is_meaningful_zone_label(label) else None

def map_shape_counts(
    map_detail: dict[str, Any] | None,
    *,
    reference: tuple[float, float] | None = None,
) -> dict[str, int]:
    """Return small diagnostic counts without exposing map coordinates."""
    counts = {"boundary": 0, "no_go": 0, "zone": 0, "path": 0, "unknown": 0}
    for shape in _promote_main_boundary(extract_map_shapes(map_detail, reference=reference)):
        counts[shape.kind] = counts.get(shape.kind, 0) + 1
    return counts


def _label_from_dict(value: dict[str, Any]) -> str | None:
    folded = {str(key).casefold(): child for key, child in value.items()}
    for key in _LABEL_KEYS:
        raw = folded.get(key)
        if not isinstance(raw, str):
            continue
        label = " ".join(raw.strip().split())
        if not label or len(label) > 100:
            continue
        # Do not expose opaque IDs as labels.
        compact = label.replace("-", "")
        if len(compact) == 32 and all(ch in "0123456789abcdefABCDEF" for ch in compact):
            continue
        return label
    return None


def _zone_id_from_dict(value: dict[str, Any]) -> int | None:
    folded = {str(key).casefold(): child for key, child in value.items()}
    for key in _ZONE_ID_KEYS:
        raw = folded.get(key)
        if isinstance(raw, bool) or raw is None:
            continue
        try:
            zone_id = int(raw)
        except (TypeError, ValueError):
            continue
        if 0 <= zone_id <= 999:
            return zone_id
    return None


def map_no_go_state_counts(
    map_detail: dict[str, Any] | None,
    *,
    reference: tuple[float, float] | None = None,
) -> dict[str, int]:
    """Return active/inactive/unknown No-Go polygon counts."""
    counts = {"active": 0, "inactive": 0, "unknown": 0}
    for shape in extract_map_shapes(map_detail, reference=reference):
        if shape.kind != "no_go":
            continue
        if shape.enabled is True:
            counts["active"] += 1
        elif shape.enabled is False:
            counts["inactive"] += 1
        else:
            counts["unknown"] += 1
    return counts


def map_no_go_state_keys(map_detail: dict[str, Any] | None) -> list[str]:
    """Return small schema diagnostics: state-like keys in No-Go contexts."""
    if not isinstance(map_detail, dict):
        return []
    result: set[str] = set()

    def walk(value: Any, path: list[str], semantic: list[str]) -> None:
        if isinstance(value, dict):
            current_kind = _classify_context(" ".join(path + semantic).casefold())
            local_semantic = list(semantic)
            for key, child in value.items():
                if key.casefold() in _SEMANTIC_KEYS and isinstance(child, (str, int, float)):
                    local_semantic.append(str(child))
            if current_kind == "no_go":
                for key, child in value.items():
                    folded = str(key).casefold()
                    if any(hint in folded for hint in _STATUS_KEY_HINTS) and isinstance(
                        child, (bool, int, float, str)
                    ):
                        result.add(str(key))
            for key, child in value.items():
                walk(child, path + [str(key)], local_semantic)
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                walk(child, path + [str(index)], semantic)

    walk(map_detail, [], [])
    return sorted(result, key=str.casefold)[:20]


def _no_go_enabled_from_dict(value: dict[str, Any]) -> bool | None:
    """Read a No-Go enabled/disabled flag without inheriting map-level active."""
    folded = {str(key).casefold(): child for key, child in value.items()}

    for key in _NO_GO_DISABLED_KEYS:
        if key not in folded:
            continue
        parsed = _boolish(folded[key])
        if parsed is not None:
            return not parsed

    for key in _NO_GO_ENABLED_KEYS:
        if key not in folded:
            continue
        parsed = _boolish(folded[key])
        if parsed is not None:
            return parsed

    for key in _NO_GO_STATE_KEYS:
        raw = folded.get(key)
        if raw is None:
            continue
        if isinstance(raw, str):
            word = raw.strip().casefold().replace("-", "_")
            if word in _TRUE_STATE_WORDS:
                return True
            if word in _FALSE_STATE_WORDS:
                return False
        parsed = _boolish(raw)
        if parsed is not None:
            return parsed
    return None


def _boolish(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        word = value.strip().casefold()
        if word in _TRUE_STATE_WORDS:
            return True
        if word in _FALSE_STATE_WORDS:
            return False
    return None


def _coordinate_sequences(
    value: Any,
    *,
    reference: tuple[float, float] | None,
    prefer_lonlat: bool,
) -> list[list[tuple[float, float]]]:
    if not isinstance(value, (list, tuple)) or not value:
        return []

    # A direct ring/list of point objects.
    if all(_looks_like_point(item) for item in value):
        result: list[tuple[float, float]] = []
        for item in value:
            point = _normalize_point(
                item,
                reference=reference,
                prefer_lonlat=prefer_lonlat,
            )
            if point is not None:
                result.append(point)
        if len(result) >= 3 and _sequence_is_local(result, reference):
            return [result]
        return []

    # Polygon / MultiPolygon / nested arrays.
    sequences: list[list[tuple[float, float]]] = []
    for child in value:
        if isinstance(child, (list, tuple)):
            sequences.extend(
                _coordinate_sequences(
                    child,
                    reference=reference,
                    prefer_lonlat=prefer_lonlat,
                )
            )
    return sequences


def _looks_like_point(value: Any) -> bool:
    if isinstance(value, dict):
        keys = {key.casefold() for key in value}
        return bool(
            ({"lat", "lng"} <= keys)
            or ({"lat", "lon"} <= keys)
            or ({"latitude", "longitude"} <= keys)
        )
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return _is_number(value[0]) and _is_number(value[1])
    return False


def _normalize_point(
    value: Any,
    *,
    reference: tuple[float, float] | None,
    prefer_lonlat: bool,
) -> tuple[float, float] | None:
    if isinstance(value, dict):
        folded = {str(key).casefold(): child for key, child in value.items()}
        lat = folded.get("lat", folded.get("latitude"))
        lon = folded.get("lng", folded.get("lon", folded.get("longitude")))
        if _is_number(lat) and _is_number(lon):
            point = (float(lat), float(lon))
            return point if _valid_geo(*point) else None
        return None

    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    if not _is_number(value[0]) or not _is_number(value[1]):
        return None

    a = float(value[0])
    b = float(value[1])
    candidates: list[tuple[float, float]] = []
    for candidate in ((a, b), (b, a)):
        if _valid_geo(*candidate) and candidate not in candidates:
            candidates.append(candidate)
    if not candidates:
        return None
    if reference is not None:
        ref_lat, ref_lon = reference
        cos_lat = max(0.01, math.cos(math.radians(ref_lat)))
        return min(
            candidates,
            key=lambda point: (point[0] - ref_lat) ** 2
            + ((point[1] - ref_lon) * cos_lat) ** 2,
        )
    if prefer_lonlat and _valid_geo(b, a):
        return b, a
    return a, b


def _sequence_is_local(
    points: list[tuple[float, float]],
    reference: tuple[float, float] | None,
) -> bool:
    if reference is None:
        return True
    ref_lat, ref_lon = reference
    cos_lat = max(0.01, math.cos(math.radians(ref_lat)))
    distances = [
        math.hypot(lat - ref_lat, (lon - ref_lon) * cos_lat)
        for lat, lon in points
    ]
    # A mower map should never span hundreds of kilometres. This guards the
    # generic parser against unrelated numeric arrays in the API response.
    return median(distances) < 1.0


def _classify_context(text: str) -> str:
    normalized = text.replace(" ", "_")
    if any(hint in normalized for hint in _NO_GO_HINTS):
        return "no_go"
    if any(hint in normalized for hint in _BOUNDARY_HINTS):
        return "boundary"
    if any(hint in normalized for hint in _ZONE_HINTS):
        return "zone"
    if any(hint in normalized for hint in _PATH_HINTS):
        return "path"
    return "unknown"


def _promote_main_boundary(shapes: list[MapShape]) -> list[MapShape]:
    """Treat the largest otherwise-unknown polygon as the outer work area."""
    if not shapes or any(shape.kind == "boundary" for shape in shapes):
        return shapes
    candidates = [
        (index, _rough_polygon_area(shape.points))
        for index, shape in enumerate(shapes)
        if shape.kind in {"unknown", "zone"}
    ]
    if not candidates:
        return shapes
    index, area = max(candidates, key=lambda item: item[1])
    if area <= 0:
        return shapes
    promoted = list(shapes)
    shape = promoted[index]
    promoted[index] = MapShape(
        "boundary", shape.points, shape.source, shape.label, shape.zone_id, shape.enabled
    )
    return promoted


def _rough_polygon_area(points: tuple[tuple[float, float], ...]) -> float:
    if len(points) < 3:
        return 0.0
    center_lat = sum(point[0] for point in points) / len(points)
    cos_lat = max(0.01, math.cos(math.radians(center_lat)))
    area = 0.0
    for index, (lat1, lon1) in enumerate(points):
        lat2, lon2 = points[(index + 1) % len(points)]
        x1, y1 = lon1 * cos_lat, lat1
        x2, y2 = lon2 * cos_lat, lat2
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def _shape_draw_priority(shape: MapShape) -> tuple[int, float]:
    priority = {"boundary": 0, "zone": 1, "path": 2, "unknown": 3, "no_go": 4}
    return priority.get(shape.kind, 3), -_rough_polygon_area(shape.points)


def _shape_label_position(shape: MapShape, screen) -> tuple[float, float]:
    """Return a stable visual centre for a polygon label."""
    points = [screen(point) for point in shape.points]
    if not points:
        return WIDTH / 2, HEIGHT / 2
    # Polygon centroid. Fall back to vertex average for near-degenerate rings.
    twice_area = 0.0
    cx = 0.0
    cy = 0.0
    for index, (x1, y1) in enumerate(points):
        x2, y2 = points[(index + 1) % len(points)]
        cross = x1 * y2 - x2 * y1
        twice_area += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(twice_area) > 1e-7:
        factor = 1.0 / (3.0 * twice_area)
        return cx * factor, cy * factor
    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


def _svg_path(points: tuple[tuple[float, float], ...], screen) -> str:
    screen_ring = [screen(point) for point in points]
    if len(screen_ring) < 3:
        return ""
    first = screen_ring[0]
    commands = [f"M {first[0]:.2f} {first[1]:.2f}"]
    commands.extend(
        f"L {point[0]:.2f} {point[1]:.2f}" for point in screen_ring[1:]
    )
    commands.append("Z")
    return " ".join(commands)


def _rings_for_node(node: dict[str, Any]) -> list[list[tuple[float, float]]]:
    rings: list[list[tuple[float, float]]] = []
    points = node.get("points")
    if isinstance(points, list):
        ring = _valid_ring(points)
        if ring:
            rings.append(ring)
    children = node.get("children")
    if isinstance(children, list):
        for child in children:
            if isinstance(child, dict):
                rings.extend(_rings_for_node(child))
    return rings


def _valid_ring(points: list[Any]) -> list[tuple[float, float]]:
    ring: list[tuple[float, float]] = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        try:
            lat = float(point[0])
            lon = float(point[1])
        except (TypeError, ValueError):
            continue
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            ring.append((lat, lon))
    return ring if len(ring) >= 3 else []


def _reference_point(mower: FleetMower) -> tuple[float, float] | None:
    if mower.coordinates:
        return mower.coordinates
    for node in mower.coverage:
        if not isinstance(node, dict):
            continue
        for ring in _rings_for_node(node):
            if ring:
                return ring[0]
    return None


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        float(value)
        return value is not None
    except (TypeError, ValueError):
        return False


def _valid_geo(lat: float, lon: float) -> bool:
    return -90 <= lat <= 90 and -180 <= lon <= 180


def _tr(
    translations: Mapping[str, str] | None,
    key: str,
    fallback: str,
    **placeholders: object,
) -> str:
    """Return one localized live-map label with a safe English fallback."""
    template = translations.get(key, fallback) if translations else fallback
    try:
        return template.format(**placeholders)
    except (KeyError, ValueError):
        return fallback.format(**placeholders)


def _period_label(
    days: int, translations: Mapping[str, str] | None = None
) -> str:
    if days <= 1:
        return _tr(translations, "period.today", "Today")
    return _tr(translations, "period.last_days", "Last {days} days", days=days)


def _header_line(
    mower: FleetMower, translations: Mapping[str, str] | None = None
) -> str:
    chunks = []
    if mower.battery_percent is not None:
        chunks.append(
            f"{_tr(translations, 'battery', 'Battery')} {mower.battery_percent}%"
        )
    status = _tr(
        translations,
        f"status.{mower.status}",
        mower.status.replace("_", " "),
    )
    chunks.append(f"{_tr(translations, 'status_label', 'Status')} {status}")
    if mower.zone is not None:
        zone_label = current_zone_name(mower)
        zone = _tr(translations, "zone", "Zone")
        chunks.append(
            f"{zone} {mower.zone}: {zone_label}" if zone_label else f"{zone} {mower.zone}"
        )
    if mower.rssi is not None:
        chunks.append(f"{_tr(translations, 'signal', 'Signal')} {mower.rssi} dBm")
    return "   |   ".join(chunks)


def timestamp_local(value: datetime | str, timezone_name: str) -> str:
    """Return an ISO timestamp in the Home Assistant configured timezone."""
    try:
        timezone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        timezone = UTC

    parsed: datetime
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return text
        try:
            # Fleet uses RFC3339/ISO 8601 values, commonly ending in Z.
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            # Keep an unexpected Fleet value visible instead of dropping it.
            return text

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(timezone).isoformat()


def _footer_line(
    mower: FleetMower,
    timezone_name: str = "UTC",
    translations: Mapping[str, str] | None = None,
) -> str:
    chunks = []
    if mower.map_name:
        chunks.append(mower.map_name)
    if mower.coverage_from:
        chunks.append(
            f"{_tr(translations, 'from', 'from')} "
            f"{timestamp_local(mower.coverage_from, timezone_name)}"
        )
    if mower.coverage_to:
        chunks.append(
            f"{_tr(translations, 'to', 'to')} "
            f"{timestamp_local(mower.coverage_to, timezone_name)}"
        )
    if mower.last_update:
        chunks.append(
            f"{_tr(translations, 'position', 'Position')} "
            f"{timestamp_local(mower.last_update, timezone_name)}"
        )
    return " | ".join(chunks) or _tr(
        translations, "live_map", "Kress Fleet live map"
    )
