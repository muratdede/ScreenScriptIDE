"""SikuliX tarzı görsel script API'si (ADB üzerinden)."""

from .engine import Engine, SearchResult
from .errors import FindFailed, ImageNotFound, NotificationError, ScriptError
from .geometry import Location, Rect
from .input import (AdbInputDriver, DryRunInputDriver, HumanizedInputDriver, InputDriver, Key)
from .matching import Matcher, MatchResult, MultiScaleMatcher, TemplateMatcher
from .notify import Notifier, NullNotifier, TelegramNotifier
from .pattern import ImageLibrary, ImagePath, Pattern
from .providers import BackgroundProvider, ImageProvider, OnDemandProvider
from .region import Match, Region, Screen
from .runtime import ContextBuilder, ScriptContext, ScriptRunner
from .settings import Settings

__all__ = [
    "Engine", "SearchResult", "FindFailed", "ImageNotFound", "ScriptError", "NotificationError",
    "Notifier", "NullNotifier", "TelegramNotifier",
    "Location", "Rect", "InputDriver", "AdbInputDriver", "DryRunInputDriver",
    "HumanizedInputDriver", "Key", "Matcher", "MatchResult", "TemplateMatcher",
    "MultiScaleMatcher", "Pattern", "ImagePath", "ImageLibrary", "ImageProvider",
    "OnDemandProvider", "BackgroundProvider", "Region", "Match", "Screen",
    "ContextBuilder", "ScriptContext", "ScriptRunner", "Settings",
]
