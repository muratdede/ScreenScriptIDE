"""Görsel eşleştirme algoritmaları. Yeni algoritma = Matcher'dan türetilmiş yeni sınıf."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterable, List, Optional

import cv2
import numpy as np

from .geometry import Rect


@dataclass(frozen=True)
class MatchResult:
    rect: Rect      # haystack (aranan görüntü) koordinatlarında
    score: float    # 0..1, yüksek = daha benzer


class Matcher(ABC):
    """Bir görüntü (haystack) içinde başka bir görüntüyü (needle) arar."""

    @abstractmethod
    def best(self, haystack: np.ndarray, needle: np.ndarray) -> Optional[MatchResult]:
        """Eşik gözetmeksizin en iyi eşleşme. needle haystack'ten büyükse None."""

    @abstractmethod
    def find_all(self, haystack: np.ndarray, needle: np.ndarray,
                 min_score: float, max_results: int = 100) -> List[MatchResult]:
        """min_score üstündeki, birbiriyle çakışmayan eşleşmeler (skora göre azalan)."""


class TemplateMatcher(Matcher):
    """OpenCV matchTemplate (TM_CCOEFF_NORMED). Hızlı; ölçek ve dönmeye duyarlı."""

    SUPPORTED = (cv2.TM_CCOEFF_NORMED, cv2.TM_CCORR_NORMED)

    def __init__(self, method: int = cv2.TM_CCOEFF_NORMED, grayscale: bool = False):
        if method not in self.SUPPORTED:
            raise ValueError("Sadece TM_CCOEFF_NORMED ve TM_CCORR_NORMED desteklenir")
        self._method = method
        self._gray = grayscale

    def best(self, haystack, needle):
        scores = self._score_map(haystack, needle)
        if scores is None:
            return None
        _, max_val, _, (x, y) = cv2.minMaxLoc(scores)
        h, w = needle.shape[:2]
        return MatchResult(Rect(x, y, w, h), float(max_val))

    def find_all(self, haystack, needle, min_score, max_results=100):
        scores = self._score_map(haystack, needle)
        if scores is None:
            return []
        h, w = needle.shape[:2]
        results: List[MatchResult] = []
        while len(results) < max_results:
            _, max_val, _, (x, y) = cv2.minMaxLoc(scores)
            if max_val < min_score:
                break
            results.append(MatchResult(Rect(x, y, w, h), float(max_val)))
            # bulunan yerin çevresini bastır ki aynı nesne tekrar bulunmasın
            x0, y0 = max(0, x - w // 2), max(0, y - h // 2)
            scores[y0:y + h // 2 + 1, x0:x + w // 2 + 1] = -1.0
        return results

    def _score_map(self, haystack, needle) -> Optional[np.ndarray]:
        if needle.shape[0] > haystack.shape[0] or needle.shape[1] > haystack.shape[1]:
            return None
        if self._gray:
            haystack = cv2.cvtColor(haystack, cv2.COLOR_BGR2GRAY)
            needle = cv2.cvtColor(needle, cv2.COLOR_BGR2GRAY)
        scores = cv2.matchTemplate(haystack, needle, self._method)
        # tek renkli şablonlarda NaN/inf oluşabilir
        return np.nan_to_num(scores, nan=0.0, posinf=0.0, neginf=0.0)


class MultiScaleMatcher(Matcher):
    """Başka bir Matcher'ı birden fazla ölçekte çalıştırır (Decorator).
    Emülatör çözünürlüğü, şablonun alındığı çözünürlükten farklıysa işe yarar."""

    def __init__(self, inner: Matcher, scales: Iterable[float] = (0.8, 0.9, 1.0, 1.1, 1.2)):
        self._inner = inner
        self._scales = tuple(scales)

    def best(self, haystack, needle):
        candidates = [self._inner.best(haystack, n) for n in self._scaled(needle)]
        candidates = [c for c in candidates if c is not None]
        return max(candidates, key=lambda c: c.score, default=None)

    def find_all(self, haystack, needle, min_score, max_results=100):
        results: List[MatchResult] = []
        for n in self._scaled(needle):
            results += self._inner.find_all(haystack, n, min_score, max_results)
        return _non_max_suppression(sorted(results, key=lambda r: -r.score))[:max_results]

    def _scaled(self, needle):
        for s in self._scales:
            if s == 1.0:
                yield needle
            else:
                w, h = max(1, int(needle.shape[1] * s)), max(1, int(needle.shape[0] * s))
                yield cv2.resize(needle, (w, h), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)


def _non_max_suppression(results: List[MatchResult], overlap: float = 0.3) -> List[MatchResult]:
    kept: List[MatchResult] = []
    for r in results:
        if all(_iou(r.rect, k.rect) < overlap for k in kept):
            kept.append(r)
    return kept


def _iou(a: Rect, b: Rect) -> float:
    inter = a.intersect(b)
    if inter is None:
        return 0.0
    ia = inter.w * inter.h
    return ia / (a.w * a.h + b.w * b.h - ia)
