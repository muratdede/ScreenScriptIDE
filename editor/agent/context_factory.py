"""ProjectConfig -> screenscript.ScriptContext. Ana kodu sadece public API'si üzerinden kullanır."""

from __future__ import annotations

import os

from adb_capture import ScreencapFormat
from screenscript import (ContextBuilder, HumanizedInputDriver, MultiScaleMatcher, ScriptContext,
                          Settings, TelegramNotifier, TemplateMatcher)

from ..core.project import Project, ProjectConfig


class ContextFactory:
    """Proje ayarlarından script bağlamı kurar. Farklı bir kurulum için alt sınıfta
    create_builder/configure_* metotlarını ezmeniz yeterli."""

    def __init__(self, project: Project):
        self._project = project

    def build(self) -> ScriptContext:
        builder = self.create_builder()
        self.configure_matcher(builder)
        self.configure_input(builder)
        self.configure_notifier(builder)
        return builder.build()

    def create_settings(self) -> Settings:
        c = self._project.config
        return Settings(min_similarity=c.similarity, auto_wait_timeout=c.timeout,
                        scan_rate=c.scan_rate, save_on_fail=c.save_on_fail)

    def create_builder(self) -> ContextBuilder:
        c = self._project.config
        return ContextBuilder(host=c.host, port=int(c.port), adb_path=c.adb_path or None,
                              screencap_format=ScreencapFormat(c.screencap_format),
                              background=c.background, dry_run=c.dry_run,
                              settings=self.create_settings(),
                              image_dirs=self._project.image_search_dirs())

    def configure_matcher(self, builder: ContextBuilder) -> None:
        builder.with_matcher(self.create_matcher(self._project.config))

    @staticmethod
    def create_matcher(config) -> MultiScaleMatcher:
        """Projeler her zaman çoklu ölçekte tarar. Editördeki eşleşme testi de bunu kullanır,
        böylece test sonucu ile script sonucu birebir aynıdır."""
        return MultiScaleMatcher(TemplateMatcher(grayscale=config.grayscale),
                                 scales=ProjectConfig.normalize_scales(config.scales))

    def configure_input(self, builder: ContextBuilder) -> None:
        if self._project.config.humanize:
            builder.with_input_decorator(HumanizedInputDriver)

    def configure_notifier(self, builder: ContextBuilder) -> None:
        """Token ve kullanıcı adı varsa push_notification Telegram'a gider; yoksa script
        push_notification çağırdığında NotificationError alır."""
        c = self._project.config
        token = c.telegram_token.strip() or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if token and c.telegram_username.strip():
            builder.with_notifier(TelegramNotifier(token, c.telegram_username,
                                                   cache_path=self._project.telegram_cache_path))
