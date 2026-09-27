"""SikuliX style Region / Match / Screen."""

from __future__ import annotations

import logging
import re
from typing import List, Optional, Union

import numpy as np

from ...i18n import tr
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
    """A rectangular area on the screen. Search and click operations are limited to this area."""

    def __init__(self, rect: Rect, engine: Engine):
        self._rect = rect
        self._engine = engine

    # ---- geometry -------------------------------------------------------- #
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
        """Point to click. The center for a Region; Match changes it with target_offset."""
        return self.center

    def region(self, x: int, y: int, w: int, h: int) -> "Region":
        """Sub-region relative to this region."""
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

    # ---- search ---------------------------------------------------------- #
    def exists(self, target: PatternLike, timeout: Optional[float] = None) -> Optional["Match"]:
        """Returns a Match if found, None otherwise (does not raise)."""
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
        log.debug(tr("exists(%s) -> %s (best %.3f)"), pattern.name, match, self._last_best)
        return match

    def wait(self, target: PatternLike, timeout: Optional[float] = None) -> "Match":
        """Waits until it appears; FindFailed when the time is up."""
        pattern = Pattern.of(target)
        timeout = self._timeout(timeout)
        match = self.exists(pattern, timeout)
        if match is None:
            if self._engine.settings.save_on_fail:
                self._engine.save_debug(f"fail_{pattern.name}", [self._rect])
            raise FindFailed(pattern.name, timeout, self._last_best)
        return match

    def find(self, target: PatternLike) -> "Match":
        """Same as wait(target, Settings.auto_wait_timeout)."""
        return self.wait(target, None)

    def find_all(self, target: PatternLike) -> List["Match"]:
        pattern = Pattern.of(target)
        res = self._engine.search(self._rect, pattern, find_all=True)
        return [Match(m.rect, m.score, pattern, self._engine) for m in res.matches]

    def wait_vanish(self, target: PatternLike, timeout: Optional[float] = None) -> bool:
        """True if it disappears, False if it does not disappear in time."""
        pattern = Pattern.of(target)
        gone = self._engine.repeat_until(
            self._timeout(timeout),
            lambda: True if not self._engine.search(self._rect, pattern).matches else None)
        return bool(gone)

    def best_score(self, target: PatternLike) -> float:
        """Single scan; best similarity score regardless of the threshold. Handy for tuning thresholds."""
        return self._engine.search(self._rect, Pattern.of(target)).best_score

    # ---- text (OCR) ------------------------------------------------------ #
    def text(self, scale: float = 1.0) -> str:
        """Text in the region. Parts on the same line are joined with spaces, lines with newlines.
        Pass scale=3 for very small texts (a single digit).

            speed = Region(900, 1005, 110, 40).text()     # "66m/s"
        """
        return "\n".join(" ".join(r.text for r in row)
                         for row in reading_rows(self._engine.read_text(self._rect, scale)))

    def read_lines(self, scale: float = 1.0) -> List["TextMatch"]:
        """Every text part in the region with its position (in reading order). TextMatch is clickable."""
        return [TextMatch(r, self._engine) for r in self._engine.read_text(self._rect, scale)]

    def exists_text(self, query: TextQuery, timeout: Optional[float] = None) -> Optional["TextMatch"]:
        """Searches the text; returns a TextMatch if found, None otherwise. A plain text query checks
        containment ignoring case and whitespace; with re.compile(...) a regex search is done."""
        matches = _text_matcher(query)
        self._last_seen: List[str] = []

        def attempt():
            results = self._engine.read_text(self._rect)
            self._last_seen = [r.text for r in results]
            for r in results:
                if matches(r.text):
                    return TextMatch(r, self._engine)
            for row in reading_rows(results):   # lines split into parts (e.g. "1,644 km")
                if len(row) > 1 and matches(" ".join(r.text for r in row)):
                    return TextMatch(_merge(row), self._engine)
            return None

        match = self._engine.repeat_until(self._timeout(timeout), attempt)
        log.debug("exists_text(%r) -> %s", query, match)
        return match

    def wait_text(self, query: TextQuery, timeout: Optional[float] = None) -> "TextMatch":
        """Waits until the text appears; TextNotFound (a FindFailed) when the time is up."""
        timeout = self._timeout(timeout)
        match = self.exists_text(query, timeout)
        if match is None:
            if self._engine.settings.save_on_fail:
                self._engine.save_debug(f"fail_text_{_query_label(query)}", [self._rect])
            raise TextNotFound(_query_label(query), timeout, self._last_seen)
        return match

    def find_text(self, query: TextQuery) -> "TextMatch":
        """Same as wait_text(query, Settings.auto_wait_timeout)."""
        return self.wait_text(query, None)

    # ---- actions --------------------------------------------------------- #
    def click(self, target: Target = None, timeout: Optional[float] = None) -> Location:
        loc = self._resolve(target, timeout)
        log.debug("click %s", loc)
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
        """Swipes by (dx, dy) from the region center."""
        c = self.center
        self._engine.input.swipe(c, c.offset(dx, dy), duration_ms)

    def type_text(self, text: str, target: Target = None) -> None:
        if target is not None:
            self.click(target)
        self._engine.input.type_text(text)

    def key(self, keycode: int) -> None:
        self._engine.input.key(keycode)

    # ---- helpers --------------------------------------------------------- #
    def capture(self) -> np.ndarray:
        """Current image of this region."""
        img = self._engine.grab()
        return img[self.y:self._rect.bottom, self.x:self._rect.right].copy()

    def highlight(self, target: Union[PatternLike, "Region", None] = None) -> str:
        """Marks the region, the given Match/Region or all matches of a pattern and saves it to the
        debug folder."""
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
    """A found match. It is a Region itself; you can search inside it again."""

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
    """A text part that was read. It is a Region itself: clickable, searchable."""

    def __init__(self, result: TextResult, engine: Engine):
        super().__init__(result.rect, engine)
        self._text = result.text
        self._score = result.score

    def text(self, scale: float = 1.0) -> str:
        """The text that was read (does not read again; read the Region again for the current value)."""
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
    """The whole screen. Its size is learned from the first frame."""

    def __init__(self, engine: Engine):
        super().__init__(engine.screen_rect(), engine)

    def refresh_size(self) -> None:
        self._engine.grab()
        self._rect = self._engine.screen_rect()

    def __repr__(self) -> str:
        return f"Screen{self._rect}"
