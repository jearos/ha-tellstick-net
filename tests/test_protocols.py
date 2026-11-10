"""Vendored protocol decoders (no Home Assistant needed).

Runs the decoders' doctests and decodes whole RawData packets as the firmware
sends them.
"""

import doctest
import importlib
import sys
import types
import unittest
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "tellstick_local"

if "tellstick_local" not in sys.modules:
    # Skip the integration's __init__.py, which imports Home Assistant
    pkg = types.ModuleType("tellstick_local")
    pkg.__path__ = [str(PKG_DIR)]
    sys.modules["tellstick_local"] = pkg

protocol = importlib.import_module("tellstick_local.tellsticknet.protocol")
oregon = importlib.import_module("tellstick_local.tellsticknet.protocols.oregon")


def load_tests(loader, tests, ignore):
    tests.addTests(doctest.DocTestSuite(oregon))
    return tests


def raw_oregon(model: int, data: int) -> bytes:
    """A RawData LiveMessage like the firmware's oregonv3.c emits."""
    return (
        b"7:RawDatah5:class6:sensor8:protocol6:oregon"
        + b"5:modeli%Xs4:datai%Xss" % (model, data)
    )


class OregonPacketTests(unittest.TestCase):
    def values(self, packet):
        return {d["name"]: d["value"] for d in packet["data"]}

    def test_temperature_humidity(self):
        packet = protocol.decode_packet(raw_oregon(0xF824, 0x13B05220640F37))
        self.assertEqual(packet["class"], "sensor")
        self.assertEqual(packet["model"], 0xF824)
        self.assertEqual(packet["sensorId"], 0x3B)
        self.assertEqual(self.values(packet), {"temp": 22.5, "humidity": 46.0})

    def test_wind(self):
        packet = protocol.decode_packet(raw_oregon(0x1984, 0x17C0400230020533))
        self.assertEqual(
            self.values(packet),
            {"winddirection": 90.0, "windaverage": 2.0, "windgust": 3.2},
        )

    def test_rain(self):
        packet = protocol.decode_packet(raw_oregon(0x2914, 0x1E105200102100B2))
        self.assertEqual(self.values(packet), {"raintotal": 30.5, "rainrate": 6.3})

    def test_bad_checksum_rejected(self):
        with self.assertRaises(ValueError):
            protocol.decode_packet(raw_oregon(0xF824, 0x13B05220640F47))

    def test_unmatched_packet_is_not_unsupported(self):
        # A known protocol whose decoders all reject this packet (code-wheel
        # method 1 is no valid command) must not look like an unsupported kind.
        packet = b"7:RawDatah8:protocol7:arctech5:modelA:codeswitch4:datai100ss"
        with self.assertRaises(ValueError):
            protocol.decode_packet(packet)

    def test_unknown_model_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            protocol.decode_packet(raw_oregon(0x5D60, 0x1234))


if __name__ == "__main__":
    unittest.main()
