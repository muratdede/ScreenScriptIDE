"""Pattern (aranacak görsel) ve görsellerin diskten bulunup önbelleğe alınması."""

from __future__ import annotations

import os
import threading
from typing import Dict, List, Optional, Tuple, Union

import cv2
import numpy as np

from .errors import ImageNotFound


class Pattern:
    """Aranacak görsel + eşleşme ayarları. Değişmezdir; ayar metotları yeni Pattern döndürür.

        Pattern("button.png").similar(0.9).target_offset(10, 0)
    """

    def __init__(self, image: Union[str, np.ndarray], similarity: Optional[float] = None,
                 offset: Tuple[int, int] = (0, 0), name: Optional[str] = None):
        if similarity is not None and not 0.0 < similarity <= 1.0:
            raise ValueError("similarity 0 ile 1 arasında olmalı")
        self._image = image
        self._similarity = similarity
        self._offset = offset
        self._name = name or (image if isinstance(image, str) else "<ndarray>")

    # ---- fluent ayarlar -------------------------------------------------- #
    def similar(self, similarity: float) -> "Pattern":
        return Pattern(self._image, similarity, self._offset, self._name)

    def exact(self) -> "Pattern":
        return self.similar(0.99)

    def target_offset(self, dx: int, dy: int) -> "Pattern":
        """Tıklama noktasını eşleşmenin merkezinden (dx, dy) kaydırır."""
        return Pattern(self._image, self._similarity, (dx, dy), self._name)

    # ---- özellikler ------------------------------------------------------ #
    @property
    def source(self) -> Union[str, np.ndarray]:
        return self._image

    @property
    def similarity(self) -> Optional[float]:
        """None ise Settings.min_similarity kullanılır."""
        return self._similarity

    @property
    def offset(self) -> Tuple[int, int]:
        return self._offset

    @property
    def name(self) -> str:
        return self._name

    @classmethod
    def of(cls, target: Union["Pattern", str, np.ndarray]) -> "Pattern":
        return target if isinstance(target, Pattern) else cls(target)

    def __repr__(self) -> str:
        sim = f"{self._similarity:.2f}" if self._similarity is not None else "varsayılan"
        return f"Pattern({self._name!r}, similar={sim}, offset={self._offset})"


class ImagePath:
    """Görsel arama yolları (SikuliX'teki ImagePath). Göreli adlar sırayla bu klasörlerde aranır."""

    def __init__(self, dirs: Optional[List[str]] = None):
        self._dirs: List[str] = []
        for d in dirs or []:
            self.add(d)

    def add(self, directory: str) -> None:
        d = os.path.abspath(directory)
        if d not in self._dirs:
            self._dirs.append(d)

    @property
    def dirs(self) -> List[str]:
        return list(self._dirs)

    def resolve(self, name: str) -> str:
        names = [name] if os.path.splitext(name)[1] else [name, name + ".png"]
        for n in names:
            if os.path.isabs(n) and os.path.isfile(n):
                return n
            for base in [os.getcwd()] + self._dirs:
                p = os.path.join(base, n)
                if os.path.isfile(p):
                    return os.path.abspath(p)
        raise ImageNotFound(f"Görsel bulunamadı: {name!r}. Aranan klasörler: {[os.getcwd()] + self._dirs}")


class ImageLibrary:
    """Pattern -> numpy görüntü. Dosya değişirse (mtime) yeniden yükler."""

    def __init__(self, image_path: ImagePath):
        self._path = image_path
        self._cache: Dict[str, Tuple[float, np.ndarray]] = {}
        self._lock = threading.Lock()

    @property
    def image_path(self) -> ImagePath:
        return self._path

    def load(self, pattern: Pattern) -> np.ndarray:
        src = pattern.source
        if isinstance(src, np.ndarray):
            return self._to_bgr(src)
        path = self._path.resolve(src)
        mtime = os.path.getmtime(path)
        with self._lock:
            cached = self._cache.get(path)
            if cached and cached[0] == mtime:
                return cached[1]
        # cv2.imread Windows'ta Türkçe karakterli yollarda sorun çıkarır; imdecode güvenli
        img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        if img is None:
            raise ImageNotFound(f"Görsel okunamadı: {path}")
        img = self._to_bgr(img)
        with self._lock:
            self._cache[path] = (mtime, img)
        return img

    @staticmethod
    def _to_bgr(img: np.ndarray) -> np.ndarray:
        if img.ndim == 2:
            return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        if img.shape[2] == 4:
            return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        return img
