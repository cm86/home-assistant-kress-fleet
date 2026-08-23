# Kress Fleet for Home Assistant

> **Unofficial / experimental custom integration.** This project is not affiliated with,
> endorsed by, or sponsored by Kress, Positec, MTrab, or Home Assistant.

Native Home Assistant integration for the current **Kress Fleet** platform using the Fleet
web application's private REST/SSO/MQTT interfaces.

**Home Assistant domain:** `kress_fleet`  
**License:** GPL-3.0  
**Minimum Home Assistant version:** 2026.8.0

**Languages:** English and German are bundled. User-visible entity names, select options and live-map captions follow the Home Assistant instance language; unsupported languages fall back to English.

## Features

- Kress Fleet sign-in with email + password; no manually copied cookies/tokens
- automatic discovery of owned and shared Fleet mowers across multiple locations
- one Home Assistant device per physical mower
- native `lawn_mower` entity with Start / Pause / Dock
- targeted RTK zone mowing with a per-mower **Mowing zone** select and **Mow selected zone** button
- live MQTT telemetry over WebSockets
- live GPS `device_tracker`
- battery, status, signal, firmware, numeric zone and friendly **Zone name** sensors
- raw numeric **Error code** plus localized **Error** description sensors
- cloud, MQTT, RTK, rain and charging diagnostics
- active Fleet map discovery
- live-map camera with work-map geometry, coverage and No-Go areas
- active No-Go areas in red; disabled No-Go areas in grey/dashed style
- compact live mower marker; mower names only when multiple mowers share a map
- per-mower coverage period selector: Today through the last 7 calendar days
- coverage de-duplication when multiple mowers share the same map/window
- Home Assistant instance timezone for coverage and position timestamps shown in the live map
- background/lazy loading so multi-megabyte map coverage does not block Home Assistant startup

Friendly zone names are resolved from Fleet metadata and shown in the **Zone name** sensor and
live-map header. Zone names are intentionally **not** drawn inside map polygons.


### Mower error codes

The integration keeps the raw numeric Fleet mower error in **Error code** for
backward-compatible automations and exposes a second **Error** enum sensor with
Home Assistant-localized text. Unknown future codes remain visible through the
numeric sensor and are reported as `Unknown error` by the description sensor.

Known community-documented protocol ranges are `0-20` and `100-120` (Vision /
RTK included). For example, code `106` resolves to **Unreachable charging
station** / **Ladestation nicht erreichbar**. The mapping is based on the
community-maintained Worx/Kress protocol documentation:
https://github.com/iobroker-community-adapters/ioBroker.worx/blob/master/docs/en/README.md

### Targeted RTK zone mowing

Version 0.3.14 adds native targeted-zone control based on the command emitted by
the official Fleet web application. Each mower exposes a **Mowing zone** select
whose options are built dynamically from that mower's Fleet map, plus a **Mow
selected zone** button. No mower UUID, zone number or zone name is hard-coded.

The integration sends the Fleet RTK single-zone command shape
`cmd=1`, `ls=34`, `le=0`, `cut.zo=0`, `cut.b=1`, with `cut.z` containing the
selected Fleet zone ID. The normal MQTT publisher adds the mower UUID, timestamp
and command ID. The v0.3.13 temporary zone-probe attributes have been removed.

## Installation with HACS
1. Open **HACS**.
2. Add `https://github.com/cm86/home-assistant-kress-fleet` as a custom
   repository of type **Integration**.
3. Install **Kress Fleet**.
4. Restart Home Assistant.
5. Go to **Settings -> Devices & services -> Add integration -> Kress Fleet**.
6. Sign in with the same Kress account used for Fleet.
## Manual installation

Copy:

```text
custom_components/kress_fleet
```

to:

```text
/config/custom_components/kress_fleet
```

Restart Home Assistant and add **Kress Fleet** from **Settings -> Devices & services**.

No YAML configuration is required.

## Live map and coverage

The live-map camera is rendered locally from Fleet map detail + coverage data. No Google Maps
API key is required or exposed.

Layer scheme:

- light green: map area not covered in the selected period
- dark green: covered / mowed area
- red: active No-Go / exclusion area
- grey dashed: disabled No-Go area

Each mower exposes a **Coverage period** select. In an English Home Assistant instance the options are:

- Today
- Last 2 days
- Last 3 days
- Last 4 days
- Last 5 days
- Last 6 days
- Last 7 days

On a German Home Assistant instance the same stable internal options are presented as `Heute`, `Letzte 2 Tage`, and so on. The raw option keys remain language-neutral for automation stability.

Changing the select triggers an immediate coverage refresh. Afterwards the selected period is
polled automatically:

- Today while working: about every 60 seconds
- 2-7 days while working: about every 5 minutes
- idle coverage: about every 10 minutes

Large coverage JSON remains in coordinator memory and is deliberately not stored as entity
attributes or written to Recorder.

### Timezone handling

Fleet API timestamps remain UTC internally. The **coverage from/to** timestamps and the live
mower **position** timestamp displayed in Home Assistant are converted to the timezone
configured for the Home Assistant instance (for example `Europe/Berlin`), including DST.

## Multiple mowers / shared maps

One config entry represents the Fleet account, not one mower. All accessible physical mowers
are discovered, including shared mowers returned by Fleet.

When multiple mowers use the same Fleet map and coverage window, `/coverage` is fetched once
and the in-memory snapshot is shared. Live MQTT state and GPS remain separate per mower.

## Dashboard example

```yaml
type: picture-entity
entity: camera.YOUR_MOWER_LIVE_MAP
show_name: false
show_state: false
camera_view: auto
```

The companion **Kress Fleet Card** is maintained as a separate HACS dashboard repository; it is
not bundled with this integration.

## Debug logging

Temporarily enable:

```yaml
logger:
  logs:
    custom_components.kress_fleet: debug
```

When sharing logs or browser captures, remove credentials and secrets. In particular, never
publish passwords, session cookies, XSRF values, OAuth authorization codes, MQTT tokens or
signatures, Home Assistant camera access tokens, ICCID/IMSI values, or raw authenticated request
headers.

## Private API / stability warning

Kress Fleet does not provide a public API contract for the interfaces used here. The integration
is based on behavior observed in the Fleet web application and can break when Kress changes its
SSO flow, REST schemas, map formats, MQTT authorization, topics, or payloads.

The current implementation has been live-tested with multiple accessible mowers across several
Fleet locations, but that does not guarantee compatibility with every Kress model/account.

## Trademark / branding

Kress, Fleet, Positec, Worx and related product names and marks belong to their respective
owners. They are used only to identify compatibility. This repository intentionally does **not**
bundle the official Kress/Fleet logo or other Kress artwork.

A neutral community icon can be submitted separately to `home-assistant/brands` if this
integration is prepared for HACS default-repository inclusion.

## Credits and license

This project is a modified/derivative work based in part on concepts, structure and code from:

- [MTrab/landroid_cloud](https://github.com/MTrab/landroid_cloud)
- [MTrab/pyworxcloud](https://github.com/MTrab/pyworxcloud)

Both upstream projects are GPL-3.0. This derivative project is distributed under GPL-3.0 as
well. See [LICENSE](LICENSE), [NOTICE](NOTICE) and [UPSTREAM.md](UPSTREAM.md).

No endorsement by the upstream authors or contributors is claimed or implied.
