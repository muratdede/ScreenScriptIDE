"""Periodic capture engine: reads a FrameSource in the background at a fixed interval."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, replace
from typing import Iterable, List, Optional

from ...i18n import tr
from .consumers import FrameConsumer
from .frames import Frame, Region
from .sources import CaptureError, FrameSource

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CaptureStats:
    frames: int = 0
    errors: int = 0
    recoveries: int = 0
    fps: float = 0.0
    avg_capture_ms: float = 0.0
    last_error: Optional[str] = None


class PeriodicCapturer:
    """Captures the source every `interval` seconds and distributes the frame to subscribers.

    - Runs at a fixed rhythm; if a capture takes longer than the interval, late ticks are skipped,
      they do not pile up.
    - After `max_consecutive_errors` errors in a row, source.recover() is called.
    - An error in one consumer does not stop the others or the loop.
    """

    def __init__(self, source: FrameSource, interval: float = 1.0,
                 consumers: Iterable[FrameConsumer] = (), crop: Optional[Region] = None,
                 max_consecutive_errors: int = 3, retry_delay: float = 2.0):
        if interval < 0:
            raise ValueError(tr("interval cannot be negative"))
        self._source = source
        self._interval = interval
        self._crop = crop
        self._max_errors = max_consecutive_errors
        self._retry_delay = retry_delay

        self._consumers: List[FrameConsumer] = list(consumers)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._stats = CaptureStats()

    # ---- subscription ---------------------------------------------------- #
    def subscribe(self, consumer: FrameConsumer) -> None:
        with self._lock:
            self._consumers.append(consumer)

    def unsubscribe(self, consumer: FrameConsumer) -> None:
        with self._lock:
            self._consumers.remove(consumer)

    # ---- lifecycle ------------------------------------------------------- #
    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def stats(self) -> CaptureStats:
        return self._stats

    def start(self) -> None:
        if self.is_running:
            return
        self._source.open()
        self._stop_event.clear()
        for c in self._snapshot():
            c.on_start()
        self._thread = threading.Thread(target=self._run, name="PeriodicCapturer", daemon=True)
        self._thread.start()
        log.info(tr("Capture started: %s, every %.2f s"), self._source.name, self._interval)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout)
            self._thread = None
        for c in self._snapshot():
            self._safe(c.on_stop)
        self._source.close()
        log.info(tr("Capture stopped. %s"), self._stats)

    def __enter__(self) -> "PeriodicCapturer":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # ---- loop ------------------------------------------------------------ #
    def _run(self) -> None:
        index = 0
        consecutive_errors = 0
        next_tick = time.monotonic()
        fps_count, fps_t0 = 0, time.monotonic()

        while not self._stop_event.is_set():
            t0 = time.monotonic()
            try:
                image = self._source.grab()
            except CaptureError as e:
                consecutive_errors += 1
                self._stats = replace(self._stats, errors=self._stats.errors + 1, last_error=str(e))
                log.warning(tr("Capture error (%d/%d): %s"), consecutive_errors, self._max_errors, e)
                if consecutive_errors >= self._max_errors:
                    consecutive_errors = self._try_recover()
                if self._stop_event.wait(self._retry_delay):
                    break
                next_tick = time.monotonic()
                continue

            consecutive_errors = 0
            capture_ms = (time.monotonic() - t0) * 1000
            if self._crop:
                image = self._crop.apply(image)
            frame = Frame(image=image, index=index, timestamp=time.time(),
                          capture_ms=capture_ms, source=self._source.name)
            index += 1
            self._dispatch(frame)

            # statistics
            fps_count += 1
            now = time.monotonic()
            n = self._stats.frames + 1
            avg = self._stats.avg_capture_ms + (capture_ms - self._stats.avg_capture_ms) / n
            fps = self._stats.fps
            if now - fps_t0 >= 1.0:
                fps, fps_count, fps_t0 = fps_count / (now - fps_t0), 0, now
            self._stats = replace(self._stats, frames=n, avg_capture_ms=avg, fps=fps)

            # fixed rhythm: skip missed ticks if we are late
            next_tick += self._interval
            if next_tick < now:
                next_tick = now
            self._stop_event.wait(max(0.0, next_tick - time.monotonic()))

    def _try_recover(self) -> int:
        try:
            self._source.recover()
            self._stats = replace(self._stats, recoveries=self._stats.recoveries + 1)
            log.info(tr("Source recovered: %s"), self._source.name)
            return 0
        except Exception as e:  # noqa: BLE001 — a recovery error must not kill the loop
            log.error(tr("Recovery failed: %s"), e)
            return 0  # reset the counter; retry on the next error series

    def _dispatch(self, frame: Frame) -> None:
        for c in self._snapshot():
            self._safe(c.on_frame, frame)

    def _snapshot(self) -> List[FrameConsumer]:
        with self._lock:
            return list(self._consumers)

    @staticmethod
    def _safe(fn, *args) -> None:
        try:
            fn(*args)
        except Exception:  # noqa: BLE001
            log.exception(tr("Consumer error: %r"), fn)
