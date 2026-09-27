"""Sending input from the editor to the device ("Clicker" mode of the Screen view).

Commands run in order on a single background thread: the UI never waits and touches reach the
device in the order they were sent.
"""

from __future__ import annotations

import queue
import threading
from typing import Callable, Optional, Tuple

from PySide6.QtCore import QObject, Signal

from ..i18n import tr
from ..plugins.adb_capture import AdbDevice
from ..plugins.screenscript import AdbInputDriver, Location

from .app_settings import AppConfig


class DeviceInput(QObject):
    failed = Signal(str)
    sent = Signal(str)   # short description of the sent command

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue: "queue.Queue[Tuple[AppConfig, str, Callable[[AdbInputDriver], None]]]" = queue.Queue()
        self._driver: Optional[AdbInputDriver] = None
        self._key: Optional[Tuple] = None
        threading.Thread(target=self._worker, daemon=True).start()

    # ---- commands -------------------------------------------------------- #
    def tap(self, config: AppConfig, x: int, y: int) -> None:
        self._put(config, f"tap {x},{y}", lambda d: d.tap(Location(x, y)))

    def swipe(self, config: AppConfig, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        self._put(config, f"swipe {x1},{y1} → {x2},{y2} ({duration_ms} ms)",
                  lambda d: d.swipe(Location(x1, y1), Location(x2, y2), max(1, duration_ms)))

    def long_press(self, config: AppConfig, x: int, y: int, duration_ms: int) -> None:
        self._put(config, tr("long press {x},{y} ({ms} ms)", x=x, y=y, ms=duration_ms),
                  lambda d: d.long_press(Location(x, y), duration_ms))

    def text(self, config: AppConfig, text: str) -> None:
        self._put(config, tr("text {text!r}", text=text), lambda d: d.type_text(text))

    def key(self, config: AppConfig, keycode: int, label: str = "") -> None:
        self._put(config, tr("key {key}", key=label or keycode), lambda d: d.key(keycode))

    # ---- internal -------------------------------------------------------- #
    def _put(self, config: AppConfig, label: str, job: Callable[[AdbInputDriver], None]) -> None:
        self._queue.put((config, label, job))

    def _worker(self) -> None:
        while True:
            config, label, job = self._queue.get()
            try:
                job(self._get_driver(config))
                self.sent.emit(label)
            except Exception as e:  # noqa: BLE001
                self._driver = None
                self.failed.emit(f"{label}: {e}")

    def _get_driver(self, config: AppConfig) -> AdbInputDriver:
        key = (config.host, int(config.port), config.adb_path)
        if self._driver is None or key != self._key:
            device = AdbDevice.from_host_port(config.host, int(config.port), adb_path=config.adb_path or None)
            device.connect()
            self._driver = AdbInputDriver(device)
            self._key = key
        return self._driver
