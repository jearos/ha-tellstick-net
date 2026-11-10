"""Tests for sensor bookkeeping (stable uid vs. changing radio ID).

Run from the repository root:
    python3 -m unittest tests.test_known_sensors -v
"""

import importlib.util
import sys
import unittest
from pathlib import Path

PATH = (
    Path(__file__).resolve().parent.parent
    / "custom_components" / "tellstick_local" / "known_sensors.py"
)
_spec = importlib.util.spec_from_file_location("known_sensors", PATH)
ks = importlib.util.module_from_spec(_spec)
sys.modules["known_sensors"] = ks
_spec.loader.exec_module(ks)

OLD = ("oregon", "6701", 41)
NEW = ("oregon", "6701", 173)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class KnownSensorsTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.changes = []
        self.sensors = ks.KnownSensors(None, self.changes.append, self.clock)

    def test_first_reception_creates_sensor_with_readable_uid(self):
        sensor, created = self.sensors.heard(OLD, ["temp", "humidity"])
        self.assertTrue(created)
        self.assertEqual(sensor.uid, "oregon-6701-41")
        self.assertEqual(sensor.quantities, ["temp", "humidity"])
        self.assertEqual(self.changes, [True])

    def test_repeat_reception_only_touches_last_seen(self):
        self.sensors.heard(OLD, ["temp"])
        self.clock.now += 40
        sensor, created = self.sensors.heard(OLD, ["temp"])
        self.assertFalse(created)
        self.assertEqual(sensor.last_seen, 1040.0)
        self.assertEqual(sensor.first_seen, 1000.0)
        self.assertEqual(self.changes, [True, False])

    def test_new_quantity_is_structural(self):
        self.sensors.heard(OLD, ["temp"])
        _, grew = self.sensors.heard(OLD, ["temp", "humidity"])
        self.assertTrue(grew)

    def test_replace_moves_new_radio_id_onto_old_sensor(self):
        old, _ = self.sensors.heard(OLD, ["temp", "humidity"])
        self.clock.now += 86400
        new, _ = self.sensors.heard(NEW, ["temp", "humidity"])

        kept = self.sensors.replace(old.uid, new.uid)

        self.assertIs(kept, old)
        self.assertEqual(kept.uid, "oregon-6701-41")  # entity ids unchanged
        self.assertEqual(kept.radio_key, NEW)
        self.assertEqual(kept.last_seen, 1000.0 + 86400)
        self.assertIsNone(self.sensors.get(new.uid))
        self.assertEqual(len(self.sensors), 1)
        # Packets with the new ID now land on the old sensor
        sensor, created = self.sensors.heard(NEW, ["temp"])
        self.assertIs(sensor, old)
        self.assertFalse(created)
        # The old ID is free again and would be a brand new sensor
        self.assertIsNone(self.sensors.find(OLD))

    def test_replace_merges_quantities(self):
        old, _ = self.sensors.heard(OLD, ["temp"])
        new, _ = self.sensors.heard(NEW, ["temp", "humidity"])
        kept = self.sensors.replace(old.uid, new.uid)
        self.assertEqual(kept.quantities, ["temp", "humidity"])

    def test_replace_with_itself_is_rejected(self):
        old, _ = self.sensors.heard(OLD, ["temp"])
        with self.assertRaises(ValueError):
            self.sensors.replace(old.uid, old.uid)

    def test_uid_collision_after_replace(self):
        # Sensor 41 gets replaced by 173, then another sensor rolls ID 41
        old, _ = self.sensors.heard(OLD, ["temp"])
        new, _ = self.sensors.heard(NEW, ["temp"])
        self.sensors.replace(old.uid, new.uid)
        other, created = self.sensors.heard(OLD, ["temp"])
        self.assertTrue(created)
        self.assertEqual(other.uid, "oregon-6701-41-2")
        self.assertIsNot(other, old)

    def test_round_trip_through_storage(self):
        old, _ = self.sensors.heard(OLD, ["temp", "humidity"])
        new, _ = self.sensors.heard(NEW, ["temp"])
        self.sensors.replace(old.uid, new.uid)
        loaded = ks.KnownSensors(self.sensors.to_data())
        self.assertEqual(
            [s.to_dict() for s in loaded], [s.to_dict() for s in self.sensors]
        )
        self.assertEqual(loaded.find(NEW).uid, "oregon-6701-41")

    def test_remove(self):
        old, _ = self.sensors.heard(OLD, ["temp"])
        self.sensors.remove(old.uid)
        self.assertEqual(len(self.sensors), 0)
        self.assertIsNone(self.sensors.find(OLD))


class RadioKeyTests(unittest.TestCase):
    def test_sensor_packet(self):
        packet = {"class": "sensor", "protocol": "oregon", "model": 6701, "sensorId": 131}
        self.assertEqual(ks.radio_key(packet), ("oregon", "6701", 131))

    def test_other_packets(self):
        self.assertIsNone(ks.radio_key({"class": "command", "protocol": "arctech"}))
        self.assertIsNone(ks.radio_key({"class": "sensor", "protocol": "oregon"}))


if __name__ == "__main__":
    unittest.main()
