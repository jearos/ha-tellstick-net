"""TellStick Net (local) - talk to a TellStick Net over the LAN, no cloud."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from .const import (
    CONF_HOUSE,
    CONF_MAC,
    CONF_SWITCHES,
    CONF_TYPE,
    CONF_UNIT,
    DOMAIN,
    EVENT_RF_COMMAND,
    FIRMWARE_UPDATE_URL,
    ISSUE_OLD_FIRMWARE,
    LATEST_FIRMWARE,
    SIGNAL_AVAILABLE,
    SIGNAL_PACKET,
    STORAGE_KEY,
    STORAGE_VERSION,
    TYPE_DIMMER,
    TYPE_SWITCH,
    selflearning_prefix,
    selflearning_unique_id,
)
from .hub import TellStickHub
from .known_sensors import KnownSensors

PLATFORMS = [Platform.LIGHT, Platform.SENSOR, Platform.SWITCH]

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
        async_check_firmware(hass, entry, firmware)

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

    _async_remove_stale_receivers(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


@callback
def _async_remove_stale_receivers(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop entities and devices of switches/dimmers no longer configured.

    Also drops an entity left in the wrong platform, so a receiver whose
    type changed is recreated as the right kind.
    """
    mac = entry.data[CONF_MAC]
    prefix = selflearning_prefix(mac)
    domain_of = {TYPE_SWITCH: "switch", TYPE_DIMMER: "light"}
    wanted = {
        selflearning_unique_id(mac, cfg[CONF_HOUSE], cfg[CONF_UNIT]): domain_of.get(
            cfg.get(CONF_TYPE, TYPE_SWITCH), "switch"
        )
        for cfg in entry.options.get(CONF_SWITCHES, [])
    }
    entities = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(entities, entry.entry_id):
        if (
            reg_entry.domain in ("switch", "light")
            and reg_entry.unique_id.startswith(prefix)
            and wanted.get(reg_entry.unique_id) != reg_entry.domain
        ):
            entities.async_remove(reg_entry.entity_id)
    devices = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
        if any(
            domain == DOMAIN and ident.startswith(prefix) and ident not in wanted
            for domain, ident in device.identifiers
        ):
            devices.async_remove_device(device.id)


async def async_unload_entry(hass: HomeAssistant, entry: TellStickConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    # A reload creates a new Store that reads from disk, so a delayed save
    # still pending here would be lost (and with it any new sensor's uid).
    await entry.runtime_data.async_save()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: TellStickConfigEntry) -> None:
    """Clean up when the integration entry is deleted."""
    ir.async_delete_issue(hass, DOMAIN, ISSUE_OLD_FIRMWARE.format(entry_id=entry.entry_id))


@callback
def async_check_firmware(
    hass: HomeAssistant, entry: ConfigEntry, firmware: str
) -> None:
    """Raise a repair issue for stock firmware without local access (< 17).

    Non-numeric versions (e.g. the open-source firmware's "X") are left
    alone: there is nothing newer to point those users to.
    """
    issue_id = ISSUE_OLD_FIRMWARE.format(entry_id=entry.entry_id)
    try:
        version = int(firmware)
    except ValueError:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    if version >= LATEST_FIRMWARE:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        learn_more_url=FIRMWARE_UPDATE_URL,
        translation_key="old_firmware",
        translation_placeholders={
            "name": entry.title,
            "firmware": firmware,
            "latest": str(LATEST_FIRMWARE),
        },
    )


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: TellStickConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow deleting sensor, switch and dimmer devices from the UI.

    A deleted sensor comes back if it is heard again; turn off "Enable newly
    heard sensors" to have it come back disabled instead. A deleted switch or
    dimmer is dropped from the options so it doesn't come back on reload.
    """
    mac = entry.data[CONF_MAC]
    if (DOMAIN, mac) in device.identifiers:
        return False  # the TellStick itself
    switches = entry.options.get(CONF_SWITCHES, [])
    kept = [
        s
        for s in switches
        if (DOMAIN, selflearning_unique_id(mac, s[CONF_HOUSE], s[CONF_UNIT]))
        not in device.identifiers
    ]
    if len(kept) != len(switches):
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_SWITCHES: kept}
        )
    data = entry.runtime_data
    for sensor in data.sensors:
        if sensor_device_identifier(mac, sensor.uid) in device.identifiers:
            data.sensors.remove(sensor.uid)
            await data.async_save()
    return True


async def _async_reload(hass: HomeAssistant, entry: TellStickConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
