"""Radio clean-up rules, fed with sequences seen on real hardware."""

import importlib
import sys
import types
import unittest
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "tellstick_local"

if "tellstick_local" not in sys.modules:
    pkg = types.ModuleType("tellstick_local")
    pkg.__path__ = [str(PKG_DIR)]
    sys.modules["tellstick_local"] = pkg

rf = importlib.import_module("tellstick_local.rf_filter")


def nexa(house=22639810, unit=1, method="turnoff"):
    return {
        "class": "command", "protocol": "arctech", "model": "selflearning",
        "house": house, "unit": unit, "group": 0, "method": method,
    }


def everflourish(house=5964):
    return {
        "class": "command", "protocol": "everflourish", "model": "selflearning",
        "house": house, "unit": 1, "method": "turnoff",
    }


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class CommandFilterTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.filter = rf.CommandFilter(clock=self.clock)

    def feed(self, *timed):
        """(seconds since start, packet) pairs -> classifications."""
        start = self.clock.now
        out = []
        for at, packet in timed:
            self.clock.now = start + at
            out.append(self.filter.classify(packet))
        return out

    def test_repeats_of_one_press(self):
        # One press of turnoff, repeats 0.15-0.4 s apart, then a new press
        self.assertEqual(
            self.feed((0, nexa()), (0.39, nexa()), (0.6, nexa()), (2.0, nexa())),
            [rf.NEW, rf.REPEAT, rf.REPEAT, rf.NEW],
        )

    def test_other_button_is_new(self):
        self.assertEqual(
            self.feed((0, nexa()), (0.2, nexa(method="turnon")), (0.3, nexa(unit=2))),
            [rf.NEW, rf.NEW, rf.NEW],
        )

    def test_held_button_keeps_repeating(self):
        # Each repeat extends the window, so a long press stays one press
        timed = [(0.5 * i, nexa()) for i in range(6)]
        self.assertEqual(self.feed(*timed), [rf.NEW] + [rf.REPEAT] * 5)

    def test_phantom_everflourish_dropped(self):
        # Recorder: everflourish turnoff unit 1, ~50 ms after each Nexa frame
        self.assertEqual(
            self.feed((0, nexa()), (0.05, everflourish()), (0.2, everflourish(8524))),
            [rf.NEW, rf.DROP, rf.DROP],
        )

    def test_real_everflourish_kept(self):
        self.assertEqual(
            self.feed((0, nexa()), (2.0, everflourish()), (2.2, everflourish())),
            [rf.NEW, rf.NEW, rf.REPEAT],
        )

    def test_sensor_packets_pass(self):
        sensor = {"class": "sensor", "protocol": "oregon", "sensorId": 82}
        self.assertEqual(self.feed((0, sensor), (0.1, sensor)), [rf.NEW, rf.NEW])

    def test_learning_ignores_slipped_bit_frame(self):
        # Recorder: 22639810 also arrived as 11319905 (one bit slipped).
        # Learning waits for a code heard twice, so only the real one counts.
        learn = rf.CommandFilter(repeat_window=5.0, clock=self.clock)
        start = self.clock.now
        seen = []
        for at, packet in (
            (0, nexa(house=11319905, unit=9)),
            (0.2, nexa()),
            (3.0, nexa()),
        ):
            self.clock.now = start + at
            if learn.classify(packet) == rf.REPEAT:
                seen.append(packet["house"])
        self.assertEqual(seen, [22639810])


if __name__ == "__main__":
    unittest.main()
