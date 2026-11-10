"""Clean-up rules for decoded 433 MHz commands. No Home Assistant imports.

Seen on real hardware (stock firmware 17, Nexa remote):
- A press is a burst of frames 0.207 s apart; a held button keeps sending.
- Arctech frames have no checksum. Often only the first frame of a press
  decodes right and the next comes out bit-shifted: data 565D3090 (house
  22639810) also arrives as 2B2E9848 (house 11319905, one bit) or 95974C24
  (rotated two bits), with wrong units. So a short press can give a single
  good frame, and a bad code can recur, but far less often than the real one.
- The firmware's Everflourish decoder also fires on every Nexa frame: the
  frame's low 24 bits as a phantom "everflourish turnoff unit 1", ~50 ms
  later. It also follows Nexa frames that fail to decode.

So the hub passes a command on at its first frame and marks the rest of
the burst as repeats; bad codes match no configured device anyway. Learning
a code, where a bad one does harm, waits until it was heard a few times.
An Everflourish frame shortly after any arctech frame is dropped.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

NEW = "new"  # deliver
REPEAT = "repeat"  # more of a burst already delivered
DROP = "drop"  # phantom, or not heard often enough yet

# Frames of one burst arrive within this many seconds of each other
REPEAT_WINDOW = 1.0
# Learning: frames of a code needed, each within this window of the last
LEARN_FRAMES = 3
LEARN_WINDOW = 10.0
# Everflourish frames this soon after an arctech frame are the same signal
GHOST_WINDOW = 1.0

_KEY_FIELDS = ("protocol", "model", "house", "unit", "group", "method")


class CommandFilter:
    """Classifies decoded packets. Non-command packets are always NEW.

    A command is NEW at its ``frames_needed``-th frame, frames of a burst
    being at most ``repeat_window`` apart.
    """

    def __init__(
        self,
        repeat_window: float = REPEAT_WINDOW,
        ghost_window: float = GHOST_WINDOW,
        clock: Callable[[], float] = time.monotonic,
        frames_needed: int = 1,
    ) -> None:
        self._frames_needed = frames_needed
        self._repeat_window = repeat_window
        self._ghost_window = ghost_window
        self._clock = clock
        # command -> (last heard, frames in this burst)
        self._bursts: dict[tuple, tuple[float, int]] = {}
        self._last_arctech: float | None = None

    def saw_frame(self, protocol: Any) -> None:
        """Note a raw frame before decoding; arctech frames that fail to
        decode still get a phantom Everflourish twin."""
        if protocol == "arctech":
            self._last_arctech = self._clock()

    def classify(self, packet: dict[str, Any]) -> str:
        if packet.get("class") != "command":
            return NEW
        now = self._clock()
        if (
            packet.get("protocol") == "everflourish"
            and self._last_arctech is not None
            and now - self._last_arctech < self._ghost_window
        ):
            return DROP
        # Forget bursts that ended; the dict stays tiny
        self._bursts = {
            k: v for k, v in self._bursts.items() if now - v[0] < self._repeat_window
        }
        key = tuple(packet.get(f) for f in _KEY_FIELDS)
        # Every frame extends its burst, so a held button stays one press
        count = self._bursts.get(key, (now, 0))[1] + 1
        self._bursts[key] = (now, count)
        if count < self._frames_needed:
            return DROP
        return NEW if count == self._frames_needed else REPEAT
