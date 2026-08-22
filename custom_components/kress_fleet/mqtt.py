# SPDX-License-Identifier: GPL-3.0-only
# This file is part of kress_fleet, a modified work derived in part from
# MTrab/landroid_cloud and MTrab/pyworxcloud (GPL-3.0).
# Kress Fleet modifications began on 2026-08-21; see NOTICE and LICENSE.

"""MQTT-over-WebSocket support for Kress Fleet."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
import secrets
from typing import Any, TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from urllib.parse import quote

import paho.mqtt.client as mqtt

from .api import FleetError, KressFleetApi
from .const import (
    MQTT_CONNECT_TIMEOUT,
    MQTT_PATH,
    MQTT_PORT,
    MQTT_REFRESH_MARGIN_SECONDS,
    MQTT_RETRY_SECONDS,
)
from .models import FleetMower

if TYPE_CHECKING:
    from .coordinator import KressFleetCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _ConnectedClient:
    client: mqtt.Client
    location_id: int
    mower_ids: tuple[str, ...]
    expiration: datetime | None


class KressFleetMqtt:
    """Maintain Fleet MQTT connections and push commandOut into HA."""

    def __init__(
        self,
        api: KressFleetApi,
        coordinator: KressFleetCoordinator,
        entry: ConfigEntry,
    ) -> None:
        self.api = api
        self.coordinator = coordinator
        self.hass = coordinator.hass
        self.entry = entry
        self._clients: list[_ConnectedClient] = []
        self._mower_clients: dict[str, mqtt.Client] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._reconnect_event = asyncio.Event()
        self._successful_auth_variant: int | None = None

    @property
    def connected(self) -> bool:
        return bool(self._clients)

    def async_start(self) -> None:
        """Start the MQTT maintenance task without blocking HA startup."""
        if self._task is None or self._task.done():
            self._stop_event.clear()
            self._task = self.entry.async_create_background_task(
                self.hass,
                self._connection_loop(),
                name="Kress Fleet MQTT connection loop",
                eager_start=True,
            )

    async def async_stop(self) -> None:
        """Stop MQTT and release paho threads."""
        self._stop_event.set()
        self._reconnect_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self._async_close_clients()

    async def _connection_loop(self) -> None:
        while not self._stop_event.is_set():
            wait_seconds = MQTT_RETRY_SECONDS
            try:
                wait_seconds = await self._async_reconnect_all()
            except asyncio.CancelledError:
                raise
            except FleetError:
                _LOGGER.warning("Kress Fleet MQTT credential refresh failed")
            except Exception:
                _LOGGER.exception("Unexpected Kress Fleet MQTT connection error")

            self._reconnect_event.clear()
            try:
                await asyncio.wait_for(
                    self._reconnect_event.wait(), timeout=max(15, wait_seconds)
                )
            except TimeoutError:
                pass

    async def _async_reconnect_all(self) -> int:
        await self._async_close_clients()

        mowers = list(self.coordinator.data.values())
        locations: dict[tuple[int, int], list[FleetMower]] = {}
        for mower in mowers:
            locations.setdefault((mower.user_id, mower.location_id), []).append(mower)

        expirations: list[datetime] = []
        for (user_id, location_id), location_mowers in locations.items():
            credentials = await self.api.async_get_mqtt_credentials(user_id, location_id)
            for credential in credentials:
                connected = await self._async_connect_credential(
                    location_id, credential, location_mowers
                )
                if connected and connected.expiration is not None:
                    expirations.append(connected.expiration)

        if not self._clients:
            _LOGGER.warning(
                "Kress Fleet REST is available, but MQTT authentication was rejected; "
                "live push will retry automatically"
            )
            return MQTT_RETRY_SECONDS

        if not expirations:
            return 30 * 60
        earliest = min(expirations)
        seconds = int((earliest - datetime.now(UTC)).total_seconds())
        return max(30, seconds - MQTT_REFRESH_MARGIN_SECONDS)

    async def _async_connect_credential(
        self,
        location_id: int,
        credential: dict[str, Any],
        location_mowers: list[FleetMower],
    ) -> _ConnectedClient | None:
        endpoint = credential.get("endpoint")
        client_id = credential.get("client_id")
        token = credential.get("token")
        signature = credential.get("signature")
        items = credential.get("items")
        if not all(isinstance(v, str) and v for v in (endpoint, client_id, token, signature)):
            _LOGGER.debug("Fleet returned an incomplete MQTT credential block")
            return None
        if not isinstance(items, dict):
            return None

        mower_ids = tuple(str(value) for value in items)
        expiration = _parse_datetime(credential.get("expiration"))
        variants = self._auth_usernames(str(client_id), str(token), str(signature))
        if self._successful_auth_variant is not None:
            preferred = self._successful_auth_variant
            variants = [variants[preferred]] + [
                value for idx, value in enumerate(variants) if idx != preferred
            ]

        for variant_number, username in enumerate(variants, start=1):
            _LOGGER.debug(
                "Trying Kress Fleet MQTT custom-authorizer form %s/%s",
                variant_number,
                len(variants),
            )
            result = await self._async_try_connect(
                endpoint=str(endpoint),
                client_id=str(client_id),
                username=username,
                items=items,
                location_id=location_id,
                mower_ids=mower_ids,
                expiration=expiration,
            )
            if result is not None:
                original_variants = self._auth_usernames(
                    str(client_id), str(token), str(signature)
                )
                try:
                    self._successful_auth_variant = original_variants.index(username)
                except ValueError:
                    pass
                for mower in location_mowers:
                    if mower.uuid in mower_ids:
                        mower.mqtt_connected = True
                        self._mower_clients[mower.uuid] = result.client
                self.coordinator.async_push_update()
                return result

        return None

    async def _async_try_connect(
        self,
        *,
        endpoint: str,
        client_id: str,
        username: str,
        items: dict[str, Any],
        location_id: int,
        mower_ids: tuple[str, ...],
        expiration: datetime | None,
    ) -> _ConnectedClient | None:
        loop = asyncio.get_running_loop()
        outcome: asyncio.Future[bool] = loop.create_future()
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            protocol=mqtt.MQTTv311,
            transport="websockets",
        )
        client.ws_set_options(path=MQTT_PATH)
        # paho's tls_set() loads the system CA bundle synchronously. Home
        # Assistant treats that filesystem/certificate work as blocking I/O,
        # so prepare TLS in the executor before the paho network thread starts.
        await self.hass.async_add_executor_job(client.tls_set)
        client.username_pw_set(username=username)
        client.reconnect_delay_set(min_delay=5, max_delay=60)

        topic_to_mower: dict[str, str] = {}
        for mower_uuid, topics in items.items():
            if isinstance(topics, dict) and isinstance(topics.get("command_out"), str):
                topic_to_mower[str(topics["command_out"])] = str(mower_uuid)

        def resolve(value: bool) -> None:
            if not outcome.done():
                outcome.set_result(value)

        def on_connect(
            _client: mqtt.Client,
            _userdata: Any,
            _flags: Any,
            reason_code: Any,
            _properties: Any,
        ) -> None:
            success = not bool(getattr(reason_code, "is_failure", False))
            if success:
                for topic in topic_to_mower:
                    _client.subscribe(topic, qos=1)
            loop.call_soon_threadsafe(resolve, success)

        def on_connect_fail(_client: mqtt.Client, _userdata: Any) -> None:
            loop.call_soon_threadsafe(resolve, False)

        def on_disconnect(
            _client: mqtt.Client,
            _userdata: Any,
            _disconnect_flags: Any,
            _reason_code: Any,
            _properties: Any,
        ) -> None:
            if not outcome.done():
                loop.call_soon_threadsafe(resolve, False)
            elif not self._stop_event.is_set():
                loop.call_soon_threadsafe(self._mark_disconnected, mower_ids)

        def on_message(
            _client: mqtt.Client, _userdata: Any, message: mqtt.MQTTMessage
        ) -> None:
            try:
                text = message.payload.decode("utf-8")
            except UnicodeDecodeError:
                _LOGGER.debug("Ignoring non-UTF8 Fleet MQTT payload")
                return
            mower_uuid = topic_to_mower.get(message.topic)
            loop.call_soon_threadsafe(
                self.coordinator.async_handle_mqtt_payload,
                mower_uuid,
                message.topic,
                text,
            )

        client.on_connect = on_connect
        client.on_connect_fail = on_connect_fail
        client.on_disconnect = on_disconnect
        client.on_message = on_message

        try:
            client.connect_async(endpoint, MQTT_PORT, keepalive=60)
            client.loop_start()
            connected = await asyncio.wait_for(
                asyncio.shield(outcome), timeout=MQTT_CONNECT_TIMEOUT
            )
        except (TimeoutError, OSError, mqtt.MQTTException):
            connected = False

        if not connected:
            try:
                client.disconnect()
            except Exception:
                pass
            await asyncio.to_thread(client.loop_stop)
            return None

        result = _ConnectedClient(
            client=client,
            location_id=location_id,
            mower_ids=mower_ids,
            expiration=expiration,
        )
        self._clients.append(result)
        _LOGGER.info("Connected to Kress Fleet live MQTT")
        return result

    def _mark_disconnected(self, mower_ids: tuple[str, ...]) -> None:
        for mower_uuid in mower_ids:
            mower = self.coordinator.data.get(mower_uuid)
            if mower:
                mower.mqtt_connected = False
            self._mower_clients.pop(mower_uuid, None)
        self.coordinator.async_push_update()
        self._reconnect_event.set()

    async def _async_close_clients(self) -> None:
        clients = self._clients
        self._clients = []
        self._mower_clients = {}
        for mower in self.coordinator.data.values():
            mower.mqtt_connected = False
        for item in clients:
            try:
                item.client.disconnect()
            except Exception:
                pass
            await asyncio.to_thread(item.client.loop_stop)
        if clients:
            self.coordinator.async_push_update()

    @staticmethod
    def _auth_usernames(client_id: str, token: str, signature: str) -> list[str]:
        """Build known AWS IoT custom-authorizer CONNECT username forms.

        Fleet returns token + signature but the browser's WebSocket upgrade carries
        no authentication. AWS IoT therefore receives these in MQTT CONNECT. The
        first form is the canonical default-authorizer form; the other forms are
        compatibility probes used by Kress/Positec deployments.
        """
        token_q = quote(token, safe="")
        signature_q = quote(signature, safe="")
        query = (
            f"x-amz-customauthorizer-signature={signature_q}&token={token_q}"
        )
        return [
            f"?{query}",
            f"{client_id}?{query}",
            query,
        ]

    async def async_publish_command(
        self, mower: FleetMower, command: dict[str, Any]
    ) -> None:
        """Publish one protocol-1 command to a Fleet mower."""
        if not mower.command_in:
            raise RuntimeError("Mower has no Fleet commandIn topic")
        client = self._mower_clients.get(mower.uuid)
        if client is None or not client.is_connected():
            raise RuntimeError("Kress Fleet MQTT is not connected")

        payload: dict[str, Any] = {
            "id": secrets.randbelow(65535) + 1,
            "uuid": mower.uuid,
            "tm": datetime.now(UTC).replace(microsecond=0).isoformat().replace(
                "+00:00", "Z"
            ),
        }
        payload.update(command)
        info = client.publish(
            mower.command_in,
            json.dumps(payload, separators=(",", ":")),
            qos=1,
            retain=False,
        )
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT publish failed with code {info.rc}")


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None
