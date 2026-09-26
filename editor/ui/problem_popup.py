"""Sorunun üzerine gelince açılan pencere (IntelliJ'deki inceleme açılır penceresi).

Her sorun ayrı satırda (ayırıcı çizgilerle) gösterilir; altta hızlı düzeltme bağlantıları yer alır:
ilk düzeltme doğrudan (Alt+Shift+Enter), diğerleri "Diğer eylemler…" menüsünde (Alt+Enter).
QToolTip'in aksine fare üzerine gelince kapanmaz; bağlantılara tıklanabilir.
"""

from __future__ import annotations

import html
from typing import Callable, List, Optional, Sequence, Tuple

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFontMetrics, QGuiApplication, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QToolButton, QVBoxLayout, QWidget

from ..core.diagnostics import Diagnostic
from . import icons
from .theme import THEME

Fix = Tuple[str, Callable[[], None]]

_LINK = "#548af7"
_BORDER = QColor("#43454a")
_SEPARATOR = "#393b40"


def _separator() -> QFrame:
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {_SEPARATOR}; border: none;")
    return line


class ProblemPopup(QWidget):
    MAX_WIDTH = 560
    RADIUS = 8

    def __init__(self, editor: QWidget):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus)
        self._editor = editor
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._fixes: List[Fix] = []
        self._keep_alive: Optional[Callable[[QPoint], bool]] = None   # fare hâlâ sorunun üzerinde mi?
        self._menu_open = False
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(1, 1, 1, 1)
        self._layout.setSpacing(0)
        self._watch = QTimer(self, interval=150)
        self._watch.timeout.connect(self._check_mouse)

    # ---- içerik ---------------------------------------------------------- #
    def show_for(self, problems: Sequence[Diagnostic], fixes: Sequence[Fix], anchor: QRect,
                 keep_alive: Optional[Callable[[QPoint], bool]] = None) -> None:
        """anchor: sorunun ekrandaki (global) dikdörtgeni; pencere altına (sığmazsa üstüne) açılır.
        keep_alive verilirse fare pencereden ve sorundan çıkınca kapanır; verilmezse (klavyeyle
        açıldıysa) tuşa basılana kadar açık kalır."""
        self._fixes = list(fixes)
        self._keep_alive = keep_alive
        self._rebuild(problems)
        self.adjustSize()
        self._place(anchor)
        self.show()
        self.raise_()
        if keep_alive:
            self._watch.start()
        else:
            self._watch.stop()

    def _clear(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _rebuild(self, problems: Sequence[Diagnostic]) -> None:
        self._clear()
        for i, d in enumerate(problems):
            if i:
                self._layout.addWidget(_separator())
            row = QWidget()
            lay = QHBoxLayout(row)
            lay.setContentsMargins(12, 9, 6 if i == 0 else 12, 9)
            lay.setSpacing(8)
            icon = QLabel()
            icon.setPixmap(icons.icon("error" if d.is_error else "warning",
                                      (THEME.error if d.is_error else THEME.warning).name()).pixmap(14, 14))
            icon.setFixedSize(14, 18)   # metnin ilk satırıyla hizalı; boş alanı yutmasın
            icon.setAlignment(Qt.AlignCenter)
            text = QLabel(html.escape(d.message))
            text.setWordWrap(True)
            text.setTextFormat(Qt.RichText)
            text.setStyleSheet(f"color: {THEME.text.name()};")
            # word-wrap'li QLabel düzende en dar hâline büzülür; genişliği metinden ver
            ideal = QFontMetrics(text.font()).horizontalAdvance(d.message) + 4
            text.setFixedWidth(min(self.MAX_WIDTH - 80, ideal))
            text.setToolTip(d.code)
            lay.addWidget(icon, 0, Qt.AlignTop)
            lay.addWidget(text)
            lay.addStretch(1)
            if i == 0 and len(self._fixes) > 1:
                more = QToolButton()
                more.setIcon(icons.icon("more"))
                more.setToolTip("Diğer eylemler (Alt+Enter)")
                more.setCursor(Qt.PointingHandCursor)
                more.clicked.connect(lambda _=False, b=more: self._show_menu(b.mapToGlobal(b.rect().bottomLeft())))
                lay.addWidget(more, 0, Qt.AlignTop)
            self._layout.addWidget(row)

        if self._fixes:
            self._layout.addWidget(_separator())
            footer = QWidget()
            lay = QHBoxLayout(footer)
            lay.setContentsMargins(12, 8, 12, 9)
            lay.setSpacing(6)
            lay.addWidget(self._link(self._fixes[0][0], lambda: self.apply(0)))
            lay.addWidget(self._shortcut("Alt+Shift+Enter"))
            if len(self._fixes) > 1:
                lay.addSpacing(14)
                more = self._link("Diğer eylemler…", None)
                more.linkActivated.connect(lambda _l, m=more: self._show_menu(m.mapToGlobal(m.rect().bottomLeft())))
                lay.addWidget(more)
                lay.addWidget(self._shortcut("Alt+Enter"))
            lay.addStretch(1)
            self._layout.addWidget(footer)

    def _link(self, text: str, on_click: Optional[Callable[[], None]]) -> QLabel:
        label = QLabel(f"<a href='#' style='color:{_LINK}; text-decoration:none'>{html.escape(text)}</a>")
        label.setTextFormat(Qt.RichText)
        label.setCursor(Qt.PointingHandCursor)
        if on_click is not None:
            label.linkActivated.connect(lambda _l: on_click())
        return label

    @staticmethod
    def _shortcut(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(f"color: {THEME.muted.name()};")
        return label

    # ---- eylemler -------------------------------------------------------- #
    @property
    def fixes(self) -> List[Fix]:
        return list(self._fixes)

    def apply(self, index: int) -> None:
        if 0 <= index < len(self._fixes):
            action = self._fixes[index][1]
            self.hide()
            action()

    def _show_menu(self, at: QPoint) -> None:
        menu = QMenu(self._editor)
        for i, (label, _fn) in enumerate(self._fixes):
            menu.addAction(label, lambda i=i: self.apply(i))
        self._menu_open = True
        try:
            menu.exec(at)
        finally:
            self._menu_open = False
            menu.deleteLater()

    def show_menu_at(self, at: QPoint) -> None:
        self._show_menu(at)

    # ---- konum / kapanma --------------------------------------------------- #
    def _place(self, anchor: QRect) -> None:
        screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = min(max(area.left(), anchor.left() - 8), area.right() - self.width())
        y = anchor.bottom() + 4
        if y + self.height() > area.bottom():
            y = anchor.top() - self.height() - 4
        self.move(x, y)

    def _check_mouse(self) -> None:
        if self._menu_open or not self.isVisible():
            return
        pos = QCursor.pos()
        if self.geometry().adjusted(-6, -6, 6, 6).contains(pos):
            return
        if self._keep_alive is not None and self._keep_alive(pos):
            return
        self.hide()

    def hideEvent(self, e):
        self._watch.stop()
        super().hideEvent(e)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(_BORDER)
        p.setBrush(THEME.surface)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self.RADIUS, self.RADIUS)
        p.end()
