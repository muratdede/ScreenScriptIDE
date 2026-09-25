"""Görüntü kaynakları. Yeni bir kaynak eklemek için FrameSource'tan türetin."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from enum import Enum

import cv2
import numpy as np

from .adb import AdbDevice, AdbError

log = logging.getLogger(__name__)


class CaptureError(RuntimeError):
    """Bir kareyi yakalarken oluşan hata."""


class FrameSource(ABC):
    """Tüm görüntü kaynaklarının arayüzü."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def grab(self) -> np.ndarray:
        """Tek bir kare yakalar ve BGR numpy dizisi döndürür. Hata durumunda CaptureError fırlatır."""

    def open(self) -> None:
        """Kaynağı kullanıma hazırlar."""

    def close(self) -> None:
        """Kaynağı serbest bırakır."""

    def recover(self) -> None:
        """Art arda hatalardan sonra çağrılır; kaynak kendini toparlamayı dener."""

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()


class ScreencapFormat(Enum):
    RAW = "raw"   # Sıkıştırmasız RGBA — cihazda PNG kodlaması yok, genelde 2-3 kat daha hızlı
    PNG = "png"   # Daha az veri aktarılır ama cihazda kodlama yavaş


class AdbScreencapSource(FrameSource):
    """`adb exec-out screencap` ile ekran görüntüsü alır."""

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

    # ---- çözücüler ------------------------------------------------------- #
    @staticmethod
    def _decode_png(data: bytes) -> np.ndarray:
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise CaptureError("PNG çözülemedi (boş ya da bozuk veri).")
        return img

    @staticmethod
    def _decode_raw(data: bytes) -> np.ndarray:
        # Başlık: width, height, format (+ Android 9+'da colorspace), hepsi little-endian uint32
        if len(data) < 12:
            raise CaptureError(f"screencap verisi çok kısa ({len(data)} bayt).")
        width, height = np.frombuffer(data[:8], dtype="<u4")
        pixel_bytes = int(width) * int(height) * 4
        header = len(data) - pixel_bytes
        if header not in (12, 16):
            raise CaptureError(f"Beklenmeyen screencap boyutu: {len(data)} bayt, {width}x{height}.")
        rgba = np.frombuffer(data, dtype=np.uint8, offset=header).reshape((height, width, 4))
        return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR)
