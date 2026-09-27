"""Frame consumers (Observer pattern). The capturer passes every new frame to its subscribers."""

from __future__ import annotations

import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from typing import Callable, Optional

import cv2

from ...i18n import tr
from .frames import Frame

log = logging.getLogger(__name__)


class FrameConsumer(ABC):
    """Interface of every class receiving frames. on_frame is called on the capturer thread; must be fast."""

    @abstractmethod
    def on_frame(self, frame: Frame) -> None: ...

    def on_start(self) -> None:
        """Called when capturing starts."""

    def on_stop(self) -> None:
        """Called when capturing stops."""


class LatestFrameBuffer(FrameConsumer):
    """Holds the latest frame thread-safely. Bot/analysis code reads frames from here."""

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
        """Waits until a frame with index greater than after_index arrives. Returns None on timeout."""
        with self._cond:
            ok = self._cond.wait_for(
                lambda: self._frame is not None and self._frame.index > after_index, timeout)
            return self._frame if ok else None


class DiskSaver(FrameConsumer):
    """Saves frames to disk. every_seconds sets the save rate independently of the capture rate."""

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
            log.info(tr("Saved: %s"), path)
        else:
            log.error(tr("Could not be saved: %s"), path)


class CallbackConsumer(FrameConsumer):
    """Turns a function into a consumer: CallbackConsumer(lambda f: print(f.index))."""

    def __init__(self, fn: Callable[[Frame], None]):
        self._fn = fn

    def on_frame(self, frame: Frame) -> None:
        self._fn(frame)
