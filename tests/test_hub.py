"""Hub tests against a fake TellStick Net on localhost (no Home Assistant needed).

Run from the repository root:
    python3 -m unittest tests.test_hub -v

The hub module is loaded standalone (without the integration's __init__.py,
which imports Home Assistant).
"""

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "tellstick_local"


def _load_hub():
    pkg = types.ModuleType("tellstick_local")
    pkg.__path__ = [str(PKG_DIR)]
    sys.modules["tellstick_local"] = pkg
    spec = importlib.util.spec_from_file_location("tellstick_local.hub", PKG_DIR / "hub.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["tellstick_local.hub"] = module
    spec.loader.exec_module(module)
    return module


hub_mod = _load_hub()

MAC = "acca54000001"
MANDOLYN = (
    b"7:RawDatah5:class6:sensor8:protocol8:mandolyn5:model"
    b"13:temperaturehumidity4:dataiAF1D466Bss"
)
# Nexa self-learning remote: house 53103098, unit 1, turn on
NEXA_ON = (
    b"7:RawDatah8:protocol7:arctech5:modelC:selflearning4:datai"
    + b"%X" % ((53103098 << 6) | (1 << 4) | 0)
    + b"ss"
)


class FakeDevice:
    """Binds the device's two UDP ports on 127.0.0.1."""

    def __init__(self):
        self.commands: list[tuple[bytes, tuple]] = []
        self.listener = None
        self.command_tx = None
        self.discovery_tx = None

    async def start(self):
        loop = asyncio.get_running_loop()
        dev = self

        class Cmd(asyncio.DatagramProtocol):
            def datagram_received(self, data, addr):
                dev.commands.append((data, addr))
                if data.startswith(b"B:reglistener"):
                    dev.listener = addr

        class Disc(asyncio.DatagramProtocol):
            def datagram_received(self, data, addr):
                if data == b"D":
                    dev.discovery_tx.sendto(
                        f"TellStickNet:{MAC.upper()}:SECRETCODE:X".encode(), addr
                    )

        self.command_tx, _ = await loop.create_datagram_endpoint(
            Cmd, local_addr=("127.0.0.1", hub_mod.COMMAND_PORT)
        )
        self.discovery_tx, _ = await loop.create_datagram_endpoint(
            Disc, local_addr=("127.0.0.1", hub_mod.DISCOVERY_PORT)
        )

    def emit(self, packet: bytes):
        self.command_tx.sendto(packet, self.listener)

    def stop(self):
        self.command_tx.close()
        self.discovery_tx.close()


async def wait_for(predicate, timeout=2.0):
    end = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


class ParseTests(unittest.TestCase):
    def test_discovery_reply_drops_secret(self):
        d = hub_mod.parse_discovery_reply(b"TellStickNet:ACCA54000001:SECRET:17", "10.0.0.5")
        self.assertEqual(d, hub_mod.DiscoveredDevice("10.0.0.5", "TellStickNet", MAC, "17"))
        self.assertNotIn("SECRET", repr(d))

    def test_discovery_reply_rejects_garbage(self):
        self.assertIsNone(hub_mod.parse_discovery_reply(b"hello", "x"))
        self.assertIsNone(hub_mod.parse_discovery_reply(b"TellStickNet:nothex:a:b", "x"))
        self.assertIsNone(hub_mod.parse_discovery_reply(b"\xff\xfe", "x"))


class HubTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.device = FakeDevice()
        await self.device.start()
        self.packets = []
        self.availability = []
        self.hub = hub_mod.TellStickHub(
            "127.0.0.1",
            self.packets.append,
            self.availability.append,
            ping_interval=0.1,
            register_interval=0.3,
            offline_after=0.35,
        )
        await self.hub.async_start()

    async def asyncTearDown(self):
        await self.hub.async_stop()
        self.device.stop()

    async def test_registers_and_becomes_available(self):
        await wait_for(lambda: self.device.listener is not None)
        await wait_for(lambda: self.hub.available)
        self.assertEqual(self.hub.firmware, "X")
        self.assertEqual(self.availability[0], True)

    async def test_reregisters_periodically(self):
        await wait_for(
            lambda: sum(c.startswith(b"B:reglistener") for c, _ in self.device.commands) >= 3,
            timeout=3,
        )

    async def test_sensor_packet_decoded(self):
        await wait_for(lambda: self.device.listener is not None)
        self.device.emit(MANDOLYN)
        await wait_for(lambda: self.packets)
        packet = self.packets[0]
        self.assertEqual(packet["class"], "sensor")
        self.assertEqual(packet["protocol"], "mandolyn")
        values = {d["name"]: d["value"] for d in packet["data"]}
        self.assertEqual(values["temp"], 20.4)
        self.assertIn("humidity", values)

    async def test_remote_command_decoded(self):
        await wait_for(lambda: self.device.listener is not None)
        self.device.emit(NEXA_ON)
        await wait_for(lambda: self.packets)
        packet = self.packets[0]
        self.assertEqual(packet["class"], "command")
        self.assertEqual(packet["house"], 53103098)
        self.assertEqual(packet["unit"], 1)
        self.assertEqual(packet["method"], "turnon")

    async def test_garbage_is_ignored(self):
        await wait_for(lambda: self.device.listener is not None)
        self.device.emit(b"not a livemessage")
        self.device.emit(b"7:RawDatah8:protocol4:nopes4:datai1ss")
        self.device.emit(MANDOLYN)
        await wait_for(lambda: self.packets)
        self.assertEqual(len(self.packets), 1)

    async def test_turn_on_matches_firmware_format(self):
        await wait_for(lambda: self.device.listener is not None)
        self.hub.turn_on(53103098, 1)
        self.hub.turn_off(53103098, 1)
        await wait_for(lambda: sum(c.startswith(b"4:send") for c, _ in self.device.commands) == 2)
        on, off = [c for c, _ in self.device.commands if c.startswith(b"4:send")]
        # Firmware (tellsticknet.c / transmit_arctech.c) reads protocol, model,
        # house, unit (0-based) and method (1 = on) from the hash.
        self.assertTrue(
            on.startswith(
                b"4:sendh8:protocol7:arctech5:modelC:selflearning"
                b"5:housei32a49fas4:uniti0s6:methodi1ss"
            ),
            on,
        )
        self.assertIn(b"6:methodi2ss", off)

    async def test_dim_sends_absolute_dim_frame(self):
        await wait_for(lambda: self.device.listener is not None)
        self.hub.dim(53103098, 2, 128)
        self.hub.dim(53103098, 2, 0)
        await wait_for(lambda: sum(c.startswith(b"4:send") for c, _ in self.device.commands) == 2)
        dim, off = [c for c, _ in self.device.commands if c.startswith(b"4:send")]
        # Raw pulse string under "S": the firmware has no native dim command
        self.assertTrue(dim.startswith(b"4:sendh1:S"), dim)
        length_hex, rest = dim[len(b"4:sendh1:S"):].split(b":", 1)
        pulses = rest[: int(length_hex, 16)]
        short, long_ = bytes([24]), bytes([127])
        one, zero = short + long_ + short + short, short + short + short + long_
        bits = lambda value, n: b"".join(one if value & (1 << i) else zero for i in range(n - 1, -1, -1))
        self.assertEqual(
            pulses,
            short + bytes([255])
            + bits(53103098, 26)  # house
            + zero  # group
            + short * 4  # the "dim" symbol in place of on/off
            + bits(1, 4)  # unit 2, 0-based
            + bits(128 // 16, 4)  # level, 16 steps
            + short,
        )
        # Level 0 is a plain off, in the firmware's native format
        self.assertTrue(off.startswith(b"4:sendh8:protocol7:arctech5:modelC:selflearning"), off)
        self.assertIn(b"6:methodi2ss", off)

    async def test_goes_offline_and_recovers(self):
        await wait_for(lambda: self.hub.available)
        self.device.stop()
        await wait_for(lambda: not self.hub.available, timeout=3)
        before = sum(c.startswith(b"B:reglistener") for c, _ in self.device.commands)
        self.device = FakeDevice()
        await self.device.start()
        await wait_for(lambda: self.hub.available, timeout=3)
        # Re-registers right away after coming back
        await wait_for(lambda: self.device.listener is not None, timeout=1)
        self.assertGreaterEqual(before, 1)


class FirmwareTests(unittest.IsolatedAsyncioTestCase):
    async def test_reports_firmware_once(self):
        device = FakeDevice()
        await device.start()
        versions = []
        hub = hub_mod.TellStickHub(
            "127.0.0.1", lambda packet: None, ping_interval=0.05, on_firmware=versions.append
        )
        await hub.async_start()
        try:
            await wait_for(lambda: versions)
            await asyncio.sleep(0.3)  # several more pings answered
            self.assertEqual(versions, ["X"])
            self.assertEqual(hub.firmware, "X")
        finally:
            await hub.async_stop()
            device.stop()


class RediscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_follows_device_to_new_address(self):
        # Configured with a stale address; the device now answers on 127.0.0.1
        device = FakeDevice()
        await device.start()
        moved = []
        hub = hub_mod.TellStickHub(
            "127.0.0.9",
            lambda packet: None,
            ping_interval=0.1,
            offline_after=0.35,
            mac=MAC,
            on_host_changed=moved.append,
            rediscover_interval=0.2,
            rediscover_target="127.0.0.1",
        )
        await hub.async_start()
        try:
            await wait_for(lambda: moved, timeout=6)
            self.assertEqual(moved, ["127.0.0.1"])
            self.assertEqual(hub.host, "127.0.0.1")
            await wait_for(lambda: device.listener is not None)
            await wait_for(lambda: hub.available)
        finally:
            await hub.async_stop()
            device.stop()

    async def test_ignores_other_devices(self):
        device = FakeDevice()
        await device.start()
        moved = []
        hub = hub_mod.TellStickHub(
            "127.0.0.9",
            lambda packet: None,
            ping_interval=0.1,
            offline_after=0.35,
            mac="acca54ffffff",
            on_host_changed=moved.append,
            rediscover_interval=0.2,
            rediscover_target="127.0.0.1",
        )
        await hub.async_start()
        try:
            await asyncio.sleep(4)
            self.assertEqual(moved, [])
            self.assertEqual(hub.host, "127.0.0.9")
        finally:
            await hub.async_stop()
            device.stop()


if __name__ == "__main__":
    unittest.main()
