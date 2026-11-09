"""433 MHz temperature/humidity sensors seen by the TellStick Net.

Sensors can't be configured up front (they announce nothing but an ID), so
an entity is created the first time a sensor is heard. Entities are keyed on
the sensor's stable uid from known_sensors.py, not its radio ID, so a sensor
that got a new random ID (battery change) can be pointed back at its old
entity with the "Replace sensor" option, keeping its history.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TellStickConfigEntry, sensor_device_identifier, sensor_unique_id
from .const import (
    CONF_NEW_SENSORS_ENABLED,
    DEFAULT_NEW_SENSORS_ENABLED,
    SIGNAL_PACKET,
)
from .entity import TellStickEntity
from .known_sensors import KnownSensor, RadioKey

DESCRIPTIONS: dict[str, SensorEntityDescription] = {
    "temp": SensorEntityDescription(
        key="temp",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    "humidity": SensorEntityDescription(
        key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
    ),
}


def _radio_key(packet: dict[str, Any]) -> RadioKey | None:
    if packet.get("class") != "sensor" or "sensorId" not in packet:
        return None
    return (str(packet["protocol"]), str(packet.get("model", "")), int(packet["sensorId"]))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TellStickConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    known = entry.runtime_data.sensors
    entities: dict[tuple[str, str], TellStickSensor] = {}
    new_enabled = entry.options.get(
        CONF_NEW_SENSORS_ENABLED, DEFAULT_NEW_SENSORS_ENABLED
    )

    def make(sensor: KnownSensor, quantity: str, enabled: bool) -> TellStickSensor:
        entity = TellStickSensor(entry, sensor, DESCRIPTIONS[quantity], enabled)
        entities[(sensor.uid, quantity)] = entity
        return entity

    restored = [
        make(sensor, quantity, True)
        for sensor in known
        for quantity in sensor.quantities
        if quantity in DESCRIPTIONS
    ]
    if restored:
        async_add_entities(restored)

    @callback
    def handle_packet(packet: dict[str, Any]) -> None:
        key = _radio_key(packet)
        if key is None:
            return
        values = {
            item.get("name"): item.get("value")
            for item in packet.get("data") or []
            if item.get("name") in DESCRIPTIONS
        }
        if not values:
            return
        sensor, _ = known.heard(key, values)
        added: list[TellStickSensor] = []
        for quantity, value in values.items():
            entity = entities.get((sensor.uid, quantity))
            if entity is None:
                entity = make(sensor, quantity, new_enabled)
                added.append(entity)
            entity.update_value(value)
        if added:
            async_add_entities(added)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, SIGNAL_PACKET.format(entry_id=entry.entry_id), handle_packet
        )
    )


class TellStickSensor(TellStickEntity, RestoreSensor):
    """One quantity (temperature or humidity) of one RF sensor."""

    def __init__(
        self,
        entry: TellStickConfigEntry,
        sensor: KnownSensor,
        description: SensorEntityDescription,
        enabled_default: bool,
    ) -> None:
        super().__init__(entry)
        self._sensor = sensor
        self.entity_description = description
        self._attr_entity_registry_enabled_default = enabled_default
        self._attr_unique_id = sensor_unique_id(self._mac, sensor.uid, description.key)
        # Name/model are only used when the device is first created; after a
        # replace the registry keeps the original (or user-given) name.
        self._attr_device_info = DeviceInfo(
            identifiers={sensor_device_identifier(self._mac, sensor.uid)},
            name=f"{sensor.protocol.capitalize()} sensor {sensor.sensor_id}",
            model=f"{sensor.protocol} {sensor.model}".strip(),
            **self._via_device,
        )
        self._pending: Any = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        # Read live: the radio ID changes when the sensor is replaced
        return {
            "protocol": self._sensor.protocol,
            "model": self._sensor.model,
            "sensor_id": self._sensor.sensor_id,
        }
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._pending is not None:
            # First reading arrived before the entity was added
            self._attr_native_value = self._pending
            self._pending = None
        elif (last := await self.async_get_last_sensor_data()) is not None:
            self._attr_native_value = last.native_value

    @callback
    def update_value(self, value: Any) -> None:
        try:
            value = float(value)
        except (TypeError, ValueError):
            return
        if self.hass is None:
            self._pending = value
            return
        self._attr_native_value = value
        self.async_write_ha_state()
