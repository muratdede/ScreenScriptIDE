"""H.264 video akışı ile hızlı ekran yakalama (`adb exec-out screenrecord --output-format=h264`).

Neden: `screencap` her karede ~8 MB sıkıştırılmamış veri taşır (1080p'de ~3 FPS). screenrecord cihazın
donanım kodlayıcısıyla H.264 akışı üretir; ~0.5 MB/sn ile 30-40 FPS alınır. Görüntü Android'in içinden
geldiği için emülatör penceresi simge durumunda / görünmezken de çalışır (screencap gibi).

Nasıl:
    screenrecord stdout ─> yerel TCP soketi ─> cv2.VideoCapture (OpenCV'nin FFmpeg'i) ─> son kare
    Ek bağımlılık yoktur. Okuyucu thread akışı sürekli çözer ve yalnızca en son kareyi tutar; böylece
    tüketici yavaş kalsa bile gecikme birikmez.

Sınırlamalar ve çözümleri:
  - Android 10 ve öncesinde tek kayıt en fazla 180 sn sürer. Oturum dolmadan yenisi paralel başlatılır,
    ilk karesi gelince devralır: akışta boşluk olmaz.
  - screenrecord yalnızca ekran değiştiğinde kare üretir. Ekran durağansa son kare geçerli kalır.
  - Akış başlayamazsa (screenrecord yok, çözülemedi) ya da henüz ilk kare gelmediyse grab()
    klasik `screencap` ile yanıt verir; script'ler hiçbir durumda kare alamadan kalmaz.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import threading
import time
from typing import List, Optional, Tuple

import numpy as np

# OpenCV bu değişkeni VideoCapture açılırken okur: tamponlama olmasın, akış analizi beklenmesin
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS",
                      "fflags;nobuffer|flags;low_delay|probesize;32|analyzeduration;0")
import cv2  # noqa: E402

from .adb import AdbDevice  # noqa: E402
from .sources import AdbScreencapSource, CaptureError, FrameSource, ScreencapFormat  # noqa: E402

log = logging.getLogger(__name__)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class _ChildReaper:
    """Windows: alt süreçleri bir Job Object'e bağlar; bu süreç (IDE ya da script) nasıl biterse
    bitsin (Durdur ile öldürülse bile) screenrecord süreçleri de kapanır. Ekran durağanken akışta veri
    olmadığından adb kopan bağlantıyı fark etmez; bu olmadan süreç 180 sn boyunca asılı kalırdı."""

    _job = None

    @classmethod
    def attach(cls, proc: subprocess.Popen) -> None:
        if os.name != "nt":
            return
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            if cls._job is None:
                k32.CreateJobObjectW.restype = wintypes.HANDLE
                job = k32.CreateJobObjectW(None, None)

                class _Basic(ctypes.Structure):
                    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                                ("SchedulingClass", wintypes.DWORD)]

                class _Io(ctypes.Structure):
                    _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount",
                                "OtherOperationCount", "ReadTransferCount", "WriteTransferCount",
                                "OtherTransferCount")]

                class _Extended(ctypes.Structure):
                    _fields_ = [("BasicLimitInformation", _Basic), ("IoInfo", _Io),
                                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

                info = _Extended()
                info.BasicLimitInformation.LimitFlags = 0x2000   # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))  # Extended = 9
                cls._job = job
            k32.OpenProcess.restype = wintypes.HANDLE
            handle = k32.OpenProcess(0x0100 | 0x0001, False, proc.pid)   # SET_QUOTA | TERMINATE
            if handle:
                k32.AssignProcessToJobObject(wintypes.HANDLE(cls._job), wintypes.HANDLE(handle))
                k32.CloseHandle(wintypes.HANDLE(handle))
        except Exception as e:  # noqa: BLE001
            log.debug("Job Object kurulamadı: %s", e)


class _StreamSession:
    """Tek bir screenrecord süreci + çözücü. Kareleri sahibine (AdbStreamSource) iletir."""

    def __init__(self, owner: "AdbStreamSource", time_limit: int):
        self._owner = owner
        self.started = time.monotonic()
        self.first_frame = threading.Event()
        self.alive = True
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(1)
        port = self._server.getsockname()[1]
        dev = owner.device
        cmd = [dev.adb_path, "-s", dev.serial, "exec-out", "screenrecord", "--output-format=h264",
               "--bit-rate", str(owner.bit_rate), "--time-limit", str(time_limit)]
        if owner.size:
            cmd += ["--size", f"{owner.size[0]}x{owner.size[1]}"]
        cmd.append("-")
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                      creationflags=_NO_WINDOW)
        _ChildReaper.attach(self._proc)
        threading.Thread(target=self._pump, daemon=True, name="screenrecord-pump").start()
        threading.Thread(target=self._decode, args=(port,), daemon=True, name="screenrecord-decode").start()

    def _pump(self) -> None:
        """screenrecord çıktısını yerel sokete aktarır (OpenCV stdin okuyamaz, tcp:// okuyabilir)."""
        conn = None
        try:
            self._server.settimeout(15)
            conn, _ = self._server.accept()
            read = self._proc.stdout.read1
            while True:
                data = read(1 << 16)
                if not data:
                    break
                conn.sendall(data)
        except OSError:
            pass
        finally:
            for s in (conn, self._server):
                try:
                    if s is not None:
                        s.close()
                except OSError:
                    pass

    def _decode(self, port: int) -> None:
        cap = None
        try:
            cap = cv2.VideoCapture(f"tcp://127.0.0.1:{port}", cv2.CAP_FFMPEG)
            if not cap.isOpened():
                raise CaptureError("H.264 akışı açılamadı")
            while self.alive:
                ok, frame = cap.read()
                if not ok:
                    break
                self.first_frame.set()
                self._owner._on_frame(self, frame)
        except Exception as e:  # noqa: BLE001
            log.debug("Akış oturumu hata ile bitti: %s", e)
        finally:
            if cap is not None:
                cap.release()
            self.alive = False
            self._owner._on_session_end(self)

    def stop(self) -> None:
        self.alive = False
        try:
            self._proc.kill()
        except OSError:
            pass
        try:
            self._server.close()
        except OSError:
            pass


class AdbStreamSource(FrameSource):
    """screenrecord H.264 akışından kare veren kaynak. grab() en son kareyi hemen döndürür."""

    TIME_LIMIT = 180          # Android <= 10 üst sınırı
    ROTATE_AFTER = 165        # bu kadar saniye sonra yeni oturum paralel başlatılır
    FIRST_FRAME_TIMEOUT = 8.0
    RESTART_BACKOFF = (1.0, 2.0, 5.0, 10.0)

    def __init__(self, device: AdbDevice, bit_rate: int = 8_000_000, size: Optional[Tuple[int, int]] = None,
                 fallback: Optional[FrameSource] = None):
        self._device = device
        self.bit_rate = bit_rate
        self.size = size
        self._fallback = fallback or AdbScreencapSource(device, ScreencapFormat.RAW)
        self._lock = threading.Condition()
        self._frame: Optional[np.ndarray] = None
        self._index = 0
        self._frame_time = 0.0
        self._current: Optional[_StreamSession] = None
        self._sessions: List[_StreamSession] = []
        self._closed = True
        self._failures = 0
        self._supervisor: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ---- FrameSource ------------------------------------------------------ #
    @property
    def name(self) -> str:
        return f"adb-stream:{self._device.serial}"

    @property
    def device(self) -> AdbDevice:
        return self._device

    def open(self) -> None:
        if not self._device.is_connected():
            self._device.connect()
        if not self._closed:
            return
        self._closed = False
        self._stop.clear()
        self._supervisor = threading.Thread(target=self._supervise, daemon=True, name="screenrecord-supervisor")
        self._supervisor.start()

    def close(self) -> None:
        self._closed = True
        self._stop.set()
        with self._lock:
            sessions, self._sessions, self._current = list(self._sessions), [], None
            self._lock.notify_all()
        for s in sessions:
            s.stop()

    def recover(self) -> None:
        self.close()
        self._device.reconnect()
        self.open()

    def grab(self) -> np.ndarray:
        """En son kare. Akış henüz kare vermediyse kısa süre bekler, gelmezse screencap kullanır."""
        if self._closed:
            self.open()
        with self._lock:
            if self._frame is None:
                self._lock.wait_for(lambda: self._frame is not None or self._closed, timeout=self.FIRST_FRAME_TIMEOUT)
            frame = self._frame
        if frame is not None:
            return frame
        log.info("Akıştan kare gelmedi; screencap kullanılıyor")
        return self._fallback.grab()

    # ---- canlı görüntü için ------------------------------------------------ #
    @property
    def frame_index(self) -> int:
        return self._index

    def wait_next(self, after_index: int, timeout: float) -> Optional[Tuple[int, np.ndarray]]:
        """after_index'ten yeni bir kare gelene kadar bekler; (indeks, kare) ya da zaman aşımında None."""
        if self._closed:
            self.open()
        with self._lock:
            if self._lock.wait_for(lambda: self._index > after_index or self._closed, timeout=timeout) \
                    and self._frame is not None and self._index > after_index:
                return self._index, self._frame
        return None

    @property
    def streaming(self) -> bool:
        s = self._current
        return s is not None and s.alive and s.first_frame.is_set()

    # ---- oturum yönetimi --------------------------------------------------- #
    def _on_frame(self, session: _StreamSession, frame: np.ndarray) -> None:
        with self._lock:
            if self._current is None or (session is not self._current and session.first_frame.is_set()
                                         and session.started > self._current.started):
                old, self._current = self._current, session   # yeni oturum devraldı
                if old is not None and old is not session:
                    threading.Thread(target=old.stop, daemon=True).start()
            if session is not self._current:
                return
            self._frame = frame
            self._index += 1
            self._frame_time = time.monotonic()
            self._failures = 0
            self._lock.notify_all()

    def _on_session_end(self, session: _StreamSession) -> None:
        with self._lock:
            if session in self._sessions:
                self._sessions.remove(session)
            if session is self._current:
                self._current = None
            self._lock.notify_all()

    def _start_session(self) -> _StreamSession:
        s = _StreamSession(self, self.TIME_LIMIT)
        with self._lock:
            self._sessions.append(s)
        return s

    def _supervise(self) -> None:
        pending: Optional[_StreamSession] = None
        while not self._stop.is_set():
            with self._lock:
                current = self._current
                alive = [s for s in self._sessions if s.alive]
            if current is None and not alive:
                # hiç oturum yok: (yeniden) başlat
                if self._failures:
                    delay = self.RESTART_BACKOFF[min(self._failures - 1, len(self.RESTART_BACKOFF) - 1)]
                    if self._stop.wait(delay):
                        break
                self._failures += 1
                try:
                    pending = self._start_session()
                except OSError as e:
                    log.warning("screenrecord başlatılamadı: %s", e)
            elif current is not None and (pending is None or not pending.alive or pending is current) and \
                    time.monotonic() - current.started > self.ROTATE_AFTER:
                # süre dolmadan yeni oturumu paralel başlat; ilk karesi gelince devralır
                try:
                    pending = self._start_session()
                except OSError as e:
                    log.warning("Yeni screenrecord oturumu başlatılamadı: %s", e)
            self._stop.wait(0.5)


def create_source(device: AdbDevice, fmt: ScreencapFormat) -> FrameSource:
    """Biçime göre kaynak: STREAM -> AdbStreamSource, RAW/PNG -> AdbScreencapSource."""
    if fmt is ScreencapFormat.STREAM:
        return AdbStreamSource(device)
    return AdbScreencapSource(device, fmt)
