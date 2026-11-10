"""Binary sensors: self-learning door/window and motion sensors (configured
in the options flow), and the battery-low flag of RF sensors (Oregon).

Door and motion sensors send the same Arctech frames as a remote: "on" when
a door opens or motion is seen, "off" when it closes. Motion sensors that
never send "off" are turned off after MOTION_OFF_AFTER seconds.

Battery entities are created like the sensor's other entities, the first
time a reading carries the flag.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
import logging

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.restore_state import RestoreEntity

from . import TellStickConfigEntry, sensor_unique_id
from .const import (
    CONF_NEW_SENSORS_ENABLED,
    CONF_SWITCHES,
    CONF_TYPE,
    DEFAULT_NEW_SENSORS_ENABLED,
    MOTION_OFF_AFTER,
    QUANTITY_BATTERY_LOW,
    SIGNAL_PACKET,
    TYPE_DOOR,
    TYPE_MOTION,
)
from .entity import SelflearningEntity, TellStickEntity, sensor_device_info
from .known_sensors import KnownSensor, radio_key

_LOGGER = logging.getLogger(__name__)

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

    known = entry.runtime_data.sensors
    batteries: dict[str, TellStickBattery] = {}
    new_enabled = entry.options.get(
        CONF_NEW_SENSORS_ENABLED, DEFAULT_NEW_SENSORS_ENABLED
    )

    def make(sensor: KnownSensor, enabled: bool) -> TellStickBattery:
        entity = batteries[sensor.uid] = TellStickBattery(entry, sensor, enabled)

        @callback
        def forget() -> None:
            # Deleted in the UI: forget it, so it is created anew when heard
            if batteries.get(sensor.uid) is entity:
                del batteries[sensor.uid]

        entity.async_on_remove(forget)
        return entity

    restored = [make(s, True) for s in known if QUANTITY_BATTERY_LOW in s.quantities]
    if restored:
        async_add_entities(restored)

    @callback
    def handle_packet(packet: dict[str, Any]) -> None:
        key = radio_key(packet)
        low = next(
            (
                item.get("value")
                for item in packet.get("data") or []
                if item.get("name") == QUANTITY_BATTERY_LOW
            ),
            None,
        )
        # sensor.py records the sensor; a brand-new one shows up here from
        # its next reading
        if key is None or low is None or (sensor := known.find(key)) is None:
            return
        entity = batteries.get(sensor.uid)
        if entity is None:
            entity = make(sensor, new_enabled)
            async_add_entities([entity])
        entity.update_value(bool(low))

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, SIGNAL_PACKET.format(entry_id=entry.entry_id), handle_packet
        )
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


class TellStickBattery(TellStickEntity, BinarySensorEntity, RestoreEntity):
    """Battery-low flag of one RF sensor, on that sensor's device."""

    _attr_device_class = BinarySensorDeviceClass.BATTERY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, entry: TellStickConfigEntry, sensor: KnownSensor, enabled_default: bool
    ) -> None:
        super().__init__(entry)
        self._sensor = sensor
        self._attr_entity_registry_enabled_default = enabled_default
        self._attr_unique_id = sensor_unique_id(self._mac, sensor.uid, QUANTITY_BATTERY_LOW)
        self._attr_device_info = sensor_device_info(self._mac, sensor, self._via_device)
        self._pending: bool | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._pending is not None:
            self._attr_is_on, self._pending = self._pending, None
        elif (last := await self.async_get_last_state()) is not None:
            self._attr_is_on = last.state == "on"

    @callback
    def update_value(self, low: bool) -> None:
        if low and not self._attr_is_on:
            # Unconfirmed on real hardware so far: worth a line in the log
            _LOGGER.info(
                "%s %s sensor %s reports low battery",
                self._sensor.protocol, self._sensor.model, self._sensor.sensor_id,
            )
        if self.hass is None:
            self._pending = low
            return
        self._attr_is_on = low
        self.async_write_ha_state()
