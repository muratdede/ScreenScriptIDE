"""ProjectConfig + AppConfig (device connection, notifications) -> screenscript.ScriptContext.
Uses the main code only through its public API."""

from __future__ import annotations

from typing import Optional

from ..plugins.adb_capture import ScreencapFormat
from ..plugins.screenscript import (ContextBuilder, HumanizedInputDriver, MultiScaleMatcher, PacedInputDriver,
                          ScriptContext, Settings, TelegramNotifier, TemplateMatcher)

from ..core.app_settings import AppConfig, telegram_cache_path
from ..core.project import Project, ProjectConfig


class ContextFactory:
    """Builds the script context from the project settings. For a different setup, override
    create_builder/configure_* in a subclass."""

    def __init__(self, project: Project, app: Optional[AppConfig] = None):
        self._project = project
        self._app = app or AppConfig.load()   # %LOCALAPPDATA%\ScreenScript\settings.json

    def build(self) -> ScriptContext:
        builder = self.create_builder()
        self.configure_matcher(builder)
        self.configure_input(builder)
        self.configure_notifier(builder)
        return builder.build()

    def create_settings(self) -> Settings:
        c = self._project.config
        return Settings(min_similarity=c.similarity, auto_wait_timeout=c.timeout,
                        scan_rate=c.scan_rate, save_on_fail=c.save_on_fail, ocr_backend=c.ocr,
                        ocr_gpu=self._app.ocr_gpu)

    def create_builder(self) -> ContextBuilder:
        c, app = self._project.config, self._app
        return ContextBuilder(host=app.host, port=int(app.port), adb_path=app.adb_path or None,
                              screencap_format=ScreencapFormat(app.screencap_format),
                              background=app.background,
                              tap_ms=int(c.tap_ms),
                              freeze_alert_seconds=float(app.freeze_seconds) if app.freeze_alert else 0.0,
                              settings=self.create_settings(),
                              image_dirs=self._project.image_search_dirs())

    def configure_matcher(self, builder: ContextBuilder) -> None:
        builder.with_matcher(self.create_matcher(self._project.config))

    @staticmethod
    def create_matcher(config) -> MultiScaleMatcher:
        """Projects always scan at multiple scales. The match test in the editor uses this too,
        so the test result is identical to the script result."""
        return MultiScaleMatcher(TemplateMatcher(grayscale=config.grayscale),
                                 scales=ProjectConfig.normalize_scales(config.scales))

    def configure_input(self, builder: ContextBuilder) -> None:
        c = self._project.config
        low, high = sorted((int(c.input_gap_min_ms), int(c.input_gap_max_ms)))
        jitter = max(0, int(c.input_jitter_ms))
        if high > 0 or jitter > 0:
            builder.with_input_decorator(
                lambda d: PacedInputDriver(d, (low / 1000, high / 1000), jitter / 1000))
        if c.humanize:
            builder.with_input_decorator(HumanizedInputDriver)

    def configure_notifier(self, builder: ContextBuilder) -> None:
        """If a token and username are set, push_notification goes to Telegram; otherwise the script
        gets a NotificationError when it calls push_notification."""
        app = self._app
        token = app.effective_telegram_token()
        if token and app.telegram_username.strip():
            builder.with_notifier(TelegramNotifier(token, app.telegram_username,
                                                   cache_path=telegram_cache_path()))
