"""Kare tüketicileri (Observer deseni). Capturer her yeni kareyi abonelerine iletir."""

from __future__ import annotations

import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from typing import Callable, Optional

import cv2

from .frames import Frame

log = logging.getLogger(__name__)


class FrameConsumer(ABC):
    """Kare alan her sınıfın arayüzü. on_frame capturer thread'inde çağrılır; hızlı olmalı."""

    @abstractmethod
    def on_frame(self, frame: Frame) -> None: ...

    def on_start(self) -> None:
        """Yakalama başlarken çağrılır."""

    def on_stop(self) -> None:
        """Yakalama dururken çağrılır."""


class LatestFrameBuffer(FrameConsumer):
    """Son kareyi thread-safe tutar. Bot/analiz kodu kareleri buradan okur."""

    def __init__(self):
        self._frame: Optional[Frame] = None
        self._cond = threading.Condition()

    def on_frame(self, frame: Frame) -> None:
        with self._cond:
            self._frame = frame
            self._cond.notify_all()

    @property
    def latest(self) -> Optional[Frame]:
        with self._cond:
            return self._frame

    def wait_next(self, after_index: int = -1, timeout: Optional[float] = None) -> Optional[Frame]:
        """index'i after_index'ten büyük bir kare gelene kadar bekler. Zaman aşımında None döner."""
        with self._cond:
            ok = self._cond.wait_for(
                lambda: self._frame is not None and self._frame.index > after_index, timeout)
            return self._frame if ok else None


class DiskSaver(FrameConsumer):
    """Kareleri diske kaydeder. every_seconds ile kayıt sıklığı yakalama sıklığından ayrı ayarlanır."""

    def __init__(self, out_dir: str = "shots", every_seconds: float = 0.0,
                 prefix: str = "frame", ext: str = ".png"):
        self._out_dir = out_dir
        self._every = every_seconds
        self._prefix = prefix
        self._ext = ext
        self._last = 0.0

    def on_start(self) -> None:
        os.makedirs(self._out_dir, exist_ok=True)

    def on_frame(self, frame: Frame) -> None:
        if frame.timestamp - self._last < self._every:
            return
        self._last = frame.timestamp
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(frame.timestamp))
        ms = int((frame.timestamp % 1) * 1000)
        path = os.path.join(self._out_dir, f"{self._prefix}_{stamp}_{ms:03d}_{frame.index:06d}{self._ext}")
        if cv2.imwrite(path, frame.image):
            log.info("Kaydedildi: %s", path)
        else:
            log.error("Kaydedilemedi: %s", path)


class CallbackConsumer(FrameConsumer):
    """Bir fonksiyonu tüketiciye çevirir: CallbackConsumer(lambda f: print(f.index))."""

    def __init__(self, fn: Callable[[Frame], None]):
        self._fn = fn

    def on_frame(self, frame: Frame) -> None:
        self._fn(frame)
