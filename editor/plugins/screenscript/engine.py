"""Engine: sağlayıcı, eşleştirici, girdi ve ayarları bir araya getirir.
Region/Match/Screen tüm işi Engine'e devreder (bağımlılıklar tek yerde)."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, TypeVar

import cv2
import numpy as np

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
    matches: List[MatchResult]   # ekran koordinatlarında, eşik üstü
    best_score: float            # eşik altında olsa bile en iyi skor (ayar yapmak için)


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

    # ---- ekran ----------------------------------------------------------- #
    def grab(self, fresh: bool = True) -> np.ndarray:
        self._last_image = self.provider.snapshot(fresh)
        return self._last_image

    @property
    def last_image(self) -> Optional[np.ndarray]:
        return self._last_image

    def screen_rect(self) -> Rect:
        img = self._last_image if self._last_image is not None else self.grab()
        return Rect(0, 0, img.shape[1], img.shape[0])

    # ---- arama ----------------------------------------------------------- #
    def similarity_for(self, pattern: Pattern) -> float:
        return pattern.similarity if pattern.similarity is not None else self.settings.min_similarity

    def search(self, area: Rect, pattern: Pattern, find_all: bool = False) -> SearchResult:
        """Tek tarama: yeni kare al, area içinde pattern'i ara."""
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
        """Tek okuma: yeni kare al, area içindeki yazıları ekran koordinatlarında döndür
        (yukarıdan aşağıya, aynı satırdakiler soldan sağa).

        scale: okumadan önce büyütme. Tek haneli sayaç gibi çok küçük yazılar 1x'te hiç
        algılanmıyor, 3x'te doğru okunuyor."""
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
        """attempt() None olmayan bir değer döndürene ya da süre dolana kadar scan_rate hızında dener.
        timeout=0 ise tek deneme yapar."""
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

    # ---- hata ayıklama --------------------------------------------------- #
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
        log.info("Debug görüntüsü: %s", path)
        return path

    # ---- girdi ----------------------------------------------------------- #
    def tap(self, loc: Location) -> None:
        self.input.tap(loc)
        if self.settings.click_delay:
            time.sleep(self.settings.click_delay)


def reading_rows(results: List[TextResult]) -> List[List[TextResult]]:
    """Satırlara ayırır: dikey merkezleri yarım yazı yüksekliğinden yakın olanlar aynı satırdır.
    Satırlar yukarıdan aşağıya, satır içi parçalar soldan sağa sıralıdır."""
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
