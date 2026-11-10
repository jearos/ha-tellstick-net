"""Arctech/Nexa self-learning dimmers, configured in the options flow.

Dim levels go out as the self-learning absolute-dim frame (16 steps). The
TellStick firmware can't receive that frame, so remotes and wall switches
only update the on/off state here; their own dimming (pressing on again
starts and stops the receiver's dim cycle) happens inside the receiver.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TellStickConfigEntry
from .const import CONF_SWITCHES, CONF_TYPE, TYPE_DIMMER
from .entity import SelflearningEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TellStickConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(
        TellStickDimmer(entry, cfg)
        for cfg in entry.options.get(CONF_SWITCHES, [])
        if cfg.get(CONF_TYPE) == TYPE_DIMMER
    )


class TellStickDimmer(SelflearningEntity, LightEntity):
    """A self-learning Nexa/Proove dimmer receiver."""

    _model = "Self-learning dimmer"
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def _restore(self, attributes: dict[str, Any]) -> None:
        if (brightness := attributes.get(ATTR_BRIGHTNESS)) is not None:
            self._attr_brightness = int(brightness)

    async def async_turn_on(self, **kwargs: Any) -> None:
        if (brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None:
            self._hub.dim(self._house, self._unit, brightness)
            self._attr_brightness = brightness
        else:
            # Plain "on": the receiver goes back to its own last level
            self._hub.turn_on(self._house, self._unit)
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._hub.turn_off(self._house, self._unit)
        self._attr_is_on = False
        self.async_write_ha_state()
