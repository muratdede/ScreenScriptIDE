"""Text recognition (OCR). Returns the texts in an image together with their boxes.

TextReader       : interface — read(image) -> List[TextResult] (box relative to the given image)
RapidOcrReader   : PaddleOCR models via onnxruntime (pip install rapidocr onnxruntime).
                   Most accurate on game fonts and numbers; the default.
WindowsOcrReader : Windows' built-in OCR (pip install winrt-Windows.Media.Ocr ...).
                   Downloads no model, very fast; weaker on small text and numbers.
ScaledReader     : Decorator — upscales/preprocesses the image for the inner reader, scales boxes back.
create_text_reader("auto" | "rapid" | "windows") builds the suitable reader.

Readers are loaded on the first read() call; construction is cheap.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

import cv2
import numpy as np

from ...i18n import tr
from .errors import ScriptError
from .geometry import Rect


class OcrUnavailable(ScriptError):
    """No usable OCR engine (package not installed or could not be started)."""


@dataclass(frozen=True)
class TextResult:
    text: str
    rect: Rect      # coordinates relative to the image that was read
    score: float    # 0..1 confidence


class TextReader(ABC):
    name: str = "ocr"

    @abstractmethod
    def read(self, image: np.ndarray) -> List[TextResult]:
        """Lines in the BGR image (in the order given by the engine)."""


# --------------------------------------------------------------------------- #
# RapidOCR
# --------------------------------------------------------------------------- #
class RapidOcrReader(TextReader):
    name = "rapid"

    # The default RapidOCR setting upscales small images so the short side becomes 736 px;
    # for screen regions this means seconds per call. Limiting the long side brings it down to
    # 20-50 ms without changing accuracy. Texts are always horizontal, so the angle classifier is off.
    DEFAULT_PARAMS = {
        "Global.use_cls": False,
        "Global.log_level": "error",
        "Det.limit_type": "max",
        "Det.limit_side_len": 960,
        "EngineConfig.onnxruntime.intra_op_num_threads": min(8, os.cpu_count() or 4),
    }

    def __init__(self, params: Optional[dict] = None, min_score: float = 0.5):
        self._params = {**self.DEFAULT_PARAMS, **(params or {})}
        self._min_score = min_score
        self._engine = None
        self._lock = threading.Lock()

    def _load(self):
        if self._engine is None:
            try:
                import onnxruntime  # noqa: F401 — must be loaded before winrt (otherwise the process crashes)
                from rapidocr import RapidOCR
            except ImportError as e:
                raise OcrUnavailable(tr("RapidOCR is not installed: pip install rapidocr onnxruntime")) from e
            self._engine = RapidOCR(params=self._params)
        return self._engine

    def read(self, image: np.ndarray) -> List[TextResult]:
        if image.size == 0:
            return []
        with self._lock:   # the onnx session is not thread-safe; also no per-call params should be passed
            out = self._load()(image)          # (they change the state permanently)
        if out.boxes is None or out.txts is None:
            return []
        results = []
        for box, text, score in zip(out.boxes, out.txts, out.scores):
            if score < self._min_score or not text.strip():
                continue
            xs, ys = box[:, 0], box[:, 1]
            x, y = int(xs.min()), int(ys.min())
            results.append(TextResult(text.strip(), Rect(x, y, int(xs.max()) - x, int(ys.max()) - y),
                                      float(score)))
        return results


# --------------------------------------------------------------------------- #
# Windows.Media.Ocr
# --------------------------------------------------------------------------- #
class WindowsOcrReader(TextReader):
    """language: a tag like "en-US"; if None, the user profile languages (one of the installed OCR
    language packs) are used. Languages with a Latin alphabet read English text as well."""

    name = "windows"

    def __init__(self, language: Optional[str] = None):
        self._language = language
        self._engine = None
        self._lock = threading.Lock()

    def _load(self):
        if self._engine is None:
            try:
                from winrt.windows.globalization import Language
                from winrt.windows.media.ocr import OcrEngine
            except ImportError as e:
                raise OcrUnavailable(
                    tr("Windows OCR is not installed: {command}",
                       command="pip install winrt-Windows.Media.Ocr "
                               "winrt-Windows.Graphics.Imaging winrt-Windows.Storage.Streams "
                               "winrt-Windows.Globalization winrt-Windows.Foundation "
                               "winrt-Windows.Foundation.Collections")) from e
            engine = (OcrEngine.try_create_from_language(Language(self._language)) if self._language
                      else OcrEngine.try_create_from_user_profile_languages())
            if engine is None:
                langs = [l.language_tag for l in OcrEngine.available_recognizer_languages]
                raise OcrUnavailable(tr("Windows OCR language is not installed: {language} (installed: {installed})",
                                        language=self._language, installed=langs))
            self._engine = engine
        return self._engine

    def read(self, image: np.ndarray) -> List[TextResult]:
        if image.size == 0:
            return []
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.storage.streams import DataWriter

        with self._lock:
            engine = self._load()
            bgra = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
            writer = DataWriter()
            writer.write_bytes(bgra.tobytes())
            bitmap = SoftwareBitmap.create_copy_from_buffer(
                writer.detach_buffer(), BitmapPixelFormat.BGRA8, bgra.shape[1], bgra.shape[0])

            async def recognize():
                return await engine.recognize_async(bitmap)

            result = asyncio.run(recognize())

        lines = []
        for line in result.lines:
            boxes = [w.bounding_rect for w in line.words]
            if not boxes or not line.text.strip():
                continue
            x1, y1 = min(b.x for b in boxes), min(b.y for b in boxes)
            x2, y2 = max(b.x + b.width for b in boxes), max(b.y + b.height for b in boxes)
            lines.append(TextResult(line.text.strip(), Rect(int(x1), int(y1), int(x2 - x1), int(y2 - y1)), 1.0))
        return lines


# --------------------------------------------------------------------------- #
# Preprocessing
# --------------------------------------------------------------------------- #
class ScaledReader(TextReader):
    """Decorator: upscales the image `scale` times (for small texts), optionally converts it to
    gray/inverts it (light text on a dark background), maps the inner reader's boxes back to the
    original scale."""

    def __init__(self, inner: TextReader, scale: float = 2.0, grayscale: bool = False, invert: bool = False):
        self._inner = inner
        self._scale = scale
        self._grayscale = grayscale
        self._invert = invert
        self.name = inner.name

    def read(self, image: np.ndarray) -> List[TextResult]:
        if image.size == 0:
            return []
        img = image
        if self._scale != 1.0:
            img = cv2.resize(img, None, fx=self._scale, fy=self._scale, interpolation=cv2.INTER_CUBIC)
        if self._grayscale or self._invert:
            img = cv2.cvtColor(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        if self._invert:
            img = 255 - img
        s = self._scale
        return [TextResult(r.text, Rect(int(r.rect.x / s), int(r.rect.y / s),
                                        int(round(r.rect.w / s)), int(round(r.rect.h / s))), r.score)
                for r in self._inner.read(img)]


class UnavailableReader(TextReader):
    """Used when there is no engine at all; tells what to install on first use."""

    name = "none"

    def read(self, image: np.ndarray) -> List[TextResult]:
        raise OcrUnavailable(tr("No OCR engine. Recommended: pip install rapidocr onnxruntime"))


# --------------------------------------------------------------------------- #
def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def available_backends() -> List[str]:
    found = []
    if _installed("rapidocr") and _installed("onnxruntime"):
        found.append("rapid")
    if _installed("winrt.windows.media.ocr"):
        found.append("windows")
    return found


def create_text_reader(backend: str = "auto") -> TextReader:
    """backend: "auto" (the most accurate of the installed ones), "rapid" or "windows".
    Packages are checked without importing; the engine is loaded on the first read."""
    if backend == "auto":
        found = available_backends()
        backend = found[0] if found else "none"
    if backend == "rapid":
        return RapidOcrReader()
    if backend == "windows":
        return ScaledReader(WindowsOcrReader(), scale=2.0)
    if backend == "none":
        return UnavailableReader()
    raise ValueError(tr("Unknown OCR engine: {backend!r} (auto, rapid, windows)", backend=backend))
