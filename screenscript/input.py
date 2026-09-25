"""Girdi sürücüleri: tıklama, kaydırma, yazı. Yeni sürücü = InputDriver'dan türetilmiş sınıf."""

from __future__ import annotations

import random
import time
from abc import ABC, abstractmethod
from enum import IntEnum

from adb_capture import AdbDevice

from .geometry import Location


class Key(IntEnum):
    """Android keycode'ları (sık kullanılanlar)."""
    HOME = 3
    BACK = 4
    ENTER = 66
    DEL = 67
    TAB = 61
    ESCAPE = 111
    APP_SWITCH = 187


class InputDriver(ABC):
    """Koordinatlar cihaz (ekran görüntüsü) pikselidir."""

    @abstractmethod
    def tap(self, loc: Location) -> None: ...

    @abstractmethod
    def swipe(self, start: Location, end: Location, duration_ms: int = 300) -> None: ...

    def long_press(self, loc: Location, duration_ms: int = 800) -> None:
        # Varsayılan: yerinde kaydırma = uzun basma
        self.swipe(loc, loc, duration_ms)

    @abstractmethod
    def type_text(self, text: str) -> None: ...

    @abstractmethod
    def key(self, keycode: int) -> None: ...


class AdbInputDriver(InputDriver):
    """`adb shell input ...` ile dokunma gönderir."""

    def __init__(self, device: AdbDevice):
        self._device = device

    def tap(self, loc):
        self._device.shell(f"input tap {loc.x} {loc.y}")

    def swipe(self, start, end, duration_ms=300):
        self._device.shell(f"input swipe {start.x} {start.y} {end.x} {end.y} {int(duration_ms)}")

    def type_text(self, text):
        # input text boşluğu %s olarak ister; kabuk karakterlerini kaçır
        escaped = "".join("\\" + c if c in "\"'`\\$&|;<>()*?#~" else c for c in text).replace(" ", "%s")
        self._device.shell(f"input text {escaped}")

    def key(self, keycode):
        self._device.shell(f"input keyevent {int(keycode)}")


class HumanizedInputDriver(InputDriver):
    """Başka bir sürücüyü sarar (Decorator): koordinata küçük sapma ve rastgele gecikme ekler."""

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
