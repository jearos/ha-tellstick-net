# CLAUDE.md

Home Assistant custom integration (`tellstick_local`, HACS) for the Telldus
TellStick Net over its local UDP interface. No cloud, no MQTT.

## Layout

- `custom_components/tellstick_local/`
  - `hub.py` - UDP transport, discovery, listener registration, liveness,
    rediscovery by MAC. **No Home Assistant imports** (tested standalone).
  - `known_sensors.py` - sensor bookkeeping. **No HA imports.**
  - `__init__.py` - entry setup, `TellStickData` runtime data, Store wiring,
    device helpers (`find_device`, `sensor_unique_id`, ...).
  - `sensor.py`, `switch.py`, `entity.py` - platforms.
  - `config_flow.py` - user/manual/DHCP discovery flow; options flow
    (add/remove switch, replace sensor, settings).
  - `tellsticknet/` - **vendored** MIT code from molobrakos/tellsticknet
    (commit c9d4dab). Keep local changes minimal and marked; patched only to
    use static protocol imports and raise on unknown protocols.
- `tests/` - stdlib `unittest`, no HA needed:
  `python3 -m unittest discover -s tests -v`.

## Device protocol (stock firmware 17 and the open-source firmware)

- UDP 30303: send `D`, reply `TellStickNet:<MAC>:<secret>:<firmware>`.
  The secret must never be stored, logged or shown (`parse_discovery_reply`
  drops it).
- UDP 42314: LiveMessage-encoded commands (`reglistener`, `send`, ...). The
  device keeps **one** listener and sends `RawData` packets to it; it forgets
  it on reboot, hence periodic re-registration.
- No authentication on either port.

Local access (`reglistener`, `setip`, `saveip`) exists only from firmware
17 (also in beta 18); fw2-4 and betas 12/16 lack it. Hence the repair issue
for numeric versions < 17.

Verified on real hardware (2026-11-09, stock firmware 17): discovery,
Oregon temperature/humidity sensors, remote presses, "Add switch by pressing
a remote", and `send` on/off to a Nexa self-learning receiver.

## Rules

- Sensor `unique_id`s are keyed on the stable uid from `known_sensors.py`,
  **never on the radio ID** - cheap 433 MHz sensors re-roll their ID on
  battery change and "Replace sensor" depends on this.
- Minimum HA is 2025.2 (`hacs.json`); new HA APIs need a fallback or a
  minimum-version bump (see `_HAS_VIA_DEVICE_ID`, `find_device`).
- Bump `manifest.json` `version` for every release.
