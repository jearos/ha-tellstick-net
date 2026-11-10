"""Shared entity bases for TellStick Net (local)."""

from __future__ import annotations

from typing import Any

from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.restore_state import RestoreEntity

from . import TellStickConfigEntry
from .const import (
    CONF_HOUSE,
    CONF_MAC,
    CONF_UNIT,
    DOMAIN,
    SIGNAL_AVAILABLE,
    SIGNAL_PACKET,
    selflearning_unique_id,
)


class TellStickEntity(Entity):
    """Follows the hub's availability."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: TellStickConfigEntry) -> None:
        self._entry = entry
        self._hub = entry.runtime_data.hub
        self._mac: str = entry.data[CONF_MAC]
        self._via_device: dict = entry.runtime_data.via_device

    @property
    def available(self) -> bool:
        return self._hub.available

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_AVAILABLE.format(entry_id=self._entry.entry_id),
                self._handle_availability,
            )
        )

    @callback
    def _handle_availability(self, available: bool) -> None:
        self.async_write_ha_state()


class SelflearningEntity(TellStickEntity, RestoreEntity):
    """A Nexa/Proove/Arctech self-learning code: a receiver (switch, dimmer)
    or a transmitter (door/motion sensor, button).

    433 MHz receivers don't report state, so their state is optimistic
    (assumed) and restored across restarts. It also follows matching RF
    commands, e.g. when the receiver's own remote or wall switch is pressed.
    Transmitters override ``_handle_command``.
    """

    _attr_assumed_state = True
    _attr_name = None  # use the device name
    _model = "Self-learning on/off"

    def __init__(self, entry: TellStickConfigEntry, cfg: dict[str, Any]) -> None:
        super().__init__(entry)
        self._house = int(cfg[CONF_HOUSE])
        self._unit = int(cfg[CONF_UNIT])
        self._attr_unique_id = selflearning_unique_id(self._mac, self._house, self._unit)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            name=cfg[CONF_NAME],
            manufacturer="Arctech (Nexa)",
            model=self._model,
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
            self._restore(last.attributes)
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_PACKET.format(entry_id=self._entry.entry_id),
                self._handle_packet,
            )
        )

    def _restore(self, attributes: dict[str, Any]) -> None:
        """Restore extra state (e.g. brightness) from the last known state."""

    @callback
    def _handle_packet(self, packet: dict[str, Any]) -> None:
        if (
            packet.get("class") != "command"
            or packet.get("repeat")  # same press again
            or packet.get("protocol") != "arctech"
            or packet.get("model") != "selflearning"
            or packet.get("house") != self._house
        ):
            return
        # A group command addresses every unit of the house code
        if packet.get("group") != 1 and packet.get("unit") != self._unit:
            return
        self._handle_command(packet.get("method"))

    def _handle_command(self, method: Any) -> None:
        if method == "turnon":
            self._attr_is_on = True
        elif method == "turnoff":
            self._attr_is_on = False
        else:
            return
        self.async_write_ha_state()
