"""Editör içinden cihaz ekran görüntüsü alma (arka plan thread'inde).

İki kip:
  grab(config)        — tek kare
  start_live(config)  — durdurulana kadar sürekli yakalar (canlı görüntü); stop_live() ile durur.
Canlı kipte UI yavaş kalırsa kareler kuyrukta birikmez: her zaman yalnızca en son kare teslim edilir.
"""

from __future__ import annotations

import threading
import time
from typing import Optional, Tuple

from PySide6.QtCore import QObject, Signal

from adb_capture import AdbDevice, AdbScreencapSource, ScreencapFormat

from .project import ProjectConfig


class ScreenGrabber(QObject):
    grabbed = Signal(object)   # numpy BGR
    failed = Signal(str)
    busy_changed = Signal(bool)
    live_changed = Signal(bool)
    _frame_pending = Signal()  # thread -> UI thread köprüsü (kuyruklu bağlantı)

    LIVE_MIN_INTERVAL = 0.05   # saniye; cihazı gereksiz yormamak için üst sınır ~20 FPS

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self._device: Optional[AdbDevice] = None
        self._device_key: Optional[Tuple] = None
        self._lock = threading.Lock()
        self._latest = None
        self._live_stop: Optional[threading.Event] = None
        self._live_thread: Optional[threading.Thread] = None
        self.fps = 0.0
        self._frame_pending.connect(self._deliver)

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def is_live(self) -> bool:
        return self._live_stop is not None

    # ---- tek kare -------------------------------------------------------- #
    def grab(self, config: ProjectConfig) -> None:
        if self._busy or self.is_live:
            return
        self._set_busy(True)
        threading.Thread(target=self._work_once, args=(config,), daemon=True).start()

    def _work_once(self, config: ProjectConfig) -> None:
        try:
            self._push(self._open_source(config).grab())
        except Exception as e:  # noqa: BLE001
            self._device = None
            self.failed.emit(str(e))
        finally:
            self._set_busy(False)

    # ---- canlı ----------------------------------------------------------- #
    def start_live(self, config: ProjectConfig) -> None:
        if self.is_live:
            return
        stop = threading.Event()
        self._live_stop = stop
        self._live_thread = threading.Thread(target=self._work_live, args=(config, stop), daemon=True)
        self._live_thread.start()
        self.live_changed.emit(True)

    def stop_live(self) -> None:
        if self._live_stop is None:
            return
        self._live_stop.set()
        self._live_stop = None
        self._live_thread = None
        self.fps = 0.0
        self.live_changed.emit(False)

    def toggle_live(self, config: ProjectConfig) -> None:
        if self.is_live:
            self.stop_live()
        else:
            self.start_live(config)

    def _work_live(self, config: ProjectConfig, stop: threading.Event) -> None:
        try:
            source = self._open_source(config)
            last = time.perf_counter()
            while not stop.is_set():
                t0 = time.perf_counter()
                image = source.grab()
                if stop.is_set():
                    break
                self._push(image)
                now = time.perf_counter()
                dt = now - last
                last = now
                self.fps = 1.0 / dt if dt > 0 else 0.0
                wait = self.LIVE_MIN_INTERVAL - (now - t0)
                if wait > 0:
                    stop.wait(wait)
        except Exception as e:  # noqa: BLE001
            self._device = None
            if not stop.is_set():
                stop.set()
                self.failed.emit(str(e))
                self._frame_pending.emit()   # UI thread'inde canlı durumunu kapatmak için

    # ---- ortak ----------------------------------------------------------- #
    def _open_source(self, config: ProjectConfig) -> AdbScreencapSource:
        source = AdbScreencapSource(self._get_device(config), ScreencapFormat(config.screencap_format))
        source.open()
        return source

    def _push(self, image) -> None:
        with self._lock:
            pending = self._latest is not None
            self._latest = image
        if not pending:
            self._frame_pending.emit()

    def _deliver(self) -> None:
        # canlı thread hata ile bittiyse (stop kendi kendine set edildiyse) durumu güncelle
        if self._live_stop is not None and self._live_stop.is_set():
            self.stop_live()
        with self._lock:
            image, self._latest = self._latest, None
        if image is not None:
            self.grabbed.emit(image)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.busy_changed.emit(busy)

    def _get_device(self, config: ProjectConfig) -> AdbDevice:
        key = (config.host, int(config.port), config.adb_path)
        if self._device is None or key != self._device_key:
            self._device = AdbDevice.from_host_port(config.host, int(config.port),
                                                    adb_path=config.adb_path or None)
            self._device_key = key
        return self._device
