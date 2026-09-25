"""Dosyalarda bul / değiştir penceresi (Ctrl+Shift+F / Ctrl+Shift+R).

IntelliJ'deki "Find in Files" açılır penceresi örnek alınmıştır: üstte arama (ve değiştirme) alanı ile
seçenekler, ortada sonuç listesi (satır parçası + dosya:satır), altta seçili sonucun önizlemesi.
Açık editörlerdeki kaydedilmemiş metin de aranır; değiştirmeler açık editörlerde geri alınabilir yapılır.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional

from PySide6.QtCore import QByteArray, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QRadioButton, QSizeGrip, QSplitter,
                               QStyle, QStyledItemDelegate, QToolButton, QVBoxLayout, QWidget)

from ..core import search
from ..core.context import EditorContext
from ..core.search import LineMatch, SearchQuery
from . import icons
from .code_editor import CodeEditor
from .shortcuts import shortcut
from .theme import THEME

ROLE_MATCH = Qt.UserRole + 1


def apply_text_change(ctx: EditorContext, path: str, transform) -> int:
    """transform(text) -> (yeni metin, değişen sayısı). Dosya bir editörde açıksa değişiklik orada
    (tek Ctrl+Z ile geri alınabilir şekilde) yapılır; editör önceden kaydedilmişse yine kaydedilir.
    Açık değilse dosya diskte, satır sonu biçimi (LF/CRLF) korunarak güncellenir."""
    docs = ctx.documents
    editor = docs.find_editor(path) if docs and hasattr(docs, "find_editor") else None
    if editor is not None:
        new, n = transform(editor.toPlainText())
        if n:
            was_clean = not editor.is_dirty
            editor.replace_document_text(new)
            if was_clean:
                editor.save()
        return n
    try:
        with open(path, encoding="utf-8", newline="") as f:
            raw = f.read()
    except (OSError, UnicodeDecodeError):
        return 0
    crlf = "\r\n" in raw
    new, n = transform(raw.replace("\r\n", "\n"))
    if n:
        with open(path, "w", encoding="utf-8", newline="\r\n" if crlf else "\n") as f:
            f.write(new)
    return n


class _ResultDelegate(QStyledItemDelegate):
    """Satır parçasını eşleşme vurgulu, sağda dosya adı + satır numarasıyla çizer."""

    def sizeHint(self, option, index):
        return QSize(100, QFontMetrics(THEME.code_font(10)).height() + 8)

    def paint(self, painter: QPainter, option, index):
        m: LineMatch = index.data(ROLE_MATCH)
        if m is None:
            return super().paint(painter, option, index)
        painter.save()
        r = option.rect
        if option.state & QStyle.State_Selected:
            painter.fillRect(r, THEME.tree_selection)
        elif option.state & QStyle.State_MouseOver:
            painter.fillRect(r, QColor("#323438"))

        # sağ: dosya adı + satır
        ui = option.font
        painter.setFont(ui)
        fm_ui = QFontMetrics(ui)
        right = f"{os.path.basename(m.path)} {m.line}"
        rw = fm_ui.horizontalAdvance(right) + 16
        painter.setPen(THEME.muted)
        painter.drawText(QRect(r.right() - rw, r.top(), rw - 8, r.height()), Qt.AlignRight | Qt.AlignVCenter, right)

        # sol: satır parçası (baştaki boşluklar atılır, uzun satırlar eşleşme görünür kalacak şekilde kırpılır)
        code = THEME.code_font(10)
        painter.setFont(code)
        fm = QFontMetrics(code)
        text = m.line_text
        strip = len(text) - len(text.lstrip())
        text, col = text[strip:], m.column - strip
        if col > 40:
            cut = col - 20
            text, col = "…" + text[cut:], col - cut + 1
        before, hit, after = text[:col], text[col:col + m.length], text[col + m.length:]
        x = r.left() + 10
        y = r.top()
        h = r.height()
        max_x = r.right() - rw
        painter.setPen(THEME.text)
        painter.drawText(QRect(x, y, max_x - x, h), Qt.AlignLeft | Qt.AlignVCenter, before)
        x += fm.horizontalAdvance(before)
        hw = fm.horizontalAdvance(hit)
        if x < max_x:
            painter.fillRect(QRect(x, y + 3, min(hw, max_x - x), h - 6), THEME.find_match)
            painter.drawText(QRect(x, y, max_x - x, h), Qt.AlignLeft | Qt.AlignVCenter, hit)
        x += hw
        if x < max_x:
            painter.drawText(QRect(x, y, max_x - x, h), Qt.AlignLeft | Qt.AlignVCenter,
                             fm.elidedText(after, Qt.ElideRight, max_x - x))
        painter.restore()


class _Header(QWidget):
    """Çerçevesiz pencerenin sürüklenebilir başlığı."""

    def __init__(self, dialog: QDialog):
        super().__init__(dialog)
        self._dialog = dialog
        self._drag: Optional[QPoint] = None

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self._dialog.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self._dialog.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None


class FindInFilesDialog(QDialog):
    MAX_RESULTS = 2000

    def __init__(self, ctx: EditorContext, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint)
        self.ctx = ctx
        self.setObjectName("find_in_files")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setStyleSheet("""
            QDialog#find_in_files { background: #2b2d30; border: 1px solid #4e5157; border-radius: 10px; }
            QLineEdit#big { background: #1e1f22; font-size: 14px; padding: 6px 8px; border-color: #393b40; }
            QLineEdit#big:focus { border-color: #3574f0; }
            QLineEdit[error="true"] { border-color: #db5c5c; }
            QListWidget { background: #2b2d30; }
            QLabel#title { font-weight: 600; font-size: 14px; }
            QRadioButton { spacing: 5px; color: #b4b8bf; }
        """)
        self.resize(900, 680)

        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)

        # ---- başlık: ad + seçenekler + dosya maskesi ------------------------ #
        header = _Header(self)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(14, 10, 10, 6)
        hl.setSpacing(4)
        self._title = QLabel("Dosyalarda Bul")
        self._title.setObjectName("title")
        hl.addWidget(self._title)
        hl.addSpacing(12)
        self._scope_project = QRadioButton("Proje", checked=True)
        self._scope_open = QRadioButton("Açık dosyalar")
        for rb in (self._scope_project, self._scope_open):
            rb.toggled.connect(self._schedule)
            hl.addWidget(rb)
        hl.addStretch()
        self.case_btn = self._toggle("Cc", "Büyük/küçük harf duyarlı (Alt+C)", "Alt+C")
        self.word_btn = self._toggle("W", "Tam kelime (Alt+W)", "Alt+W")
        self.regex_btn = self._toggle(".*", "Düzenli ifade / regex (Alt+X)", "Alt+X")
        for b in (self.case_btn, self.word_btn, self.regex_btn):
            hl.addWidget(b)
        hl.addSpacing(8)
        mask_label = QLabel("Dosya maskesi")
        mask_label.setObjectName("hint")
        hl.addWidget(mask_label)
        self._mask = QComboBox()
        self._mask.setEditable(True)
        self._mask.addItems(["*", "*.py", "*.json", "*.txt, *.md"])
        self._mask.setMinimumWidth(110)
        self._mask.setToolTip("Virgülle ayırın, ör. *.py, *.txt")
        self._mask.currentTextChanged.connect(self._schedule)
        hl.addWidget(self._mask)
        close = QToolButton()
        close.setIcon(icons.icon("close", "#9da0a8", 14))
        close.setToolTip("Kapat (Esc)")
        close.clicked.connect(self.close)
        hl.addWidget(close)
        root.addWidget(header)

        # ---- arama / değiştirme alanları --------------------------------- #
        fields = QVBoxLayout()
        fields.setContentsMargins(12, 2, 12, 8)
        fields.setSpacing(6)
        self.find_edit = QLineEdit()
        self.find_edit.setObjectName("big")
        self.find_edit.setPlaceholderText("Aranacak metin")
        self.find_edit.addAction(icons.icon("search", "#868a91"), QLineEdit.LeadingPosition)
        self.find_edit.setClearButtonEnabled(True)
        self.find_edit.textChanged.connect(self._schedule)
        fields.addWidget(self.find_edit)
        self.replace_edit = QLineEdit()
        self.replace_edit.setObjectName("big")
        self.replace_edit.setPlaceholderText("Yerine konacak metin (regex: \\1 grupları)")
        self.replace_edit.addAction(icons.icon("refresh", "#868a91"), QLineEdit.LeadingPosition)
        fields.addWidget(self.replace_edit)
        root.addLayout(fields)

        # ---- sonuçlar + önizleme ----------------------------------------- #
        self._results = QListWidget()
        self._results.setItemDelegate(_ResultDelegate(self._results))
        self._results.setUniformItemSizes(True)
        self._results.setMouseTracking(True)
        self._results.setSelectionMode(QAbstractItemView.SingleSelection)
        self._results.currentItemChanged.connect(lambda cur, _prev: self._preview(cur))
        self._results.itemDoubleClicked.connect(lambda _it: self.open_current())

        self._preview_host = QWidget()
        pv = QVBoxLayout(self._preview_host)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(0)
        self._preview_title = QLabel()
        self._preview_title.setObjectName("hint")
        self._preview_title.setContentsMargins(12, 5, 12, 5)
        self._preview_title.setStyleSheet("background: #2b2d30; border-top: 1px solid #1e1f22;")
        pv.addWidget(self._preview_title)
        self._preview_editor: Optional[CodeEditor] = None

        split = QSplitter(Qt.Vertical)
        split.addWidget(self._results)
        split.addWidget(self._preview_host)
        split.setSizes([300, 260])
        split.setChildrenCollapsible(False)
        root.addWidget(split, 1)

        # ---- alt çubuk ----------------------------------------------------- #
        footer = QHBoxLayout()
        footer.setContentsMargins(12, 8, 4, 4)
        self._status = QLabel()
        self._status.setObjectName("hint")
        footer.addWidget(self._status, 1)
        self._replace_btn = QPushButton("Değiştir")
        self._replace_btn.setToolTip("Seçili sonucu değiştir")
        self._replace_btn.clicked.connect(self.replace_current)
        self._replace_all_btn = QPushButton("Tümünü Değiştir")
        self._replace_all_btn.clicked.connect(self.replace_all)
        self._open_btn = QPushButton("Editörde Aç")
        self._open_btn.setDefault(True)
        self._open_btn.setToolTip("Enter")
        self._open_btn.clicked.connect(self.open_current)
        for b in (self._replace_btn, self._replace_all_btn, self._open_btn):
            footer.addWidget(b)
        grip = QSizeGrip(self)
        footer.addWidget(grip, 0, Qt.AlignBottom | Qt.AlignRight)
        root.addLayout(footer)

        self._timer = QTimer(self, singleShot=True, interval=250)
        self._timer.timeout.connect(self.run_search)
        self._pattern: Optional[re.Pattern] = None
        self._query: Optional[SearchQuery] = None

        shortcut("Escape", self, self.close, Qt.WindowShortcut)
        for key in ("Return", "Enter"):
            shortcut(key, self.find_edit, self.open_current)
        shortcut("Down", self.find_edit, lambda: self._move(1))
        shortcut("Up", self.find_edit, lambda: self._move(-1))
        shortcut("Return", self._results, self.open_current)
        self.replace_edit.returnPressed.connect(self.replace_current)

        geo = ctx.settings.value("find_in_files/geometry")
        if isinstance(geo, QByteArray):
            self.restoreGeometry(geo)
        self.set_replace_mode(False)

    def _toggle(self, text, tip, key) -> QToolButton:
        b = QToolButton(text=text)
        b.setCheckable(True)
        b.setToolTip(tip)
        b.setFixedSize(26, 24)
        b.setStyleSheet("QToolButton { font-weight: 600; padding: 0; border-radius: 4px; color: #9da0a8; }"
                        "QToolButton:checked { background: #3574f0; color: #ffffff; }")
        b.toggled.connect(self._schedule)
        shortcut(key, self, b.toggle, Qt.WindowShortcut)
        return b

    # ---- açma ------------------------------------------------------------ #
    def set_replace_mode(self, replace: bool) -> None:
        self._replace_mode = replace
        self._title.setText("Dosyalarda Değiştir" if replace else "Dosyalarda Bul")
        self.replace_edit.setVisible(replace)
        self._replace_btn.setVisible(replace)
        self._replace_all_btn.setVisible(replace)

    def open_with(self, text: str = "", replace: bool = False) -> None:
        self.set_replace_mode(replace)
        if text:
            self.find_edit.setText(text)
        if not self.isVisible():
            parent = self.parentWidget()
            if parent is not None and not self.ctx.settings.value("find_in_files/geometry"):
                pg = parent.frameGeometry()
                self.move(pg.center().x() - self.width() // 2, pg.top() + 80)
            self.show()
        self.raise_()
        self.activateWindow()
        self.find_edit.setFocus()
        self.find_edit.selectAll()
        self.run_search()

    def closeEvent(self, e):
        self.ctx.settings.setValue("find_in_files/geometry", self.saveGeometry())
        super().closeEvent(e)

    # ---- arama ----------------------------------------------------------- #
    def _schedule(self, *_):
        self._timer.start()

    def _query_now(self) -> SearchQuery:
        return SearchQuery(self.find_edit.text(), self.case_btn.isChecked(), self.word_btn.isChecked(),
                           self.regex_btn.isChecked())

    def _open_text(self, path: str) -> Optional[str]:
        docs = self.ctx.documents
        editor = docs.find_editor(path) if docs and hasattr(docs, "find_editor") else None
        return editor.toPlainText() if editor else None

    def _set_error(self, error: bool) -> None:
        self.find_edit.setProperty("error", error)
        self.find_edit.style().unpolish(self.find_edit)
        self.find_edit.style().polish(self.find_edit)

    def run_search(self) -> None:
        self._timer.stop()
        keep = self._current_key()
        self._results.clear()
        self._query = self._query_now()
        try:
            self._pattern = self._query.compile()
            self._set_error(False)
        except re.error as e:
            self._pattern = None
            self._set_error(True)
            self._status.setText(f"<span style='color:#db5c5c'>Hatalı regex: {e}</span>")
            self._preview(None)
            return
        project = self.ctx.project
        if self._pattern is None or project is None:
            self._status.setText("Proje açık değil." if project is None else "")
            self._preview(None)
            return
        masks = [m for m in re.split(r"[,;]", self._mask.currentText())]
        if self._scope_open.isChecked():
            results: List[LineMatch] = []
            docs = self.ctx.documents
            for editor in (docs.editors() if docs else []):
                results += search.find_in_text(self._pattern, editor.toPlainText(), editor.path,
                                               self.MAX_RESULTS - len(results))
        else:
            results = search.search_files(project.root, self._pattern, masks, self._open_text, self.MAX_RESULTS)

        select_row = 0
        for i, m in enumerate(results):
            item = QListWidgetItem()
            item.setData(ROLE_MATCH, m)
            item.setToolTip(f"{project.relative(m.path)}:{m.line}")
            self._results.addItem(item)
            if keep == (m.path, m.line, m.column):
                select_row = i
        files = len({m.path for m in results})
        more = f" (ilk {self.MAX_RESULTS})" if len(results) >= self.MAX_RESULTS else ""
        self._status.setText(f"{len(results)} eşleşme · {files} dosya{more}" if results else "Sonuç yok")
        if results:
            self._results.setCurrentRow(select_row)
        else:
            self._preview(None)
        self._replace_all_btn.setEnabled(bool(results))
        self._replace_btn.setEnabled(bool(results))

    def _current_key(self):
        item = self._results.currentItem()
        m = item.data(ROLE_MATCH) if item else None
        return (m.path, m.line, m.column) if m else None

    def _current_match(self) -> Optional[LineMatch]:
        item = self._results.currentItem()
        return item.data(ROLE_MATCH) if item else None

    def _move(self, step: int) -> None:
        n = self._results.count()
        if n:
            self._results.setCurrentRow(min(max(self._results.currentRow() + step, 0), n - 1))

    # ---- önizleme -------------------------------------------------------- #
    def _preview(self, item) -> None:
        m: Optional[LineMatch] = item.data(ROLE_MATCH) if item else None
        editor = self._preview_editor
        if m is None:
            self._preview_title.setText("")
            if editor:
                editor.setPlainText("")
            return
        if editor is None or os.path.normcase(editor.path) != os.path.normcase(m.path):
            if editor is not None:
                editor.deleteLater()
            editor = CodeEditor(m.path, self.ctx)
            editor.setReadOnly(True)
            text = self._open_text(m.path)
            editor.setPlainText(text if text is not None else (search.read_text(m.path) or ""))
            self._preview_host.layout().addWidget(editor, 1)
            self._preview_editor = editor
        project = self.ctx.project
        rel = project.relative(m.path) if project and project.contains(m.path) else m.path
        self._preview_title.setText(f"{rel.replace(os.sep, '/')}  ·  satır {m.line}")
        editor.select_match_at(m.line, m.column, m.length)
        # yeni oluşturulan editör henüz boyutlanmamış olabilir; yerleşimden sonra tekrar ortala
        QTimer.singleShot(0, lambda e=editor, mm=m: e.select_match_at(mm.line, mm.column, mm.length)
                          if e is self._preview_editor else None)

    # ---- eylemler -------------------------------------------------------- #
    def open_current(self) -> None:
        m = self._current_match()
        if m is None or not self.ctx.documents:
            return
        doc = self.ctx.documents.open_path(m.path)
        if isinstance(doc, CodeEditor):
            doc.select_match_at(m.line, m.column, m.length)
            doc.setFocus()
        self.close()

    def replace_current(self) -> None:
        m = self._current_match()
        if m is None or self._pattern is None:
            return
        pattern, query, replacement = self._pattern, self._query, self.replace_edit.text()

        def transform(text: str):
            lines = text.split("\n")
            if m.line > len(lines):
                return text, 0
            offset = sum(len(l) + 1 for l in lines[:m.line - 1]) + m.column
            hit = pattern.match(text, offset)
            if not hit:
                return text, 0
            return text[:hit.start()] + query.expand(hit, replacement) + text[hit.end():], 1

        row = self._results.currentRow()
        apply_text_change(self.ctx, m.path, transform)
        self.run_search()
        if self._results.count():
            self._results.setCurrentRow(min(row, self._results.count() - 1))

    def replace_all(self) -> None:
        if self._pattern is None or not self._results.count():
            return
        by_file: Dict[str, int] = {}
        for i in range(self._results.count()):
            m = self._results.item(i).data(ROLE_MATCH)
            by_file[m.path] = by_file.get(m.path, 0) + 1
        total = sum(by_file.values())
        if QMessageBox.question(self, "Tümünü değiştir",
                                f"{len(by_file)} dosyada {total} eşleşme “{self.replace_edit.text()}” ile "
                                f"değiştirilecek. Devam edilsin mi?") != QMessageBox.Yes:
            return
        pattern, query, replacement = self._pattern, self._query, self.replace_edit.text()
        changed = 0
        for path in by_file:
            changed += apply_text_change(self.ctx, path, lambda text: search.replace_in_text(pattern, query, text, replacement))
        self.ctx.bus.status_message.emit(f"{len(by_file)} dosyada {changed} eşleşme değiştirildi")
        self.ctx.bus.images_changed.emit()
        self.run_search()
