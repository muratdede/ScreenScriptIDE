"""Base of all built-in windows (views).

Adding a new view:
    class MyView(DockView):
        view_id = "my_view"; title = N_("Mine"); icon_name = "file"; default_area = Qt.RightDockWidgetArea
        def build(self): return QLabel(tr("hello"))
and pass view_classes=[..., MyView] to MainWindow (or add it to views.DEFAULT_VIEWS).

The class attribute `title` is an English source string marked with N_(); instances hold the translation.

IntelliJ style: every view has an icon on the side stripe; only one view of the same group (tab_group)
is visible at a time. View specific buttons are added to the title bar with title_actions().
"""

from __future__ import annotations

import sys
from typing import Dict, List

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QAction, QColor, QPainter, QPen
from PySide6.QtWidgets import QDockWidget, QHBoxLayout, QLabel, QToolButton, QWidget

from ...core.context import EditorContext
from ...i18n import tr
from .. import icons

_FLOATING_FRAME_WIDTH = 2
_FLOATING_FRAME_COLOR = QColor("#6f737a")
_FLOATING_FRAME = f"QDockWidget {{ border: {_FLOATING_FRAME_WIDTH}px solid {_FLOATING_FRAME_COLOR.name()}; }}"


class _TitleBar(QWidget):
    def __init__(self, dock: "DockView"):
        super().__init__(dock)
        self.setObjectName("dock_titlebar")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setFixedHeight(34)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 6, 0)
        layout.setSpacing(2)
        self._label = QLabel(dock.windowTitle())
        self._label.setObjectName("dock_title")
        layout.addWidget(self._label)
        layout.addStretch(1)
        self._layout = layout
        dock.windowTitleChanged.connect(self._label.setText)

        self.pin_btn = self.add_button(icons.icon("pin", "#9da0a8"),
                                       tr("Always on top (stays open even when the main window is minimized)"), dock.set_pinned)
        self.pin_btn.setCheckable(True)
        self.pin_btn.setVisible(False)
        float_btn = self.add_button(icons.icon("float", "#9da0a8"), tr("Open in a separate window / dock back"),
                                    lambda: dock.setFloating(not dock.isFloating()))
        float_btn.setVisible(bool(dock.features() & QDockWidget.DockWidgetFloatable))
        self.add_button(icons.icon("minimize", "#9da0a8"), tr("Hide"), dock.hide)

    def add_button(self, icon, tip, slot, index: int = -1) -> QToolButton:
        b = QToolButton()
        b.setIcon(icon)
        b.setToolTip(tip)
        b.setAutoRaise(True)
        b.clicked.connect(slot)
        if index < 0:
            self._layout.addWidget(b)
        else:
            self._layout.insertWidget(index, b)
        return b

    def add_action(self, action: QAction, index: int) -> QToolButton:
        b = QToolButton()
        b.setDefaultAction(action)
        b.setAutoRaise(True)
        self._layout.insertWidget(index, b)
        return b

    def mouseDoubleClickEvent(self, e):
        dock = self.parentWidget()
        if isinstance(dock, QDockWidget) and dock.features() & QDockWidget.DockWidgetFloatable:
            dock.setFloating(not dock.isFloating())


def _set_window_owner(widget: QWidget, owner: QWidget = None) -> None:
    """On Windows, owned windows are hidden when their owner is minimized; removing the owner keeps
    the floating window open independently of the main window."""
    if sys.platform != "win32":
        return
    import ctypes
    GWLP_HWNDPARENT = -8
    ctypes.windll.user32.SetWindowLongPtrW(int(widget.winId()), GWLP_HWNDPARENT,
                                           int(owner.winId()) if owner is not None else 0)


class DockView(QDockWidget):
    view_id: str = ""
    title: str = ""             # English source text (N_); translated per instance
    icon_name: str = "file"      # icon name in icons.py (side stripe)
    default_area: Qt.DockWidgetArea = Qt.LeftDockWidgetArea
    tab_group: str = ""          # only one view of the same group is visible at a time
    stripe_bottom: bool = False  # placed in the bottom section of the left stripe (like Run/Terminal in IntelliJ)
    shortcut: str = ""           # shortcut in the view menu (e.g. "Alt+1")
    pinnable: bool = False       # show the "always on top" pin while floating

    def __init__(self, ctx: EditorContext, parent=None):
        self.title = tr(type(self).title)   # instance attribute: the translated title
        super().__init__(self.title, parent)
        if not self.view_id:
            raise TypeError(f"{type(self).__name__}.view_id is not defined")
        self.setObjectName(self.view_id)
        self.ctx = ctx
        self._pinned = False
        self.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetFloatable)
        self.topLevelChanged.connect(self._update_floating_frame)
        self._title_bar = _TitleBar(self)
        self.setTitleBarWidget(self._title_bar)
        self.setWidget(self.build())
        # view specific title buttons (before the hide/float buttons)
        insert_at = self._title_bar._layout.count() - 2
        for action in self.title_actions():
            self._title_bar.add_action(action, insert_at)
            insert_at += 1

    def _update_floating_frame(self, floating: bool) -> None:
        # the floating window's frame is thick and light so it stands out from the background
        # (the stylesheet only reserves the frame width; paintEvent draws the line)
        self.setStyleSheet(_FLOATING_FRAME if floating else "")
        self._title_bar.pin_btn.setVisible(floating and self.pinnable)
        if not floating and self._pinned:
            # QDockWidget resets the window flags anyway when docked
            self._pinned = False
            self._title_bar.pin_btn.setChecked(False)
            self.pinned_changed(False)

    @property
    def pinned(self) -> bool:
        return self._pinned

    def set_pinned(self, on: bool) -> None:
        """Keeps the floating window always on top; it stays open even when the main window is minimized."""
        on = bool(on) and self.isFloating()
        self._title_bar.pin_btn.setChecked(on)
        if on == self._pinned:
            return
        self._pinned = on
        geometry = self.geometry()
        self.setWindowFlag(Qt.WindowStaysOnTopHint, on)   # hides the window; show it again
        self.setGeometry(geometry)
        self.show()
        _set_window_owner(self, None if on else self.parentWidget())
        self.raise_()
        self.pinned_changed(on)

    def pinned_changed(self, pinned: bool) -> None:
        """Called when the pin is toggled; subclasses may override it to switch to a compact look."""

    def paintEvent(self, e):
        super().paintEvent(e)
        if not self.isFloating():
            return
        p = QPainter(self)
        pen = QPen(_FLOATING_FRAME_COLOR, _FLOATING_FRAME_WIDTH)
        pen.setJoinStyle(Qt.MiterJoin)
        p.setPen(pen)
        half = _FLOATING_FRAME_WIDTH / 2
        p.drawRect(QRectF(self.rect()).adjusted(half, half, -half, -half))

    def build(self) -> QWidget:
        """Builds the content of the view. Subclasses must override it."""
        raise NotImplementedError

    def title_actions(self) -> List[QAction]:
        """Actions shown in the title bar (e.g. clear, refresh)."""
        return []

    def attach_actions(self, actions: Dict[str, QAction]) -> None:
        """Called by MainWindow after it has created the global actions (run, debug, step_over, ...)."""

    def make_action(self, icon_name: str, text: str, slot, color: str = icons.DEFAULT_COLOR) -> QAction:
        a = QAction(icons.icon(icon_name, color), text, self)
        a.setToolTip(text)
        a.triggered.connect(slot)
        return a

    def reveal(self) -> None:
        win = self.parentWidget()
        if hasattr(win, "show_view"):
            win.show_view(self)
        else:
            self.show()
            self.raise_()
