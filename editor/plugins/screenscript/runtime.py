"""Script execution environment.

ScriptContext : Holds Engine + Screen, produces the global names given to the script.
ContextBuilder: Builds a ScriptContext with default components; every component can be replaced.
ScriptRunner  : Runs a .py script file with these globals.
"""

from __future__ import annotations

import logging
import os
import runpy
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from ...i18n import tr
from ..adb_capture import AdbDevice, FrameSource, ScreencapFormat, create_source

from .engine import Engine
from .errors import FindFailed, ImageNotFound, NotificationError, ScriptError, TextNotFound
from .geometry import Location, Rect
from .input import (AdbInputDriver, HumanizedInputDriver, InputDriver, Key)
from .matching import Matcher, MultiScaleMatcher, TemplateMatcher
from .notify import Notifier, NullNotifier
from .ocr import OcrUnavailable, TextReader
from .pattern import ImageLibrary, ImagePath, Pattern
from .providers import BackgroundProvider, ImageProvider, OnDemandProvider
from .region import Match, Region, Screen, TextMatch
from .settings import Settings

log = logging.getLogger("script")


class ScriptContext:
    """A script session. Opens/closes resources and provides the API namespace."""

    def __init__(self, engine: Engine, notifier: Optional[Notifier] = None):
        self.engine = engine
        self.notifier: Notifier = notifier or NullNotifier()
        self.screen: Optional[Screen] = None
        self._extra: Dict[str, Any] = {}

    def open(self) -> "ScriptContext":
        self.engine.provider.open()
        self.screen = Screen(self.engine)
        log.info(tr("Screen: %dx%d"), self.screen.w, self.screen.h)
        return self

    def close(self) -> None:
        self.engine.provider.close()

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    def register(self, name: str, obj: Any) -> None:
        """Adds an extra global name for scripts (e.g. your own helper functions)."""
        self._extra[name] = obj

    def namespace(self, args: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Global names visible to the script."""
        s = self.screen
        if s is None:
            raise ScriptError(tr("open() must be called first"))
        ns: Dict[str, Any] = {
            # objects
            "SCREEN": s, "SETTINGS": self.engine.settings, "ARGS": dict(args or {}),
            # types
            "Pattern": Pattern, "Region": lambda x, y, w, h: Region(Rect(x, y, w, h), self.engine),
            "Location": Location, "Match": Match, "TextMatch": TextMatch, "Key": Key,
            "FindFailed": FindFailed, "ImageNotFound": ImageNotFound,
            "TextNotFound": TextNotFound, "OcrUnavailable": OcrUnavailable,
            "NotificationError": NotificationError,
            # SCREEN shortcuts (global functions like in SikuliX)
            "find": s.find, "find_all": s.find_all, "exists": s.exists, "wait": s.wait,
            "wait_vanish": s.wait_vanish, "best_score": s.best_score,
            "click": s.click, "long_press": s.long_press, "drag_drop": s.drag_drop,
            "swipe": s.swipe, "type_text": s.type_text, "key": s.key,
            "highlight": s.highlight, "capture": s.capture,
            # text (OCR)
            "read_text": s.text, "read_lines": s.read_lines, "find_text": s.find_text,
            "exists_text": s.exists_text, "wait_text": s.wait_text,
            # general
            "sleep": time.sleep, "log": log.info,
            "add_image_path": self.engine.library.image_path.add,
            "push_notification": self.notifier.send,
        }
        ns.update(self._extra)
        return ns


@dataclass
class ContextBuilder:
    """Builds the default components. Pass whatever you want to replace with with_*:

        ctx = (ContextBuilder(port=5555)
               .with_matcher(MultiScaleMatcher(TemplateMatcher()))
               .with_input_decorator(lambda d: HumanizedInputDriver(d, jitter_px=5))
               .build())
    """

    host: str = "127.0.0.1"
    port: int = 5555
    adb_path: Optional[str] = None
    screencap_format: ScreencapFormat = ScreencapFormat.STREAM
    background: bool = False
    background_interval: float = 0.3
    tap_ms: int = 0              # >0: taps are held down this many ms (natural touch)
    settings: Settings = field(default_factory=Settings)
    image_dirs: list = field(default_factory=list)

    _source: Optional[FrameSource] = None
    _provider: Optional[ImageProvider] = None
    _matcher: Optional[Matcher] = None
    _input: Optional[InputDriver] = None
    _input_decorators: list = field(default_factory=list)
    _notifier: Optional[Notifier] = None
    _text_reader: Optional[TextReader] = None

    def with_source(self, source: FrameSource) -> "ContextBuilder":
        self._source = source
        return self

    def with_provider(self, provider: ImageProvider) -> "ContextBuilder":
        self._provider = provider
        return self

    def with_matcher(self, matcher: Matcher) -> "ContextBuilder":
        self._matcher = matcher
        return self

    def with_input(self, driver: InputDriver) -> "ContextBuilder":
        self._input = driver
        return self

    def with_input_decorator(self, wrap: Callable[[InputDriver], InputDriver]) -> "ContextBuilder":
        self._input_decorators.append(wrap)
        return self

    def with_notifier(self, notifier: Notifier) -> "ContextBuilder":
        self._notifier = notifier
        return self

    def with_text_reader(self, reader: TextReader) -> "ContextBuilder":
        """Default: create_text_reader() based on Settings.ocr_backend."""
        self._text_reader = reader
        return self

    def build(self) -> ScriptContext:
        device: Optional[AdbDevice] = None

        def get_device() -> AdbDevice:
            nonlocal device
            if device is None:
                device = AdbDevice.from_host_port(self.host, self.port, adb_path=self.adb_path)
            return device

        source = self._source or create_source(get_device(), self.screencap_format)
        provider = self._provider or (
            BackgroundProvider(source, self.background_interval) if self.background
            else OnDemandProvider(source))

        if self._input is not None:
            driver = self._input
        else:
            driver = AdbInputDriver(get_device(), press_ms=self.tap_ms)
        for wrap in self._input_decorators:
            driver = wrap(driver)

        engine = Engine(provider=provider,
                        matcher=self._matcher or TemplateMatcher(),
                        input_driver=driver,
                        library=ImageLibrary(ImagePath(self.image_dirs)),
                        settings=self.settings,
                        text_reader=self._text_reader)
        return ScriptContext(engine, self._notifier)


class ScriptRunner:
    """Runs a script file with the globals of a ScriptContext."""

    def __init__(self, context: ScriptContext):
        self._ctx = context

    def run(self, script_path: str, args: Optional[Dict[str, str]] = None) -> int:
        script_path = os.path.abspath(script_path)
        if not os.path.isfile(script_path):
            log.error(tr("Script not found: %s"), script_path)
            return 2

        # images next to the script and in the images/ folder are found automatically
        script_dir = os.path.dirname(script_path)
        paths = self._ctx.engine.library.image_path
        for d in (script_dir, os.path.join(script_dir, "images"),
                  os.path.join(os.path.dirname(script_dir), "images")):
            if os.path.isdir(d):
                paths.add(d)

        log.info(tr("Script starting: %s"), script_path)
        started = time.monotonic()
        old_argv, old_path = sys.argv, list(sys.path)
        try:
            with self._ctx:
                sys.argv = [script_path]
                # modules/packages next to the script can be imported (like python script.py)
                sys.path.insert(0, script_dir)
                runpy.run_path(script_path, init_globals=self._ctx.namespace(args), run_name="__main__")
            log.info(tr("Script finished (%.1f s)"), time.monotonic() - started)
            return 0
        except KeyboardInterrupt:
            log.warning(tr("Stopped by the user"))
            return 130
        except ScriptError as e:
            log.error("%s: %s", type(e).__name__, e)
            return 1
        except SystemExit as e:
            if isinstance(e.code, int) or e.code is None:
                return e.code or 0
            log.error("%s", e.code)
            return 1
        except Exception:  # noqa: BLE001 — script error; show the traceback
            log.error(tr("Script error:\n%s"), traceback.format_exc())
            return 1
        finally:
            sys.argv = old_argv
            sys.path[:] = old_path


__all__ = ["ScriptContext", "ContextBuilder", "ScriptRunner",
           "MultiScaleMatcher", "TemplateMatcher", "HumanizedInputDriver"]
