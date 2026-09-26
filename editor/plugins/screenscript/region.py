"""SikuliX tarzı Region / Match / Screen."""

from __future__ import annotations

import logging
import re
from typing import List, Optional, Union

import numpy as np

from .engine import Engine, reading_rows
from .errors import FindFailed, TextNotFound
from .geometry import Location, Rect
from .ocr import TextResult
from .pattern import Pattern

log = logging.getLogger(__name__)

PatternLike = Union[Pattern, str, np.ndarray]
Target = Union[PatternLike, "Region", Location, None]
TextQuery = Union[str, "re.Pattern[str]"]


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

    # ---- metin (OCR) ----------------------------------------------------- #
    def text(self, scale: float = 1.0) -> str:
        """Bölgedeki yazı. Aynı satırdaki parçalar boşlukla, satırlar alt satırla birleşir.
        Çok küçük yazılarda (tek rakam) scale=3 verin.

            speed = Region(900, 1005, 110, 40).text()     # "66m/s"
        """
        return "\n".join(" ".join(r.text for r in row)
                         for row in reading_rows(self._engine.read_text(self._rect, scale)))

    def read_lines(self, scale: float = 1.0) -> List["TextMatch"]:
        """Bölgedeki her yazı parçası konumuyla (okuma sırasında). TextMatch tıklanabilir."""
        return [TextMatch(r, self._engine) for r in self._engine.read_text(self._rect, scale)]

    def exists_text(self, query: TextQuery, timeout: Optional[float] = None) -> Optional["TextMatch"]:
        """Yazıyı arar; bulursa TextMatch, bulamazsa None. query düz metinse büyük/küçük harf
        ve boşluk farkı gözetmeden içerir mi diye bakar; re.compile(...) verilirse regex arar."""
        matches = _text_matcher(query)
        self._last_seen: List[str] = []

        def attempt():
            results = self._engine.read_text(self._rect)
            self._last_seen = [r.text for r in results]
            for r in results:
                if matches(r.text):
                    return TextMatch(r, self._engine)
            for row in reading_rows(results):   # parçalara bölünmüş satırlar (ör. "1,644 km")
                if len(row) > 1 and matches(" ".join(r.text for r in row)):
                    return TextMatch(_merge(row), self._engine)
            return None

        match = self._engine.repeat_until(self._timeout(timeout), attempt)
        log.debug("exists_text(%r) -> %s", query, match)
        return match

    def wait_text(self, query: TextQuery, timeout: Optional[float] = None) -> "TextMatch":
        """Yazı görünene kadar bekler; süre dolarsa TextNotFound (bir FindFailed)."""
        timeout = self._timeout(timeout)
        match = self.exists_text(query, timeout)
        if match is None:
            if self._engine.settings.save_on_fail:
                self._engine.save_debug(f"fail_text_{_query_label(query)}", [self._rect])
            raise TextNotFound(_query_label(query), timeout, self._last_seen)
        return match

    def find_text(self, query: TextQuery) -> "TextMatch":
        """wait_text(query, Settings.auto_wait_timeout) ile aynı."""
        return self.wait_text(query, None)

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


class TextMatch(Region):
    """Okunmuş bir yazı parçası. Kendisi de bir Region'dır: tıklanabilir, içinde arama yapılabilir."""

    def __init__(self, result: TextResult, engine: Engine):
        super().__init__(result.rect, engine)
        self._text = result.text
        self._score = result.score

    def text(self, scale: float = 1.0) -> str:
        """Okunmuş yazı (yeniden okumaz; güncel hali için Region'ı tekrar okuyun)."""
        return self._text

    @property
    def score(self) -> float:
        return self._score

    def highlight(self, target: Union[PatternLike, Region, None] = None) -> str:
        if target is not None:
            return super().highlight(target)
        return self._engine.save_debug(f"text_{self._text}", [self.rect], [self._score])

    def __str__(self) -> str:
        return self._text

    def __repr__(self) -> str:
        return f"TextMatch({self._text!r}, score={self._score:.2f}, {self.rect})"


def _normalize(text: str) -> str:
    return " ".join(text.split()).casefold()


def _text_matcher(query: TextQuery):
    if isinstance(query, re.Pattern):
        return lambda text: query.search(text) is not None
    needle = _normalize(query)
    return lambda text: needle in _normalize(text)


def _query_label(query: TextQuery) -> str:
    return f"/{query.pattern}/" if isinstance(query, re.Pattern) else repr(query)


def _merge(results: List[TextResult]) -> TextResult:
    x1, y1 = min(r.rect.x for r in results), min(r.rect.y for r in results)
    x2, y2 = max(r.rect.right for r in results), max(r.rect.bottom for r in results)
    return TextResult(" ".join(r.text for r in results), Rect(x1, y1, x2 - x1, y2 - y1),
                      min(r.score for r in results))


class Screen(Region):
    """Tüm ekran. Boyutu ilk kareden öğrenilir."""

    def __init__(self, engine: Engine):
        super().__init__(engine.screen_rect(), engine)

    def refresh_size(self) -> None:
        self._engine.grab()
        self._rect = self._engine.screen_rect()

    def __repr__(self) -> str:
        return f"Screen{self._rect}"
