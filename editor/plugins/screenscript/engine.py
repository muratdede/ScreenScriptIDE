"""Engine: combines the provider, matcher, input and settings.
Region/Match/Screen delegate all work to the Engine (dependencies in one place)."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, TypeVar

import cv2
import numpy as np

from ...i18n import tr
from .geometry import Location, Rect
from .input import InputDriver
from .matching import Matcher, MatchResult
from .ocr import TextReader, TextResult, create_text_reader
from .pattern import ImageLibrary, Pattern
from .providers import ImageProvider
from .settings import Settings

log = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass(frozen=True)
class SearchResult:
    matches: List[MatchResult]   # in screen coordinates, above the threshold
    best_score: float            # best score even if below the threshold (for tuning)


class Engine:
    def __init__(self, provider: ImageProvider, matcher: Matcher, input_driver: InputDriver,
                 library: ImageLibrary, settings: Optional[Settings] = None,
                 text_reader: Optional[TextReader] = None):
        self.provider = provider
        self.matcher = matcher
        self.input = input_driver
        self.library = library
        self.settings = settings or Settings()
        self.text_reader = text_reader or create_text_reader(self.settings.ocr_backend)
        self._last_image: Optional[np.ndarray] = None

    # ---- screen ---------------------------------------------------------- #
    def grab(self, fresh: bool = True) -> np.ndarray:
        self._last_image = self.provider.snapshot(fresh)
        return self._last_image

    @property
    def last_image(self) -> Optional[np.ndarray]:
        return self._last_image

    def screen_rect(self) -> Rect:
        img = self._last_image if self._last_image is not None else self.grab()
        return Rect(0, 0, img.shape[1], img.shape[0])

    # ---- search ---------------------------------------------------------- #
    def similarity_for(self, pattern: Pattern) -> float:
        return pattern.similarity if pattern.similarity is not None else self.settings.min_similarity

    def search(self, area: Rect, pattern: Pattern, find_all: bool = False) -> SearchResult:
        """Single scan: take a new frame, search the pattern inside area."""
        image = self.grab()
        area = area.intersect(Rect(0, 0, image.shape[1], image.shape[0])) or Rect(0, 0, 0, 0)
        haystack = image[area.y:area.bottom, area.x:area.right]
        needle = self.library.load(pattern)
        threshold = self.similarity_for(pattern)

        if haystack.size == 0:
            return SearchResult([], 0.0)
        best = self.matcher.best(haystack, needle)
        best_score = best.score if best else 0.0
        if find_all:
            found = self.matcher.find_all(haystack, needle, threshold)
        else:
            found = [best] if best and best.score >= threshold else []
        shifted = [MatchResult(m.rect.offset(area.x, area.y), m.score) for m in found]
        return SearchResult(shifted, best_score)

    def read_text(self, area: Rect, scale: float = 1.0) -> List[TextResult]:
        """Single read: take a new frame, return the texts inside area in screen coordinates
        (top to bottom, left to right within the same line).

        scale: upscaling before reading. Very small texts like a single-digit counter are not
        detected at all at 1x but read correctly at 3x."""
        image = self.grab()
        area = area.intersect(Rect(0, 0, image.shape[1], image.shape[0])) or Rect(0, 0, 0, 0)
        crop = image[area.y:area.bottom, area.x:area.right]
        if crop.size == 0:
            return []
        if scale != 1.0:
            crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        found = [TextResult(r.text, Rect(area.x + int(r.rect.x / scale), area.y + int(r.rect.y / scale),
                                         int(round(r.rect.w / scale)), int(round(r.rect.h / scale))), r.score)
                 for r in self.text_reader.read(crop)]
        return sort_reading_order(found)

    def repeat_until(self, timeout: float, attempt: Callable[[], Optional[T]]) -> Optional[T]:
        """Tries at scan_rate until attempt() returns a non-None value or the time is up.
        timeout=0 makes a single attempt."""
        deadline = time.monotonic() + max(0.0, timeout)
        period = 1.0 / max(self.settings.scan_rate, 0.1)
        while True:
            started = time.monotonic()
            result = attempt()
            if result is not None:
                return result
            if started >= deadline:
                return None
            time.sleep(max(0.0, min(period - (time.monotonic() - started), deadline - time.monotonic())))

    # ---- debugging ------------------------------------------------------- #
    def save_debug(self, label: str, rects: List[Rect] = (), scores: List[float] = ()) -> str:
        image = self._last_image if self._last_image is not None else self.grab()
        canvas = image.copy()
        for i, r in enumerate(rects):
            cv2.rectangle(canvas, (r.x, r.y), (r.right, r.bottom), (0, 0, 255), 2)
            cv2.drawMarker(canvas, (r.center.x, r.center.y), (0, 255, 255), cv2.MARKER_CROSS, 12, 2)
            if i < len(scores):
                cv2.putText(canvas, f"{scores[i]:.3f}", (r.x, max(12, r.y - 4)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
        os.makedirs(self.settings.debug_dir, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)[:40]
        ms = int((time.time() % 1) * 1000)
        path = os.path.join(self.settings.debug_dir, f"{time.strftime('%Y%m%d_%H%M%S')}_{ms:03d}_{safe}.png")
        cv2.imwrite(path, canvas)
        log.debug(tr("Debug image: %s"), path)
        return path

    # ---- input ----------------------------------------------------------- #
    def tap(self, loc: Location) -> None:
        self.input.tap(loc)
        if self.settings.click_delay:
            time.sleep(self.settings.click_delay)


def reading_rows(results: List[TextResult]) -> List[List[TextResult]]:
    """Splits into lines: items whose vertical centers are closer than half the text height are on
    the same line. Lines are ordered top to bottom, items within a line left to right."""
    rows: List[List[TextResult]] = []
    for r in sorted(results, key=lambda r: r.rect.center.y):
        row = rows[-1] if rows else None
        if row and abs(r.rect.center.y - row[0].rect.center.y) <= max(4, min(r.rect.h, row[0].rect.h) // 2):
            row.append(r)
        else:
            rows.append([r])
    return [sorted(row, key=lambda r: r.rect.x) for row in rows]


def sort_reading_order(results: List[TextResult]) -> List[TextResult]:
    return [r for row in reading_rows(results) for r in row]
