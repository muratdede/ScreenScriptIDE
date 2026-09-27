"""Application setup."""

from __future__ import annotations

import os
import sys
import threading
from typing import Optional

from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
from PySide6.QtWidgets import QApplication

from . import i18n
from .core.app_settings import AppConfig
from .core.context import EditorContext
from .ui.main_window import MainWindow
from .ui.theme import THEME, Theme

INSTALL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def install_qt_translations(app: QApplication, language: str) -> None:
    """Translations of Qt's own texts (standard dialog buttons, context menus of text fields ...)."""
    QLocale.setDefault(QLocale(language))
    if language == i18n.DEFAULT_LANGUAGE:
        return
    path = QLibraryInfo.path(QLibraryInfo.TranslationsPath)
    for name in ("qtbase", "qt"):
        translator = QTranslator(app)
        if translator.load(f"{name}_{language}", path):
            app.installTranslator(translator)
            break


class EditorApplication:
    """QApplication + EditorContext + MainWindow. The theme or the view list can be changed."""

    def __init__(self, argv=None, theme: Theme = THEME, view_classes=None):
        # the language is fixed for the lifetime of the process; changing it requires a restart
        language = i18n.set_language(AppConfig.load().language)
        self.qt = QApplication.instance() or QApplication(list(argv or sys.argv))
        self.qt.setApplicationName("ScreenScript IDE")
        install_qt_translations(self.qt, language)
        theme.apply(self.qt)
        self.ctx = EditorContext(INSTALL_ROOT)
        self.window = MainWindow(self.ctx, view_classes)
        self._warm_up_completion()

    def _warm_up_completion(self) -> None:
        """jedi's first call is slow; warm it up in the background so the first Ctrl+Space doesn't wait."""
        from .core.completion import CodePosition

        def work():
            try:
                self.ctx.completion.complete(CodePosition("SCREEN.", 1, 7))
            except Exception:  # noqa: BLE001
                pass
        threading.Thread(target=work, daemon=True).start()

    def open_initial_project(self, path: Optional[str]) -> None:
        if path:
            self.window.open_project(path)
            return
        for recent in self.ctx.recent_projects():
            if os.path.isdir(recent):
                self.window.open_project(recent)
                return

    def run(self, project: Optional[str] = None) -> int:
        self.window.show()
        self.open_initial_project(project)
        return self.qt.exec()


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    project = argv[1] if len(argv) > 1 and not argv[1].startswith("-") else None
    return EditorApplication(argv).run(project)
