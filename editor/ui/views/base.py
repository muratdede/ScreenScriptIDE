"""Tüm yerleşik pencerelerin (view) tabanı.

Yeni view eklemek:
    class MyView(DockView):
        view_id = "my_view"; title = "Benim"; icon_name = "file"; default_area = Qt.RightDockWidgetArea
        def build(self): return QLabel("merhaba")
ve MainWindow'a view_classes=[..., MyView] verin (ya da views.DEFAULT_VIEWS'e ekleyin).

IntelliJ tarzı: her view'ın kenar şeridinde (stripe) bir ikonu vardır; aynı gruptaki (tab_group)
view'lardan aynı anda yalnızca biri görünür. Başlık çubuğuna view'a özgü düğmeler title_actions() ile eklenir.
"""

from __future__ import annotations

import sys
from typing import Dict, List

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QAction, QColor, QPainter, QPen
from PySide6.QtWidgets import QDockWidget, QHBoxLayout, QLabel, QToolButton, QWidget

from ...core.context import EditorContext
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
                                       "Her zaman üstte (ana pencere küçültülse de açık kalır)", dock.set_pinned)
        self.pin_btn.setCheckable(True)
        self.pin_btn.setVisible(False)
        float_btn = self.add_button(icons.icon("float", "#9da0a8"), "Ayrı pencerede aç / geri yerleştir",
                                    lambda: dock.setFloating(not dock.isFloating()))
        float_btn.setVisible(bool(dock.features() & QDockWidget.DockWidgetFloatable))
        self.add_button(icons.icon("minimize", "#9da0a8"), "Gizle", dock.hide)

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
    """Windows'ta sahipli (owned) pencereler sahibi küçültülünce gizlenir; sahipliği kaldırmak
    ayrık pencerenin ana pencereden bağımsız açık kalmasını sağlar."""
    if sys.platform != "win32":
        return
    import ctypes
    GWLP_HWNDPARENT = -8
    ctypes.windll.user32.SetWindowLongPtrW(int(widget.winId()), GWLP_HWNDPARENT,
                                           int(owner.winId()) if owner is not None else 0)


class DockView(QDockWidget):
    view_id: str = ""
    title: str = ""
    icon_name: str = "file"      # icons.py'deki ikon adı (kenar şeridi)
    default_area: Qt.DockWidgetArea = Qt.LeftDockWidgetArea
    tab_group: str = ""          # aynı gruptaki view'lardan aynı anda biri görünür
    stripe_bottom: bool = False  # sol şeritte alt bölüme yerleşsin (IntelliJ'deki Run/Terminal gibi)
    shortcut: str = ""           # görünüm menüsünde kısayol (ör. "Alt+1")
    pinnable: bool = False       # ayrık penceredeyken "her zaman üstte" raptiyesi gösterilsin

    def __init__(self, ctx: EditorContext, parent=None):
        super().__init__(self.title, parent)
        if not self.view_id:
            raise TypeError(f"{type(self).__name__}.view_id tanımlı değil")
        self.setObjectName(self.view_id)
        self.ctx = ctx
        self._pinned = False
        self.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetFloatable)
        self.topLevelChanged.connect(self._update_floating_frame)
        self._title_bar = _TitleBar(self)
        self.setTitleBarWidget(self._title_bar)
        self.setWidget(self.build())
        # view'a özgü başlık düğmeleri (gizle/ayır düğmelerinden önce)
        insert_at = self._title_bar._layout.count() - 2
        for action in self.title_actions():
            self._title_bar.add_action(action, insert_at)
            insert_at += 1

    def _update_floating_frame(self, floating: bool) -> None:
        # ayrık pencerenin çerçevesi arka plandan ayırt edilsin diye kalın ve açık renkli
        # (stylesheet yalnızca çerçeve genişliğini ayırır; çizgiyi paintEvent çizer)
        self.setStyleSheet(_FLOATING_FRAME if floating else "")
        self._title_bar.pin_btn.setVisible(floating and self.pinnable)
        if not floating and self._pinned:
            # yerleştirilince QDockWidget pencere bayraklarını zaten sıfırlar
            self._pinned = False
            self._title_bar.pin_btn.setChecked(False)
            self.pinned_changed(False)

    @property
    def pinned(self) -> bool:
        return self._pinned

    def set_pinned(self, on: bool) -> None:
        """Ayrık pencereyi her zaman üstte tutar; ana pencere küçültülse de açık kalır."""
        on = bool(on) and self.isFloating()
        self._title_bar.pin_btn.setChecked(on)
        if on == self._pinned:
            return
        self._pinned = on
        geometry = self.geometry()
        self.setWindowFlag(Qt.WindowStaysOnTopHint, on)   # pencereyi gizler; yeniden göster
        self.setGeometry(geometry)
        self.show()
        _set_window_owner(self, None if on else self.parentWidget())
        self.raise_()
        self.pinned_changed(on)

    def pinned_changed(self, pinned: bool) -> None:
        """Raptiye açılıp kapanınca çağrılır; alt sınıflar sade görünüme geçmek için ezebilir."""

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
        """View'ın içeriğini oluşturur. Alt sınıflar ezmeli."""
        raise NotImplementedError

    def title_actions(self) -> List[QAction]:
        """Başlık çubuğunda gösterilecek eylemler (ör. temizle, yenile)."""
        return []

    def attach_actions(self, actions: Dict[str, QAction]) -> None:
        """MainWindow, genel eylemleri (run, debug, step_over, ...) oluşturduktan sonra çağırır."""

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
