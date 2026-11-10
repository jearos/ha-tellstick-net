# TellStick Net (local) for Home Assistant

[![Validate](https://github.com/jearos/ha-tellstick-net/actions/workflows/validate.yml/badge.svg)](https://github.com/jearos/ha-tellstick-net/actions/workflows/validate.yml)
[![Release](https://img.shields.io/github/v/release/jearos/ha-tellstick-net)](https://github.com/jearos/ha-tellstick-net/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2025.2%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io/)
[![License: MIT](https://img.shields.io/github/license/jearos/ha-tellstick-net)](LICENSE)

Use a **Telldus TellStick Net** from Home Assistant **over your LAN** - no
Telldus Live account, no MQTT bridge.

Firmware 17, the last stable TellStick Net firmware, has an undocumented local
UDP interface. This integration talks to it directly, so Home Assistant never
goes through Telldus Live.

> Not affiliated with or endorsed by Telldus Technologies. "TellStick" is a
> trademark of its owner and is used here only to say which device this works
> with.

## Features

- **Discovery**: finds TellStick Nets on the LAN, and picks them up
  automatically via DHCP.
- **Sensors**: 433 MHz temperature/humidity, wind and rain sensors are
  created automatically the first time they're heard. Push-based, no polling.
- **Keeps history across battery changes**: many cheap sensors pick a new
  random ID when the battery is changed. *Replace sensor* moves the new ID onto
  the old sensor, keeping its entities, name, area and history.
- **Switches**: Nexa / Proove / Arctech *self-learning* on/off receivers, either
  paired with a new code or copied from an existing remote by pressing one of
  its buttons.
- **Dimmers** (untested): self-learning dimmer receivers as lights with
  brightness (16 steps), added the same way as switches.
- **Door/window and motion sensors** (untested): self-learning transmitters
  such as Nexa contacts and PIR sensors as binary sensors. Motion turns off
  after 60 s if the sensor never sends "off".
- **Buttons** (untested): a remote or wall-switch button as an event entity
  (on/off presses), for the automation editor.
- **Remote events**: every decoded remote/wall-switch press fires a
  `tellstick_local_command` event for automations, once per press (the
  radio repeats are filtered). Phantom Everflourish presses the firmware
  decodes from Nexa signals are dropped.
- **Follows the device** if it gets a new IP address (DHCP discovery, plus
  rediscovery by MAC while it's offline).
- **Firmware check**: a repair issue if the device runs firmware without the
  local interface.

### Tested with

| | |
|---|---|
| Device | TellStick Net, stock firmware 17 |
| Sensors | Oregon Scientific temperature/humidity |
| Switches | Nexa self-learning receivers and remotes |
| Dimmers | not yet - reports welcome |
| Door/motion sensors, buttons | not yet - reports welcome |
| Home Assistant | 2026.10 |

Decoders for Fine Offset, Mandolyn and other sensors are included but
untested. Reports (working or not) are welcome as GitHub issues.

Oregon Scientific models decoded (only 0x1A2D tested on hardware):

| ID | Sensors (e.g.) | Values |
|---|---|---|
| 0x1A2D, 0x1D20, 0xF824, 0xF8B4 | THGR122NX, THGR228N, THGN123N, THGR810, THGN801 | temperature, humidity |
| 0xEA4C, 0xEC40, 0xC844 | THN132N, THWR800 | temperature |
| 0x1984, 0x1994 | WGR800 | wind speed, gust, direction |
| 0x2914 | PCR800 | rain total, rain rate |

## Requirements

- Home Assistant 2025.2 or newer.
- A TellStick Net with **firmware 17**; see [Firmware](#firmware). Stock
  firmware works, no reflashing needed.
- Home Assistant and the TellStick Net on the **same network segment**:
  - Discovery uses UDP broadcast, which doesn't cross VLANs or routers. Across
    VLANs, add the device by IP address.
  - The device sends its events to Home Assistant over UDP. Home Assistant OS
    and Supervised are fine; in Docker, run the container with host
    networking (`network_mode: host`).
  - Firewalls must allow UDP 30303 and 42314 to the device and its replies
    back.

The TellStick ZNet Lite has an official local API and isn't supported here.

## Installation

### HACS (custom repository)

1. HACS → ⋮ → *Custom repositories* → add
   `https://github.com/jearos/ha-tellstick-net`, category *Integration*.
2. Install *TellStick Net (local)* and restart Home Assistant.

### Manual

Copy `custom_components/tellstick_local/` into your Home Assistant
`config/custom_components/` folder and restart.

## Setup

*Settings → Devices & services → Add integration → TellStick Net (local)*.
Pick the device from the list, or enter its IP address. A TellStick Net that
gets an address via DHCP may also show up as *Discovered*.

Sensors appear by themselves as they transmit. Give each sensor's device an
area (*Settings → Devices*) to have it show up in the default dashboard.

Then use **Configure** on the integration for:

| Option | What it does |
|---|---|
| Add Nexa self-learning switch or dimmer | Creates a switch or dimmer with a new random code. Save, put the receiver in learn mode (often: just plug it in) and turn it on in Home Assistant to pair. |
| Add switch or dimmer by pressing a remote | Listens for 60 seconds; press an on/off button on a Nexa/Proove self-learning remote or wall switch. A switch or dimmer with that button's code is created, so receivers that already obey the button obey Home Assistant too, without pairing. |
| Remove switches and dimmers | Removes them, including their devices. |
| Replace sensor | After a battery change: pick the old sensor and the new one. The old one keeps its history and follows the new ID; the duplicate is removed. |
| Settings | Whether newly heard sensors are enabled automatically. Turn off if you pick up your neighbours' sensors. |

Switch state is assumed (433 MHz is one-way). It also follows presses of the
same code over the air: for a switch copied from a remote, pressing that
remote updates Home Assistant. A switch paired with its own new code doesn't
see the remote's presses, because the remote sends a different code.

Dimmers take a brightness from Home Assistant and send it as an absolute
level (16 steps); "on" without a brightness lets the receiver return to its
own last level. Remotes and wall switches dim *relatively* (press on again to
start and stop the receiver's dim cycle) and only send on/off, so a dimmer's
brightness in Home Assistant follows Home Assistant, not the remote.

Unwanted sensors can be deleted from their device page. If they keep
transmitting they come back (disabled, if *Settings* says so).

## Automations on remote presses

```yaml
triggers:
  - trigger: event
    event_type: tellstick_local_command
    event_data:
      house: 53103098
      unit: 1
      method: turnon
```

Event data: `mac`, `protocol`, `model`, `house`, `unit`, `group`, `method`
(and `code` for some protocols). To find a remote's `house` and `unit`, listen
for `tellstick_local_command` under *Developer tools → Events* and press the
button.

## Firmware

**Firmware 17 is required.** The local interface first appeared in firmware
17, the last stable release. If the device reports an older version, Home
Assistant shows a repair issue.

| Firmware | Local interface |
|---|---|
| 2, 3, 4 | no |
| beta 12, beta 16 | no |
| **17** | **yes** (tested) |
| beta 18 (never released as stable) | yes (untested) |

The version a device runs is shown on its device page in Home Assistant.

### Updating the firmware

Only needed if your device runs firmware older than 17. The integration
doesn't update firmware itself (yet) and doesn't ship Telldus' firmware.
Updating is done from a computer over **TFTP**, following
[Telldus' own guide](https://developer.telldus.com/wiki/Firmware_linux). The
TellStick Net has a TFTP bootloader: the running firmware restarts into it
when a TFTP upload arrives, so you don't need to unplug anything.

> If an update is interrupted the device can be recovered (see below), but
> don't pull the power while uploading.

1. **Find the device's IP address**: the integration's device page in Home
   Assistant, or your router's list of connected devices. The computer must be
   on the same subnet (the device ignores restart requests from other
   subnets).
2. **Download the firmware**: `tellstick_net_fw17.hex` from the top level of
   Telldus' firmware folder, linked from their guide
   (`http://download.telldus.com/TellStick/Software/firmware/`). The `old/`
   and `beta/` subfolders hold older versions and unreleased betas; don't use
   those.
3. **Upload it with TFTP.**

   *Linux / macOS* - `tftp` is included on macOS; on Linux install e.g.
   `tftp-hpa`. In a terminal, in the folder with the file:

   ```
   tftp <device-ip>
   tftp> rexmt 1
   tftp> timeout 60
   tftp> put tellstick_net_fw17.hex
   tftp> quit
   ```

   `rexmt 1` / `timeout 60` make the client retry every second for up to a
   minute: the first packets only make the device restart into its
   bootloader, which then accepts the upload. Wait until `put` reports that
   the transfer is complete.

   *Windows* - turn on *TFTP Client* under *Turn Windows features on or off*,
   or use Telldus' *TellStickNetFlasher* tool; see Telldus' guides linked from
   the page above.
4. **Check**: after a short while the device comes back, normally with the
   same IP. Home Assistant shows the new version on the TellStick's device
   page and the repair issue disappears.

### If the update fails (recovery mode)

If an upload is interrupted, the device stays in its bootloader: the green
LED on the network connector **blinks in groups of seven**, and it listens on
the fixed address **`192.168.97.60`** for a new upload.

1. Give your computer an address on that subnet, e.g. `192.168.97.10`,
   netmask `255.255.255.0` (an extra address/alias on the wired interface,
   connected to the same switch as the TellStick).
2. Upload the firmware as above, to `192.168.97.60`.
3. Remove the extra address again. The device returns to its normal address
   from DHCP.

Telldus' full guide:
[How to restore a bricked TellStick Net](https://developer.telldus.com/wiki/How_to_restore_a_bricked_TellStick_Net)
(with variants for Linux, macOS and Windows).

## Security

The TellStick Net's local interface has **no authentication**: anything on
your LAN can send RF commands through it, register as its listener, change
its IP settings or restart it, and its discovery reply includes the device's
activation secret in plain text (this integration never stores or logs it).
433 MHz itself is unencrypted and one-way. Keep the device on a trusted
network/VLAN and don't use it to switch anything where an unexpected or
missed command would be dangerous.

## Limitations

- Switches and dimmers: only Arctech self-learning for now (no
  code-wheel/old Nexa, no other protocols). Dimmers are untested.
- Nexa frames have no checksum, and the TellStick often decodes a press's
  repeats with slipped bits. Those show up as extra
  `tellstick_local_command` events with odd house codes and units, so
  automations should match on `house` and `unit`. They never match a
  configured device, and *Add device by pressing* only takes a code heard
  three times within 10 s.
- One device per house code and unit: a remote button copied as a switch
  can't also be added as a button entity (its presses still fire
  `tellstick_local_command` events, and the switch follows them).
- Absolute dim commands sent by other controllers (e.g. a Nexa Bridge) are
  not reported by the TellStick, so their brightness can't be followed.
- Sensors: no UV (Oregon 0xD874/0xEC70), no barometer (0x5D60) yet. See
  *Which other devices are in range?* below to find out what's around.
- The device supports one listener at a time. Running this next to another
  local client (e.g. the tellsticknet MQTT bridge) makes them steal events
  from each other.
- No confirmation that a receiver actually switched (radio limitation).

## Troubleshooting

**The device isn't found during setup**
- Check that Home Assistant and the TellStick Net are on the same network
  segment (see [Requirements](#requirements)), then enter its IP address
  manually.
- The device needs firmware 17. If its network LED blinks in groups of seven,
  it's in recovery mode; see
  [If the update fails](#if-the-update-fails-recovery-mode).

**The device is found, but no sensors appear**
- Sensors appear only once they transmit, usually within a minute or two.
- Check *Configure → Settings*: with automatic enabling off, new sensors are
  added disabled.
- Another program registered as the TellStick's listener (e.g. the
  tellsticknet MQTT bridge, or a second Home Assistant) takes the events away.
- In Docker without host networking, the device's events can't reach Home
  Assistant.

**A sensor shows up as a new device after a battery change**
- Use *Configure → Replace sensor*; see [Setup](#setup).

**A switch doesn't switch the receiver**
- Pair it first (receiver in learn mode, then turn the switch on), or create
  it with *Add switch by pressing a remote*.
- Check the range: the TellStick Net transmits on 433 MHz from where it
  stands.

**Which other devices are in range?**
- With the log level at `info` (or `debug`), each kind of 433 MHz device the
  TellStick Net hears but this integration can't decode yet is logged once,
  e.g. `Heard a 433 MHz device this integration can't decode yet: oregon
  0x5D60`. Handy before opening a feature request.

For debug logs, add to `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.tellstick_local: debug
```

## How it works

```
Home Assistant ──UDP 30303 "D"──────────▶ TellStick Net   discovery / liveness
               ──UDP 42314 reglistener──▶                 "send RF events to me"
               ◀─UDP RawData────────────                  every received RF packet
               ──UDP 42314 send─────────▶                 transmit RF command
```

Packets use Telldus' LiveMessage encoding. The device forwards raw RF data;
decoding happens in Home Assistant.

## Development

```bash
python3 -m unittest discover -s tests -v
```

The tests run without Home Assistant: `hub.py` and `known_sensors.py` have no
Home Assistant imports and are tested against a fake device on localhost.

## Credits

The RF protocol decoders/encoders in `custom_components/tellstick_local/tellsticknet/`
are vendored from [molobrakos/tellsticknet](https://github.com/molobrakos/tellsticknet)
(MIT, see the `LICENSE` in that folder), which first documented local access
to the TellStick Net.

## License

MIT, see [LICENSE](LICENSE).
