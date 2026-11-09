"""TellStick Net (local) - talk to a TellStick Net over the LAN, no cloud."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from .const import (
    CONF_MAC,
    DOMAIN,
    EVENT_RF_COMMAND,
    SIGNAL_AVAILABLE,
    SIGNAL_PACKET,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .hub import TellStickHub
from .known_sensors import KnownSensors

PLATFORMS = [Platform.SENSOR, Platform.SWITCH]

# last_seen alone changes on every reception; don't hit the disk for that
_LAST_SEEN_SAVE_DELAY = 900


# HA 2026.8 replaced DeviceInfo's via_device (identifier tuple) with
# via_device_id (registry id); the old key stops working in 2027.8.
_HAS_VIA_DEVICE_ID = "via_device_id" in (
    dr.DeviceInfo.__required_keys__ | dr.DeviceInfo.__optional_keys__
)


@dataclass
class TellStickData:
    hub: TellStickHub
    sensors: KnownSensors
    store: Store[dict[str, Any]]
    hub_device_id: str
    # Spread into child DeviceInfo to link sensors/switches to the TellStick
    via_device: dict[str, Any]

    async def async_save(self) -> None:
        await self.store.async_save(self.sensors.to_data())


type TellStickConfigEntry = ConfigEntry[TellStickData]


def find_device(
    hass: HomeAssistant, entry: ConfigEntry, identifier: tuple[str, str]
) -> dr.DeviceEntry | None:
    """Look up one of this entry's devices by identifier.

    Works on every supported HA version (async_get_device is deprecated
    since 2026.8, async_get_device_by_identifier only exists from 2026.8).
    """
    for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id):
        if identifier in device.identifiers:
            return device
    return None


def sensor_device_identifier(mac: str, uid: str) -> tuple[str, str]:
    return (DOMAIN, f"{mac}-{uid}")


def sensor_unique_id(mac: str, uid: str, quantity: str) -> str:
    return f"{mac}-{uid}-{quantity}"

# Keys copied from a decoded RF command into the bus event
_EVENT_KEYS = ("protocol", "model", "house", "unit", "group", "method", "code")


async def async_setup_entry(hass: HomeAssistant, entry: TellStickConfigEntry) -> bool:
    """Set up a TellStick Net from a config entry."""
    host: str = entry.data[CONF_HOST]
    mac: str = entry.data[CONF_MAC]

    @callback
    def on_packet(packet: dict[str, Any]) -> None:
        async_dispatcher_send(
            hass, SIGNAL_PACKET.format(entry_id=entry.entry_id), packet
        )
        if packet.get("class") == "command":
            hass.bus.async_fire(
                EVENT_RF_COMMAND,
                {
                    "mac": mac,
                    **{k: packet[k] for k in _EVENT_KEYS if k in packet},
                },
            )

    @callback
    def on_availability(available: bool) -> None:
        async_dispatcher_send(
            hass, SIGNAL_AVAILABLE.format(entry_id=entry.entry_id), available
        )

    @callback
    def on_firmware(firmware: str) -> None:
        device_registry = dr.async_get(hass)
        device = device_registry.async_get(hub_device.id)
        if device is not None and device.sw_version != firmware:
            device_registry.async_update_device(device.id, sw_version=firmware)

    store: Store[dict[str, Any]] = Store(
        hass, STORAGE_VERSION, STORAGE_KEY.format(entry_id=entry.entry_id)
    )
    save_pending = False

    def data_to_save() -> dict[str, Any]:
        nonlocal save_pending
        save_pending = False
        return sensors.to_data()

    @callback
    def on_sensors_changed(structural: bool) -> None:
        nonlocal save_pending
        if structural or not save_pending:
            save_pending = True
            store.async_delay_save(
                data_to_save, 1 if structural else _LAST_SEEN_SAVE_DELAY
            )

    sensors = KnownSensors(await store.async_load(), on_sensors_changed)

    hub_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, mac)},
        connections={(dr.CONNECTION_NETWORK_MAC, dr.format_mac(mac))},
        manufacturer="Telldus",
        model="TellStick Net",
        name=entry.title,
    )

    @callback
    def on_host_changed(new_host: str) -> None:
        # Persist it; the update listener then reloads with the new address.
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_HOST: new_host}
        )

    hub = TellStickHub(
        host,
        on_packet,
        on_availability,
        mac=mac,
        on_host_changed=on_host_changed,
        on_firmware=on_firmware,
    )
    try:
        await hub.async_start()
    except OSError as err:
        raise ConfigEntryNotReady(f"Cannot open UDP socket: {err}") from err

    entry.runtime_data = TellStickData(
        hub,
        sensors,
        store,
        hub_device.id,
        {"via_device_id": hub_device.id}
        if _HAS_VIA_DEVICE_ID
        else {"via_device": (DOMAIN, mac)},
    )
    entry.async_on_unload(hub.async_stop)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TellStickConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    # A reload creates a new Store that reads from disk, so a delayed save
    # still pending here would be lost (and with it any new sensor's uid).
    await entry.runtime_data.async_save()
    return unloaded


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: TellStickConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow deleting sensor devices (e.g. a neighbour's) from the UI.

    The sensor comes back if it is heard again; turn off "Enable newly heard
    sensors" to have it come back disabled instead.
    """
    mac = entry.data[CONF_MAC]
    if (DOMAIN, mac) in device.identifiers:
        return False  # the TellStick itself
    data = entry.runtime_data
    for sensor in data.sensors:
        if sensor_device_identifier(mac, sensor.uid) in device.identifiers:
            data.sensors.remove(sensor.uid)
            await data.async_save()
    return True


async def _async_reload(hass: HomeAssistant, entry: TellStickConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
