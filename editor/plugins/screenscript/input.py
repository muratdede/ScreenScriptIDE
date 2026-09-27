"""Input drivers: tap, swipe, text. New driver = class derived from InputDriver."""

from __future__ import annotations

import random
import time
from abc import ABC, abstractmethod
from enum import IntEnum

from ..adb_capture import AdbDevice

from .geometry import Location


class Key(IntEnum):
    """Android keycodes (frequently used ones)."""
    HOME = 3
    BACK = 4
    ENTER = 66
    DEL = 67
    TAB = 61
    ESCAPE = 111
    APP_SWITCH = 187


class InputDriver(ABC):
    """Coordinates are device (screenshot) pixels."""

    @abstractmethod
    def tap(self, loc: Location) -> None: ...

    @abstractmethod
    def swipe(self, start: Location, end: Location, duration_ms: int = 300) -> None: ...

    def long_press(self, loc: Location, duration_ms: int = 800) -> None:
        # default: an in-place swipe = long press
        self.swipe(loc, loc, duration_ms)

    @abstractmethod
    def type_text(self, text: str) -> None: ...

    @abstractmethod
    def key(self, keycode: int) -> None: ...


class AdbInputDriver(InputDriver):
    """Sends touches with `adb shell input ...`.

    If press_ms > 0, a tap is sent as a press where the finger stays down that long and then lifts
    (`input swipe x y x y ms`). `input tap` sends down and up at the same time; some games
    (especially under load) can miss such short touches."""

    def __init__(self, device: AdbDevice, press_ms: int = 0):
        self._device = device
        self._press_ms = int(press_ms)

    def tap(self, loc):
        if self._press_ms > 0:
            self._device.shell(f"input swipe {loc.x} {loc.y} {loc.x} {loc.y} {self._press_ms}")
        else:
            self._device.shell(f"input tap {loc.x} {loc.y}")

    def swipe(self, start, end, duration_ms=300):
        self._device.shell(f"input swipe {start.x} {start.y} {end.x} {end.y} {int(duration_ms)}")

    def type_text(self, text):
        # input text wants spaces as %s; escape shell characters
        escaped = "".join("\\" + c if c in "\"'`\\$&|;<>()*?#~" else c for c in text).replace(" ", "%s")
        self._device.shell(f"input text {escaped}")

    def key(self, keycode):
        self._device.shell(f"input keyevent {int(keycode)}")


class HumanizedInputDriver(InputDriver):
    """Wraps another driver (Decorator): adds a small coordinate jitter and a random delay."""

    def __init__(self, inner: InputDriver, jitter_px: int = 3, delay_range=(0.05, 0.25)):
        self._inner = inner
        self._jitter = jitter_px
        self._delay = delay_range

    def tap(self, loc):
        self._pause()
        self._inner.tap(self._jit(loc))

    def swipe(self, start, end, duration_ms=300):
        self._pause()
        self._inner.swipe(self._jit(start), self._jit(end), int(duration_ms * random.uniform(0.85, 1.15)))

    def long_press(self, loc, duration_ms=800):
        self._pause()
        self._inner.long_press(self._jit(loc), int(duration_ms * random.uniform(0.9, 1.2)))

    def type_text(self, text):
        self._pause()
        self._inner.type_text(text)

    def key(self, keycode):
        self._pause()
        self._inner.key(keycode)

    def _jit(self, loc: Location) -> Location:
        j = self._jitter
        return loc.offset(random.randint(-j, j), random.randint(-j, j)) if j else loc

    def _pause(self):
        time.sleep(random.uniform(*self._delay))
