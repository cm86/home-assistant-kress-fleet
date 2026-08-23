# Changelog

## 0.3.13 - 2026-08-23

### Fixed

- Stop subscribing to Fleet `commandIn` on the primary live MQTT connection. Some Fleet/AWS IoT policies disconnect the whole client when that subscribe is not authorized, which made the mower entity repeatedly become unavailable.
- Restore the stable `commandOut`-only MQTT subscription used before 0.3.12 so normal live telemetry and mower controls stay connected.

### Diagnostics

- Replace the risky `commandIn` capture with a read-only RTK zone probe based on already received `commandOut` telemetry.
- Expose privacy-filtered `diagnostic_zone_probe_sc_once` and `diagnostic_zone_probe_cut_task` attributes on the mower entity for the next zone-start test.
- No new mower command is sent by this release.

## 0.3.12 - 2026-08-23

### Added

- Add a temporary, privacy-filtered Fleet `commandIn` capture to discover the exact RTK zone-start command used by the official Kress app.
- Expose capture readiness and the last sanitized `commandIn` payload as diagnostic attributes on each `lawn_mower` entity.
- Redact mower/account identifiers, credentials, coordinates, UUID-like strings and long token-like values before storing captured data.

### Safety

- This release does **not** send any new mower commands and does not change existing start, pause or dock behavior.
- If the Fleet MQTT policy does not permit subscribing to `commandIn`, `diagnostic_command_capture_ready` remains `false` and normal telemetry/control continues unchanged.

## 0.3.11 - 2026-08-23

### Added

- Add a localized **Error** enum sensor while keeping the existing numeric **Error code** sensor unchanged for automations and diagnostics.
- Decode the community-documented mower error ranges `0-20` and `100-120`, including RTK error `106` as **Unreachable charging station** / **Ladestation nicht erreichbar**.
- Translate all known error states in English and German, with an `Unknown error` fallback for future/unsupported codes.

### Compatibility

- Existing numeric error-code entity IDs and values remain unchanged.
- No change to mower control, Fleet authentication, MQTT commands, Live Map rendering, zone resolution or coverage behavior.

## 0.3.10 - 2026-08-23

### Changed

- Upgrade GitHub Actions checkout and Python setup workflows to the current Node 24 compatible major versions.
- Generate GitHub release notes directly from the matching version section in `CHANGELOG.md`.
- Make tagged releases fail early when their version has no changelog entry, preventing empty or misleading HACS release notes.

### Notes

- No change to mower control, Fleet authentication, MQTT handling, Live Map rendering, zone resolution or coverage behavior.

## 0.3.9 - 2026-08-23

- Fix the Home Assistant translation schema rejected by Hassfest.
- Flatten Live Map captions under `common` so every common translation value is a string.
- Preserve the renderer's existing internal keys through an explicit translation-key mapping.
- No change to mower commands, Live Map rendering, active-zone resolution or coverage behavior.

## 0.3.8 - 2026-08-23

- Resolve the active zone for RTK Fleet task telemetry from `dat.cut.tsk[*].z[*]` when `dat.cut.z` is absent.
- Treat a task-zone as active only when it is the single zone with non-zero live route counters (`rtg` / `rtn`), avoiding guesses on ambiguous payloads.
- Keep direct `dat.cut.z` telemetry as the highest-priority source and retain the last unambiguous zone only within the same active mowing/zoning session and map.
- Remove the temporary v0.3.5-v0.3.7 zone telemetry diagnostics from Live Map attributes.

## 0.3.7 - 2026-08-23

- Extend temporary zone diagnostics with safe values from the active Fleet mowing task.
- Expose only allowlisted task fields (`id`, `st`, `tm`, `tr`) and zone fields (`a`, `id`, `p`, `rtg`, `rtn`) from `dat.cut.tsk[0]`.
- Continue to redact arbitrary strings and avoid raw MQTT payloads, UUIDs, MAC addresses, coordinates, credentials and tokens.
- No change to mower commands, zone resolution, map rendering or coverage behavior.

## 0.3.6 - 2026-08-23

- Extend temporary active-zone diagnostics after Fleet RTK telemetry was observed without `dat.cut.z` and without `dat.lz`.
- Expose the shape/value of `dat.cut.tsk`, safe structural summaries for likely telemetry blocks, and recursively discovered zone/task-like key paths.
- Redact arbitrary string values and continue to avoid raw MQTT payloads, UUIDs, MAC addresses, coordinates, credentials and tokens.
- No change to mower commands, zone resolution, map rendering or coverage behavior.

## 0.3.5 - 2026-08-23

- Add temporary privacy-safe Live Map telemetry diagnostics for active-zone investigation.
- Expose Fleet status ID/state, raw `dat.cut.z`, raw `dat.lz`, and only the key names present in `dat` / `dat.cut`.
- Do not expose raw MQTT payloads, UUIDs, coordinates, credentials or tokens.
- No change to mower commands, zone resolution, map rendering or coverage behavior.

## 0.3.4 - 2026-08-23

- Keep the last explicitly reported Fleet zone while the mower remains in the same active mowing/zoning session, preventing transient MQTT packets without `dat.cut.z` from changing the Zone and Zone name sensors to `unknown`.
- Scope the retained zone to the current mower, active run and map; no mower UUID, zone ID or zone name is hard-coded, and stale zones are not carried into a later mowing session.
- Expose `current_zone_source` on the Live Map camera diagnostics (`telemetry` or `last_reported`) to make zone-source troubleshooting explicit.


## 0.3.3 - 2026-08-23

- Move live-map SVG rendering to Home Assistant's executor so opening the Live Map no longer performs CPU-heavy map/coverage traversal on the main event loop.
- Move live-map camera diagnostics out of `extra_state_attributes`; state writes now expose only cached small diagnostics instead of reparsing Fleet map geometry.
- Resolve the friendly `Zone name` sensor in the executor as well, avoiding map parsing during coordinator state writes.
- Serialize concurrent camera image requests so multiple frontend requests cannot trigger duplicate SVG renders for the same cache miss.
- Reuse already parsed map shapes for the live-map header's current zone label instead of reparsing the map during SVG rendering.

## 0.3.2 - 2026-08-22

- Localize user-visible entity names through Home Assistant translation keys instead of hard-coded English/German names.
- Make coverage select values language-neutral internally and translate the displayed period names from the Home Assistant language (`Today` / `Heute`, `Last 2 days` / `Letzte 2 Tage`, etc.).
- Localize all live-map captions, legend labels, status text and footer prefixes using the Home Assistant instance language, with English fallback for unsupported languages.
- Keep entity IDs, attribute keys and raw select/state values stable and language-neutral for automations.
- Note for upgrades from 0.3.1: coverage service/automation option values are now the stable keys (`today`, `last_2_days`, ...); the UI translates them for display.
- Document English coverage examples consistently in the English README.
- Remove the remaining private-development domain note from public notices.
- Align the internal `VERSION` constant with the manifest version.

## 0.3.1 - 2026-08-22

- Resolve Fleet product names before Home Assistant first creates newly discovered mower devices, so the post-setup rename/area dialog proposes names such as `Mähgatron` instead of UUID fragments such as `Kress 99337e8f`.
- Keep the fast startup path for already-known devices by restoring their existing friendly device-registry metadata and skipping the synchronous name lookup when it is not needed.
- Automatically repair integration-provided UUID fallback names from pre-0.3.1 builds on the next integration reload without touching `name_by_user` overrides.
- If Fleet product details are temporarily unavailable, fall back to human-readable model-based names rather than exposing UUID fragments.

## 0.3.0 - 2026-08-22

- First public-release candidate under the permanent Home Assistant domain `kress_fleet`.
- Set `integration_type` to `hub` because one Fleet account/config entry exposes multiple mower devices.
- Add HACS metadata, HACS validation, hassfest, repository hygiene checks, issue templates and release automation.
- Remove bundled official Kress/Fleet brand artwork; trademark names remain identification-only.
- Remove friendly zone-name text from inside live-map polygons. Zone names remain available in the live-map header and `Zone name` sensor.
- Keep coverage and live-position timestamps localized to the Home Assistant instance timezone.
- Remove Python bytecode/cache artifacts from the distributable source tree.
- Add public security, publishing and upstream-provenance documentation.

## Pre-public development history

## 0.2.10 - 2026-08-22

- Render coverage `from` / `to` timestamps in the Home Assistant instance timezone, matching the already-localized live position timestamp.
- Expose the camera attributes `coverage_from` and `coverage_to` in the Home Assistant instance timezone as well.
- Keep all Fleet API request/storage timestamps in UTC; only the Home Assistant-facing presentation is localized.

## 0.2.9 - 2026-08-22

- Render the live-map position timestamp in the timezone configured for the Home Assistant instance.
- Keep coverage `from`/`to` timestamps unchanged in UTC because they represent the Fleet API request boundaries.
- Include the configured Home Assistant timezone in the SVG cache key so a timezone change cannot reuse a stale footer.
- No changes to MQTT, coverage calculation, map geometry, zone-name resolution or startup behavior.

## 0.2.8 - 2026-08-21

- Filter numeric-only Fleet metadata values from friendly zone names and zone ID/name mappings.
- Resolve the live MQTT `dat.cut.z` value strictly against the discovered Fleet zone ID/name map.
- Load active map detail as a Home Assistant background task so the `Zone name` sensor can resolve names without first opening the live-map camera.
- Add `current_zone_id` to the live-map diagnostics alongside `current_zone_name`.
- Coverage remains lazy and the MQTT/startup background-task fixes from 0.2.6 are unchanged.

## 0.2.7 - 2026-08-21

- Improved named-zone discovery across Fleet map detail, product-item detail and MQTT `cfg`.
- Recognizes Fleet named area/section/region/partition containers as mowing zones.
- Preserves zone semantics when a named area stores its polygon under `boundary`.
- `Zone name` now resolves explicit zone ID/name mappings from all cached Fleet sources.
- Live-map diagnostics expose only zone names and contributing source types, never raw map/product payloads.


## 0.2.6 - 2026-08-21

- Fix Home Assistant bootstrap being held open by the lifetime `KressFleetMqtt._connection_loop()` task.
- Start the MQTT maintenance loop with Home Assistant's config-entry background-task API so it no longer participates in startup waiting.
- Run product-detail enrichment as a config-entry background task as well.
- No change to Fleet authentication, MQTT protocol, entities, commands, map rendering, coverage, or No-Go behavior.

## 0.2.5 - 2026-08-21

- Remove mapped mower product-detail lookups from the Home Assistant startup critical path.
- Discover physical mower UUIDs immediately from Fleet MQTT topics intersected with map allocations.
- Probe only unmapped MQTT identifiers with a 2.5 second per-item cap so a slow mower can no longer stall startup.
- Restore known mower name/model/serial metadata from the Home Assistant device registry during fast startup.
- Enrich product details and last REST status in the background after entities/MQTT are already available.
- Isolate periodic product refreshes per mower with an 8 second timeout so one slow/offline mower cannot delay every Fleet entity.

## 0.2.4 - 2026-08-21

- Speed up Home Assistant startup by removing the blocking initial full refresh.
- Map detail and potentially multi-megabyte coverage are now loaded lazily when a live-map camera is opened.
- MQTT/entities become available immediately after authentication/discovery.
- Coverage refreshes only run periodically after a map has actually been loaded.
- Concurrent camera requests for the same map/range are deduplicated.

## 0.2.3 - 2026-08-21

- Replace the red live-position dot with a compact Fleet-style robotic mower marker.
- Hide mower labels when a map has only one mower.
- Show mower names only when multiple mowers share the same Fleet map.

## 0.2.2 - 2026-08-21

- Distinguish active, inactive and unknown-state No-Go/exclusion geometry.
- Render active No-Go areas in Fleet red; render explicitly disabled No-Go areas grey/translucent with a dashed outline.
- Keep unknown No-Go state red as a fail-safe when Fleet changes private schema fields.
- Recognize common Fleet-style activation fields (`enabled`, `active`, `disabled`, `state`, `status`, and common variants) only within No-Go context, never from the map-version `active` flag.
- Add camera diagnostics: `no_go_active`, `no_go_inactive`, `no_go_unknown`, and `no_go_state_keys`.

## 0.2.1 - 2026-08-21

- Add local Home Assistant brand assets (`brand/icon.png` and `brand/logo.png`) for HA 2026.3+.
- Match Fleet map colors more closely: red No-Go, light-green not-yet-covered map area, dark-green covered/mowed area.
- Render path/passage geometry in orange when the Fleet map payload exposes it.
- Preserve user-visible zone labels while parsing nested Fleet map geometry and draw unique zone names on the live-map camera.
- Add a `Zone name` sensor and camera diagnostics (`zone_names`, `current_zone_name`) when Fleet exposes a numeric zone-to-name mapping.
- Keep the numeric `Zone` sensor unchanged for automation compatibility.

## 0.2.0 - 2026-08-21

- Add a per-mower **Coverage-Zeitraum** select with Today / last 2 through last 7 calendar days.
- Coverage requests now use local midnight of the first selected day and refresh immediately when the select changes.
- Multi-day history refreshes at a reduced cadence while mowing to avoid re-downloading multi-megabyte static history every minute.
- Keep coverage de-duplication map-aware **and** period-aware when multiple mowers share a map.
- Render Fleet `/maps/{id}` geometry below the coverage overlay, including recognized work boundaries and zones.
- Render recognized No-Go/exclusion areas with a visible hatched layer above coverage.
- Add schema-tolerant map geometry parsing for coordinate arrays, `{lat,lng}` objects and GeoJSON-style nested coordinates.
- Add small camera diagnostics (`map_shapes`, `map_boundaries`, `no_go_zones`, `map_zones`) without exposing raw coordinates.
- Fetch `/maps` once per Fleet location and map detail once per unique active map instead of once per mower.

## 0.1.7 - 2026-08-21

- Move paho MQTT TLS certificate setup (`tls_set`) to Home Assistant's executor so system CA loading no longer blocks the event loop.
- No changes to the confirmed Fleet SSO, REST discovery, coverage or MQTT authentication flow.

## 0.1.6 - 2026-08-21

- Confirmed the Fleet browser SSO flow end-to-end: `/api/actor` now authenticates successfully.
- Handle Fleet MQTT credential blocks that expose a companion MQTT-only identifier for each physical mower.
- Ignore identifiers whose `/product-items/{uuid}` lookup returns HTTP 404 instead of creating phantom Home Assistant devices.
- Prevent a single product-item 404 from failing the entire coordinator refresh.
- Add discovery diagnostics that report MQTT identifier count versus real product mower count without logging UUIDs.
- Prefer the product UUID embedded in MQTT payloads over companion topic identifiers so live telemetry can still be associated correctly.

## 0.1.5 - 2026-08-21

- Replaced the legacy password-grant/session bootstrap with the real Fleet browser SSO flow.
- Starts authentication at `fleet.kress.com/login` so Fleet generates its current OAuth client, state and PKCE challenge/verifier itself.
- Automatically parses and submits the Kress Identity login form while preserving hidden CSRF fields and cookies.
- Follows the authorization-code callback back to Fleet and reuses the resulting Laravel/XSRF session.
- Re-runs Fleet SSO automatically after HTTP 401/403/419.
- Added safe SSO diagnostics that log only hosts/paths, status codes, field names and cookie names, never passwords, codes, state values or tokens.

## 0.1.4 - 2026-08-21

- Fix a config-flow deadlock when Fleet rejects the first `/api/actor` request and session re-bootstrap is required.
- Add hard setup/reauth timeouts so a failed cloud login can no longer leave a Home Assistant config flow stuck forever.
- Add a lightweight account probe during configuration instead of fully loading every mower while the setup dialog is open.
- Discover product details concurrently for accounts with several owned/shared mowers.
- Add safe HTTP-stage debug logging without credentials or tokens.

## 0.1.3 - 2026-08-21

- Send Kress OAuth grants as JSON, matching the current async pyworxcloud implementation.
- Do not close Home Assistant-managed aiohttp sessions.
