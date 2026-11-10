"""Radio clean-up rules, fed with frames captured on real hardware."""

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

FRAME = 0.207  # spacing of a Nexa remote's repeated frames


def nexa(house=22639810, unit=1, method="turnon"):
    return {
        "class": "command", "protocol": "arctech", "model": "selflearning",
        "house": house, "unit": unit, "group": 0, "method": method,
    }


def everflourish(house=5964):
    return {
        "class": "command", "protocol": "everflourish", "model": "selflearning",
        "house": house, "unit": 1, "method": "turnoff",
    }


# Five short presses of "on", button 1, captured 2026-11-10 23:36:07-20
# (seconds, decoded frame). Bit-slipped copies of 565D3090 are mixed in.
CAPTURE = [
    (7.954, nexa(39214384, 5, "turnoff")),  # 95974C24, rotated 2 bits
    (8.212, nexa()),
    (10.342, nexa()),
    (10.514, nexa(46901068, 10, "turnoff")),  # B2E9D309
    (10.685, nexa()),
    (12.552, nexa(11319905, 9, "turnoff")),  # 2B2E9848, shifted 1 bit
    (12.726, nexa(12731212, 10, "turnoff")),  # 3090D309
    (14.948, nexa()),
    (15.206, nexa()),
    (17.348, nexa(11319905, 9, "turnoff")),
    (17.606, nexa()),
    (19.821, nexa()),
    (19.994, nexa(46901068, 10, "turnoff")),
]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class CommandFilterTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.start = self.clock.now

    def run_filter(self, timed, **kwargs):
        """Feed (seconds, packet) pairs; return [(packet, classification)]."""
        f = rf.CommandFilter(clock=self.clock, **kwargs)
        out = []
        for at, packet in timed:
            self.clock.now = self.start + at
            if packet["protocol"] == "arctech":
                f.saw_frame("arctech")
            out.append((packet, f.classify(packet)))
        return out

    def test_every_press_delivered_once(self):
        delivered = [
            p["house"] for p, kind in self.run_filter(CAPTURE) if kind == rf.NEW
        ]
        # All five presses (8.2, 10.3, 14.9, 17.6, 19.8) get through once
        self.assertEqual(delivered.count(22639810), 5)

    def test_held_button_is_one_press(self):
        # Captured 23:31:36-39: 11 frames 0.207 s apart
        kinds = [k for _, k in self.run_filter([(i * FRAME, nexa()) for i in range(11)])]
        self.assertEqual(kinds, [rf.NEW] + [rf.REPEAT] * 10)

    def test_learning_takes_the_recurring_code(self):
        learned = [
            p["house"]
            for p, kind in self.run_filter(
                CAPTURE, repeat_window=rf.LEARN_WINDOW, frames_needed=rf.LEARN_FRAMES
            )
            if kind == rf.NEW
        ]
        self.assertEqual(learned[0], 22639810)

    def test_learning_ignores_a_code_heard_twice(self):
        # 11319905 u9 recurred 4.8 s apart; twice isn't enough
        kinds = [
            k
            for _, k in self.run_filter(
                [(12.552, nexa(11319905, 9)), (17.348, nexa(11319905, 9))],
                repeat_window=rf.LEARN_WINDOW,
                frames_needed=rf.LEARN_FRAMES,
            )
        ]
        self.assertEqual(kinds, [rf.DROP, rf.DROP])

    def test_phantom_everflourish_dropped(self):
        # Captured: everflourish 5964 turnoff 0.050 s after every Nexa frame
        kinds = [
            k
            for _, k in self.run_filter(
                [(0, nexa()), (0.05, everflourish()), (FRAME, nexa()), (FRAME + 0.05, everflourish())]
            )
        ]
        self.assertEqual(kinds, [rf.NEW, rf.DROP, rf.REPEAT, rf.DROP])

    def test_phantom_of_undecodable_frame_dropped(self):
        # An arctech frame that failed to decode never reaches classify()
        f = rf.CommandFilter(clock=self.clock)
        f.saw_frame("arctech")
        self.clock.now += 0.4
        self.assertEqual(f.classify(everflourish()), rf.DROP)

    def test_real_everflourish_kept(self):
        kinds = [
            k for _, k in self.run_filter([(0, nexa()), (3.0, everflourish()), (3.2, everflourish())])
        ]
        self.assertEqual(kinds, [rf.NEW, rf.NEW, rf.REPEAT])

    def test_sensor_packets_pass(self):
        sensor = {"class": "sensor", "protocol": "oregon", "sensorId": 82}
        kinds = [k for _, k in self.run_filter([(0, sensor), (0.1, sensor)])]
        self.assertEqual(kinds, [rf.NEW, rf.NEW])


if __name__ == "__main__":
    unittest.main()
