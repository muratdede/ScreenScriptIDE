"""Ekran görüntüsü sağlayıcıları: script API'si ile adb_capture arasındaki köprü."""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from ..adb_capture import FrameSource, LatestFrameBuffer, PeriodicCapturer


class ImageProvider(ABC):
    """Script API'nin ekranı gördüğü tek yer."""

    @abstractmethod
    def snapshot(self, fresh: bool = True) -> np.ndarray:
        """Güncel ekran görüntüsü (BGR). fresh=True ise öncekinden daha yeni bir kare garanti edilir."""

    def open(self) -> None: ...

    def close(self) -> None: ...


class OnDemandProvider(ImageProvider):
    """Her istekte kaynaktan doğrudan kare çeker. Basit ve her zaman günceldir."""

    def __init__(self, source: FrameSource):
        self._source = source

    def open(self):
        self._source.open()

    def close(self):
        self._source.close()

    def snapshot(self, fresh=True):
        return self._source.grab()


class BackgroundProvider(ImageProvider):
    """PeriodicCapturer arka planda sürekli yakalar; snapshot en son kareyi verir.
    Script başka iş yaparken de ekran taze kalır."""

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
            raise TimeoutError(f"{self._timeout} sn içinde kare gelmedi. Son hata: "
                               f"{self._capturer.stats.last_error}")
        self._last_index = frame.index
        return frame.image
