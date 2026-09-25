"""Canlı önizleme. OpenCV pencereleri ana thread'de çalışmalı; bu yüzden tüketici değil, ayrı döngü."""

from __future__ import annotations

import os
import time
from typing import Callable, Optional

import cv2

from .consumers import LatestFrameBuffer


class PreviewWindow:
    def __init__(self, buffer: LatestFrameBuffer, title: str = "Onizleme (q: cikis, s: kaydet)",
                 stats_provider: Optional[Callable[[], object]] = None, snapshot_dir: str = "shots",
                 max_width: int = 1280):
        self._buffer = buffer
        self._title = title
        self._stats = stats_provider
        self._snapshot_dir = snapshot_dir
        self._max_width = max_width

    def run(self, should_continue: Callable[[], bool] = lambda: True) -> None:
        """q'ya basılana ya da should_continue() False dönene kadar ana thread'i bloklar."""
        last_index = -1
        try:
            while should_continue():
                frame = self._buffer.latest
                if frame is not None and frame.index != last_index:
                    last_index = frame.index
                    cv2.imshow(self._title, self._render(frame))
                key = cv2.waitKey(30) & 0xFF
                if key == ord("q"):
                    break
                if key == ord("s") and frame is not None:
                    os.makedirs(self._snapshot_dir, exist_ok=True)
                    path = os.path.join(self._snapshot_dir, f"manual_{time.strftime('%Y%m%d_%H%M%S')}.png")
                    cv2.imwrite(path, frame.image)
                    print(f"[+] {path}")
                if last_index >= 0 and cv2.getWindowProperty(self._title, cv2.WND_PROP_VISIBLE) < 1:
                    break  # pencere X ile kapatıldı
        finally:
            cv2.destroyWindow(self._title)

    def _render(self, frame):
        img = frame.image
        if img.shape[1] > self._max_width:
            scale = self._max_width / img.shape[1]
            img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        else:
            img = img.copy()
        text = f"#{frame.index}  {frame.width}x{frame.height}  {frame.capture_ms:.0f} ms"
        if self._stats:
            s = self._stats()
            text += f"  {s.fps:.1f} FPS  hata:{s.errors}"
        cv2.putText(img, text, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4)
        cv2.putText(img, text, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 1)
        return img
