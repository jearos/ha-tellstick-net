"""Config and options flow for TellStick Net (local)."""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from . import (
    TellStickConfigEntry,
    find_device,
    sensor_device_identifier,
    sensor_unique_id,
)
from .const import (
    CONF_HOUSE,
    CONF_MAC,
    CONF_NEW_SENSORS_ENABLED,
    CONF_SWITCHES,
    CONF_TYPE,
    CONF_UNIT,
    DEFAULT_NEW_SENSORS_ENABLED,
    DOMAIN,
    HOUSE_MAX,
    HOUSE_MIN,
    SIGNAL_PACKET,
    TYPE_DIMMER,
    TYPE_SWITCH,
    UNIT_MAX,
    UNIT_MIN,
)
from .hub import DiscoveredDevice, async_discover
from .known_sensors import KnownSensor

MANUAL = "manual"
# How long "Add switch by pressing a remote" listens for a button press
LEARN_TIMEOUT = 60
OLD_SENSOR = "old_sensor"
NEW_SENSOR = "new_sensor"


def _type_selector() -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[TYPE_SWITCH, TYPE_DIMMER],
            translation_key="receiver_type",
            mode=selector.SelectSelectorMode.LIST,
        )
    )


def _receiver(name: str, house: int, unit: int, kind: str) -> dict[str, Any]:
    """A switches-list item; the type key is only stored for dimmers."""
    item: dict[str, Any] = {CONF_NAME: name, CONF_HOUSE: house, CONF_UNIT: unit}
    if kind == TYPE_DIMMER:
        item[CONF_TYPE] = TYPE_DIMMER
    return item


def _ago(timestamp: float | None) -> str:
    if timestamp is None:
        return "never heard"
    seconds = max(0, time.time() - timestamp)
    for size, unit in ((86400, "d"), (3600, "h"), (60, "min")):
        if seconds >= size:
            return f"heard {int(seconds // size)} {unit} ago"
    return "heard just now"


class TellStickConfigFlow(ConfigFlow, domain=DOMAIN):
    """Find a TellStick Net on the LAN, or take an IP address."""

    VERSION = 1

    def __init__(self) -> None:
        self._found: dict[str, DiscoveredDevice] = {}
        self._discovered: DiscoveredDevice | None = None

    @staticmethod
    @callback
    def async_get_options_flow(entry: TellStickConfigEntry) -> OptionsFlow:
        return TellStickOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            if user_input[CONF_HOST] == MANUAL:
                return await self.async_step_manual()
            return await self._async_create(self._found[user_input[CONF_HOST]])

        configured = {
            entry.unique_id for entry in self._async_current_entries(include_ignore=False)
        }
        self._found = {
            d.host: d for d in await async_discover() if d.mac not in configured
        }
        if not self._found:
            return await self.async_step_manual()

        options = [
            selector.SelectOptionDict(
                value=d.host, label=f"{d.host} ({d.mac}, firmware {d.firmware})"
            )
            for d in self._found.values()
        ]
        options.append(selector.SelectOptionDict(value=MANUAL, label="Enter IP address manually"))
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options)
                    )
                }
            ),
        )

    async def async_step_dhcp(
        self, discovery_info: DhcpServiceInfo
    ) -> ConfigFlowResult:
        """A device with Telldus's MAC prefix got a DHCP lease.

        Already configured: just follow it to its new address (HA reloads the
        entry). New: check it really is a TellStick Net (ZNet etc. share the
        prefix) and offer to set it up.
        """
        mac = discovery_info.macaddress.replace(":", "").lower()
        await self.async_set_unique_id(mac)
        self._abort_if_unique_id_configured(updates={CONF_HOST: discovery_info.ip})

        found = await async_discover(timeout=2.0, target=discovery_info.ip)
        device = next((d for d in found if d.mac == mac), None)
        if device is None:
            return self.async_abort(reason="not_tellstick_net")
        self._discovered = device
        self.context["title_placeholders"] = {"name": f"TellStick Net {device.host}"}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._discovered is not None
        if user_input is not None:
            return await self._async_create(self._discovered)
        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "host": self._discovered.host,
                "mac": self._discovered.mac,
                "firmware": self._discovered.firmware,
            },
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            found = await async_discover(timeout=2.0, target=host)
            if found:
                return await self._async_create(found[0])
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
            errors=errors,
        )

    async def _async_create(self, device: DiscoveredDevice) -> ConfigFlowResult:
        await self.async_set_unique_id(device.mac)
        # Same device, new IP (DHCP): update the entry instead of duplicating it
        self._abort_if_unique_id_configured(updates={CONF_HOST: device.host})
        return self.async_create_entry(
            title=f"TellStick Net {device.mac[-6:].upper()}",
            data={CONF_HOST: device.host, CONF_MAC: device.mac},
            options={
                CONF_SWITCHES: [],
                CONF_NEW_SENSORS_ENABLED: DEFAULT_NEW_SENSORS_ENABLED,
            },
        )


class TellStickOptionsFlow(OptionsFlow):
    """Manage switches and sensor settings."""

    def __init__(self) -> None:
        self._learn_task: asyncio.Task[dict[str, Any]] | None = None
        self._learned: dict[str, Any] | None = None

    def _options(self) -> dict[str, Any]:
        return dict(self.config_entry.options)

    def _switches(self) -> list[dict[str, Any]]:
        return list(self.config_entry.options.get(CONF_SWITCHES, []))

    def _known_sensors(self) -> list[KnownSensor]:
        # Sensor bookkeeping lives in the running entry, not in options
        if self.config_entry.state is not ConfigEntryState.LOADED:
            return []
        return list(self.config_entry.runtime_data.sensors)

    def _sensor_label(self, sensor: KnownSensor) -> str:
        mac = self.config_entry.data[CONF_MAC]
        device = find_device(
            self.hass, self.config_entry, sensor_device_identifier(mac, sensor.uid)
        )
        name = (device.name_by_user or device.name) if device else sensor.uid
        readings = []
        entity_registry = er.async_get(self.hass)
        for quantity in sensor.quantities:
            entity_id = entity_registry.async_get_entity_id(
                "sensor", DOMAIN, sensor_unique_id(mac, sensor.uid, quantity)
            )
            state = self.hass.states.get(entity_id) if entity_id else None
            if state and state.state not in ("unknown", "unavailable"):
                readings.append(f"{state.state} {state.attributes.get('unit_of_measurement', '')}".strip())
        details = ", ".join(
            [f"{sensor.protocol} {sensor.model} id {sensor.sensor_id}", *readings, _ago(sensor.last_seen)]
        )
        return f"{name} ({details})"

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        menu = ["add_switch"]
        if self.config_entry.state is ConfigEntryState.LOADED:
            menu.append("learn_switch")
        if self._switches():
            menu.append("remove_switch")
        if len(self._known_sensors()) >= 2:
            menu.append("replace_sensor")
        menu.append("settings")
        return self.async_show_menu(step_id="init", menu_options=menu)

    async def async_step_add_switch(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        switches = self._switches()
        if user_input is not None:
            house = int(user_input[CONF_HOUSE])
            unit = int(user_input[CONF_UNIT])
            if any(s[CONF_HOUSE] == house and s[CONF_UNIT] == unit for s in switches):
                errors["base"] = "already_exists"
            else:
                kind = user_input.get(CONF_TYPE, TYPE_SWITCH)
                name = user_input.get(CONF_NAME, "").strip() or f"Nexa {house} unit {unit}"
                switches.append(_receiver(name, house, unit, kind))
                return self.async_create_entry(
                    data={**self._options(), CONF_SWITCHES: switches}
                )

        # A fresh random house code is what you want when pairing a new
        # receiver. To take over an existing remote's code, read it from a
        # tellstick_local_command event and type it in instead.
        defaults = user_input or {
            CONF_NAME: "",
            CONF_TYPE: TYPE_SWITCH,
            CONF_HOUSE: random.randint(HOUSE_MIN, HOUSE_MAX),
            CONF_UNIT: UNIT_MIN,
        }
        return self.async_show_form(
            step_id="add_switch",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_NAME, default=defaults[CONF_NAME]): str,
                    vol.Required(CONF_TYPE, default=defaults[CONF_TYPE]): _type_selector(),
                    vol.Required(CONF_HOUSE, default=defaults[CONF_HOUSE]): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=HOUSE_MIN, max=HOUSE_MAX, step=1,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(CONF_UNIT, default=defaults[CONF_UNIT]): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=UNIT_MIN, max=UNIT_MAX, step=1,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_learn_switch(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Listen for a self-learning remote/wall switch and copy its code."""
        if self._learn_task is None:
            self._learn_task = self.hass.async_create_task(
                self._async_wait_for_remote(), eager_start=False
            )
        if not self._learn_task.done():
            return self.async_show_progress(
                progress_action="press_remote",
                progress_task=self._learn_task,
                description_placeholders={"seconds": str(LEARN_TIMEOUT)},
            )
        task, self._learn_task = self._learn_task, None
        try:
            self._learned = task.result()
        except TimeoutError:
            return self.async_show_progress_done(next_step_id="learn_timeout")
        return self.async_show_progress_done(next_step_id="learn_confirm")

    async def _async_wait_for_remote(self) -> dict[str, Any]:
        found: asyncio.Future[dict[str, Any]] = self.hass.loop.create_future()

        @callback
        def on_packet(packet: dict[str, Any]) -> None:
            if (
                not found.done()
                and packet.get("class") == "command"
                and packet.get("protocol") == "arctech"
                and packet.get("model") == "selflearning"
                and packet.get("group") != 1  # "all on/off" names no unit
                and isinstance(packet.get("house"), int)
                and isinstance(packet.get("unit"), int)
            ):
                found.set_result(packet)

        unsub = async_dispatcher_connect(
            self.hass,
            SIGNAL_PACKET.format(entry_id=self.config_entry.entry_id),
            on_packet,
        )
        try:
            async with asyncio.timeout(LEARN_TIMEOUT):
                return await found
        finally:
            unsub()

    async def async_step_learn_timeout(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="learn_timeout", menu_options=["learn_switch", "init"]
        )

    async def async_step_learn_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._learned is not None
        house, unit = self._learned["house"], self._learned["unit"]
        switches = self._switches()
        exists = any(s[CONF_HOUSE] == house and s[CONF_UNIT] == unit for s in switches)
        if exists:
            return self.async_abort(
                reason="already_exists",
                description_placeholders={"house": str(house), "unit": str(unit)},
            )
        if user_input is not None:
            name = user_input.get(CONF_NAME, "").strip() or f"Nexa {house} unit {unit}"
            switches.append(
                _receiver(name, house, unit, user_input.get(CONF_TYPE, TYPE_SWITCH))
            )
            return self.async_create_entry(
                data={**self._options(), CONF_SWITCHES: switches}
            )
        return self.async_show_form(
            step_id="learn_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default=f"Nexa {house} unit {unit}"): str,
                    vol.Required(CONF_TYPE, default=TYPE_SWITCH): _type_selector(),
                }
            ),
            description_placeholders={"house": str(house), "unit": str(unit)},
        )

    async def async_step_remove_switch(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        switches = self._switches()
        if user_input is not None:
            remove = set(user_input["switches"])
            kept = [s for s in switches if f"{s[CONF_HOUSE]}-{s[CONF_UNIT]}" not in remove]
            return self.async_create_entry(data={**self._options(), CONF_SWITCHES: kept})

        options = [
            selector.SelectOptionDict(
                value=f"{s[CONF_HOUSE]}-{s[CONF_UNIT]}",
                label=f"{s[CONF_NAME]} ({s.get(CONF_TYPE, TYPE_SWITCH)}, house {s[CONF_HOUSE]}, unit {s[CONF_UNIT]})",
            )
            for s in switches
        ]
        return self.async_show_form(
            step_id="remove_switch",
            data_schema=vol.Schema(
                {
                    vol.Required("switches"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options, multiple=True)
                    )
                }
            ),
        )

    async def async_step_replace_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Point an existing sensor (and its history) at a new radio ID."""
        errors: dict[str, str] = {}
        sensors = self._known_sensors()
        if user_input is not None:
            old, new = user_input[OLD_SENSOR], user_input[NEW_SENSOR]
            if old == new:
                errors["base"] = "same_sensor"
            else:
                await self._async_replace_sensor(old, new)
                return self.async_create_entry(data=self._options())

        # Old: longest silent first. New: most recently appeared first.
        old_options = [
            selector.SelectOptionDict(value=s.uid, label=self._sensor_label(s))
            for s in sorted(sensors, key=lambda s: s.last_seen or 0)
        ]
        new_options = [
            selector.SelectOptionDict(value=s.uid, label=self._sensor_label(s))
            for s in sorted(sensors, key=lambda s: s.first_seen or 0, reverse=True)
        ]
        return self.async_show_form(
            step_id="replace_sensor",
            data_schema=vol.Schema(
                {
                    vol.Required(OLD_SENSOR): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=old_options)
                    ),
                    vol.Required(NEW_SENSOR): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=new_options)
                    ),
                }
            ),
            errors=errors,
        )

    async def _async_replace_sensor(self, keep_uid: str, new_uid: str) -> None:
        entry = self.config_entry
        data = entry.runtime_data
        mac = entry.data[CONF_MAC]
        dropped = data.sensors.get(new_uid)
        keep = data.sensors.replace(keep_uid, new_uid)
        await data.async_save()

        # Remove the entities/device auto-created for the new radio ID; the
        # kept sensor's entities (and their history) take over from here.
        entity_registry = er.async_get(self.hass)
        for quantity in dropped.quantities if dropped else []:
            if entity_id := entity_registry.async_get_entity_id(
                "sensor", DOMAIN, sensor_unique_id(mac, new_uid, quantity)
            ):
                entity_registry.async_remove(entity_id)
        device_registry = dr.async_get(self.hass)
        if device := find_device(
            self.hass, entry, sensor_device_identifier(mac, new_uid)
        ):
            device_registry.async_remove_device(device.id)
        if device := find_device(
            self.hass, entry, sensor_device_identifier(mac, keep_uid)
        ):
            device_registry.async_update_device(
                device.id, model=f"{keep.protocol} {keep.model}".strip()
            )

        # Options don't change, so the update listener won't reload for us.
        # Reload so entities for any quantity the new sensor added show up.
        self.hass.config_entries.async_schedule_reload(entry.entry_id)

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={**self._options(), **user_input})

        current = self.config_entry.options.get(
            CONF_NEW_SENSORS_ENABLED, DEFAULT_NEW_SENSORS_ENABLED
        )
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {vol.Required(CONF_NEW_SENSORS_ENABLED, default=current): bool}
            ),
        )
