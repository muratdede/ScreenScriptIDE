"""Pattern (image to search) and locating images on disk + caching them."""

from __future__ import annotations

import os
import threading
from typing import Dict, List, Optional, Tuple, Union

import cv2
import numpy as np

from ...i18n import tr
from .errors import ImageNotFound


class Pattern:
    """Image to search + matching settings. Immutable; setting methods return a new Pattern.

        Pattern("button.png").similar(0.9).target_offset(10, 0)
    """

    def __init__(self, image: Union[str, np.ndarray], similarity: Optional[float] = None,
                 offset: Tuple[int, int] = (0, 0), name: Optional[str] = None):
        if similarity is not None and not 0.0 < similarity <= 1.0:
            raise ValueError(tr("similarity must be between 0 and 1"))
        self._image = image
        self._similarity = similarity
        self._offset = offset
        self._name = name or (image if isinstance(image, str) else "<ndarray>")

    # ---- fluent settings ------------------------------------------------- #
    def similar(self, similarity: float) -> "Pattern":
        return Pattern(self._image, similarity, self._offset, self._name)

    def exact(self) -> "Pattern":
        return self.similar(0.99)

    def target_offset(self, dx: int, dy: int) -> "Pattern":
        """Shifts the click point by (dx, dy) from the center of the match."""
        return Pattern(self._image, self._similarity, (dx, dy), self._name)

    # ---- properties ------------------------------------------------------ #
    @property
    def source(self) -> Union[str, np.ndarray]:
        return self._image

    @property
    def similarity(self) -> Optional[float]:
        """If None, Settings.min_similarity is used."""
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
        sim = f"{self._similarity:.2f}" if self._similarity is not None else "default"
        return f"Pattern({self._name!r}, similar={sim}, offset={self._offset})"


class ImagePath:
    """Image search paths (ImagePath in SikuliX). Relative names are searched in these folders in order."""

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
        raise ImageNotFound(tr("Image not found: {name!r}. Searched folders: {dirs}",
                               name=name, dirs=[os.getcwd()] + self._dirs))


class ImageLibrary:
    """Pattern -> numpy image. Reloads when the file changes (mtime)."""

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
        # cv2.imread fails on Windows for paths with non-ASCII characters; imdecode is safe
        img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        if img is None:
            raise ImageNotFound(tr("Image could not be read: {path}", path=path))
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
