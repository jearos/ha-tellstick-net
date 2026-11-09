"""Shared entity base for TellStick Net (local)."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from . import TellStickConfigEntry
from .const import CONF_MAC, SIGNAL_AVAILABLE


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
