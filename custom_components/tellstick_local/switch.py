"""Arctech/Nexa self-learning on/off receivers, configured in the options flow."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TellStickConfigEntry
from .const import CONF_SWITCHES, CONF_TYPE, TYPE_SWITCH
from .entity import SelflearningEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TellStickConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(
        TellStickSwitch(entry, cfg)
        for cfg in entry.options.get(CONF_SWITCHES, [])
        if cfg.get(CONF_TYPE, TYPE_SWITCH) == TYPE_SWITCH
    )


class TellStickSwitch(SelflearningEntity, SwitchEntity):
    """A self-learning Nexa/Proove/Anslut style on/off receiver."""

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._hub.turn_on(self._house, self._unit)
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._hub.turn_off(self._house, self._unit)
        self._attr_is_on = False
        self.async_write_ha_state()
