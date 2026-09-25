"""Editör içi bul / değiştir çubuğu (Ctrl+F / Ctrl+R), IntelliJ'deki gibi editörün üst kenarına yerleşir.

Arama mantığı CodeEditor'dadır (find_* metotları); bu sınıf yalnızca arayüzdür.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QToolButton, QVBoxLayout, QWidget

from ..core.search import SearchQuery
from . import icons
from .shortcuts import shortcut

if TYPE_CHECKING:
    from .code_editor import CodeEditor


def _toggle(text: str, tip: str) -> QToolButton:
    b = QToolButton(text=text)
    b.setCheckable(True)
    b.setToolTip(tip)
    b.setFixedSize(24, 22)
    b.setStyleSheet("QToolButton { font-weight: 600; padding: 0; border-radius: 4px; color: #9da0a8; }"
                    "QToolButton:checked { background: #3574f0; color: #ffffff; }")
    return b


def _icon_button(name: str, tip: str, slot) -> QToolButton:
    b = QToolButton()
    b.setIcon(icons.icon(name, icons.DEFAULT_COLOR, 14))
    b.setToolTip(tip)
    b.setFixedSize(24, 22)
    b.clicked.connect(slot)
    return b


class FindBar(QWidget):
    def __init__(self, editor: "CodeEditor"):
        super().__init__(editor)
        self._editor = editor
        self.setObjectName("find_bar")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setStyleSheet("""
            QWidget#find_bar { background: #2b2d30; border-bottom: 1px solid #1e1f22; }
            QLineEdit { background: #1e1f22; border: 1px solid #393b40; border-radius: 4px; padding: 2px 6px; }
            QLineEdit:focus { border-color: #3574f0; }
            QLineEdit[error="true"] { border-color: #db5c5c; }
            QPushButton, QToolButton#text_btn { padding: 2px 8px; }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 4, 8, 4)
        root.setSpacing(4)

        # ---- 1. satır: ara ----------------------------------------------- #
        row = QHBoxLayout()
        row.setSpacing(3)
        self._expand = QToolButton()
        self._expand.setFixedSize(22, 22)
        self._expand.setToolTip("Değiştir'i aç/kapat (Ctrl+R)")
        self._expand.clicked.connect(lambda: self.set_replace_mode(not self.replace_mode))
        row.addWidget(self._expand)

        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("Bul")
        self.find_edit.addAction(icons.icon("search", "#868a91", 14), QLineEdit.LeadingPosition)
        self.find_edit.setMinimumWidth(260)
        self.find_edit.textChanged.connect(self._schedule)
        row.addWidget(self.find_edit, 1)

        self.case_btn = _toggle("Cc", "Büyük/küçük harf duyarlı (Alt+C)")
        self.word_btn = _toggle("W", "Tam kelime (Alt+W)")
        self.regex_btn = _toggle(".*", "Düzenli ifade / regex (Alt+X)")
        for b, key in ((self.case_btn, "Alt+C"), (self.word_btn, "Alt+W"), (self.regex_btn, "Alt+X")):
            b.toggled.connect(self._schedule)
            row.addWidget(b)
            shortcut(key, self, b.toggle, Qt.WidgetWithChildrenShortcut)

        self.count_label = QLabel()
        self.count_label.setObjectName("hint")
        self.count_label.setMinimumWidth(92)
        self.count_label.setAlignment(Qt.AlignCenter)
        row.addWidget(self.count_label)
        row.addWidget(_icon_button("chevron_up", "Önceki (Shift+Enter / Shift+F3)", editor.find_previous))
        row.addWidget(_icon_button("chevron_down", "Sonraki (Enter / F3)", editor.find_next))
        row.addStretch()
        row.addWidget(_icon_button("close", "Kapat (Esc)", editor.hide_find))
        root.addLayout(row)

        # ---- 2. satır: değiştir ------------------------------------------ #
        self._replace_row = QWidget()
        rrow = QHBoxLayout(self._replace_row)
        rrow.setContentsMargins(25, 0, 0, 0)
        rrow.setSpacing(3)
        self.replace_edit = QLineEdit()
        self.replace_edit.setPlaceholderText("Değiştir" + " (regex: \\1 grupları)")
        self.replace_edit.addAction(icons.icon("refresh", "#868a91", 14), QLineEdit.LeadingPosition)
        self.replace_edit.setMinimumWidth(260)
        rrow.addWidget(self.replace_edit, 1)
        rep = QToolButton(text="Değiştir")
        rep.setObjectName("text_btn")
        rep.setToolTip("Seçili eşleşmeyi değiştir ve sonrakine geç (Enter)")
        rep.clicked.connect(editor.replace_current)
        rep_all = QToolButton(text="Tümünü Değiştir")
        rep_all.setObjectName("text_btn")
        rep_all.setToolTip("Tüm eşleşmeleri değiştir (Ctrl+Alt+Enter)")
        rep_all.clicked.connect(editor.replace_all)
        rrow.addWidget(rep)
        rrow.addWidget(rep_all)
        rrow.addStretch()
        root.addWidget(self._replace_row)

        self._timer = QTimer(self, singleShot=True, interval=80)
        self._timer.timeout.connect(lambda: editor.find_update(select=True))

        sc = lambda key, slot: shortcut(key, self, slot, Qt.WidgetWithChildrenShortcut)
        sc("Escape", editor.hide_find)            # F3 / Shift+F3 ana penceredeki eylemlerdir
        sc("Ctrl+Alt+Return", editor.replace_all)
        self.find_edit.returnPressed.connect(editor.find_next)
        self.replace_edit.returnPressed.connect(editor.replace_current)
        shortcut("Shift+Return", self.find_edit, editor.find_previous)

        self.replace_mode = False
        self.set_replace_mode(False)

    # ---- durum ----------------------------------------------------------- #
    def set_replace_mode(self, on: bool) -> None:
        self.replace_mode = on
        self._replace_row.setVisible(on)
        self._expand.setIcon(icons.icon("chevron_down" if on else "chevron_right", "#9da0a8", 14))
        self.adjustSize()
        self._editor.relayout_find_bar()

    def query(self) -> SearchQuery:
        return SearchQuery(self.find_edit.text(), self.case_btn.isChecked(), self.word_btn.isChecked(),
                           self.regex_btn.isChecked())

    def set_error(self, error: bool) -> None:
        if self.find_edit.property("error") != error:
            self.find_edit.setProperty("error", error)
            self.find_edit.style().unpolish(self.find_edit)
            self.find_edit.style().polish(self.find_edit)

    def set_count(self, current: int, total: int, error: str = "") -> None:
        self.set_error(bool(error))
        if error:
            self.count_label.setText(error)
            self.count_label.setStyleSheet("color: #db5c5c;")
        elif not self.find_edit.text():
            self.count_label.setText("")
        elif total == 0:
            self.count_label.setText("Sonuç yok")
            self.count_label.setStyleSheet("color: #db5c5c;")
        else:
            self.count_label.setText(f"{current}/{total}" if current else f"{total} sonuç")
            self.count_label.setStyleSheet("")

    def _schedule(self, *_):
        self._timer.start()
