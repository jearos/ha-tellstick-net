"""Clean-up rules for decoded 433 MHz commands. No Home Assistant imports.

Seen on real hardware (stock firmware 17, Nexa remote):
- A remote sends every press several times; the TellStick reports most of
  those repeats, 0.15-0.4 s apart.
- Arctech frames have no checksum. Some arrive with a slipped bit: house
  22639810 also shows up as 11319905 (half) or 5659952 (a quarter), with
  wrong units. Such a frame almost never repeats identically.
- The firmware's Everflourish decoder also fires on Nexa signals: a phantom
  "everflourish turnoff unit 1" ~50 ms after the arctech frame.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

NEW = "new"
REPEAT = "repeat"
DROP = "drop"

# Repeats of one press arrive within this many seconds of each other
REPEAT_WINDOW = 1.0
# Everflourish frames this soon after an arctech frame are the same signal
GHOST_WINDOW = 0.3

_KEY_FIELDS = ("protocol", "model", "house", "unit", "group", "method")


class CommandFilter:
    """Classifies command packets as NEW, REPEAT (of a recent identical
    command) or DROP (phantom). Other packets are always NEW."""

    def __init__(
        self,
        repeat_window: float = REPEAT_WINDOW,
        ghost_window: float = GHOST_WINDOW,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._repeat_window = repeat_window
        self._ghost_window = ghost_window
        self._clock = clock
        self._last: dict[tuple, float] = {}
        self._last_arctech: float | None = None

    def classify(self, packet: dict[str, Any]) -> str:
        if packet.get("class") != "command":
            return NEW
        now = self._clock()
        protocol = packet.get("protocol")
        if protocol == "arctech":
            self._last_arctech = now
        elif (
            protocol == "everflourish"
            and self._last_arctech is not None
            and now - self._last_arctech < self._ghost_window
        ):
            return DROP
        # Forget commands too old to be repeated; the dict stays tiny
        self._last = {
            k: t for k, t in self._last.items() if now - t < self._repeat_window
        }
        key = tuple(packet.get(f) for f in _KEY_FIELDS)
        # A held button keeps repeating: every repeat extends the window
        repeat = key in self._last
        self._last[key] = now
        return REPEAT if repeat else NEW
