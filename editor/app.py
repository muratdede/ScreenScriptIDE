"""Uygulama kurulumu."""

from __future__ import annotations

import os
import sys
import threading
from typing import Optional

from PySide6.QtWidgets import QApplication

from .core.context import EditorContext
from .ui.main_window import MainWindow
from .ui.theme import THEME, Theme

INSTALL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class EditorApplication:
    """QApplication + EditorContext + MainWindow. Tema ya da view listesi değiştirilebilir."""

    def __init__(self, argv=None, theme: Theme = THEME, view_classes=None):
        self.qt = QApplication.instance() or QApplication(list(argv or sys.argv))
        self.qt.setApplicationName("ScreenScript IDE")
        theme.apply(self.qt)
        self.ctx = EditorContext(INSTALL_ROOT)
        self.window = MainWindow(self.ctx, view_classes)
        self._warm_up_completion()

    def _warm_up_completion(self) -> None:
        """jedi'nin ilk çağrısı yavaştır; arka planda ısıt ki ilk Ctrl+Space beklemesin."""
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
