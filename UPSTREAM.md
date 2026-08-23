# Upstream provenance

`kress_fleet` is a GPL-3.0 derivative project based in part on concepts,
structure and code from MTrab's Home Assistant / Worx-Landroid projects:

- https://github.com/MTrab/landroid_cloud
- https://github.com/MTrab/pyworxcloud

The Kress Fleet backend implemented here substantially diverges from the legacy
cloud backend. Fleet-specific work includes the observed Fleet browser SSO flow,
Fleet REST discovery, Fleet map/coverage processing, AWS IoT MQTT authorization,
live telemetry decoding, multi-mower handling and Home Assistant live-map
rendering.

Fleet-specific modifications began on 2026-08-21.

The exact upstream commit used during the earliest local prototyping was not
recorded. This does not change the GPL-3.0 licensing/attribution of the reused
material; this file documents the known project-level provenance without
claiming a more precise commit history than is available.


## Error-code protocol reference

The mower error-code labels used by `kress_fleet` are cross-checked against the
community-maintained `ioBroker.worx` Worx/Kress protocol documentation:

- https://github.com/iobroker-community-adapters/ioBroker.worx

That project is GPL-3.0 licensed. The mapping is treated here as protocol
metadata and is exposed through language-neutral state keys plus Home Assistant
translations.
