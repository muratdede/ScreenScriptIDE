"""SikuliX style visual script API (over ADB)."""

from .engine import Engine, SearchResult
from .errors import FindFailed, ImageNotFound, NotificationError, ScriptError, TextNotFound
from .geometry import Location, Rect
from .input import (AdbInputDriver, HumanizedInputDriver, InputDriver, Key)
from .matching import Matcher, MatchResult, MultiScaleMatcher, TemplateMatcher
from .notify import Notifier, NullNotifier, TelegramNotifier
from .ocr import (OcrUnavailable, RapidOcrReader, ScaledReader, TextReader, TextResult,
                  WindowsOcrReader, available_backends, create_text_reader)
from .pattern import ImageLibrary, ImagePath, Pattern
from .providers import BackgroundProvider, ImageProvider, OnDemandProvider
from .region import Match, Region, Screen, TextMatch
from .runtime import ContextBuilder, ScriptContext, ScriptRunner
from .settings import Settings

__all__ = [
    "Engine", "SearchResult", "FindFailed", "ImageNotFound", "ScriptError", "NotificationError",
    "Notifier", "NullNotifier", "TelegramNotifier",
    "Location", "Rect", "InputDriver", "AdbInputDriver",
    "HumanizedInputDriver", "Key", "Matcher", "MatchResult", "TemplateMatcher",
    "MultiScaleMatcher", "Pattern", "ImagePath", "ImageLibrary", "ImageProvider",
    "OnDemandProvider", "BackgroundProvider", "Region", "Match", "Screen",
    "ContextBuilder", "ScriptContext", "ScriptRunner", "Settings",
    "TextReader", "TextResult", "TextMatch", "TextNotFound", "OcrUnavailable",
    "RapidOcrReader", "WindowsOcrReader", "ScaledReader", "create_text_reader", "available_backends",
]
