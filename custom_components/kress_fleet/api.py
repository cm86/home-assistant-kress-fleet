# SPDX-License-Identifier: GPL-3.0-only
# This file is part of kress_fleet, a modified work derived in part from
# MTrab/landroid_cloud and MTrab/pyworxcloud (GPL-3.0).
# Kress Fleet modifications began on 2026-08-21; see NOTICE and LICENSE.

"""Async REST and browser-like SSO client for Kress Fleet."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
import json
import logging
import re
from typing import Any
from urllib.parse import quote, unquote, urljoin
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp
from yarl import URL

from .const import (
    API_VERSION,
    APP_VERSION,
    BRAND_PREFIX,
    FLEET_BASE_URL,
    SSO_BASE_URL,
)
from .models import FleetMower

_LOGGER = logging.getLogger(__name__)


class FleetError(Exception):
    """Base Fleet exception."""


class FleetAuthError(FleetError):
    """Authentication failed."""


class FleetConnectionError(FleetError):
    """Cloud connection failed."""


class FleetApiError(FleetError):
    """Fleet API request failed."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class _LoginFormParser(HTMLParser):
    """Extract HTML forms and inputs without pulling another dependency."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "form":
            self._current = {
                "action": values.get("action", ""),
                "method": values.get("method", "post").lower(),
                "inputs": [],
            }
            self.forms.append(self._current)
            return
        if tag.lower() == "input" and self._current is not None:
            name = values.get("name", "")
            if not name:
                return
            self._current["inputs"].append(
                {
                    "name": name,
                    "type": values.get("type", "text").lower(),
                    "value": values.get("value", ""),
                }
            )

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "form":
            self._current = None


class KressFleetApi:
    """Kress Fleet REST client."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
        timezone_name: str = "UTC",
    ) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._timezone_name = timezone_name
        self._csrf_meta: str | None = None
        self._actor: dict[str, Any] | None = None
        self._auth_user: dict[str, Any] | None = None
        self._auth_lock = asyncio.Lock()
        self._authenticated = False
        self._session_generation = 0

    @property
    def actor(self) -> dict[str, Any] | None:
        return self._actor

    async def async_close(self) -> None:
        """Release client resources.

        The aiohttp session is created/owned by Home Assistant via
        async_create_clientsession(), so this integration must never close it.
        """
        return

    async def async_authenticate(self) -> None:
        """Create a real Fleet web session through its OAuth2/PKCE SSO flow.

        Fleet itself creates state, code_challenge and the PKCE verifier when
        /login is opened.  Following that route is deliberately preferred to
        reimplementing private PKCE parameters in Home Assistant.
        """
        async with self._auth_lock:
            await self._async_fleet_sso_login(force=True)

        actor = await self._async_request("GET", "/api/actor", retry_auth=True)
        unwrapped = _unwrap(actor)
        if not isinstance(unwrapped, dict):
            raise FleetAuthError("Fleet actor response is invalid")
        self._actor = unwrapped
        _LOGGER.debug("Kress Fleet authentication completed")

    async def async_ensure_auth(self) -> None:
        """Ensure a Fleet session exists; expired sessions are retried on 401."""
        if self._authenticated:
            return
        async with self._auth_lock:
            if not self._authenticated:
                await self._async_fleet_sso_login(force=False)

    async def _async_fleet_sso_login(self, *, force: bool) -> None:
        """Perform Fleet -> id.kress.com -> Fleet login in one cookie jar."""
        if self._authenticated and not force:
            return

        self._authenticated = False
        self._actor = None
        self._auth_user = None
        self._csrf_meta = None

        # Starting at Fleet's own login endpoint is intentional: Fleet then
        # generates the current OAuth client, state and PKCE challenge itself
        # and keeps the matching verifier in its server-side session.
        fleet_url = URL(FLEET_BASE_URL)
        id_url = URL(SSO_BASE_URL)
        self._session.cookie_jar.clear_domain(fleet_url.host or "fleet.kress.com")
        self._session.cookie_jar.clear_domain(id_url.host or "id.kress.com")

        redirect_target = f"{FLEET_BASE_URL}/"
        start_url = f"{FLEET_BASE_URL}/login?redirect={quote(redirect_target, safe='')}"

        status, final_url, html = await self._async_fetch_html(
            "GET",
            start_url,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        _LOGGER.debug(
            "Fleet SSO entry -> HTTP %s final=%s",
            status,
            _safe_url_for_log(final_url),
        )

        # If Fleet already gave us an authenticated session, do not submit any
        # credential form. Normally the redirect ends at id.kress.com here.
        if final_url.host == URL(FLEET_BASE_URL).host:
            self._parse_bootstrap_html(html)
            if self._auth_user or self._has_fleet_session_cookie():
                await self._async_finish_fleet_session(html)
                return

        if final_url.host != URL(SSO_BASE_URL).host:
            raise FleetAuthError(
                f"Unexpected Kress SSO redirect ({_safe_url_for_log(final_url)})"
            )

        form = _extract_login_form(html)
        if form is None:
            raise FleetAuthError("Could not find the Kress SSO login form")

        fields: dict[str, str] = {}
        email_field: str | None = None
        password_field: str | None = None
        for item in form["inputs"]:
            name = str(item["name"])
            input_type = str(item["type"])
            value = str(item["value"])
            # Submit hidden/default values, including CSRF tokens. Unchecked
            # checkboxes are intentionally skipped unless the server provided a
            # hidden counterpart.
            if input_type not in {"submit", "button", "file", "checkbox", "radio"}:
                fields[name] = value
            lowered = name.casefold()
            if input_type == "email" or lowered in {"email", "username", "user"} or "email" in lowered:
                email_field = email_field or name
            if input_type == "password" or "password" in lowered or lowered == "pass":
                password_field = password_field or name

        if email_field is None or password_field is None:
            raise FleetAuthError("Kress SSO login form fields could not be identified")

        fields[email_field] = self._email
        fields[password_field] = self._password
        action = urljoin(str(final_url), str(form.get("action") or ""))
        method = str(form.get("method") or "post").upper()

        _LOGGER.debug(
            "Kress SSO form discovered: method=%s action=%s fields=%s",
            method,
            _safe_url_for_log(URL(action)),
            sorted(name for name in fields if name not in {email_field, password_field}),
        )

        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": str(final_url),
            "Origin": f"{final_url.scheme}://{final_url.host}",
        }
        if method == "GET":
            status, after_url, after_html = await self._async_fetch_html(
                "GET", action, params=fields, headers=headers
            )
        else:
            status, after_url, after_html = await self._async_fetch_html(
                "POST", action, data=fields, headers=headers
            )

        _LOGGER.debug(
            "Kress SSO credential submit -> HTTP %s final=%s",
            status,
            _safe_url_for_log(after_url),
        )

        if after_url.host == URL(SSO_BASE_URL).host:
            # Remaining on an id.kress.com password form means authentication
            # was rejected. If it is some other page (e.g. a future consent
            # screen), report that distinctly so the next capture is obvious.
            if _extract_login_form(after_html) is not None:
                raise FleetAuthError("Kress SSO rejected the email/password")
            raise FleetAuthError(
                f"Kress SSO stopped before Fleet ({_safe_url_for_log(after_url)})"
            )

        if after_url.host != URL(FLEET_BASE_URL).host:
            raise FleetAuthError(
                f"Kress SSO returned to an unexpected host ({_safe_url_for_log(after_url)})"
            )

        await self._async_finish_fleet_session(after_html)

    async def _async_finish_fleet_session(self, html: str) -> None:
        """Parse Fleet callback cookies/page and mark the session usable."""
        self._parse_bootstrap_html(html)

        # The callback commonly ends on /splash. Fetching / once makes sure the
        # Laravel XSRF cookie/meta value is present before the API is called.
        if self._xsrf_token() is None:
            _, _, root_html = await self._async_fetch_html(
                "GET",
                f"{FLEET_BASE_URL}/",
                headers={"Accept": "text/html,application/xhtml+xml"},
            )
            self._parse_bootstrap_html(root_html)

        cookie_names = sorted(
            cookie.key
            for cookie in self._session.cookie_jar.filter_cookies(URL(FLEET_BASE_URL)).values()
        )
        _LOGGER.debug(
            "Fleet SSO callback complete: auth_user=%s xsrf=%s cookies=%s",
            bool(self._auth_user),
            bool(self._xsrf_token()),
            cookie_names,
        )
        self._authenticated = True
        self._session_generation += 1

    async def _async_fetch_html(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, URL, str]:
        try:
            async with self._session.request(
                method,
                url,
                data=data,
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
                allow_redirects=True,
            ) as response:
                text = await response.text(errors="replace")
                if response.status >= 500:
                    raise FleetConnectionError(
                        f"Kress service returned HTTP {response.status}"
                    )
                return response.status, response.url, text
        except (aiohttp.ClientError, TimeoutError) as err:
            raise FleetConnectionError("Could not reach Kress SSO/Fleet") from err

    def _parse_bootstrap_html(self, html: str) -> None:
        csrf_match = re.search(
            r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)',
            html,
            flags=re.IGNORECASE,
        )
        if csrf_match:
            self._csrf_meta = csrf_match.group(1)

        auth_match = re.search(
            r'<meta\s+name=["\']auth-user["\']\s+content=["\']([^"\']+)',
            html,
            flags=re.IGNORECASE,
        )
        if auth_match:
            raw = (
                auth_match.group(1)
                .replace("&quot;", '"')
                .replace("&amp;", "&")
                .replace("&#039;", "'")
            )
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                value = None
            if isinstance(value, dict):
                self._auth_user = value

    def _has_fleet_session_cookie(self) -> bool:
        cookies = self._session.cookie_jar.filter_cookies(URL(FLEET_BASE_URL))
        return any(name.casefold() in {"session", "laravel_session"} for name in cookies)

    def _xsrf_token(self) -> str | None:
        cookies = self._session.cookie_jar.filter_cookies(URL(FLEET_BASE_URL))
        cookie = cookies.get("XSRF-TOKEN")
        if cookie and cookie.value:
            return unquote(cookie.value)
        return self._csrf_meta

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "x-api-version": API_VERSION,
            "x-app-version": APP_VERSION,
            "x-brand-prefix": BRAND_PREFIX,
        }
        if xsrf := self._xsrf_token():
            headers["x-xsrf-token"] = xsrf
        return headers

    async def _async_request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        has_json_body: bool = False,
        retry_auth: bool = True,
    ) -> Any:
        if retry_auth:
            await self.async_ensure_auth()

        async def perform() -> tuple[int, Any]:
            kwargs: dict[str, Any] = {
                "headers": self._headers(),
                "timeout": aiohttp.ClientTimeout(total=30),
            }
            if has_json_body:
                kwargs["json"] = json_body
            try:
                async with self._session.request(
                    method,
                    f"{FLEET_BASE_URL}{path}",
                    **kwargs,
                ) as response:
                    body = await _response_json(response)
                    return response.status, body
            except (aiohttp.ClientError, TimeoutError) as err:
                raise FleetConnectionError("Kress Fleet request failed") from err

        generation = self._session_generation
        status, body = await perform()
        _LOGGER.debug("Fleet %s %s -> HTTP %s", method, path, status)
        if status in (401, 403, 419) and retry_auth:
            async with self._auth_lock:
                # Another concurrent request may already have refreshed the
                # Fleet session while this coroutine waited for the lock.
                if self._session_generation == generation:
                    _LOGGER.debug("Fleet session expired; running SSO again")
                    await self._async_fleet_sso_login(force=True)
            status, body = await perform()
            _LOGGER.debug("Fleet %s %s after SSO refresh -> HTTP %s", method, path, status)

        if status in (401, 403, 419):
            self._authenticated = False
            raise FleetAuthError(f"Kress Fleet rejected the session (HTTP {status})")
        if status >= 400:
            raise FleetApiError(_safe_error(body, status), status=status)
        return body

    async def async_get_actor(self) -> dict[str, Any]:
        body = await self._async_request("GET", "/api/actor")
        data = _unwrap(body)
        if not isinstance(data, dict):
            raise FleetApiError("Invalid actor response")
        self._actor = data
        return data

    def actor_user_id(self) -> int:
        """Return the Fleet user id from actor/bootstrap data."""
        # Prefer direct user identifiers. Recursively searching a large actor
        # response for a generic "id" can accidentally select a location or
        # product id instead of the authenticated Fleet user.
        for source in (self._actor, self._auth_user):
            if not isinstance(source, dict):
                continue
            for key in ("id", "user_id", "userId"):
                value = source.get(key)
                try:
                    if value is not None:
                        return int(value)
                except (TypeError, ValueError):
                    continue
            user = source.get("user")
            if isinstance(user, dict):
                for key in ("id", "user_id", "userId"):
                    value = user.get(key)
                    try:
                        if value is not None:
                            return int(value)
                    except (TypeError, ValueError):
                        continue
        raise FleetApiError("Could not determine Fleet user id")

    async def async_get_locations(self, user_id: int) -> list[dict[str, Any]]:
        body = await self._async_request("GET", f"/api/users/{user_id}/locations")
        data = _unwrap(body)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("locations", "items"):
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    async def async_get_mqtt_credentials(self, user_id: int, location_id: int) -> list[dict[str, Any]]:
        body = await self._async_request(
            "POST", f"/api/users/{user_id}/locations/{location_id}/mqtt"
        )
        data = _unwrap(body)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            return [data]
        return []

    async def async_get_maps(self, user_id: int, location_id: int) -> list[dict[str, Any]]:
        body = await self._async_request(
            "GET", f"/api/users/{user_id}/locations/{location_id}/maps"
        )
        data = _unwrap(body)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []

    async def async_get_map_detail(
        self, user_id: int, location_id: int, map_id: str
    ) -> dict[str, Any] | None:
        body = await self._async_request(
            "GET",
            f"/api/users/{user_id}/locations/{location_id}/maps/{map_id}",
        )
        data = _unwrap(body)
        return data if isinstance(data, dict) else None

    async def async_get_product_item(
        self, user_id: int, location_id: int, mower_uuid: str
    ) -> dict[str, Any]:
        body = await self._async_request(
            "GET",
            f"/api/users/{user_id}/locations/{location_id}/product-items/{mower_uuid}",
        )
        data = _unwrap(body)
        return data if isinstance(data, dict) else {}

    async def async_probe(self) -> None:
        """Quickly verify that the account exposes at least one Fleet mower.

        The config flow intentionally does not fetch every product detail, map
        and coverage object.  Accounts may contain many owned/shared mowers, so
        doing the full discovery while the dialog is open makes setup fragile.
        """
        if self._actor is None:
            await self.async_get_actor()
        user_id = self.actor_user_id()
        locations = await self.async_get_locations(user_id)
        if not locations:
            raise FleetApiError("No Kress Fleet locations were discovered")

        location_ids: list[int] = []
        for location in locations:
            value = _find_first(location, ("id", "location_id", "locationId"))
            try:
                location_ids.append(int(value))
            except (TypeError, ValueError):
                continue

        async def has_mower(location_id: int) -> bool:
            try:
                credentials = await self.async_get_mqtt_credentials(user_id, location_id)
            except FleetError:
                return False
            for credential in credentials:
                items = credential.get("items")
                if isinstance(items, dict) and items:
                    return True
            return False

        if location_ids:
            results = await asyncio.gather(*(has_mower(location_id) for location_id in location_ids))
            if any(results):
                return
        raise FleetApiError("No Kress Fleet mower was discovered")

    async def async_discover(self) -> dict[str, FleetMower]:
        """Discover mowers without blocking startup on every product detail.

        Fleet currently exposes two MQTT identifiers per physical mower.  The
        real product UUID is also present in the map allocation list, so mapped
        mowers can be created immediately from MQTT + map metadata.  Their
        product details (name/model/serial/last status) are enriched after HA
        setup in the background.

        MQTT identifiers that are not allocated to any map are probed briefly
        so a legitimate mower without a map is still discovered, while a slow
        or broken product endpoint can no longer stall Home Assistant startup.
        """
        if self._actor is None:
            await self.async_get_actor()
        user_id = self.actor_user_id()
        locations = await self.async_get_locations(user_id)

        async def discover_location(location: dict[str, Any]) -> list[FleetMower]:
            location_id_value = _find_first(
                location, ("id", "location_id", "locationId")
            )
            try:
                location_id = int(location_id_value)
            except (TypeError, ValueError):
                return []

            try:
                credentials, maps = await asyncio.gather(
                    self.async_get_mqtt_credentials(user_id, location_id),
                    self.async_get_maps(user_id, location_id),
                )
            except FleetApiError:
                _LOGGER.debug("Could not read one Fleet location", exc_info=True)
                return []

            topic_items: dict[str, dict[str, Any]] = {}
            for credential in credentials:
                items = credential.get("items")
                if isinstance(items, dict):
                    for mower_uuid, topics in items.items():
                        if isinstance(topics, dict):
                            topic_items[str(mower_uuid)] = topics

            map_assignments = _map_assignments(maps)

            def skeleton(mower_uuid: str, topics: dict[str, Any]) -> FleetMower:
                map_id, map_name = map_assignments.get(mower_uuid, (None, None))
                return FleetMower(
                    uuid=mower_uuid,
                    user_id=user_id,
                    location_id=location_id,
                    name="Kress Fleet",
                    map_id=map_id,
                    map_name=map_name,
                    command_in=_string_or_none(topics.get("command_in")),
                    command_out=_string_or_none(topics.get("command_out")),
                )

            # Map allocations identify the physical product UUIDs without an
            # extra product-item request.  This is the fast path used by normal
            # Fleet mower setups and removes the slowest part of HA startup.
            mapped_ids = [
                mower_uuid
                for mower_uuid in topic_items
                if mower_uuid in map_assignments
            ]
            valid: list[FleetMower] = [
                skeleton(mower_uuid, topic_items[mower_uuid])
                for mower_uuid in mapped_ids
            ]

            # A mower may legitimately have no map assigned yet. Probe only the
            # remaining identifiers, and cap each probe so one bad endpoint can
            # never hold up Home Assistant for the normal 30 second REST timeout.
            unmapped_ids = [
                mower_uuid for mower_uuid in topic_items if mower_uuid not in map_assignments
            ]

            async def probe_unmapped(mower_uuid: str) -> FleetMower | None:
                try:
                    async with asyncio.timeout(2.5):
                        detail = await self.async_get_product_item(
                            user_id, location_id, mower_uuid
                        )
                except TimeoutError:
                    _LOGGER.debug(
                        "Deferring slow unmapped Fleet product identifier in location %s",
                        location_id,
                    )
                    return None
                except FleetApiError as err:
                    if err.status == 404:
                        return None
                    _LOGGER.debug(
                        "Unmapped Fleet product probe failed in location %s",
                        location_id,
                        exc_info=True,
                    )
                    return None
                except FleetError:
                    _LOGGER.debug(
                        "Unmapped Fleet product probe failed in location %s",
                        location_id,
                        exc_info=True,
                    )
                    return None

                mower = skeleton(mower_uuid, topic_items[mower_uuid])
                mower.product_detail = detail
                if name := _extract_name(detail):
                    mower.name = name
                mower.model = _extract_model(detail)
                mower.serial_number = _extract_serial(detail)
                mower.metadata_ready = True
                online = _find_first(detail, ("online", "is_online"))
                if online is not None:
                    mower.online = bool(online)
                payload = _find_payload(detail)
                if payload:
                    mower.update_payload(payload)
                return mower

            if unmapped_ids:
                probed = await asyncio.gather(
                    *(probe_unmapped(mower_uuid) for mower_uuid in unmapped_ids)
                )
                valid.extend(mower for mower in probed if mower is not None)

            _LOGGER.debug(
                "Fleet location %s startup discovery: %s MQTT identifiers, "
                "%s mapped mower(s), %s unmapped candidate(s), %s mower(s) ready",
                location_id,
                len(topic_items),
                len(mapped_ids),
                len(unmapped_ids),
                len(valid),
            )
            return valid

        discovered = await asyncio.gather(
            *(discover_location(location) for location in locations)
        )
        mowers = {mower.uuid: mower for group in discovered for mower in group}

        if not mowers:
            raise FleetApiError("No Kress Fleet mower was discovered")
        _LOGGER.info("Discovered %s Kress Fleet mower(s)", len(mowers))
        return mowers

    async def async_update_product_state(self, mower: FleetMower) -> None:
        """Refresh the product item as a low-rate fallback for MQTT."""
        try:
            detail = await self.async_get_product_item(
                mower.user_id, mower.location_id, mower.uuid
            )
        except FleetApiError as err:
            # A mower that disappears temporarily from product-items must not
            # make every Fleet entity unavailable. MQTT can still update it.
            if err.status == 404:
                _LOGGER.debug(
                    "Fleet product item temporarily unavailable for mower %s",
                    mower.uuid[:8],
                )
                return
            raise
        mower.product_detail = detail
        online = _find_first(detail, ("online", "is_online"))
        if online is not None:
            mower.online = bool(online)
        payload = _find_payload(detail)
        if payload:
            mower.update_payload(payload)
        if name := _extract_name(detail):
            mower.name = name
        if model := _extract_model(detail):
            mower.model = model
        if serial := _extract_serial(detail):
            mower.serial_number = serial
        mower.metadata_ready = True

    async def async_update_map_metadata(self, mower: FleetMower) -> None:
        """Refresh map assignment/detail for one mower."""
        await self.async_update_map_metadata_group([mower])

    async def async_update_map_metadata_group(
        self, mowers: list[FleetMower]
    ) -> None:
        """Refresh maps once per location and map detail once per unique map.

        Fleet accounts can expose many mowers in the same location.  The old
        implementation fetched ``/maps`` once for every mower, which was safe
        but wasteful.  This grouped refresh keeps the same semantics while
        avoiding duplicate requests.
        """
        by_location: dict[tuple[int, int], list[FleetMower]] = {}
        for mower in mowers:
            by_location.setdefault((mower.user_id, mower.location_id), []).append(mower)

        async def refresh_location(group: list[FleetMower]) -> None:
            representative = group[0]
            maps = await self.async_get_maps(
                representative.user_id, representative.location_id
            )
            assignments = _map_assignments(maps)
            for mower in group:
                if mower.uuid in assignments:
                    new_map_id, new_map_name = assignments[mower.uuid]
                    if new_map_id != mower.map_id:
                        # A changed assignment invalidates any lazily loaded
                        # geometry/coverage from the previous map.
                        mower.map_detail = None
                        mower.map_revision += 1
                        mower.coverage = []
                        mower.coverage_from = None
                        mower.coverage_to = None
                        mower.coverage_revision = 0
                    mower.map_id, mower.map_name = new_map_id, new_map_name

            map_groups: dict[str, list[FleetMower]] = {}
            for mower in group:
                # Keep never-viewed maps lazy even during periodic metadata
                # refreshes. Only refresh detail for maps that have actually
                # been loaded by a camera before.
                if mower.map_id and mower.map_detail is not None:
                    map_groups.setdefault(mower.map_id, []).append(mower)

            async def refresh_map(assigned: list[FleetMower]) -> None:
                sample = assigned[0]
                try:
                    detail = await self.async_get_map_detail(
                        sample.user_id, sample.location_id, sample.map_id or ""
                    )
                except FleetError:
                    _LOGGER.debug("Map detail refresh failed", exc_info=True)
                    return
                for mower in assigned:
                    if detail != mower.map_detail:
                        mower.map_detail = detail
                        mower.map_revision += 1

            await asyncio.gather(
                *(refresh_map(assigned) for assigned in map_groups.values())
            )

        await asyncio.gather(
            *(refresh_location(group) for group in by_location.values())
        )

    async def async_get_coverage(
        self, mower: FleetMower, days: int | None = None
    ) -> tuple[list[dict[str, Any]], str | None, str | None]:
        """Fetch one map-level coverage snapshot.

        Coverage belongs to a Fleet map, not to an individual mower. The
        coordinator therefore calls this once per unique map and shares the
        result between every mower currently assigned to that map.
        """
        if not mower.map_id:
            return [], None, None
        period_days = max(1, min(7, int(days or mower.coverage_days or 1)))
        from_value = _local_period_start_utc(self._timezone_name, period_days)
        body = await self._async_request(
            "POST",
            f"/api/users/{mower.user_id}/locations/{mower.location_id}/maps/{mower.map_id}/coverage",
            json_body={"from": from_value},
            has_json_body=True,
        )
        data = _unwrap(body)
        if not isinstance(data, dict):
            return [], None, None
        raw_coverage = data.get("coverage")
        coverage = (
            [item for item in raw_coverage if isinstance(item, dict)]
            if isinstance(raw_coverage, list)
            else []
        )
        return (
            coverage,
            _string_or_none(data.get("from")),
            _string_or_none(data.get("to")),
        )

    async def async_update_coverage(self, mower: FleetMower) -> None:
        """Backward-compatible single-mower coverage refresh."""
        coverage, coverage_from, coverage_to = await self.async_get_coverage(
            mower, mower.coverage_days
        )
        mower.coverage = coverage
        mower.coverage_from = coverage_from
        mower.coverage_to = coverage_to
        mower.coverage_revision += 1



def _extract_login_form(html: str) -> dict[str, Any] | None:
    """Return the first form containing a password input."""
    parser = _LoginFormParser()
    try:
        parser.feed(html)
    except Exception:
        return None
    for form in parser.forms:
        inputs = form.get("inputs", [])
        if any(str(item.get("type", "")).casefold() == "password" for item in inputs):
            return form
    return None


def _safe_url_for_log(url: URL) -> str:
    """Log only scheme/host/path; OAuth query values contain secrets."""
    return f"{url.scheme}://{url.host}{url.path}"

def _local_period_start_utc(timezone_name: str, days: int = 1) -> str:
    """Return local midnight for an inclusive 1-7 day calendar window in UTC.

    ``days=1`` means today from local midnight. ``days=2`` means yesterday
    plus today, matching the Fleet application's calendar-style history range.
    """
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        tz = UTC
    period_days = max(1, min(7, int(days)))
    now = datetime.now(tz)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = midnight - timedelta(days=period_days - 1)
    utc_value = start.astimezone(UTC)
    return utc_value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _local_midnight_utc(timezone_name: str) -> str:
    """Backward-compatible alias for today's local midnight."""
    return _local_period_start_utc(timezone_name, 1)


async def _response_json(response: aiohttp.ClientResponse) -> Any:
    text = await response.text(errors="replace")
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"message": text[:500]}


def _unwrap(value: Any) -> Any:
    if isinstance(value, dict) and "data" in value:
        return value["data"]
    return value


def _safe_error(body: Any, status: int) -> str:
    if isinstance(body, dict):
        for key in ("message", "error_description", "error"):
            value = body.get(key)
            if isinstance(value, str) and value:
                # Never leak token-ish fields returned by an auth endpoint.
                return f"Kress cloud error (HTTP {status}): {value[:180]}"
    return f"Kress cloud returned HTTP {status}"


def _find_first(value: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        for key in keys:
            if key in value and value[key] not in (None, ""):
                return value[key]
        for child in value.values():
            result = _find_first(child, keys)
            if result not in (None, ""):
                return result
    elif isinstance(value, list):
        for child in value:
            result = _find_first(child, keys)
            if result not in (None, ""):
                return result
    return None


def _extract_name(detail: dict[str, Any]) -> str | None:
    value = _find_first(detail, ("name", "nickname", "display_name"))
    return _string_or_none(value)


def _extract_model(detail: dict[str, Any]) -> str | None:
    model = detail.get("model")
    if isinstance(model, dict):
        for key in ("code", "name", "model"):
            if model.get(key):
                return str(model[key])
    value = _find_first(detail, ("model_code", "modelCode", "model"))
    if isinstance(value, (str, int, float)):
        return str(value)
    return None


def _extract_serial(detail: dict[str, Any]) -> str | None:
    value = _find_first(detail, ("serial_number", "serialNumber", "serial", "sn"))
    return _string_or_none(value)


def _find_payload(detail: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("last_status", "lastStatus", "status"):
        value = detail.get(key)
        if isinstance(value, dict):
            payload = value.get("payload")
            if isinstance(payload, dict) and ("cfg" in payload or "dat" in payload):
                return payload
    if "cfg" in detail or "dat" in detail:
        return detail
    return None


def _map_assignments(maps: list[dict[str, Any]]) -> dict[str, tuple[str | None, str | None]]:
    result: dict[str, tuple[str | None, str | None]] = {}
    for record in maps:
        map_data = record.get("map") if isinstance(record.get("map"), dict) else record
        map_id = _string_or_none(map_data.get("id")) if isinstance(map_data, dict) else None
        if not map_id:
            continue
        map_name = _string_or_none(map_data.get("name")) if isinstance(map_data, dict) else None
        active = bool(map_data.get("active", True)) if isinstance(map_data, dict) else True
        allocated = record.get("product_items_allocated")
        if not isinstance(allocated, list):
            allocated = record.get("productItemsAllocated")
        if not isinstance(allocated, list):
            continue
        for item in allocated:
            mower_uuid = None
            if isinstance(item, str):
                mower_uuid = item
            elif isinstance(item, dict):
                mower_uuid = _string_or_none(_find_first(item, ("uuid", "id")))
            if mower_uuid and (active or mower_uuid not in result):
                result[mower_uuid] = (map_id, map_name)
    return result


def _string_or_none(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None
