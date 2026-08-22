# SPDX-License-Identifier: GPL-3.0-only
# This file is part of kress_fleet, a modified work derived in part from
# MTrab/landroid_cloud and MTrab/pyworxcloud (GPL-3.0).
# Kress Fleet modifications began on 2026-08-21; see NOTICE and LICENSE.

"""Constants for Kress Fleet."""

from __future__ import annotations

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "kress_fleet"
NAME: Final = "Kress Fleet"
VERSION: Final = "0.3.0"

PLATFORMS: Final = [
    Platform.LAWN_MOWER,
    Platform.DEVICE_TRACKER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.CAMERA,
    Platform.SELECT,
]

FLEET_BASE_URL: Final = "https://fleet.kress.com"
SSO_BASE_URL: Final = "https://id.kress.com"

# Fleet OAuth client/state/PKCE values are deliberately NOT hard-coded.
# The integration starts at Fleet /login and lets Fleet generate them.

API_VERSION: Final = "2026-06-04"
APP_VERSION: Final = "SNAPSHOT"
BRAND_PREFIX: Final = "KR"

DEFAULT_COVERAGE_INTERVAL: Final = 60
MQTT_PORT: Final = 443
MQTT_PATH: Final = "/mqtt"
MQTT_CONNECT_TIMEOUT: Final = 8
MQTT_RETRY_SECONDS: Final = 60
MQTT_REFRESH_MARGIN_SECONDS: Final = 120


COVERAGE_PERIOD_OPTIONS: Final[dict[str, int]] = {
    "Heute": 1,
    "Letzten 2 Tage": 2,
    "Letzten 3 Tage": 3,
    "Letzten 4 Tage": 4,
    "Letzten 5 Tage": 5,
    "Letzten 6 Tage": 6,
    "Letzten 7 Tage": 7,
}
