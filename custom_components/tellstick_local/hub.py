"""UDP link to a TellStick Net running local-access firmware.

Deliberately free of Home Assistant imports so it can be exercised against a
fake device in plain asyncio.

Protocol summary (see firmware/ in this repository):
- Discovery: send b"D" to UDP 30303, the device answers from port 30303 with
  "TellStickNet:<MAC>:<code>:<firmware version>".
- Commands: LiveMessage-encoded packets to UDP 42314, e.g. "reglistener",
  "send". The firmware keeps exactly one listener (the last sender of
  "reglistener") and forwards every received RF packet to it as "RawData".
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .tellsticknet import const as tn_const
from .tellsticknet.protocol import decode_packet, encode, encode_packet

_LOGGER = logging.getLogger(__name__)

DISCOVERY_PORT = 30303
COMMAND_PORT = 42314

# The firmware forgets its listener on reboot, so register again regularly.
REGISTER_INTERVAL = 300.0
# Discovery queries double as a liveness check.
PING_INTERVAL = 30.0
# Considered offline after this long without any reply or RF packet.
OFFLINE_AFTER = 95.0
# While offline, broadcast for our MAC this often in case DHCP moved it.
REDISCOVER_INTERVAL = 120.0


@dataclass(frozen=True)
class DiscoveredDevice:
    """A TellStick Net found on the LAN."""

    host: str
    product: str
    mac: str
    firmware: str


def parse_discovery_reply(data: bytes, host: str) -> DiscoveredDevice | None:
    """Parse a discovery reply. The middle field (activation code/secret) is
    deliberately dropped so it never ends up in logs or config entries."""
    try:
        text = data.decode("ascii").strip()
    except UnicodeDecodeError:
        return None
    parts = text.split(":")
    if len(parts) < 4 or not parts[0].startswith("TellStickNet"):
        return None
    mac = parts[1].lower()
    if len(mac) != 12 or any(c not in "0123456789abcdef" for c in mac):
        return None
    return DiscoveredDevice(host=host, product=parts[0], mac=mac, firmware=parts[-1])


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.found: dict[str, DiscoveredDevice] = {}

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        device = parse_discovery_reply(data, addr[0])
        if device is not None:
            self.found[device.mac] = device


async def async_discover(
    timeout: float = 3.0, target: str = "255.255.255.255"
) -> list[DiscoveredDevice]:
    """Broadcast a discovery query (or unicast it to `target`)."""
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setblocking(False)
    sock.bind(("", 0))
    transport, proto = await loop.create_datagram_endpoint(
        _DiscoveryProtocol, sock=sock
    )
    try:
        # Repeat a few times; UDP broadcast is lossy on some Wi-Fi setups.
        for _ in range(3):
            transport.sendto(b"D", (target, DISCOVERY_PORT))
            await asyncio.sleep(timeout / 3)
    finally:
        transport.close()
    return sorted(proto.found.values(), key=lambda d: d.host)


class _HubProtocol(asyncio.DatagramProtocol):
    def __init__(self, hub: TellStickHub) -> None:
        self._hub = hub

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._hub._datagram_received(data, addr)

    def error_received(self, exc: Exception) -> None:
        _LOGGER.debug("UDP error from TellStick Net: %s", exc)


class TellStickHub:
    """Talks to one TellStick Net over UDP."""

    def __init__(
        self,
        host: str,
        on_packet: Callable[[dict[str, Any]], None],
        on_availability: Callable[[bool], None] | None = None,
        *,
        register_interval: float = REGISTER_INTERVAL,
        ping_interval: float = PING_INTERVAL,
        offline_after: float = OFFLINE_AFTER,
        mac: str | None = None,
        on_host_changed: Callable[[str], None] | None = None,
        rediscover_interval: float = REDISCOVER_INTERVAL,
        rediscover_target: str = "255.255.255.255",
        on_firmware: Callable[[str], None] | None = None,
    ) -> None:
        self.host = host
        # With a MAC, the hub looks for the device elsewhere on the LAN when
        # it goes quiet (new DHCP lease) and follows it.
        self._mac = mac.lower() if mac else None
        self._on_host_changed = on_host_changed
        self._rediscover_interval = rediscover_interval
        self._rediscover_target = rediscover_target
        self._on_firmware = on_firmware
        self.firmware: str | None = None
        self._on_packet = on_packet
        self._on_availability = on_availability
        self._register_interval = register_interval
        self._ping_interval = ping_interval
        self._offline_after = offline_after
        self._transport: asyncio.DatagramTransport | None = None
        self._task: asyncio.Task | None = None
        self._last_seen: float | None = None
        self._available = False

    @property
    def available(self) -> bool:
        return self._available

    async def async_start(self) -> None:
        loop = asyncio.get_running_loop()
        # Ephemeral local port: the firmware sends RF events back to whatever
        # port "reglistener" came from.
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _HubProtocol(self), local_addr=("0.0.0.0", 0)
        )
        self._register()
        self._ping()
        self._task = loop.create_task(self._maintain())

    async def async_stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def turn_on(self, house: int, unit: int) -> None:
        self._send_arctech(house, unit, tn_const.TURNON)

    def turn_off(self, house: int, unit: int) -> None:
        self._send_arctech(house, unit, tn_const.TURNOFF)

    def _send_arctech(self, house: int, unit: int, method: int) -> None:
        # encode() takes a 1-based unit and returns the arguments for the
        # firmware's native arctech/selflearning on/off command.
        args = encode(
            protocol="arctech",
            model="selflearning",
            house=house,
            unit=unit,
            method=method,
            param=None,
        )
        self._send_command("send", **args)

    def _register(self) -> None:
        self._send_command("reglistener")

    def _ping(self) -> None:
        if self._transport is not None:
            self._transport.sendto(b"D", (self.host, DISCOVERY_PORT))

    def _send_command(self, command: str, **args: Any) -> None:
        if self._transport is None:
            raise ConnectionError("TellStick Net link is not running")
        packet = encode_packet(command, **args)
        _LOGGER.debug("-> %s %s", self.host, packet)
        self._transport.sendto(packet, (self.host, COMMAND_PORT))

    async def _maintain(self) -> None:
        last_register = time.monotonic()
        last_rediscover = time.monotonic()
        while True:
            await asyncio.sleep(self._ping_interval)
            now = time.monotonic()
            self._ping()
            if now - last_register >= self._register_interval:
                self._register()
                last_register = now
            if self._available and (
                self._last_seen is None
                or now - self._last_seen > self._offline_after
            ):
                _LOGGER.warning("TellStick Net at %s stopped responding", self.host)
                self._set_available(False)
            if (
                not self._available
                and self._mac
                and now - last_rediscover >= self._rediscover_interval
            ):
                last_rediscover = now
                await self._rediscover()

    async def _rediscover(self) -> None:
        try:
            found = await async_discover(target=self._rediscover_target)
        except OSError as err:
            _LOGGER.debug("Rediscovery failed: %s", err)
            return
        for device in found:
            if device.mac == self._mac and device.host != self.host:
                _LOGGER.warning(
                    "TellStick Net %s moved from %s to %s",
                    self._mac, self.host, device.host,
                )
                self.host = device.host
                self._register()
                self._ping()
                if self._on_host_changed is not None:
                    self._on_host_changed(device.host)
                return

    def _set_available(self, available: bool) -> None:
        if available == self._available:
            return
        self._available = available
        if self._on_availability is not None:
            self._on_availability(available)

    def _seen(self) -> None:
        self._last_seen = time.monotonic()
        if not self._available:
            # Back from offline (or first contact): it may have rebooted and
            # lost its listener. Registering twice is harmless.
            self._register()
            self._set_available(True)

    def _datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if addr[0] != self.host:
            return
        if addr[1] == DISCOVERY_PORT:
            device = parse_discovery_reply(data, addr[0])
            if device is not None:
                if device.firmware != self.firmware:
                    self.firmware = device.firmware
                    if self._on_firmware is not None:
                        self._on_firmware(device.firmware)
                self._seen()
            return

        self._seen()
        _LOGGER.debug("<- %s %s", self.host, data)
        try:
            packet = decode_packet(data)
        except Exception as err:  # noqa: BLE001 - decoders raise anything
            _LOGGER.debug("Could not decode %s: %r", data, err)
            return
        if packet:
            self._on_packet(packet)
