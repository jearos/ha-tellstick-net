"""Arctech/Nexa self-learning on/off receivers, configured in the options flow.

433 MHz receivers don't report state, so state is optimistic (assumed) and
restored across restarts. It also follows matching RF commands, e.g. when the
receiver's own remote is pressed.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import TellStickConfigEntry
from .const import CONF_HOUSE, CONF_MAC, CONF_SWITCHES, CONF_UNIT, DOMAIN, SIGNAL_PACKET
from .entity import TellStickEntity


def switch_unique_id(mac: str, house: int, unit: int) -> str:
    return f"{mac}-arctech-selflearning-{house}-{unit}"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TellStickConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    mac = entry.data[CONF_MAC]
    configured = entry.options.get(CONF_SWITCHES, [])
    entities = [TellStickSwitch(entry, cfg) for cfg in configured]

    # Drop registry entries for switches that were removed in the options flow
    wanted = {e.unique_id for e in entities}
    registry = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            reg_entry.domain == "switch"
            and reg_entry.unique_id.startswith(f"{mac}-arctech-selflearning-")
            and reg_entry.unique_id not in wanted
        ):
            registry.async_remove(reg_entry.entity_id)

    async_add_entities(entities)


class TellStickSwitch(TellStickEntity, SwitchEntity, RestoreEntity):
    """A self-learning Nexa/Proove/Anslut style receiver."""

    _attr_assumed_state = True
    _attr_name = None  # use the device name

    def __init__(self, entry: TellStickConfigEntry, cfg: dict[str, Any]) -> None:
        super().__init__(entry)
        self._house = int(cfg[CONF_HOUSE])
        self._unit = int(cfg[CONF_UNIT])
        self._attr_unique_id = switch_unique_id(self._mac, self._house, self._unit)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            name=cfg[CONF_NAME],
            manufacturer="Arctech (Nexa)",
            model="Self-learning on/off",
            **self._via_device,
        )
        self._attr_extra_state_attributes = {
            CONF_HOUSE: self._house,
            CONF_UNIT: self._unit,
        }
        self._attr_is_on = False

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self._attr_is_on = last.state == "on"
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_PACKET.format(entry_id=self._entry.entry_id),
                self._handle_packet,
            )
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._hub.turn_on(self._house, self._unit)
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._hub.turn_off(self._house, self._unit)
        self._attr_is_on = False
        self.async_write_ha_state()

    @callback
    def _handle_packet(self, packet: dict[str, Any]) -> None:
        if (
            packet.get("class") != "command"
            or packet.get("protocol") != "arctech"
            or packet.get("model") != "selflearning"
            or packet.get("house") != self._house
        ):
            return
        # A group command addresses every unit of the house code
        if packet.get("group") != 1 and packet.get("unit") != self._unit:
            return
        method = packet.get("method")
        if method == "turnon":
            self._attr_is_on = True
        elif method == "turnoff":
            self._attr_is_on = False
        else:
            return
        self.async_write_ha_state()
