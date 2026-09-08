# SPDX-License-Identifier: GPL-3.0-only
# This file is part of kress_fleet, a modified work derived in part from
# MTrab/landroid_cloud and MTrab/pyworxcloud (GPL-3.0).
# Kress Fleet modifications began on 2026-08-21; see NOTICE and LICENSE.

"""Config flow for Kress normal cloud and Kress Fleet."""

from __future__ import annotations

import asyncio
import logging

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from pyworxcloud import WorxCloud
from pyworxcloud.exceptions import (
    APIException, AuthorizationError, ForbiddenError, InternalServerError,
    NoConnectionError, NotFoundError, RequestError, ServiceUnavailableError,
    TooManyRequestsError,
)

from .api import FleetAuthError, FleetConnectionError, FleetError, KressFleetApi
from .const import (
    BACKEND_FLEET,
    BACKEND_KRESS,
    CONFIG_ENTRY_VERSION,
    CONF_BACKEND,
    DOMAIN,
    NAME,
)

_LOGGER = logging.getLogger(__name__)


def _backend(entry) -> str:
    return str(entry.data.get(CONF_BACKEND, BACKEND_FLEET))


def _unique_id(email: str, backend: str) -> str:
    return f"{email.casefold()}::{backend}"


def _account_already_configured(hass, email: str, backend: str) -> bool:
    wanted = email.casefold()
    return any(
        str(entry.data.get(CONF_USERNAME, "")).casefold() == wanted
        and _backend(entry) == backend
        for entry in hass.config_entries.async_entries(DOMAIN)
    )


async def _validate_fleet(hass, email: str, password: str) -> None:
    session = async_create_clientsession(
        hass, cookie_jar=aiohttp.CookieJar(unsafe=True, quote_cookie=False)
    )
    api = KressFleetApi(session, email, password, hass.config.time_zone)
    try:
        async with asyncio.timeout(75):
            await api.async_authenticate()
            await api.async_probe()
    finally:
        await api.async_close()


async def _validate_normal(hass, email: str, password: str) -> None:
    cloud = WorxCloud(email, password, "kress", tz=hass.config.time_zone)
    try:
        async with asyncio.timeout(45):
            await cloud.authenticate()
            connected = await cloud.connect()
            if not connected:
                raise NoConnectionError("No Kress mower found")
    finally:
        await cloud.disconnect()


class KressFleetConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle Kress configuration."""

    VERSION = CONFIG_ENTRY_VERSION

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            email = str(user_input[CONF_USERNAME]).strip()
            password = user_input[CONF_PASSWORD]
            backend = str(user_input[CONF_BACKEND])

            if _account_already_configured(self.hass, email, backend):
                return self.async_abort(reason="already_configured")

            await self.async_set_unique_id(_unique_id(email, backend))
            self._abort_if_unique_id_configured()

            try:
                if backend == BACKEND_FLEET:
                    await _validate_fleet(self.hass, email, password)
                else:
                    await _validate_normal(self.hass, email, password)
            except TimeoutError:
                errors["base"] = "cannot_connect"
            except (FleetAuthError, AuthorizationError):
                errors["base"] = "invalid_auth"
            except (
                FleetConnectionError, FleetError, NoConnectionError,
                ServiceUnavailableError, RequestError, ForbiddenError,
                NotFoundError, InternalServerError, TooManyRequestsError, APIException,
            ) as err:
                _LOGGER.warning("Kress setup failed: %s", err)
                errors["base"] = "cannot_connect"
            else:
                title = "Kress Fleet" if backend == BACKEND_FLEET else "Kress"
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_BACKEND: backend,
                        CONF_USERNAME: email,
                        CONF_PASSWORD: password,
                    },
                )

        schema = vol.Schema({
            vol.Required(CONF_BACKEND, default=BACKEND_FLEET): vol.In({
                BACKEND_FLEET: "Kress Fleet",
                BACKEND_KRESS: "Kress / Mission",
            }),
            vol.Required(CONF_USERNAME): str,
            vol.Required(CONF_PASSWORD): str,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data):
        self._reauth_entry = self._get_reauth_entry()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        errors: dict[str, str] = {}
        entry = self._reauth_entry
        if user_input is not None:
            password = user_input[CONF_PASSWORD]
            backend = _backend(entry)
            try:
                if backend == BACKEND_FLEET:
                    await _validate_fleet(
                        self.hass, entry.data[CONF_USERNAME], password
                    )
                else:
                    await _validate_normal(
                        self.hass, entry.data[CONF_USERNAME], password
                    )
            except (FleetAuthError, AuthorizationError):
                errors["base"] = "invalid_auth"
            except (Exception,):
                _LOGGER.warning("Kress reauthentication failed", exc_info=True)
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: password}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )
