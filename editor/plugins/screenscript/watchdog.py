"""Freeze watchdog: notifies (push_notification channel) when the emulator stops responding while a
script runs. Two signals:

  * the screen content has not changed for `freeze_seconds` although the script kept looking at it.
    An emulator whose rendering stopped keeps serving the same frame; a live game screen always
    moves somewhere (background, clock, animations).
  * an adb command timed out: the device no longer answers (e.g. `input tap` hung for 10 s).

Each alert is sent once, with the last screen, and is not repeated until the screen moves again
(a notice is sent then too). Sending happens on a background thread so the script is never
blocked; stop() waits for pending sends, so an alert raised just before the script dies (an adb
timeout usually ends it) still goes out.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import List, Optional

import cv2
import numpy as np

from ...i18n import tr
from ..adb_capture import add_timeout_listener, remove_timeout_listener
from .notify import Notifier, NullNotifier

log = logging.getLogger("script")


class FreezeWatchdog:
    INTERVAL = 2.0            # how often the latest frame is compared (s)
    THUMB = (96, 54)          # frames are compared as small grayscale thumbnails
    CHANGE_LEVEL = 6          # largest thumbnail pixel difference (0-255) that still counts as "no change"
    TIMEOUT_ALERT_GAP = 600   # at most one adb timeout alert per this many seconds
    SEND_WAIT = 20.0          # stop() waits this long for notifications still being sent

    def __init__(self, engine, notifier: Notifier, freeze_seconds: float):
        self._engine = engine
        self._notifier = notifier
        self._freeze_seconds = freeze_seconds
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._senders: List[threading.Thread] = []
        self._reference: Optional[np.ndarray] = None
        self._changed_at = 0.0
        self._alerted = False
        self._last_timeout_alert = -self.TIMEOUT_ALERT_GAP

    def start(self) -> None:
        self._stop.clear()
        self._changed_at = time.monotonic()
        add_timeout_listener(self._on_adb_timeout)
        self._thread = threading.Thread(target=self._run, daemon=True, name="freeze-watchdog")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        remove_timeout_listener(self._on_adb_timeout)
        if self._thread is not None:
            self._thread.join(self.INTERVAL + 1)
        deadline = time.monotonic() + self.SEND_WAIT
        for sender in self._senders:
            sender.join(max(0.0, deadline - time.monotonic()))

    # ---- signals ----------------------------------------------------------- #
    def _run(self) -> None:
        while not self._stop.wait(self.INTERVAL):
            try:
                self._check()
            except Exception:  # noqa: BLE001 — the watchdog must never take the script down
                log.exception(tr("Freeze check failed"))

    def _check(self) -> None:
        image, grabbed = self._engine.last_image, self._engine.last_grab_time
        if image is None:
            return
        thumb = cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), self.THUMB, interpolation=cv2.INTER_AREA)
        if self._reference is None or thumb.shape != self._reference.shape or \
                int(cv2.absdiff(thumb, self._reference).max()) > self.CHANGE_LEVEL:
            self._reference = thumb
            self._changed_at = time.monotonic()
            if self._alerted:
                self._alerted = False
                self._alert(tr("The emulator screen is moving again."), None)
            return
        # Only the time the script spent looking at the unchanged screen counts: a script that
        # sleeps (does not grab) says nothing about the emulator.
        frozen = grabbed - self._changed_at
        if not self._alerted and frozen >= self._freeze_seconds:
            self._alerted = True
            self._alert(tr("The emulator screen has not changed for {seconds} s; it may have frozen.",
                           seconds=int(frozen)), image)

    def _on_adb_timeout(self, command: str) -> None:
        now = time.monotonic()
        if now - self._last_timeout_alert < self.TIMEOUT_ALERT_GAP:
            return
        self._last_timeout_alert = now
        self._alert(tr("The emulator did not answer an adb command in time ({command}); it may have frozen.",
                       command=command), self._engine.last_image)

    # ---- sending ------------------------------------------------------------- #
    def _alert(self, text: str, image: Optional[np.ndarray]) -> None:
        log.warning(text)
        if isinstance(self._notifier, NullNotifier):
            return

        def send():
            try:
                if image is not None:
                    self._notifier.send(image, caption=text)
                else:
                    self._notifier.send(text)
            except Exception as e:  # noqa: BLE001 — NotificationError or a network error
                log.warning(tr("Freeze notification could not be sent: %s"), e)

        sender = threading.Thread(target=send, daemon=True, name="freeze-alert")
        sender.start()
        self._senders = [s for s in self._senders if s.is_alive()] + [sender]
