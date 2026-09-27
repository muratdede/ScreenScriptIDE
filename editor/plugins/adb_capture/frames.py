"""Data classes: a captured frame and a crop region."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Region:
    """A rectangular region in an image (pixels)."""

    x: int
    y: int
    width: int
    height: int

    @classmethod
    def parse(cls, text: str) -> "Region":
        """Creates a region from 'x,y,w,h' text."""
        x, y, w, h = (int(v) for v in text.split(","))
        return cls(x, y, w, h)

    def apply(self, image: np.ndarray) -> np.ndarray:
        return image[self.y:self.y + self.height, self.x:self.x + self.width]


@dataclass(frozen=True)
class Frame:
    """A single captured frame. image is in OpenCV format (BGR, uint8)."""

    image: np.ndarray
    index: int
    timestamp: float       # time.time()
    capture_ms: float      # how many ms the capture took
    source: str

    @property
    def width(self) -> int:
        return self.image.shape[1]

    @property
    def height(self) -> int:
        return self.image.shape[0]
