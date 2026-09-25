"""SikuliX tarzı Region / Match / Screen."""

from __future__ import annotations

import logging
from typing import List, Optional, Union

import numpy as np

from .engine import Engine
from .errors import FindFailed
from .geometry import Location, Rect
from .pattern import Pattern

log = logging.getLogger(__name__)

PatternLike = Union[Pattern, str, np.ndarray]
Target = Union[PatternLike, "Region", Location, None]


class Region:
    """Ekranda dikdörtgen bir alan. Arama ve tıklama işlemleri bu alanla sınırlıdır."""

    def __init__(self, rect: Rect, engine: Engine):
        self._rect = rect
        self._engine = engine

    # ---- geometri -------------------------------------------------------- #
    @property
    def rect(self) -> Rect:
        return self._rect

    x = property(lambda self: self._rect.x)
    y = property(lambda self: self._rect.y)
    w = property(lambda self: self._rect.w)
    h = property(lambda self: self._rect.h)

    @property
    def center(self) -> Location:
        return self._rect.center

    @property
    def target(self) -> Location:
        """Tıklanacak nokta. Region için merkez; Match bunu target_offset ile değiştirir."""
        return self.center

    def region(self, x: int, y: int, w: int, h: int) -> "Region":
        """Bu bölgeye göre alt bölge."""
        return Region(Rect(self.x + x, self.y + y, w, h), self._engine)

    def grow(self, px: int) -> "Region":
        return Region(self._rect.grow(px), self._engine)

    def left(self, w: int) -> "Region":
        return Region(Rect(self.x - w, self.y, w, self.h), self._engine)

    def right(self, w: int) -> "Region":
        return Region(Rect(self._rect.right, self.y, w, self.h), self._engine)

    def above(self, h: int) -> "Region":
        return Region(Rect(self.x, self.y - h, self.w, h), self._engine)

    def below(self, h: int) -> "Region":
        return Region(Rect(self.x, self._rect.bottom, self.w, h), self._engine)

    # ---- arama ----------------------------------------------------------- #
    def exists(self, target: PatternLike, timeout: Optional[float] = None) -> Optional["Match"]:
        """Bulursa Match, bulamazsa None döndürür (hata fırlatmaz)."""
        pattern = Pattern.of(target)
        timeout = self._timeout(timeout)
        self._last_best = 0.0

        def attempt():
            res = self._engine.search(self._rect, pattern)
            self._last_best = max(self._last_best, res.best_score)
            if res.matches:
                m = res.matches[0]
                return Match(m.rect, m.score, pattern, self._engine)
            return None

        match = self._engine.repeat_until(timeout, attempt)
        log.debug("exists(%s) -> %s (en iyi %.3f)", pattern.name, match, self._last_best)
        return match

    def wait(self, target: PatternLike, timeout: Optional[float] = None) -> "Match":
        """Görünene kadar bekler; süre dolarsa FindFailed."""
        pattern = Pattern.of(target)
        timeout = self._timeout(timeout)
        match = self.exists(pattern, timeout)
        if match is None:
            if self._engine.settings.save_on_fail:
                self._engine.save_debug(f"fail_{pattern.name}", [self._rect])
            raise FindFailed(pattern.name, timeout, self._last_best)
        return match

    def find(self, target: PatternLike) -> "Match":
        """wait(target, Settings.auto_wait_timeout) ile aynı."""
        return self.wait(target, None)

    def find_all(self, target: PatternLike) -> List["Match"]:
        pattern = Pattern.of(target)
        res = self._engine.search(self._rect, pattern, find_all=True)
        return [Match(m.rect, m.score, pattern, self._engine) for m in res.matches]

    def wait_vanish(self, target: PatternLike, timeout: Optional[float] = None) -> bool:
        """Kaybolursa True, süre içinde kaybolmazsa False."""
        pattern = Pattern.of(target)
        gone = self._engine.repeat_until(
            self._timeout(timeout),
            lambda: True if not self._engine.search(self._rect, pattern).matches else None)
        return bool(gone)

    def best_score(self, target: PatternLike) -> float:
        """Tek tarama; eşik gözetmeksizin en iyi benzerlik skoru. Eşik ayarlamak için kullanışlı."""
        return self._engine.search(self._rect, Pattern.of(target)).best_score

    # ---- eylemler -------------------------------------------------------- #
    def click(self, target: Target = None, timeout: Optional[float] = None) -> Location:
        loc = self._resolve(target, timeout)
        log.info("click %s", loc)
        self._engine.tap(loc)
        return loc

    def long_press(self, target: Target = None, duration_ms: int = 800,
                   timeout: Optional[float] = None) -> Location:
        loc = self._resolve(target, timeout)
        self._engine.input.long_press(loc, duration_ms)
        return loc

    def drag_drop(self, start: Target, end: Target, duration_ms: int = 400) -> None:
        self._engine.input.swipe(self._resolve(start, None), self._resolve(end, None), duration_ms)

    def swipe(self, dx: int, dy: int, duration_ms: int = 300) -> None:
        """Bölge merkezinden (dx, dy) kadar kaydırır."""
        c = self.center
        self._engine.input.swipe(c, c.offset(dx, dy), duration_ms)

    def type_text(self, text: str, target: Target = None) -> None:
        if target is not None:
            self.click(target)
        self._engine.input.type_text(text)

    def key(self, keycode: int) -> None:
        self._engine.input.key(keycode)

    # ---- yardımcılar ----------------------------------------------------- #
    def capture(self) -> np.ndarray:
        """Bu bölgenin güncel görüntüsü."""
        img = self._engine.grab()
        return img[self.y:self._rect.bottom, self.x:self._rect.right].copy()

    def highlight(self, target: Union[PatternLike, "Region", None] = None) -> str:
        """Bölgeyi, verilen Match/Region'ı ya da bir pattern'in tüm eşleşmelerini işaretleyip
        debug klasörüne kaydeder."""
        if target is None:
            return self._engine.save_debug(f"region_{self.x}_{self.y}", [self._rect])
        if isinstance(target, Region):
            return target.highlight()
        matches = self.find_all(target)
        return self._engine.save_debug(Pattern.of(target).name, [m.rect for m in matches],
                                       [m.score for m in matches])

    def _resolve(self, target: Target, timeout: Optional[float]) -> Location:
        if target is None:
            return self.target
        if isinstance(target, Location):
            return target
        if isinstance(target, Region):
            return target.target
        return self.wait(target, timeout).target

    def _timeout(self, timeout: Optional[float]) -> float:
        return self._engine.settings.auto_wait_timeout if timeout is None else timeout

    def __repr__(self) -> str:
        return f"Region{self._rect}"


class Match(Region):
    """Bulunmuş bir eşleşme. Kendisi de bir Region'dır; içinde tekrar arama yapılabilir."""

    def __init__(self, rect: Rect, score: float, pattern: Pattern, engine: Engine):
        super().__init__(rect, engine)
        self._score = score
        self._pattern = pattern

    @property
    def score(self) -> float:
        return self._score

    @property
    def pattern(self) -> Pattern:
        return self._pattern

    @property
    def target(self) -> Location:
        dx, dy = self._pattern.offset
        return self.center.offset(dx, dy)

    def highlight(self, target: Union[PatternLike, Region, None] = None) -> str:
        if target is not None:
            return super().highlight(target)
        return self._engine.save_debug(self._pattern.name, [self.rect], [self._score])

    def __repr__(self) -> str:
        return f"Match({self._pattern.name!r}, score={self._score:.3f}, {self.rect}, target={self.target})"


class Screen(Region):
    """Tüm ekran. Boyutu ilk kareden öğrenilir."""

    def __init__(self, engine: Engine):
        super().__init__(engine.screen_rect(), engine)

    def refresh_size(self) -> None:
        self._engine.grab()
        self._rect = self._engine.screen_rect()

    def __repr__(self) -> str:
        return f"Screen{self._rect}"
