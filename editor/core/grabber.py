"""Editör içinden cihaz ekran görüntüsü alma (arka plan thread'inde).

İki kip:
  grab(config)        — tek kare
  start_live(config)  — durdurulana kadar sürekli yakalar (canlı görüntü); stop_live() ile durur.
Canlı kipte UI yavaş kalırsa kareler kuyrukta birikmez: her zaman yalnızca en son kare teslim edilir.

"stream" biçiminde (screenrecord H.264) canlı görüntü kareleri geldikçe iletir (~35 FPS). Akışın açılması
~2 sn sürdüğü için Durdur'dan sonra akış bir süre (STREAM_IDLE_SECONDS) açık tutulur; Devam anında başlar.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Optional, Tuple

from PySide6.QtCore import QObject, Signal

from adb_capture import AdbDevice, AdbScreencapSource, AdbStreamSource, FrameSource, ScreencapFormat

from .app_settings import AppConfig


class ScreenGrabber(QObject):
    grabbed = Signal(object)   # numpy BGR
    failed = Signal(str)
    busy_changed = Signal(bool)
    live_changed = Signal(bool)
    _frame_pending = Signal()  # thread -> UI thread köprüsü (kuyruklu bağlantı)

    LIVE_MIN_INTERVAL = 0.05   # screencap kipi: cihazı gereksiz yormamak için üst sınır ~20 FPS
    STREAM_IDLE_SECONDS = 60   # canlı durdurulunca akış bu kadar açık kalır

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
        self._stream: Optional[AdbStreamSource] = None
        self._stream_key: Optional[Tuple] = None
        self._stream_idle: Optional[threading.Timer] = None
        self._stream_lock = threading.Lock()
        self._frame_pending.connect(self._deliver)

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def is_live(self) -> bool:
        return self._live_stop is not None

    # ---- tek kare -------------------------------------------------------- #
    def grab(self, config: AppConfig) -> None:
        if self._busy or self.is_live:
            return
        self._set_busy(True)
        threading.Thread(target=self._work_once, args=(config,), daemon=True).start()

    def _work_once(self, config: AppConfig) -> None:
        try:
            stream = self._stream
            if stream is not None and stream.streaming and self._stream_key == self._key(config):
                self._push(stream.grab())   # akış zaten açık: son kare anında
            else:
                # tek kare için akış açmaya (~2 sn) değmez; screencap
                fmt = ScreencapFormat(config.screencap_format)
                source = AdbScreencapSource(self._get_device(config),
                                            ScreencapFormat.RAW if fmt is ScreencapFormat.STREAM else fmt)
                source.open()
                self._push(source.grab())
        except Exception as e:  # noqa: BLE001
            self._device = None
            self.failed.emit(str(e))
        finally:
            self._set_busy(False)

    # ---- canlı ----------------------------------------------------------- #
    def start_live(self, config: AppConfig) -> None:
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
        self._schedule_stream_close()

    def toggle_live(self, config: AppConfig) -> None:
        if self.is_live:
            self.stop_live()
        else:
            self.start_live(config)

    def _work_live(self, config: AppConfig, stop: threading.Event) -> None:
        try:
            source = self._open_source(config)
            if isinstance(source, AdbStreamSource):
                self._work_stream(source, stop)
                return
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

    def _work_stream(self, source: AdbStreamSource, stop: threading.Event) -> None:
        """Akıştan kareler geldikçe iletir. Ekran değişmiyorsa kare gelmez (FPS 0 görünür)."""
        stamps = deque(maxlen=60)
        index = 0
        first = source.grab()   # ilk kare (gerekirse screencap yedeği)
        if not stop.is_set():
            self._push(first)
        index = source.frame_index
        while not stop.is_set():
            got = source.wait_next(index, timeout=0.5)
            now = time.perf_counter()
            while stamps and now - stamps[0] > 1.0:
                stamps.popleft()
            if got is None:
                self.fps = float(len(stamps))
                continue
            index, image = got
            if stop.is_set():
                break
            stamps.append(now)
            self.fps = float(len(stamps))
            self._push(image)

    # ---- ortak ----------------------------------------------------------- #
    @staticmethod
    def _key(config: AppConfig) -> Tuple:
        return (config.host, int(config.port), config.adb_path)

    def _open_source(self, config: AppConfig) -> FrameSource:
        fmt = ScreencapFormat(config.screencap_format)
        if fmt is not ScreencapFormat.STREAM:
            source = AdbScreencapSource(self._get_device(config), fmt)
            source.open()
            return source
        with self._stream_lock:
            if self._stream_idle is not None:
                self._stream_idle.cancel()
                self._stream_idle = None
            if self._stream is None or self._stream_key != self._key(config):
                if self._stream is not None:
                    self._stream.close()
                self._stream = AdbStreamSource(self._get_device(config))
                self._stream_key = self._key(config)
            stream = self._stream
        stream.open()
        return stream

    def _schedule_stream_close(self) -> None:
        with self._stream_lock:
            if self._stream is None:
                return
            if self._stream_idle is not None:
                self._stream_idle.cancel()
            self._stream_idle = threading.Timer(self.STREAM_IDLE_SECONDS, self.close_stream)
            self._stream_idle.daemon = True
            self._stream_idle.start()

    def close_stream(self) -> None:
        """Açık akışı kapatır (boşta kalınca, proje değişince, uygulamadan çıkarken).
        Canlı görüntü sürüyorsa kapatılmaz."""
        with self._stream_lock:
            if self._stream_idle is not None:
                self._stream_idle.cancel()
                self._stream_idle = None
            if self.is_live:
                return
            stream, self._stream, self._stream_key = self._stream, None, None
        if stream is not None:
            stream.close()

    def shutdown(self) -> None:
        self.stop_live()
        self.close_stream()

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

    def _get_device(self, config: AppConfig) -> AdbDevice:
        key = (config.host, int(config.port), config.adb_path)
        if self._device is None or key != self._device_key:
            self._device = AdbDevice.from_host_port(config.host, int(config.port),
                                                    adb_path=config.adb_path or None)
            self._device_key = key
        return self._device
