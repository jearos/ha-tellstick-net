"""Self-learning remote buttons and wall switches as event entities.

Every decoded press also fires a tellstick_local_command bus event; an event
entity makes the button usable in the automation editor and shows its last
press.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TellStickConfigEntry
from .const import CONF_SWITCHES, CONF_TYPE, TYPE_BUTTON
from .entity import SelflearningEntity

EVENT_TYPES = {"turnon": "turn_on", "turnoff": "turn_off"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TellStickConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(
        TellStickButton(entry, cfg)
        for cfg in entry.options.get(CONF_SWITCHES, [])
        if cfg.get(CONF_TYPE) == TYPE_BUTTON
    )


class TellStickButton(SelflearningEntity, EventEntity):
    """One on/off button pair of a self-learning remote or wall switch."""

    _attr_assumed_state = False
    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = list(EVENT_TYPES.values())
    _attr_translation_key = "button"
    _model = "Self-learning remote"

    def _handle_command(self, method: Any) -> None:
        if (event_type := EVENT_TYPES.get(method)) is None:
            return
        self._trigger_event(event_type)
        self.async_write_ha_state()
