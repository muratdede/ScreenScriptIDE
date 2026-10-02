"""Fast screen capture with an H.264 video stream (`adb exec-out screenrecord --output-format=h264`).

Why: `screencap` transfers ~8 MB of uncompressed data per frame (~3 FPS at 1080p). screenrecord produces
an H.264 stream with the device's hardware encoder; ~0.5 MB/s gives 30-40 FPS. Since the image comes from
inside Android, it also works while the emulator window is minimized / hidden (like screencap).

How:
    screenrecord stdout ─> local TCP socket ─> cv2.VideoCapture (OpenCV's FFmpeg) ─> latest frame
    No extra dependency. The reader thread decodes the stream continuously and keeps only the latest
    frame; so latency does not pile up even if the consumer is slow.

Limitations and workarounds:
  - On Android 10 and earlier a single recording lasts at most 180 s. A new session is started in
    parallel before it expires and takes over when its first frame arrives: no gap in the stream.
  - screenrecord only produces frames when the screen changes. If the screen is static the last frame stays valid.
  - If the stream cannot start (no screenrecord, not decodable) or no first frame has arrived yet, grab()
    answers with the classic `screencap`; scripts are never left without a frame.
"""

from __future__ import annotations

import logging
import os
import itertools
import socket
import subprocess
import threading
import time
from collections import deque
from typing import List, Optional, Tuple

import numpy as np

# OpenCV reads this variable when opening VideoCapture: no waiting for stream analysis. Not
# "fflags;nobuffer": with it the first frame of every session arrives only after ~20 s (measured),
# so rotated sessions leave gaps and slow ones hit the 30 s decoder timeout with no frame at all.
# Assigned, not setdefault: the script process inherits the editor's value, so a stale one must not stick.
# (OpenCV only sees the value when it is inherited from the parent process, not when set in-process.)
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "flags;low_delay|probesize;32|analyzeduration;0"
import cv2  # noqa: E402

from ...i18n import tr  # noqa: E402
from .adb import AdbDevice  # noqa: E402
from .sources import AdbScreencapSource, CaptureError, FrameSource, ScreencapFormat  # noqa: E402

log = logging.getLogger(__name__)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class _ChildReaper:
    """Windows: attaches child processes to a Job Object; however this process (IDE or script) ends
    (even when killed with Stop) the screenrecord processes are closed too. While the screen is static
    there is no data in the stream, so adb does not notice a dropped connection; without this the
    process would hang for 180 s."""

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
            log.debug(tr("Job Object could not be created: %s"), e)


class _StreamSession:
    """A single screenrecord process + decoder. Passes frames to its owner (AdbStreamSource)."""

    _ids = itertools.count(1)

    def __init__(self, owner: "AdbStreamSource", time_limit: int):
        self._owner = owner
        self.id = next(self._ids)
        self.started = time.monotonic()
        self.first_frame = threading.Event()
        self.alive = True
        self.frames = 0
        self.end_reason = ""                    # why the session ended (for the debug log)
        self._stderr: deque = deque(maxlen=10)  # last lines adb / screenrecord wrote to stderr
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
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      creationflags=_NO_WINDOW)
        _ChildReaper.attach(self._proc)
        threading.Thread(target=self._pump, daemon=True, name="screenrecord-pump").start()
        threading.Thread(target=self._read_stderr, daemon=True, name="screenrecord-stderr").start()
        threading.Thread(target=self._decode, args=(port,), daemon=True, name="screenrecord-decode").start()

    def _pump(self) -> None:
        """Pipes the screenrecord output into a local socket (OpenCV cannot read stdin but can read tcp://)."""
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

    def _read_stderr(self) -> None:
        try:
            for line in self._proc.stderr:
                self._stderr.append(line.decode(errors="ignore").strip())
        except (OSError, ValueError):
            pass

    @property
    def stderr_tail(self) -> str:
        return " | ".join(line for line in self._stderr if line)

    @property
    def pid(self) -> int:
        return self._proc.pid

    def _decode(self, port: int) -> None:
        cap = None
        try:
            cap = cv2.VideoCapture(f"tcp://127.0.0.1:{port}", cv2.CAP_FFMPEG)
            if not cap.isOpened():
                raise CaptureError(tr("H.264 stream could not be opened"))
            while self.alive:
                ok, frame = cap.read()
                if not ok:
                    if self.alive:
                        self.end_reason = "decoder returned no frame (stream ended or timed out)"
                    break
                if not self.first_frame.is_set():
                    log.debug(tr("Stream session #%d: first frame after %.1f s"),
                              self.id, time.monotonic() - self.started)
                self.frames += 1
                self.first_frame.set()
                self._owner._on_frame(self, frame)
        except Exception as e:  # noqa: BLE001
            self.end_reason = "error: %s" % e
            log.debug(tr("Stream session ended with an error: %s"), e)
        finally:
            if cap is not None:
                cap.release()
            # A session that ends on its own (decoder timeout, broken stream) must take its adb
            # process with it: otherwise the device keeps running screenrecord until its time
            # limit, and every failed restart adds one more encoder until none can start.
            self.stop()
            self._owner._on_session_end(self)

    def stop(self, reason: str = "") -> None:
        if reason and not self.end_reason:
            self.end_reason = reason
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
    """Source delivering frames from the screenrecord H.264 stream. grab() returns the latest frame immediately."""

    TIME_LIMIT = 180          # upper limit on Android <= 10
    ROTATE_AFTER = 165        # after this many seconds a new session is started in parallel
    FIRST_FRAME_TIMEOUT = 1.5     # no long wait while the stream warms up; screencap gives a correct frame in ~0.3 s anyway
    # Wait before restarting after the n-th failed session in a row. A session that delivers no
    # frame already costs ~30 s (decoder timeout); when the stream keeps failing, grab() serves
    # screencap anyway, so restarts are spread out to at most one every few minutes.
    RESTART_BACKOFF = (1.0, 2.0, 5.0, 10.0, 60.0, 300.0)

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
            s.stop("source closed")

    def recover(self) -> None:
        self.close()
        self._device.reconnect()
        self.open()

    def grab(self) -> np.ndarray:
        """Latest frame. If the stream has no frame yet, waits briefly, then falls back to screencap."""
        if self._closed:
            self.open()
        with self._lock:
            # Wait for the stream only while it is healthy; when sessions keep failing (no frame
            # since the last restart) every grab would lose FIRST_FRAME_TIMEOUT before screencap.
            if self._frame is None and self._failures <= 1:
                self._lock.wait_for(lambda: self._frame is not None or self._closed, timeout=self.FIRST_FRAME_TIMEOUT)
            frame = self._frame
        if frame is not None:
            return frame
        log.debug(tr("No frame from the stream yet; using screencap"))
        return self._fallback.grab()

    # ---- for the live view ------------------------------------------------ #
    @property
    def frame_index(self) -> int:
        return self._index

    def wait_next(self, after_index: int, timeout: float) -> Optional[Tuple[int, np.ndarray]]:
        """Waits for a frame newer than after_index; (index, frame), or None on timeout."""
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

    # ---- session management ------------------------------------------------ #
    def _on_frame(self, session: _StreamSession, frame: np.ndarray) -> None:
        with self._lock:
            if self._current is None or (session is not self._current and session.first_frame.is_set()
                                         and session.started > self._current.started):
                old, self._current = self._current, session   # the new session took over
                if old is not None and old is not session:
                    log.debug(tr("Stream session #%d took over from #%d"), session.id, old.id)
                    threading.Thread(target=old.stop, args=("replaced by a newer session",),
                                     daemon=True).start()
            if session is not self._current:
                return
            self._frame = frame
            self._index += 1
            self._frame_time = time.monotonic()
            self._failures = 0
            self._lock.notify_all()

    def _on_session_end(self, session: _StreamSession) -> None:
        log.debug(tr("Stream session #%d ended after %.0f s, %d frames: %s%s"),
                  session.id, time.monotonic() - session.started, session.frames,
                  session.end_reason or "?",
                  (" [adb: %s]" % session.stderr_tail) if session.stderr_tail else "")
        with self._lock:
            if session in self._sessions:
                self._sessions.remove(session)
            if session is self._current:
                self._current = None
                # The frame of a dead session is stale: if the next session never delivers (the
                # stream keeps timing out), serving it would freeze the screen for the script
                # forever. Without a frame, grab() falls back to screencap.
                self._frame = None
            self._lock.notify_all()

    def _start_session(self) -> _StreamSession:
        s = _StreamSession(self, self.TIME_LIMIT)
        log.debug(tr("Stream session #%d started (adb pid %d, consecutive failures %d)"),
                  s.id, s.pid, self._failures)
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
                # no session at all: (re)start
                if self._failures:
                    delay = self.RESTART_BACKOFF[min(self._failures - 1, len(self.RESTART_BACKOFF) - 1)]
                    if self._failures == 3:
                        log.warning(tr("The screen stream delivers no frames; screencap is used, "
                                       "the stream is retried less and less often"))
                    if self._stop.wait(delay):
                        break
                self._failures += 1
                try:
                    pending = self._start_session()
                except OSError as e:
                    log.warning(tr("screenrecord could not be started: %s"), e)
            elif current is not None and (pending is None or not pending.alive or pending is current) and \
                    time.monotonic() - current.started > self.ROTATE_AFTER:
                # start a new session in parallel before the time is up; it takes over on its first frame
                try:
                    pending = self._start_session()
                except OSError as e:
                    log.warning(tr("New screenrecord session could not be started: %s"), e)
            self._stop.wait(0.5)


def create_source(device: AdbDevice, fmt: ScreencapFormat) -> FrameSource:
    """Source by format: STREAM -> AdbStreamSource, RAW/PNG -> AdbScreencapSource."""
    if fmt is ScreencapFormat.STREAM:
        return AdbStreamSource(device)
    return AdbScreencapSource(device, fmt)
