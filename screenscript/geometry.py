"""Değişmez geometri tipleri."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Location:
    x: int
    y: int

    def offset(self, dx: int, dy: int) -> "Location":
        return Location(self.x + dx, self.y + dy)

    def __str__(self) -> str:
        return f"({self.x}, {self.y})"


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def center(self) -> Location:
        return Location(self.x + self.w // 2, self.y + self.h // 2)

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    def offset(self, dx: int, dy: int) -> "Rect":
        return Rect(self.x + dx, self.y + dy, self.w, self.h)

    def grow(self, px: int) -> "Rect":
        return Rect(self.x - px, self.y - px, self.w + 2 * px, self.h + 2 * px)

    def contains(self, loc: Location) -> bool:
        return self.x <= loc.x < self.right and self.y <= loc.y < self.bottom

    def intersect(self, other: "Rect") -> Optional["Rect"]:
        x1, y1 = max(self.x, other.x), max(self.y, other.y)
        x2, y2 = min(self.right, other.right), min(self.bottom, other.bottom)
        if x2 <= x1 or y2 <= y1:
            return None
        return Rect(x1, y1, x2 - x1, y2 - y1)

    def __str__(self) -> str:
        return f"[{self.x},{self.y} {self.w}x{self.h}]"
