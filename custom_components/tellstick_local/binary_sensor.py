"""Self-learning door/window and motion sensors, configured in the options flow.

They send the same Arctech frames as a remote: "on" when a door opens or
motion is seen, "off" when it closes. Motion sensors that never send "off"
are turned off after MOTION_OFF_AFTER seconds.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from . import TellStickConfigEntry
from .const import CONF_SWITCHES, CONF_TYPE, MOTION_OFF_AFTER, TYPE_DOOR, TYPE_MOTION
from .entity import SelflearningEntity

DEVICE_CLASS = {
    TYPE_DOOR: BinarySensorDeviceClass.DOOR,
    TYPE_MOTION: BinarySensorDeviceClass.MOTION,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TellStickConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(
        TellStickBinarySensor(entry, cfg)
        for cfg in entry.options.get(CONF_SWITCHES, [])
        if cfg.get(CONF_TYPE) in DEVICE_CLASS
    )


class TellStickBinarySensor(SelflearningEntity, BinarySensorEntity):
    """A self-learning door/window contact or motion sensor."""

    _attr_assumed_state = False
    _model = "Self-learning sensor"

    def __init__(self, entry: TellStickConfigEntry, cfg: dict[str, Any]) -> None:
        super().__init__(entry, cfg)
        self._attr_device_class = DEVICE_CLASS[cfg[CONF_TYPE]]
        self._cancel_off: Any = None

    @property
    def _is_motion(self) -> bool:
        return self.device_class == BinarySensorDeviceClass.MOTION

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._is_motion:
            # Motion seen before a restart is long over
            self._attr_is_on = False
        self.async_on_remove(self._stop_timer)

    def _stop_timer(self) -> None:
        if self._cancel_off is not None:
            self._cancel_off()
            self._cancel_off = None

    def _handle_command(self, method: Any) -> None:
        if self._is_motion:
            self._stop_timer()
            if method == "turnon":
                self._cancel_off = async_call_later(
                    self.hass, MOTION_OFF_AFTER, self._auto_off
                )
        super()._handle_command(method)

    @callback
    def _auto_off(self, _now: Any) -> None:
        self._cancel_off = None
        self._attr_is_on = False
        self.async_write_ha_state()
