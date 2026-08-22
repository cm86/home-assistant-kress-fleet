# SPDX-License-Identifier: GPL-3.0-only
# This file is part of kress_fleet, a modified work derived in part from
# MTrab/landroid_cloud and MTrab/pyworxcloud (GPL-3.0).
# Kress Fleet modifications began on 2026-08-21; see NOTICE and LICENSE.

"""Config flow for Kress Fleet."""

from __future__ import annotations

import asyncio
import logging

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import FleetAuthError, FleetConnectionError, FleetError, KressFleetApi
from .const import DOMAIN, NAME

_LOGGER = logging.getLogger(__name__)


class KressFleetConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle Kress Fleet configuration."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Set up Kress Fleet with email and password."""
        errors: dict[str, str] = {}
        if user_input is not None:
            email = str(user_input[CONF_USERNAME]).strip()
            await self.async_set_unique_id(email.casefold())
            self._abort_if_unique_id_configured()

            session = async_create_clientsession(
                self.hass,
                cookie_jar=aiohttp.CookieJar(unsafe=True, quote_cookie=False),
            )
            api = KressFleetApi(
                session,
                email,
                user_input[CONF_PASSWORD],
                self.hass.config.time_zone,
            )
            try:
                async with asyncio.timeout(75):
                    await api.async_authenticate()
                    await api.async_probe()
            except TimeoutError:
                _LOGGER.warning("Kress Fleet setup timed out")
                errors["base"] = "cannot_connect"
            except FleetAuthError as err:
                _LOGGER.warning("Kress Fleet authentication failed: %s", err)
                errors["base"] = "invalid_auth"
            except FleetConnectionError as err:
                _LOGGER.warning("Kress Fleet connection failed: %s", err)
                errors["base"] = "cannot_connect"
            except FleetError as err:
                _LOGGER.warning("Kress Fleet discovery failed: %s", err)
                errors["base"] = "no_mowers"
            else:
                return self.async_create_entry(
                    title=NAME,
                    data={
                        CONF_USERNAME: email,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )
            finally:
                await api.async_close()

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data):
        """Start a reauthentication flow."""
        self._reauth_entry = self._get_reauth_entry()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Confirm new credentials."""
        errors: dict[str, str] = {}
        entry = self._reauth_entry
        if user_input is not None:
            session = async_create_clientsession(
                self.hass,
                cookie_jar=aiohttp.CookieJar(unsafe=True, quote_cookie=False),
            )
            api = KressFleetApi(
                session,
                entry.data[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                self.hass.config.time_zone,
            )
            try:
                async with asyncio.timeout(60):
                    await api.async_authenticate()
            except TimeoutError:
                _LOGGER.warning("Kress Fleet reauthentication timed out")
                errors["base"] = "cannot_connect"
            except FleetAuthError as err:
                _LOGGER.warning("Kress Fleet reauthentication failed: %s", err)
                errors["base"] = "invalid_auth"
            except FleetError as err:
                _LOGGER.warning("Kress Fleet reauthentication connection failed: %s", err)
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
            finally:
                await api.async_close()

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )
