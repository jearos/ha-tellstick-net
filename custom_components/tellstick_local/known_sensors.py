"""Bookkeeping for RF sensors, kept apart from their radio identity.

Cheap 433 MHz sensors (Oregon, Fine Offset, Mandolyn, ...) pick a new random
ID when the battery is changed or the sensor is reset. If the Home Assistant
entity were keyed on that radio ID, every battery change would create a new
entity and orphan the old one's history.

So each sensor gets a stable ``uid`` the first time it is heard (derived from
its radio key at that moment, then frozen). The radio key it currently
transmits with is just a mutable property of the sensor, and ``replace()``
moves a newly heard radio key onto an existing sensor: the entity, its name
and its history stay, only the radio ID it listens for changes.

No Home Assistant imports here, so this can be unit-tested standalone.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

type RadioKey = tuple[str, str, int]  # (protocol, model, sensor id)


@dataclass
class KnownSensor:
    uid: str
    protocol: str
    model: str
    sensor_id: int
    quantities: list[str] = field(default_factory=list)
    first_seen: float | None = None
    last_seen: float | None = None

    @property
    def radio_key(self) -> RadioKey:
        return (self.protocol, self.model, self.sensor_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "protocol": self.protocol,
            "model": self.model,
            "sensor_id": self.sensor_id,
            "quantities": list(self.quantities),
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KnownSensor:
        return cls(
            uid=str(data["uid"]),
            protocol=str(data["protocol"]),
            model=str(data["model"]),
            sensor_id=int(data["sensor_id"]),
            quantities=[str(q) for q in data.get("quantities", [])],
            first_seen=data.get("first_seen"),
            last_seen=data.get("last_seen"),
        )


class KnownSensors:
    """All sensors one TellStick has heard, indexed by uid and by radio key.

    ``on_change(structural)`` is called after every mutation. ``structural``
    is True when sensors were added, replaced or removed (worth saving right
    away) and False when only ``last_seen`` moved (fine to save lazily).
    """

    def __init__(
        self,
        data: dict[str, Any] | None = None,
        on_change: Callable[[bool], None] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._on_change = on_change or (lambda structural: None)
        self._clock = clock
        self._by_uid: dict[str, KnownSensor] = {}
        for item in (data or {}).get("sensors", []):
            sensor = KnownSensor.from_dict(item)
            self._by_uid[sensor.uid] = sensor
        self._by_radio = {s.radio_key: s for s in self._by_uid.values()}

    def to_data(self) -> dict[str, Any]:
        return {"sensors": [s.to_dict() for s in self._by_uid.values()]}

    def __iter__(self):
        return iter(list(self._by_uid.values()))

    def __len__(self) -> int:
        return len(self._by_uid)

    def get(self, uid: str) -> KnownSensor | None:
        return self._by_uid.get(uid)

    def find(self, key: RadioKey) -> KnownSensor | None:
        return self._by_radio.get(key)

    def heard(self, key: RadioKey, quantities: Iterable[str]) -> tuple[KnownSensor, bool]:
        """Record a reception. Returns (sensor, created_or_grew).

        The second value is True when the sensor is new or reported a quantity
        it hadn't before, i.e. when new entities are needed.
        """
        now = self._clock()
        sensor = self._by_radio.get(key)
        structural = False
        if sensor is None:
            sensor = KnownSensor(
                uid=self._new_uid(key),
                protocol=key[0],
                model=key[1],
                sensor_id=key[2],
                first_seen=now,
            )
            self._by_uid[sensor.uid] = sensor
            self._by_radio[key] = sensor
            structural = True
        for quantity in quantities:
            if quantity not in sensor.quantities:
                sensor.quantities.append(quantity)
                structural = True
        sensor.last_seen = now
        self._on_change(structural)
        return sensor, structural

    def replace(self, keep_uid: str, new_uid: str) -> KnownSensor:
        """Make sensor ``keep_uid`` listen to ``new_uid``'s radio key.

        ``new_uid`` is dropped. Use it after a battery change gave a sensor a
        new random ID: ``keep_uid`` is the old sensor (with the history),
        ``new_uid`` the entry that was auto-created for the new ID.
        """
        if keep_uid == new_uid:
            raise ValueError("cannot replace a sensor with itself")
        keep = self._by_uid[keep_uid]
        new = self._by_uid[new_uid]
        self._by_radio.pop(keep.radio_key, None)
        self._by_radio.pop(new.radio_key, None)
        del self._by_uid[new_uid]
        keep.protocol, keep.model, keep.sensor_id = new.radio_key
        for quantity in new.quantities:
            if quantity not in keep.quantities:
                keep.quantities.append(quantity)
        keep.last_seen = max(
            (t for t in (keep.last_seen, new.last_seen) if t is not None), default=None
        )
        self._by_radio[keep.radio_key] = keep
        self._on_change(True)
        return keep

    def remove(self, uid: str) -> None:
        sensor = self._by_uid.pop(uid, None)
        if sensor is not None:
            self._by_radio.pop(sensor.radio_key, None)
            self._on_change(True)

    def _new_uid(self, key: RadioKey) -> str:
        # Readable and deterministic for a sensor's first ID, so a lost
        # storage file still maps a never-replaced sensor to its old entities.
        base = "-".join(str(part) for part in key)
        uid, n = base, 2
        while uid in self._by_uid:
            uid, n = f"{base}-{n}", n + 1
        return uid
