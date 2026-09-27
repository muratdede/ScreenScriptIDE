"""Problem summary in the top right corner of the code area (the inspection widget in IntelliJ).

Placed right below the tabs, over the editor content (without background). Shows the error/warning
counts of the open file (a green check if there are none); the tooltip contains the project totals.
Emits `clicked` when clicked (the main window opens the Problems panel).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QTabWidget, QWidget

from ..core.context import EditorContext
from ..core.diagnostics import counts
from ..i18n import ntr, tr
from . import icons
from .theme import THEME


class ProblemsBadge(QWidget):
    clicked = Signal()

    MARGIN = 6   # from the viewport edge (left of the vertical scroll bar)

    def __init__(self, ctx: EditorContext, tabs: QTabWidget):
        super().__init__(tabs)
        self._ctx = ctx
        self._tabs = tabs
        self._path = ""
        self._watched = None   # the editor viewport whose position is tracked
        self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        self._parts = {}
        for kind, color in (("error", THEME.error), ("warning", THEME.warning), ("check", THEME.run_green)):
            icon_label = QLabel()
            icon_label.setPixmap(icons.icon(kind, color.name()).pixmap(14, 14))
            count = QLabel()
            count.setStyleSheet(f"color: {THEME.text.name()}; background: transparent;")
            lay.addWidget(icon_label)
            lay.addWidget(count)
            if kind != "check":
                lay.addSpacing(5)
            self._parts[kind] = (icon_label, count)

        ctx.diagnostics.changed.connect(lambda _p: self.refresh())   # the tooltip contains the project total too
        ctx.diagnostics.reset.connect(self.refresh)
        ctx.bus.active_file_changed.connect(self.set_path)
        tabs.currentChanged.connect(lambda _i: self._follow_current())
        tabs.installEventFilter(self)
        self.refresh()

    # ---- content --------------------------------------------------------- #
    def set_path(self, path: str) -> None:
        self._path = path or ""
        self.refresh()

    def refresh(self) -> None:
        store = self._ctx.diagnostics
        is_script = self._path.endswith(".py")
        errors, warnings = counts(store.get(self._path)) if is_script else (0, 0)
        self._show("error", errors)
        self._show("warning", warnings)
        self._show("check", 1 if is_script and not errors and not warnings else 0, text="")
        total_e, total_w = store.totals()
        self.setToolTip("\n".join([
            tr("This file: {errors}, {warnings}", errors=ntr("{n} error", "{n} errors", errors),
               warnings=ntr("{n} warning", "{n} warnings", warnings)),
            tr("Project: {errors}, {warnings}", errors=ntr("{n} error", "{n} errors", total_e),
               warnings=ntr("{n} warning", "{n} warnings", total_w)),
            tr("Click: Problems panel (Alt+6)   ·   F8 / Shift+F8: next / previous problem")]))
        self.setVisible(is_script and self._viewport() is not None)
        self.adjustSize()
        self.reposition()

    def _show(self, kind: str, n: int, text=None) -> None:
        icon_label, count = self._parts[kind]
        icon_label.setVisible(n > 0)
        count.setVisible(n > 0 and text != "")
        count.setText(str(n) if text is None else text)

    # ---- position -------------------------------------------------------- #
    def _viewport(self):
        w = self._tabs.currentWidget()
        vp = getattr(w, "viewport", None)
        return vp() if callable(vp) else None

    def _follow_current(self) -> None:
        vp = self._viewport()
        if vp is not self._watched:
            if self._watched is not None:
                try:
                    self._watched.removeEventFilter(self)
                except RuntimeError:   # the editor was closed and deleted
                    pass
            self._watched = vp
            if vp is not None:
                vp.installEventFilter(self)   # the viewport moves down when the find bar opens
        self.refresh()

    def reposition(self) -> None:
        vp = self._viewport()
        if vp is None:
            return
        top_right = vp.mapTo(self._tabs, QPoint(vp.width(), 0))
        self.move(top_right.x() - self.width() - self.MARGIN - 4, top_right.y() + self.MARGIN)
        self.raise_()

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Resize, QEvent.Move, QEvent.Show):
            try:
                self.reposition()
            except RuntimeError:   # on shutdown the editor may have been deleted first
                pass
        return False

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)
