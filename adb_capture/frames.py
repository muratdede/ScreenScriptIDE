"""Veri sınıfları: yakalanan kare ve kırpma bölgesi."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Region:
    """Görüntüde dikdörtgen bir bölge (piksel)."""

    x: int
    y: int
    width: int
    height: int

    @classmethod
    def parse(cls, text: str) -> "Region":
        """'x,y,w,h' metninden bölge oluşturur."""
        x, y, w, h = (int(v) for v in text.split(","))
        return cls(x, y, w, h)

    def apply(self, image: np.ndarray) -> np.ndarray:
        return image[self.y:self.y + self.height, self.x:self.x + self.width]


@dataclass(frozen=True)
class Frame:
    """Yakalanmış tek bir kare. image OpenCV formatındadır (BGR, uint8)."""

    image: np.ndarray
    index: int
    timestamp: float       # time.time()
    capture_ms: float      # yakalamanın kaç ms sürdüğü
    source: str

    @property
    def width(self) -> int:
        return self.image.shape[1]

    @property
    def height(self) -> int:
        return self.image.shape[0]
