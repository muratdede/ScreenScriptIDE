"""Image sources. Derive from FrameSource to add a new source."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from enum import Enum

import cv2
import numpy as np

from ...i18n import tr
from .adb import AdbDevice, AdbError

log = logging.getLogger(__name__)


class CaptureError(RuntimeError):
    """Error while capturing a frame."""


class FrameSource(ABC):
    """Interface of all image sources."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def grab(self) -> np.ndarray:
        """Captures a single frame and returns a BGR numpy array. Raises CaptureError on failure."""

    def open(self) -> None:
        """Prepares the source for use."""

    def close(self) -> None:
        """Releases the source."""

    def recover(self) -> None:
        """Called after consecutive errors; the source tries to recover itself."""

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()


class ScreencapFormat(Enum):
    RAW = "raw"   # uncompressed RGBA — no PNG encoding on the device, usually 2-3 times faster
    PNG = "png"   # less data is transferred but encoding on the device is slow
    STREAM = "stream"   # screenrecord H.264 stream (stream.AdbStreamSource) — ~35 FPS at 1080p


class AdbScreencapSource(FrameSource):
    """Takes screenshots with `adb exec-out screencap`."""

    def __init__(self, device: AdbDevice, fmt: ScreencapFormat = ScreencapFormat.RAW):
        self._device = device
        self._fmt = fmt

    @property
    def name(self) -> str:
        return f"adb:{self._device.serial}"

    @property
    def device(self) -> AdbDevice:
        return self._device

    def open(self) -> None:
        if not self._device.is_connected():
            self._device.connect()

    def recover(self) -> None:
        self._device.reconnect()

    def grab(self) -> np.ndarray:
        try:
            if self._fmt is ScreencapFormat.PNG:
                return self._decode_png(self._device.exec_out("screencap", "-p"))
            return self._decode_raw(self._device.exec_out("screencap"))
        except AdbError as e:
            raise CaptureError(str(e)) from e

    # ---- decoders -------------------------------------------------------- #
    @staticmethod
    def _decode_png(data: bytes) -> np.ndarray:
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise CaptureError(tr("PNG could not be decoded (empty or corrupt data)."))
        return img

    @staticmethod
    def _decode_raw(data: bytes) -> np.ndarray:
        # header: width, height, format (+ colorspace on Android 9+), all little-endian uint32
        if len(data) < 12:
            raise CaptureError(tr("screencap data is too short ({size} bytes).", size=len(data)))
        width, height = np.frombuffer(data[:8], dtype="<u4")
        pixel_bytes = int(width) * int(height) * 4
        header = len(data) - pixel_bytes
        if header not in (12, 16):
            raise CaptureError(tr("Unexpected screencap size: {size} bytes, {width}x{height}.",
                                  size=len(data), width=width, height=height))
        rgba = np.frombuffer(data, dtype=np.uint8, offset=header).reshape((height, width, 4))
        return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR)
