"""Screenshot providers: the bridge between the script API and adb_capture."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod

import numpy as np

from ...i18n import tr
from ..adb_capture import CaptureError, FrameSource, LatestFrameBuffer, PeriodicCapturer

log = logging.getLogger(__name__)


class ImageProvider(ABC):
    """The only place where the script API sees the screen."""

    @abstractmethod
    def snapshot(self, fresh: bool = True) -> np.ndarray:
        """Current screenshot (BGR). If fresh=True a frame newer than the previous one is guaranteed."""

    def open(self) -> None: ...

    def close(self) -> None: ...


class OnDemandProvider(ImageProvider):
    """Grabs a frame directly from the source on every request. Simple and always up to date.

    A failed grab (e.g. an adb timeout while the emulator is busy) is retried after
    source.recover(); the error reaches the script only when every attempt fails. A single
    hiccup must not end a script that has been running for hours."""

    RETRIES = 2
    RETRY_DELAY = 1.0

    def __init__(self, source: FrameSource):
        self._source = source

    def open(self):
        self._source.open()

    def close(self):
        self._source.close()

    def snapshot(self, fresh=True):
        for attempt in range(self.RETRIES + 1):
            try:
                return self._source.grab()
            except CaptureError as e:
                if attempt == self.RETRIES:
                    raise
                log.warning(tr("Capture error (%d/%d): %s"), attempt + 1, self.RETRIES + 1, e)
            time.sleep(self.RETRY_DELAY)
            try:
                self._source.recover()
            except Exception as e:  # noqa: BLE001 — the next grab attempt reports the real error
                log.warning(tr("Recovery failed: %s"), e)


class BackgroundProvider(ImageProvider):
    """PeriodicCapturer captures continuously in the background; snapshot returns the latest frame.
    The screen stays fresh while the script does other work."""

    def __init__(self, source: FrameSource, interval: float = 0.3, timeout: float = 10.0):
        self._buffer = LatestFrameBuffer()
        self._capturer = PeriodicCapturer(source, interval=interval, consumers=[self._buffer])
        self._timeout = timeout
        self._last_index = -1

    @property
    def capturer(self) -> PeriodicCapturer:
        return self._capturer

    def open(self):
        self._capturer.start()

    def close(self):
        self._capturer.stop()

    def snapshot(self, fresh=True):
        frame = self._buffer.wait_next(self._last_index if fresh else -1, timeout=self._timeout)
        if frame is None:
            raise TimeoutError(tr("No frame arrived within {timeout} s. Last error: {error}",
                                  timeout=self._timeout, error=self._capturer.stats.last_error))
        self._last_index = frame.index
        return frame.image
