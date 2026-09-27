"""IntelliJ tarzı gruplandırılmış ayar penceresi: solda arama + kategori ağacı, sağda seçili sayfa,
altta Tamam / İptal / Uygula.

Kullanım (alt sınıfta):
    self.add_page(("Cihaz", "Bağlantı"), [
        ("Bağlantı", [("Host", host_edit), ("", checkbox)]),   # (bölüm başlığı, [(etiket, widget)])
    ])
    self.finish()   # sayfalar eklendikten sonra

apply() ezilir; Tamam ve Uygula önce apply()'ı çağırır, sonra applied sinyalini yayar.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractButton, QAbstractSpinBox, QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QScrollArea, QSizePolicy, QSplitter, QStackedWidget, QTreeWidget, QTreeWidgetItem,
                               QTreeWidgetItemIterator, QVBoxLayout, QWidget)

from . import icons

Row = Tuple[str, QWidget]
Section = Tuple[str, Sequence[Row]]

_PAGE_ROLE = Qt.UserRole
_KEYWORDS_ROLE = Qt.UserRole + 1

_STYLE = """
    QWidget#settings_nav { background: #2b2d30; }
    QWidget#settings_page { background: #2b2d30; }
    QTreeWidget#settings_tree { background: #2b2d30; }
    QTreeWidget#settings_tree::item { padding: 4px 2px; }
    QTreeView::indicator { width: 14px; height: 14px; border: 1px solid #6f737a; border-radius: 3px;
        background: #2b2d30; }
    QTreeView::indicator:hover { border-color: #9da0a8; }
    QTreeView::indicator:checked { background: #3574f0; border-color: #3574f0; image: url(%CHECK%); }
    QTreeView::indicator:indeterminate { background: #3574f0; border-color: #3574f0; image: url(%DASH%); }
    QLabel#settings_crumb { color: #dfe1e5; font-weight: 600; font-size: 14px; }
    QLabel#settings_section { color: #dfe1e5; }
    QFrame#settings_line { background: #393b40; max-height: 1px; min-height: 1px; border: none; }
    QFrame#settings_footer { background: #1e1f22; max-height: 1px; min-height: 1px; border: none; }
"""


def hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("hint")
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)   # form genişliğince satır kaydır
    return label


class GroupedSettingsDialog(QDialog):
    applied = Signal()

    _last_page: Dict[str, str] = {}   # sınıf adı -> son açık sayfa (oturum boyunca hatırlanır)

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(880, 620)
        self.setMinimumSize(640, 420)
        self.setStyleSheet(_STYLE.replace("%CHECK%", icons.icon_file("check", "#ffffff"))
                           .replace("%DASH%", icons.icon_file("minimize", "#ffffff")))
        self._items: Dict[Tuple[str, ...], QTreeWidgetItem] = {}

        # sol: arama + ağaç
        nav = QWidget(objectName="settings_nav")
        nl = QVBoxLayout(nav)
        nl.setContentsMargins(8, 8, 4, 8)
        nl.setSpacing(6)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Ara")
        self._search.setClearButtonEnabled(True)
        self._search.addAction(icons.icon("search", "#9da0a8"), QLineEdit.LeadingPosition)
        self._search.textChanged.connect(self._filter)
        self._tree = QTreeWidget(objectName="settings_tree")
        self._tree.setHeaderHidden(True)
        self._tree.setIndentation(14)
        self._tree.setRootIsDecorated(True)
        self._tree.currentItemChanged.connect(self._on_current)
        nl.addWidget(self._search)
        nl.addWidget(self._tree, 1)

        # sağ: başlık (breadcrumb) + sayfalar
        right = QWidget(objectName="settings_page")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(16, 12, 8, 0)
        rl.setSpacing(8)
        self._crumb = QLabel(objectName="settings_crumb")
        self._stack = QStackedWidget()
        rl.addWidget(self._crumb)
        rl.addWidget(self._stack, 1)

        splitter = QSplitter()
        splitter.addWidget(nav)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([240, 640])
        splitter.setChildrenCollapsible(False)

        footer_line = QFrame(objectName="settings_footer")
        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Apply)
        self._buttons.button(QDialogButtonBox.Ok).setText("Tamam")
        self._buttons.button(QDialogButtonBox.Cancel).setText("İptal")
        self._apply_btn = self._buttons.button(QDialogButtonBox.Apply)
        self._apply_btn.setText("Uygula")
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self._do_apply)
        self._buttons.accepted.connect(self._on_ok)
        self._buttons.rejected.connect(self.reject)
        bottom = QWidget()
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(12, 10, 12, 10)
        bl.addStretch(1)
        bl.addWidget(self._buttons)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(splitter, 1)
        layout.addWidget(footer_line)
        layout.addWidget(bottom)

    # ---- sayfa oluşturma -------------------------------------------------- #
    def add_page(self, path: Sequence[str], sections: Iterable[Section]) -> QWidget:
        """path: ağaçtaki konum, ör. ("Cihaz", "Bağlantı"). Eksik üst düğümler sayfasız oluşturulur;
        sayfası olmayan düğüm seçilince ilk alt sayfası gösterilir."""
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 0, 12, 12)
        pl.setSpacing(4)
        keywords: List[str] = list(path)
        fills = False   # dikey genişleyen widget (ör. liste) varsa sayfayı o doldurur
        for i, (title, rows) in enumerate(sections):
            if title:
                pl.addWidget(self._section_header(title, first=i == 0))
                keywords.append(title)
            form = QFormLayout()
            form.setContentsMargins(0 if not title else 2, 2, 0, 6)
            form.setHorizontalSpacing(12)
            form.setVerticalSpacing(8)
            form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)   # sayı/seçim kutuları dar kalır
            for label, widget in rows:
                form.addRow(f"{label}:" if label else "", widget)
                keywords.append(label)
                keywords.extend(self._widget_texts(widget))
                fills |= widget.sizePolicy().verticalPolicy() in (QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
            pl.addLayout(form, 1 if fills else 0)
        if not fills:
            pl.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(page)
        self._stack.addWidget(scroll)

        item = self._item(tuple(path))
        item.setData(0, _PAGE_ROLE, scroll)
        item.setData(0, _KEYWORDS_ROLE, " ".join(k for k in keywords if k).casefold())
        return page

    def finish(self) -> None:
        """Sayfalar eklendikten sonra: ağacı açar, son sayfayı seçer, değişiklik izlemeyi kurar."""
        self._tree.expandAll()
        last = self._last_page.get(type(self).__name__)
        target = next((it for key, it in self._items.items() if " / ".join(key) == last), None)
        self._tree.setCurrentItem(target or self._tree.topLevelItem(0))
        self._watch_changes()

    def select_page(self, *path: str) -> None:
        item = self._items.get(tuple(path))
        if item:
            self._tree.setCurrentItem(item)

    def _item(self, path: Tuple[str, ...]) -> QTreeWidgetItem:
        if path in self._items:
            return self._items[path]
        if len(path) > 1:
            item = QTreeWidgetItem(self._item(path[:-1]), [path[-1]])
        else:
            item = QTreeWidgetItem(self._tree, [path[-1]])
        self._items[path] = item
        return item

    @staticmethod
    def _section_header(title: str, first: bool) -> QWidget:
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0 if first else 14, 0, 2)
        row.setSpacing(8)
        row.addWidget(QLabel(title, objectName="settings_section"))
        line = QFrame(objectName="settings_line")
        line.setFrameShape(QFrame.HLine)
        row.addWidget(line, 1)
        return w

    @staticmethod
    def _widget_texts(widget: QWidget) -> List[str]:
        """Arama için widget (ve alt widget'larındaki) görünür metinler."""
        texts = []
        for w in [widget] + widget.findChildren(QWidget):
            if isinstance(w, (QLabel, QAbstractButton)):
                texts.append(w.text())
            elif isinstance(w, QComboBox):
                texts.extend(w.itemText(i) for i in range(w.count()))
            elif isinstance(w, QTreeWidget):
                it = QTreeWidgetItemIterator(w)
                while it.value():
                    texts.append(it.value().text(0))
                    it += 1
            if w.toolTip():
                texts.append(w.toolTip())
        return texts

    # ---- gezinme ---------------------------------------------------------- #
    def _page_item(self, item: Optional[QTreeWidgetItem]) -> Optional[QTreeWidgetItem]:
        """Sayfası olmayan düğüm için ilk (görünür) sayfalı alt düğüm."""
        while item is not None and item.data(0, _PAGE_ROLE) is None:
            children = [item.child(i) for i in range(item.childCount()) if not item.child(i).isHidden()]
            item = children[0] if children else None
        return item

    def _on_current(self, item: Optional[QTreeWidgetItem], _previous=None) -> None:
        if item is None:
            return
        page_item = self._page_item(item)
        if page_item is None:
            return
        self._stack.setCurrentWidget(page_item.data(0, _PAGE_ROLE))
        names = []
        it = page_item
        while it is not None:
            names.insert(0, it.text(0))
            it = it.parent()
        self._crumb.setText("  ›  ".join(names))
        self._last_page[type(self).__name__] = " / ".join(names)

    def _filter(self, text: str) -> None:
        needle = text.strip().casefold()
        first_match = None
        # önce yapraklar/sayfalar, sonra üst düğümler (alt düğümü görünürse görünür)
        for path in sorted(self._items, key=len, reverse=True):
            item = self._items[path]
            own = needle in (item.data(0, _KEYWORDS_ROLE) or "") or needle in item.text(0).casefold()
            child_visible = any(not item.child(i).isHidden() for i in range(item.childCount()))
            visible = not needle or own or child_visible
            item.setHidden(not visible)
        it = QTreeWidgetItemIterator(self._tree)
        while it.value():
            item = it.value()
            if not item.isHidden() and item.data(0, _PAGE_ROLE) is not None:
                first_match = item
                break
            it += 1
        self._tree.expandAll()
        current = self._tree.currentItem()
        if first_match and (current is None or current.isHidden() or (needle and current.data(0, _PAGE_ROLE)
                                                                      is None)):
            self._tree.setCurrentItem(first_match)

    # ---- uygula ----------------------------------------------------------- #
    def _watch_changes(self) -> None:
        mark = lambda *_: self._apply_btn.setEnabled(True)   # noqa: E731
        for w in self._stack.findChildren(QWidget):
            if isinstance(w, QAbstractSpinBox):
                w.editingFinished.connect(mark)
                if hasattr(w, "valueChanged"):
                    w.valueChanged.connect(mark)
            elif isinstance(w, QLineEdit):
                w.textChanged.connect(mark)
            elif isinstance(w, QPlainTextEdit):
                w.textChanged.connect(mark)
            elif isinstance(w, QComboBox):
                w.currentIndexChanged.connect(mark)
            elif isinstance(w, QAbstractButton) and w.isCheckable():
                w.toggled.connect(mark)
            elif isinstance(w, QTreeWidget) and w.objectName() != "settings_tree":
                w.itemChanged.connect(mark)   # işaretlenebilir öğeler

    def apply(self) -> None:
        """Alt sınıf: penceredeki değerleri hedef yapılandırmaya yazar."""

    def _do_apply(self) -> None:
        self.apply()
        self.applied.emit()
        self._apply_btn.setEnabled(False)

    def _on_ok(self) -> None:
        self._do_apply()
        self.accept()
