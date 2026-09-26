"""Kod alanının sağ üst köşesindeki sorun özeti (IntelliJ'deki inceleme göstergesi).

Sekmelerin hemen altında, editör içeriğinin üzerine (arka plansız) yerleşir. Açık dosyanın
hata/uyarı sayılarını gösterir (sorun yoksa yeşil onay); araç ipucunda proje toplamı yer alır.
Tıklanınca `clicked` yayılır (ana pencere Sorunlar panelini açar).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QTabWidget, QWidget

from ..core.context import EditorContext
from ..core.diagnostics import counts
from . import icons
from .theme import THEME


class ProblemsBadge(QWidget):
    clicked = Signal()

    MARGIN = 6   # viewport kenarından (dikey kaydırma çubuğunun solunda)

    def __init__(self, ctx: EditorContext, tabs: QTabWidget):
        super().__init__(tabs)
        self._ctx = ctx
        self._tabs = tabs
        self._path = ""
        self._watched = None   # konumu izlenen editör viewport'u
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

        ctx.diagnostics.changed.connect(lambda _p: self.refresh())   # araç ipucunda proje toplamı da var
        ctx.diagnostics.reset.connect(self.refresh)
        ctx.bus.active_file_changed.connect(self.set_path)
        tabs.currentChanged.connect(lambda _i: self._follow_current())
        tabs.installEventFilter(self)
        self.refresh()

    # ---- içerik ---------------------------------------------------------- #
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
            f"Bu dosya: {errors} hata, {warnings} uyarı",
            f"Proje: {total_e} hata, {total_w} uyarı",
            "Tıklayın: Sorunlar paneli (Alt+6)   ·   F8 / Shift+F8: sonraki / önceki sorun"]))
        self.setVisible(is_script and self._viewport() is not None)
        self.adjustSize()
        self.reposition()

    def _show(self, kind: str, n: int, text=None) -> None:
        icon_label, count = self._parts[kind]
        icon_label.setVisible(n > 0)
        count.setVisible(n > 0 and text != "")
        count.setText(str(n) if text is None else text)

    # ---- konum ----------------------------------------------------------- #
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
                except RuntimeError:   # editör kapanıp silinmiş
                    pass
            self._watched = vp
            if vp is not None:
                vp.installEventFilter(self)   # bul çubuğu açılınca viewport aşağı kayar
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
            except RuntimeError:   # kapanışta editör önce silinmiş olabilir
                pass
        return False

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)
