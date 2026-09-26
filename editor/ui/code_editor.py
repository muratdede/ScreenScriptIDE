"""Kod editörü: satır numarası/breakpoint oluğu, otomatik girinti, Ctrl+Space tamamlama,
imza yardımı ve üzerine gelince açıklama/görsel önizleme."""

from __future__ import annotations

import html
import keyword
import os
import re
from functools import lru_cache
from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import QModelIndex, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (QColor, QCursor, QGuiApplication, QIcon, QPainter, QPen, QPixmap, QStandardItem,
                           QStandardItemModel, QTextCursor, QTextDocument, QTextFormat)
from PySide6.QtWidgets import QCompleter, QLabel, QPlainTextEdit, QTextEdit, QToolTip, QWidget

from ..core.completion import CodePosition, api_names
from ..core.context import EditorContext
from .highlighter import PythonHighlighter
from .theme import THEME

INDENT = "    "
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp")
_IDENT_BEFORE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")
_IDENT = re.compile(r"[^\W\d]\w*")
_IMAGE_LITERAL = re.compile(r"""(["'])([^"'\n]+\.(?:png|jpg|jpeg|bmp))\1""", re.IGNORECASE)


def file_stamp(path: str) -> Optional[Tuple[int, int]]:
    """Diskteki dosyanın (mtime_ns, boyut) damgası; dosya yoksa None."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def _utf16_mapper(text: str) -> Callable[[int], int]:
    """Python str indeksini QTextDocument (UTF-16) konumuna çevirir. Emoji gibi BMP dışı karakter
    yoksa ikisi aynıdır."""
    if all(ord(ch) < 0x10000 for ch in text):
        return lambda i: i
    prefix = [0]
    for ch in text:
        prefix.append(prefix[-1] + (2 if ord(ch) >= 0x10000 else 1))
    return lambda i: prefix[min(max(i, 0), len(text))]
_KIND_COLORS = {"function": "#56a8f5", "class": "#e5c07b", "keyword": "#cf8e6d", "module": "#c678dd",
                "instance": "#56b6c2", "statement": "#98c379", "param": "#d19a66", "property": "#b3ae60"}


@lru_cache(maxsize=32)
def _kind_icon(kind: str) -> QIcon:
    pm = QPixmap(16, 16)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(_KIND_COLORS.get(kind, "#7f848e")))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(1, 1, 14, 14, 3, 3)
    p.setPen(QColor("#1e1f22"))
    f = p.font()
    f.setBold(True)
    f.setPixelSize(10)
    p.setFont(f)
    p.drawText(QRect(0, 0, 16, 16), Qt.AlignCenter, (kind[:1] or "?").upper())
    p.end()
    return QIcon(pm)


class _Gutter(QWidget):
    """Satır numaraları + breakpoint noktaları + debug oku."""

    def __init__(self, editor: "CodeEditor"):
        super().__init__(editor)
        self._editor = editor
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Breakpoint eklemek/kaldırmak için tıklayın (F9)")

    def sizeHint(self):
        return QSize(self._editor.gutter_width(), 0)

    def paintEvent(self, event):
        self._editor.paint_gutter(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._editor.toggle_breakpoint_at_y(event.position().y())


class SignaturePopup(QLabel):
    """Parametre bilgisi kutusu. QToolTip yerine kullanılır çünkü:
    - satırın üstüne (sığmazsa altına) yerleşir, yazılan metni kapatmaz;
    - fareyle üzerine gelince kaybolmaz (girdiye kapalıdır, tıklamalar alttaki editöre geçer)."""

    def __init__(self, editor: "CodeEditor"):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowTransparentForInput |
                         Qt.WindowDoesNotAcceptFocus)
        self._editor = editor
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setTextFormat(Qt.RichText)
        self.setWordWrap(True)
        self.setContentsMargins(10, 6, 10, 6)
        self.setFont(THEME.code_font(10))
        self.setStyleSheet("color: #dfe1e5; background: transparent;")

    MAX_WIDTH = 640

    def show_for(self, body: str) -> None:
        self.setText(body)
        doc = QTextDocument()
        doc.setDefaultFont(self.font())
        doc.setHtml(body)
        m = self.contentsMargins()
        width = min(self.MAX_WIDTH, int(doc.idealWidth()) + m.left() + m.right() + 4)
        self.setFixedWidth(width)
        self.setFixedHeight(self.heightForWidth(width))
        self.reposition()
        self.show()
        self.raise_()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QColor("#43454a"))
        p.setBrush(THEME.surface)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
        p.end()
        super().paintEvent(event)

    def reposition(self) -> None:
        ed = self._editor
        rect = ed.cursorRect()
        top_left = ed.viewport().mapToGlobal(rect.topLeft())
        bottom = ed.viewport().mapToGlobal(rect.bottomLeft())
        screen = QGuiApplication.screenAt(top_left) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = min(max(area.left(), top_left.x() - 12), area.right() - self.width())
        y = top_left.y() - self.height() - 3           # tercihen satırın üstü
        if y < area.top():
            y = bottom.y() + 4                          # sığmazsa altı
        self.move(x, y)


class CodeEditor(QPlainTextEdit):
    ROLE_NAME = Qt.UserRole + 1

    def __init__(self, path: str, ctx: EditorContext, parent=None):
        super().__init__(parent)
        self._path = os.path.abspath(path)
        self._ctx = ctx
        self.disk_stamp: Optional[Tuple[int, int]] = None   # son okunan/yazılan halin damgası
        self._debug_line = 0
        self._find_bar = None   # bul/değiştir çubuğu (ilk Ctrl+F'de oluşturulur)
        self._carets: List[QTextCursor] = []   # çoklu imleç modu (boş = normal)
        self._multi_typing = False

        self.setFont(THEME.code_font())
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self._highlighter = PythonHighlighter(self.document(), api_names()) if path.endswith(".py") else None

        self._gutter = _Gutter(self)
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._on_update_request)
        self.cursorPositionChanged.connect(self._update_extra_selections)
        self._update_gutter_width()
        ctx.breakpoints.changed.connect(self._on_breakpoints_changed)
        ctx.breakpoints.muted_changed.connect(lambda _m: self._gutter.update())

        # tamamlama
        self._model = QStandardItemModel(self)
        self._completer = QCompleter(self._model, self)
        self._completer.setWidget(self)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setMaxVisibleItems(12)
        self._completer.activated[QModelIndex].connect(self._insert_completion)
        self._completer.popup().setFont(THEME.code_font(10))
        self._auto_timer = QTimer(self, singleShot=True, interval=180)
        self._auto_timer.timeout.connect(lambda: self.show_completions(explicit=False))

        # parametre bilgisi: açıkken imleç hareket ettikçe güncellenir, çağrının dışına çıkınca kapanır
        self._sig_popup = SignaturePopup(self)
        self._sig_timer = QTimer(self, singleShot=True, interval=120)
        self._sig_timer.timeout.connect(lambda: self.show_signature(refresh=True))
        self.cursorPositionChanged.connect(self._on_cursor_for_signature)
        self.verticalScrollBar().valueChanged.connect(self._on_scroll_for_signature)
        self.horizontalScrollBar().valueChanged.connect(self._on_scroll_for_signature)
        self.destroyed.connect(self._sig_popup.deleteLater)

        # bul / değiştir (Ctrl+F / Ctrl+R)
        self._find_ranges: List[Tuple[int, int]] = []   # QTextDocument konumları (başlangıç, bitiş)
        self._find_query = None
        self._find_pattern = None
        self._find_timer = QTimer(self, singleShot=True, interval=150)
        self._find_timer.timeout.connect(lambda: self.find_update(select=False))
        self.document().contentsChanged.connect(self._on_contents_for_find)

        # çoklu imleç: orta tuşla / Alt ile sürükleyerek sütun seçimi, Alt+Shift+tık ile imleç ekleme
        self._column_anchor: Optional[QPoint] = None
        self.setCursorWidth(2)

        # Ctrl+tık ile tanıma git: Ctrl basılıyken imlecin altındaki isim bağlantı gibi çizilir
        self._link: Optional[Tuple[int, int]] = None
        self.viewport().setMouseTracking(True)

    # ---- dosya ----------------------------------------------------------- #
    @property
    def path(self) -> str:
        return self._path

    def set_path(self, path: str) -> None:
        self._path = os.path.abspath(path)

    @property
    def is_dirty(self) -> bool:
        return self.document().isModified()

    def load(self) -> None:
        with open(self._path, encoding="utf-8", errors="replace") as f:
            self.setPlainText(f.read())
        self.document().setModified(False)
        self.disk_stamp = file_stamp(self._path)

    def save(self) -> None:
        text = self.toPlainText()
        if not text.endswith("\n"):
            text += "\n"
        with open(self._path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        self.document().setModified(False)
        self.disk_stamp = file_stamp(self._path)

    def reload_from_disk(self) -> None:
        """Dosya dışarıda değişti: içeriği yeniden oku; imleç, kaydırma ve geri alma geçmişi korunur."""
        with open(self._path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        self.disk_stamp = file_stamp(self._path)
        if text == self.toPlainText():
            self.document().setModified(False)
            return
        cursor = self.textCursor()
        line, col = cursor.blockNumber(), cursor.positionInBlock()
        vbar, hbar = self.verticalScrollBar().value(), self.horizontalScrollBar().value()
        c = QTextCursor(self.document())
        c.beginEditBlock()
        c.select(QTextCursor.Document)
        c.insertText(text)
        c.endEditBlock()
        self.document().setModified(False)
        block = self.document().findBlockByNumber(min(line, self.blockCount() - 1))
        c = QTextCursor(block)
        c.movePosition(QTextCursor.Right, n=min(col, block.length() - 1))
        self.setTextCursor(c)
        self.verticalScrollBar().setValue(vbar)
        self.horizontalScrollBar().setValue(hbar)

    # ---- konum ----------------------------------------------------------- #
    def current_line(self) -> int:
        return self.textCursor().blockNumber() + 1

    def go_to_line(self, line: int) -> None:
        block = self.document().findBlockByNumber(max(0, line - 1))
        if block.isValid():
            cursor = QTextCursor(block)
            self.setTextCursor(cursor)
            self.centerCursor()

    def set_debug_line(self, line: int) -> None:
        self._debug_line = line
        if line:
            self.go_to_line(line)
        self._update_extra_selections()
        self._gutter.update()

    # ---- oluk ------------------------------------------------------------ #
    def gutter_width(self) -> int:
        digits = max(3, len(str(self.blockCount())))
        return 24 + self.fontMetrics().horizontalAdvance("9") * digits + 8

    def _find_bar_height(self) -> int:
        bar = self._find_bar
        return bar.sizeHint().height() if bar is not None and bar.isVisible() else 0

    def _update_gutter_width(self, *_):
        self.setViewportMargins(self.gutter_width(), self._find_bar_height(), 0, 0)

    def _on_update_request(self, rect, dy):
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.relayout_find_bar()

    def relayout_find_bar(self) -> None:
        cr = self.contentsRect()
        top = self._find_bar_height()
        self._update_gutter_width()
        self._gutter.setGeometry(QRect(cr.left(), cr.top() + top, self.gutter_width(), cr.height() - top))
        if self._find_bar is not None and self._find_bar.isVisible():
            self._find_bar.setGeometry(QRect(cr.left(), cr.top(), cr.width(), top))

    def paint_gutter(self, event):
        painter = QPainter(self._gutter)
        painter.fillRect(event.rect(), THEME.background)
        painter.setRenderHint(QPainter.Antialiasing)
        breakpoints = self._ctx.breakpoints.lines(self._path)
        muted = self._ctx.breakpoints.muted
        current = self.current_line()
        block = self.firstVisibleBlock()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        line_h = self.fontMetrics().height()
        width = self._gutter.width()
        while block.isValid() and top <= event.rect().bottom():
            height = round(self.blockBoundingRect(block).height())
            number = block.blockNumber() + 1
            if block.isVisible():
                if number in breakpoints:
                    d = min(12, line_h - 4)
                    if muted:   # geçici kapalı: içi boş halka
                        painter.setBrush(Qt.NoBrush)
                        painter.setPen(QPen(THEME.breakpoint, 1.5))
                        painter.drawEllipse(QRectF(6.75, top + (line_h - d) / 2 + 0.75, d - 1.5, d - 1.5))
                    else:
                        painter.setBrush(THEME.breakpoint)
                        painter.setPen(Qt.NoPen)
                        painter.drawEllipse(6, top + (line_h - d) // 2, d, d)
                if number == self._debug_line:
                    painter.setBrush(THEME.debug_arrow)
                    painter.setPen(Qt.NoPen)
                    y = top + line_h // 2
                    painter.drawPolygon([QPointF(4, y - 5), QPointF(14, y), QPointF(4, y + 5)])
                painter.setPen(THEME.line_number_current if number == current else THEME.line_number)
                painter.drawText(0, top, width - 8, line_h, Qt.AlignRight | Qt.AlignVCenter, str(number))
            block = block.next()
            top += height
        painter.end()

    def toggle_breakpoint_at_y(self, y: float) -> None:
        # oluk ve viewport aynı üst kenardan başlar; y doğrudan viewport koordinatıdır
        block = self.cursorForPosition(QPoint(0, int(y))).block()
        self._ctx.breakpoints.toggle(self._path, block.blockNumber() + 1)

    def toggle_breakpoint(self) -> None:
        self._ctx.breakpoints.toggle(self._path, self.current_line())

    def _on_breakpoints_changed(self, path: str) -> None:
        if os.path.normcase(os.path.abspath(path)) == os.path.normcase(self._path):
            self._gutter.update()

    # ---- vurgular -------------------------------------------------------- #
    def _update_extra_selections(self):
        selections = []
        cur = QTextEdit.ExtraSelection()
        cur.format.setBackground(THEME.current_line)
        cur.format.setProperty(QTextFormat.FullWidthSelection, True)
        cur.cursor = self.textCursor()
        cur.cursor.clearSelection()
        selections.append(cur)
        if self._debug_line:
            block = self.document().findBlockByNumber(self._debug_line - 1)
            if block.isValid():
                dbg = QTextEdit.ExtraSelection()
                dbg.format.setBackground(THEME.debug_line)
                dbg.format.setProperty(QTextFormat.FullWidthSelection, True)
                dbg.cursor = QTextCursor(block)
                selections.append(dbg)
        if self._find_ranges and self._find_bar is not None and self._find_bar.isVisible():
            for start, end in self._find_ranges[:self.MAX_HIGHLIGHTS]:
                sel = QTextEdit.ExtraSelection()
                sel.format.setBackground(THEME.find_match)
                sel.cursor = self._range_cursor(start, end)
                selections.append(sel)
        for c in self._carets[:-1]:   # birincil imlecin seçimini Qt çizer
            if c.hasSelection():
                sel = QTextEdit.ExtraSelection()
                sel.format.setBackground(THEME.selection)
                sel.cursor = QTextCursor(c)
                selections.append(sel)
        if self._link:
            link = QTextEdit.ExtraSelection()
            link.format.setForeground(QColor("#548af7"))
            link.format.setFontUnderline(True)
            link.cursor = self._range_cursor(*self._link)
            selections.append(link)
        self.setExtraSelections(selections)
        self._gutter.update()

    def _range_cursor(self, start: int, end: int) -> QTextCursor:
        c = QTextCursor(self.document())
        c.setPosition(start)
        c.setPosition(end, QTextCursor.KeepAnchor)
        return c

    # ---- bul / değiştir (Ctrl+F / Ctrl+R) -------------------------------- #
    MAX_HIGHLIGHTS = 3000

    def show_find(self, replace: bool = False) -> None:
        from .find_bar import FindBar
        self.exit_multi_caret()
        if self._find_bar is None:
            self._find_bar = FindBar(self)
        bar = self._find_bar
        selected = self.textCursor().selectedText()
        if selected and " " not in selected:   # tek satırlık seçim arama metni olur
            bar.find_edit.blockSignals(True)
            bar.find_edit.setText(selected)
            bar.find_edit.blockSignals(False)
        bar.show()
        bar.set_replace_mode(replace)
        self.relayout_find_bar()
        bar.find_edit.setFocus()
        bar.find_edit.selectAll()
        self.find_update(select=not self.textCursor().hasSelection())

    def hide_find(self) -> None:
        if self._find_bar is not None and self._find_bar.isVisible():
            self._find_bar.hide()
            self._find_ranges = []
            self.relayout_find_bar()
            self._update_extra_selections()
            self.setFocus()

    @property
    def find_visible(self) -> bool:
        return self._find_bar is not None and self._find_bar.isVisible()

    def _on_contents_for_find(self) -> None:
        if self.find_visible:
            self._find_timer.start()

    def find_update(self, select: bool = True) -> None:
        """Eşleşmeleri yeniden hesaplar ve vurgular. select: imleçten sonraki ilk eşleşmeyi seç."""
        bar = self._find_bar
        if bar is None:
            return
        query = bar.query()
        try:
            pattern = query.compile()
        except re.error:
            self._find_ranges, self._find_pattern = [], None
            bar.set_count(0, 0, "Hatalı regex")
            self._update_extra_selections()
            return
        self._find_query, self._find_pattern = query, pattern
        self._find_ranges = []
        if pattern is not None:
            text = self.toPlainText()
            to_doc = _utf16_mapper(text)
            for m in pattern.finditer(text):
                if m.end() > m.start():
                    self._find_ranges.append((to_doc(m.start()), to_doc(m.end())))
        if select and self._find_ranges:
            start = self.textCursor().selectionStart()
            index = next((i for i, (s, _e) in enumerate(self._find_ranges) if s >= start), 0)
            self._select_match(index)
        else:
            self._update_find_count()
            self._update_extra_selections()

    def _current_match_index(self) -> int:
        c = self.textCursor()
        key = (c.selectionStart(), c.selectionEnd())
        try:
            return self._find_ranges.index(key)
        except ValueError:
            return -1

    def _update_find_count(self) -> None:
        if self._find_bar is not None:
            i = self._current_match_index()
            self._find_bar.set_count(i + 1 if i >= 0 else 0, len(self._find_ranges))

    def _select_match(self, index: int) -> None:
        start, end = self._find_ranges[index]
        self.setTextCursor(self._range_cursor(start, end))
        if not self.viewport().rect().contains(self.cursorRect()):
            self.centerCursor()
        self._update_find_count()
        self._update_extra_selections()

    def find_next(self) -> None:
        self._find_step(+1)

    def find_previous(self) -> None:
        self._find_step(-1)

    def _find_step(self, direction: int) -> None:
        if self._find_bar is None:
            self.show_find()
            return
        if not self._find_ranges:
            self.find_update(select=False)
        if not self._find_ranges:
            return
        c = self.textCursor()
        if direction > 0:
            pos = c.selectionEnd()
            index = next((i for i, (s, _e) in enumerate(self._find_ranges) if s >= pos), 0)   # sona gelince başa
        else:
            pos = c.selectionStart()
            index = next((i for i in range(len(self._find_ranges) - 1, -1, -1)
                          if self._find_ranges[i][1] <= pos), len(self._find_ranges) - 1)
        self._select_match(index)

    def _replacement_for(self, doc_start: int, doc_end: int) -> Optional[str]:
        """Seçili aralık bir eşleşmeyse yerine konacak metni döndürür (regex grupları açılır)."""
        cursor = self._range_cursor(doc_start, doc_end)
        m = self._find_pattern.fullmatch(cursor.selectedText().replace(" ", "\n"))
        return self._find_query.expand(m, self._find_bar.replace_edit.text()) if m else None

    def replace_current(self) -> None:
        if self._find_bar is None or self._find_pattern is None:
            return
        index = self._current_match_index()
        if index < 0:
            self.find_next()
            return
        start, end = self._find_ranges[index]
        text = self._replacement_for(start, end)
        if text is not None:
            c = self._range_cursor(start, end)
            c.insertText(text)
            self.setTextCursor(c)   # imleç değiştirilen metnin sonunda: sıradaki eşleşmeye geç
        self.find_update(select=False)
        if self._find_ranges:
            self.find_next()

    def replace_all(self) -> None:
        if self._find_bar is None or self._find_pattern is None or not self._find_ranges:
            return
        count = 0
        edit = QTextCursor(self.document())
        edit.beginEditBlock()   # tek Ctrl+Z ile geri alınır
        for start, end in reversed(self._find_ranges):
            text = self._replacement_for(start, end)
            if text is not None:
                c = self._range_cursor(start, end)
                c.insertText(text)
                count += 1
        edit.endEditBlock()
        self.find_update(select=False)
        self._ctx.bus.status_message.emit(f"{count} eşleşme değiştirildi")

    def replace_document_text(self, text: str) -> None:
        """Tüm metni tek bir geri alınabilir adımda değiştirir; imleç satırı korunur."""
        line = self.current_line()
        c = QTextCursor(self.document())
        c.beginEditBlock()
        c.select(QTextCursor.Document)
        c.insertText(text)
        c.endEditBlock()
        self.go_to_line(line)

    def select_match_at(self, line: int, column: int, length: int) -> None:
        """Dosyalarda bul sonucundan gelince: satır + (Python str) sütun + uzunluk aralığını seç."""
        block = self.document().findBlockByNumber(max(0, line - 1))
        if not block.isValid():
            return
        to_doc = _utf16_mapper(block.text())
        start = block.position() + to_doc(column)
        end = block.position() + to_doc(min(len(block.text()), column + length))
        self.setTextCursor(self._range_cursor(start, end))
        self.centerCursor()

    # ---- Ctrl+tık: tanıma git -------------------------------------------- #
    def _link_range_at(self, pos: QPoint) -> Optional[Tuple[int, int]]:
        cursor = self.cursorForPosition(pos)
        # imleç satır sonunun ötesindeyse bağlantı yok
        if self.cursorRect(cursor).right() + self.fontMetrics().horizontalAdvance("M") < pos.x():
            return None
        block = cursor.block()
        text, col = block.text(), cursor.positionInBlock()
        to_doc = _utf16_mapper(text)
        for m in _IMAGE_LITERAL.finditer(text):   # "images/x.png" -> görseli aç
            if m.start() <= col <= m.end():
                return block.position() + to_doc(m.start()), block.position() + to_doc(m.end())
        for m in _IDENT.finditer(text):
            if m.start() <= col <= m.end() and not keyword.iskeyword(m.group(0)):
                return block.position() + to_doc(m.start()), block.position() + to_doc(m.end())
        return None

    def _set_link(self, rng: Optional[Tuple[int, int]]) -> None:
        if rng != self._link:
            self._link = rng
            self.viewport().setCursor(Qt.PointingHandCursor if rng else Qt.IBeamCursor)
            self._update_extra_selections()

    def mouseMoveEvent(self, e):
        if self._column_anchor is not None:
            self._build_column_carets(self._column_anchor, e.position().toPoint())
            return
        if e.modifiers() & Qt.ControlModifier and not e.buttons():
            self._set_link(self._link_range_at(e.position().toPoint()))
        elif self._link:
            self._set_link(None)
        super().mouseMoveEvent(e)

    def mousePressEvent(self, e):
        mods = e.modifiers()
        pos = e.position().toPoint()
        if e.button() == Qt.LeftButton and mods & Qt.ControlModifier and not mods & Qt.AltModifier:
            rng = self._link_range_at(pos)
            if rng:
                self._set_link(None)
                self.go_to_definition(rng[0], e.globalPosition().toPoint())
                return
        # orta tuş ya da Alt+sürükle: sütun (blok) seçimi -> çoklu imleç
        if e.button() == Qt.MiddleButton or (e.button() == Qt.LeftButton and mods == Qt.AltModifier):
            self.setFocus()
            self._column_anchor = pos
            self._build_column_carets(pos, pos)
            return
        # Alt+Shift+tık: imleç ekle / kaldır
        if e.button() == Qt.LeftButton and mods == (Qt.AltModifier | Qt.ShiftModifier):
            self._toggle_caret_at(pos)
            return
        if self._carets:
            self.exit_multi_caret()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if self._column_anchor is not None:
            self._column_anchor = None
            if len(self._carets) <= 1:   # tek satır: normal seçime dön
                single = self._carets[0] if self._carets else None
                self.exit_multi_caret()
                if single is not None:
                    self.setTextCursor(single)
            return
        super().mouseReleaseEvent(e)

    # ---- çoklu imleç (orta tuş / Alt ile sütun seçimi) --------------------- #
    @property
    def multi_caret(self) -> bool:
        return len(self._carets) > 1

    def _block_center_y(self, block) -> int:
        geo = self.blockBoundingGeometry(block).translated(self.contentOffset())
        return int(geo.top() + self.fontMetrics().height() / 2)

    def _build_column_carets(self, p1: QPoint, p2: QPoint) -> None:
        """p1-p2 dikdörtgenindeki her satır için bir imleç (kısa satırlarda satır sonunda)."""
        self._multi_typing = False
        doc = self.document()
        b1 = self.cursorForPosition(QPoint(0, p1.y())).block()
        b2 = self.cursorForPosition(QPoint(0, p2.y())).block()
        step_down = b2.blockNumber() >= b1.blockNumber()
        x1, x2 = max(0, p1.x()), max(0, p2.x())
        carets = []
        block = b1
        while block.isValid():
            y = self._block_center_y(block)
            c = QTextCursor(doc)
            c.setPosition(self._block_pos_at_x(block, x1, y))
            c.setPosition(self._block_pos_at_x(block, x2, y), QTextCursor.KeepAnchor)
            carets.append(c)
            if block == b2:
                break
            block = block.next() if step_down else block.previous()
        self._set_carets(carets)

    def _block_pos_at_x(self, block, x: int, y: int) -> int:
        pos = self.cursorForPosition(QPoint(x, y))
        if pos.block() != block:   # görünür alan dışındaki satırlar: sütunu satır içinde hesapla
            col = round(x / max(1, self.fontMetrics().horizontalAdvance(" ")))
            return block.position() + min(col, len(block.text()))
        return pos.position()

    def _toggle_caret_at(self, pos: QPoint) -> None:
        new = self.cursorForPosition(pos)
        carets = self._carets or [QTextCursor(self.textCursor())]
        kept = [c for c in carets if c.position() != new.position()]
        if len(kept) == len(carets):
            kept.append(new)
        self._set_carets(kept or [new])

    def _set_carets(self, carets: List[QTextCursor]) -> None:
        unique, seen = [], set()
        for c in carets:
            key = (c.anchor(), c.position())
            if key not in seen:
                seen.add(key)
                unique.append(c)
        self._carets = unique
        if unique:
            self.setTextCursor(QTextCursor(unique[-1]))   # birincil imleç (kaydırma bunu izler)
        self._update_extra_selections()
        self.viewport().update()

    def exit_multi_caret(self) -> None:
        self._multi_typing = False
        if self._carets:
            self._carets = []
            self._update_extra_selections()
            self.viewport().update()

    def _multi_edit(self, fn, typing: bool = False) -> None:
        """fn(cursor, index) her imlece uygulanır; tek geri alma adımıdır. Art arda yazılan
        karakterler (typing=True) normal editördeki gibi tek adımda birleşir."""
        ordered = sorted(self._carets, key=lambda c: c.position())
        edit = QTextCursor(self.document())
        if typing and self._multi_typing:
            edit.joinPreviousEditBlock()
        else:
            edit.beginEditBlock()
        self._multi_typing = typing
        for i, c in enumerate(ordered):
            fn(c, i)
        edit.endEditBlock()
        self._set_carets(self._carets)

    def _multi_key(self, e) -> bool:
        """Çoklu imleç modunda tuşu işler; işlenmediyse False (normal davranış)."""
        key, mods = e.key(), e.modifiers()
        ctrl, shift = bool(mods & Qt.ControlModifier), bool(mods & Qt.ShiftModifier)
        alt = bool(mods & Qt.AltModifier)
        if key == Qt.Key_Escape or key in (Qt.Key_Return, Qt.Key_Enter) or \
                (ctrl and key in (Qt.Key_Z, Qt.Key_Y, Qt.Key_A)):
            self.exit_multi_caret()
            return key == Qt.Key_Escape
        if ctrl and key == Qt.Key_C:
            self._multi_copy()
            return True
        if ctrl and key == Qt.Key_X:
            self._multi_copy()
            self._multi_edit(lambda c, _i: c.removeSelectedText())
            return True
        if ctrl and key == Qt.Key_V:
            self._multi_paste()
            return True
        if key == Qt.Key_Backspace:
            def back(c, _i):
                if c.hasSelection():
                    c.removeSelectedText()
                elif c.positionInBlock() > 0:   # satır başında satırları birleştirme
                    if ctrl:
                        c.movePosition(QTextCursor.PreviousWord, QTextCursor.KeepAnchor)
                        c.removeSelectedText()
                    else:
                        c.deletePreviousChar()
            self._multi_edit(back)
            return True
        if key == Qt.Key_Delete:
            def delete(c, _i):
                if c.hasSelection():
                    c.removeSelectedText()
                elif not c.atBlockEnd():
                    c.deleteChar()
            self._multi_edit(delete)
            return True
        moves = {Qt.Key_Left: QTextCursor.PreviousWord if ctrl else QTextCursor.Left,
                 Qt.Key_Right: QTextCursor.NextWord if ctrl else QTextCursor.Right,
                 Qt.Key_Home: QTextCursor.StartOfLine, Qt.Key_End: QTextCursor.EndOfLine,
                 Qt.Key_Up: QTextCursor.Up, Qt.Key_Down: QTextCursor.Down}
        if key in moves:
            self._multi_typing = False
            mode = QTextCursor.KeepAnchor if shift else QTextCursor.MoveAnchor
            for c in self._carets:
                if not shift and c.hasSelection() and key in (Qt.Key_Left, Qt.Key_Right):
                    c.setPosition(c.selectionStart() if key == Qt.Key_Left else c.selectionEnd())
                else:
                    c.movePosition(moves[key], mode)
            self._set_carets(self._carets)
            return True
        if key == Qt.Key_Tab and not mods:
            self._multi_edit(lambda c, _i: c.insertText(INDENT))
            return True
        text = e.text()
        # AltGr (Ctrl+Alt) ile yazılan karakterler (@, {, [ ...) de metindir
        if text and text.isprintable() and (not ctrl or alt):
            self._multi_edit(lambda c, _i: c.insertText(text), typing=True)
            return True
        return False

    def _multi_copy(self) -> None:
        ordered = sorted(self._carets, key=lambda c: c.position())
        QGuiApplication.clipboard().setText("\n".join(c.selectedText().replace(" ", "\n") for c in ordered))

    def _multi_paste(self) -> None:
        clip = QGuiApplication.clipboard().text()
        lines = clip.split("\n")
        if lines and lines[-1] == "" and len(lines) > 1:
            lines.pop()
        per_line = len(lines) == len(self._carets)   # satır sayısı imleç sayısına eşitse her satıra biri
        self._multi_edit(lambda c, i: c.insertText(lines[i] if per_line else clip))

    def paintEvent(self, e):
        super().paintEvent(e)
        if len(self._carets) > 1:
            p = QPainter(self.viewport())
            for c in self._carets[:-1]:   # birincil imleci Qt çizer
                r = self.cursorRect(c)
                p.fillRect(QRect(r.x(), r.y(), 2, r.height()), THEME.text)
            p.end()

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key_Control:
            self._set_link(None)
        super().keyReleaseEvent(e)

    def leaveEvent(self, e):
        self._set_link(None)
        super().leaveEvent(e)

    def go_to_definition(self, doc_pos: Optional[int] = None, popup_at: Optional[QPoint] = None) -> None:
        """doc_pos verilmezse imleçteki isim. Görsel literal'i ise görseli sekmede açar."""
        cursor = QTextCursor(self.document())
        cursor.setPosition(self.textCursor().position() if doc_pos is None else doc_pos)
        block, col = cursor.block(), cursor.positionInBlock()
        for m in _IMAGE_LITERAL.finditer(block.text()):
            if m.start() <= col <= m.end():
                path = self._resolve_image(m.group(2))
                if path:
                    self._ctx.bus.open_file_requested.emit(path, 0)
                else:
                    self._no_definition(f"Görsel bulunamadı: {m.group(2)}", popup_at)
                return
        # jedi sütunu kelimenin içinde olmalı
        pos = CodePosition(self.toPlainText(), block.blockNumber() + 1, col + 1 if doc_pos is not None else col,
                           self._path)
        target = self._ctx.completion.definition(pos)
        if target is None:
            self._no_definition("Tanım bulunamadı", popup_at)
            return
        editor = self if os.path.normcase(target.path) == os.path.normcase(self._path) else None
        if editor is None:
            editor = self._ctx.documents.open_path(target.path) if self._ctx.documents else None
        if isinstance(editor, CodeEditor):
            editor.select_match_at(target.line, target.column, len(target.name) or 1)
            editor.setFocus()

    def _no_definition(self, message: str, at: Optional[QPoint]) -> None:
        at = at or self.viewport().mapToGlobal(self.cursorRect().bottomLeft())
        QToolTip.showText(at, message, self)

    # ---- klavye ---------------------------------------------------------- #
    def keyPressEvent(self, event):
        popup = self._completer.popup()
        key, mods = event.key(), event.modifiers()
        if self._carets and self._multi_key(event):
            return

        if popup.isVisible() and key in (Qt.Key_Enter, Qt.Key_Return, Qt.Key_Escape, Qt.Key_Tab, Qt.Key_Backtab):
            if key == Qt.Key_Tab and popup.currentIndex().isValid():
                self._insert_completion(popup.currentIndex())
                popup.hide()
                return
            event.ignore()
            return
        if key == Qt.Key_Escape and self._sig_popup.isVisible():
            self.hide_signature()
            return
        if key == Qt.Key_Escape and self.find_visible:
            self.hide_find()
            return
        if key == Qt.Key_Control:
            local = self.viewport().mapFromGlobal(QCursor.pos())
            if self.viewport().rect().contains(local):
                self._set_link(self._link_range_at(local))

        if key == Qt.Key_Space and mods & Qt.ControlModifier and mods & Qt.ShiftModifier:
            self.show_signature(force=True)
            return
        if key == Qt.Key_Space and mods & Qt.ControlModifier:
            self.show_completions(explicit=True)
            return
        if key == Qt.Key_Slash and mods & Qt.ControlModifier:
            self.toggle_comment()
            return
        if key == Qt.Key_Tab and not mods:
            self._indent(True)
            return
        if key == Qt.Key_Backtab:
            self._indent(False)
            return
        if key in (Qt.Key_Return, Qt.Key_Enter) and not popup.isVisible():
            self._newline_with_indent()
            return
        if key == Qt.Key_Backspace and not mods and self._smart_backspace():
            return

        super().keyPressEvent(event)
        self._after_key(event.text())

    def _after_key(self, text: str) -> None:
        popup = self._completer.popup()
        prefix = self._word_before_cursor()
        if text == ".":
            self._auto_timer.start(0)
        elif popup.isVisible():
            if not prefix:
                popup.hide()
            else:
                self._completer.setCompletionPrefix(prefix)
                if self._completer.completionCount() == 0:
                    popup.hide()
                else:
                    popup.setCurrentIndex(self._completer.completionModel().index(0, 0))
        elif text and (text.isalnum() or text == "_") and len(prefix) >= 3:
            self._auto_timer.start()

        if text in ("(", ","):
            self._sig_timer.stop()
            self.show_signature()

    def _word_before_cursor(self) -> str:
        cursor = self.textCursor()
        before = cursor.block().text()[:cursor.positionInBlock()]
        m = _IDENT_BEFORE.search(before)
        return m.group(0) if m else ""

    def _position(self) -> CodePosition:
        c = self.textCursor()
        return CodePosition(self.toPlainText(), c.blockNumber() + 1, c.positionInBlock(), self._path)

    # ---- tamamlama ------------------------------------------------------- #
    def show_completions(self, explicit: bool = True) -> None:
        if not self._path.endswith(".py"):
            return
        items = self._ctx.completion.complete(self._position())
        prefix = self._word_before_cursor()
        if not explicit and not items:
            self._completer.popup().hide()
            return
        self._model.clear()
        for it in items[:400]:
            row = QStandardItem(_kind_icon(it.kind), it.name)
            row.setData(it.name, self.ROLE_NAME)
            row.setToolTip(f"{it.kind}: {it.detail}" if it.detail else it.kind)
            row.setEditable(False)
            self._model.appendRow(row)
        self._completer.setCompletionPrefix(prefix)
        if self._completer.completionCount() == 0:
            self._completer.popup().hide()
            return
        popup = self._completer.popup()
        popup.setCurrentIndex(self._completer.completionModel().index(0, 0))
        rect = self.cursorRect()
        rect.setWidth(max(260, popup.sizeHintForColumn(0) + popup.verticalScrollBar().sizeHint().width() + 24))
        rect.translate(self.gutter_width(), 0)
        self._completer.complete(rect)

    def _insert_completion(self, index) -> None:
        name = index.data(self.ROLE_NAME) or index.data()
        if not name:
            return
        prefix = self._word_before_cursor()
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.Left, QTextCursor.KeepAnchor, len(prefix))
        cursor.insertText(name)
        self.setTextCursor(cursor)

    def show_signature(self, force: bool = False, refresh: bool = False) -> None:
        """force: Ctrl+Shift+Space ile açıkça istendi. refresh: açık kutuyu imlece göre güncelle."""
        if not self._path.endswith(".py"):
            return
        if refresh and not self._sig_popup.isVisible():
            return
        sig = self._ctx.completion.signature(self._position())
        if sig is None:
            if force or refresh:
                self.hide_signature()
            return
        parts = []
        for i, p in enumerate(sig.params):
            p = html.escape(p)
            parts.append(f"<b style='color:#ffffff'><u>{p}</u></b>" if i == sig.active
                         else f"<span style='color:#a9adb5'>{p}</span>")
        name = html.escape(sig.label.split("(", 1)[0])
        body = f"<span style='color:#56a8f5'>{name}</span>({', '.join(parts)})"
        if sig.doc:
            doc = html.escape(sig.doc.strip()[:300]).replace("\n", "<br>")
            body += f"<div style='color:#868a91; margin-top:4px; font-family:sans-serif'>{doc}</div>"
        self._sig_popup.show_for(body)

    def hide_signature(self) -> None:
        self._sig_timer.stop()
        self._sig_popup.hide()

    def _on_cursor_for_signature(self) -> None:
        if self._sig_popup.isVisible():
            self._sig_timer.start()

    def _on_scroll_for_signature(self, *_):
        if self._sig_popup.isVisible():
            if self.viewport().rect().contains(self.cursorRect()):
                self._sig_popup.reposition()
            else:
                self.hide_signature()

    def focusOutEvent(self, event):
        # tamamlama listesi açılırken odak kısa süre popup'a geçer; o durumda kutuyu kapatma
        if event.reason() != Qt.PopupFocusReason:
            self.hide_signature()
        super().focusOutEvent(event)

    def hideEvent(self, event):
        self.hide_signature()
        super().hideEvent(event)

    # ---- sürükle-bırak: görsel dosyası -> "göreli/yol.png" ---------------- #
    @staticmethod
    def _image_paths(mime) -> list:
        if not mime.hasUrls():
            return []
        paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
        return [p for p in paths if p.lower().endswith(IMAGE_EXTENSIONS)]

    def image_reference(self, path: str) -> str:
        """Script'in çalışma klasörü proje köküdür; proje içindeki görsel köke göre göreli yazılır."""
        project = self._ctx.project
        if project and project.contains(path):
            path = project.relative(path)
        return path.replace("\\", "/")

    def canInsertFromMimeData(self, source) -> bool:
        return bool(self._image_paths(source)) or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source) -> None:
        paths = self._image_paths(source)
        if not paths:
            super().insertFromMimeData(source)
            return
        self.textCursor().insertText(", ".join(f'"{self.image_reference(p)}"' for p in paths))

    def dropEvent(self, event):
        super().dropEvent(event)
        self.activateWindow()
        self.setFocus()

    # ---- düzenleme yardımcıları ----------------------------------------- #
    def _newline_with_indent(self) -> None:
        cursor = self.textCursor()
        text = cursor.block().text()[:cursor.positionInBlock()]
        indent = text[:len(text) - len(text.lstrip())]
        stripped = text.rstrip()
        if stripped.endswith(":"):
            indent += INDENT
        elif stripped.split(" ", 1)[0] in ("return", "pass", "break", "continue", "raise") and indent:
            indent = indent[:-len(INDENT)] if indent.endswith(INDENT) else indent
        cursor.insertText("\n" + indent)
        self.ensureCursorVisible()

    def _smart_backspace(self) -> bool:
        cursor = self.textCursor()
        if cursor.hasSelection():
            return False
        before = cursor.block().text()[:cursor.positionInBlock()]
        if before and not before.strip() and len(before) % len(INDENT) == 0:
            cursor.movePosition(QTextCursor.Left, QTextCursor.KeepAnchor, len(INDENT))
            cursor.removeSelectedText()
            return True
        return False

    def _selected_blocks(self):
        cursor = self.textCursor()
        doc = self.document()
        start = doc.findBlock(cursor.selectionStart())
        end = doc.findBlock(cursor.selectionEnd())
        if cursor.hasSelection() and cursor.selectionEnd() == end.position() and end != start:
            end = end.previous()
        block = start
        blocks = []
        while block.isValid():
            blocks.append(block)
            if block == end:
                break
            block = block.next()
        return blocks

    def _indent(self, forward: bool) -> None:
        cursor = self.textCursor()
        if forward and not cursor.hasSelection():
            cursor.insertText(INDENT)
            return
        cursor.beginEditBlock()
        for block in self._selected_blocks():
            c = QTextCursor(block)
            if forward:
                c.insertText(INDENT)
            else:
                text = block.text()
                n = min(len(INDENT), len(text) - len(text.lstrip(" ")))
                c.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, n)
                c.removeSelectedText()
        cursor.endEditBlock()

    def toggle_comment(self) -> None:
        blocks = [b for b in self._selected_blocks() if b.text().strip()]
        if not blocks:
            return
        uncomment = all(b.text().lstrip().startswith("#") for b in blocks)
        col = min(len(b.text()) - len(b.text().lstrip()) for b in blocks)
        cursor = self.textCursor()
        cursor.beginEditBlock()
        for block in blocks:
            c = QTextCursor(block)
            text = block.text()
            if uncomment:
                i = text.index("#")
                n = 2 if text[i:i + 2] == "# " else 1
                c.setPosition(block.position() + i)
                c.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, n)
                c.removeSelectedText()
            else:
                c.setPosition(block.position() + col)
                c.insertText("# ")
        cursor.endEditBlock()

    # ---- üzerine gelince ------------------------------------------------- #
    def event(self, e):
        if e.type() == e.Type.ToolTip:
            self._show_hover(e)
            return True
        return super().event(e)

    def _show_hover(self, e) -> None:
        pos = e.pos() - self.viewport().pos()
        if pos.x() < 0:
            return
        cursor = self.cursorForPosition(pos)
        text = cursor.block().text()
        col = cursor.positionInBlock()
        # string içindeki görsel adı -> küçük önizleme
        for m in re.finditer(r"""["']([^"']+\.(?:png|jpg|jpeg|bmp))["']""", text, re.IGNORECASE):
            if m.start() <= col <= m.end():
                path = self._resolve_image(m.group(1))
                if path:
                    QToolTip.showText(e.globalPos(), f"<img src='{html.escape(path)}' "
                                      f"style='max-width:300px'><br>{html.escape(os.path.basename(path))}", self)
                else:
                    QToolTip.showText(e.globalPos(), f"Görsel bulunamadı: {html.escape(m.group(1))}", self)
                return
        word_cursor = QTextCursor(cursor)
        word_cursor.select(QTextCursor.WordUnderCursor)
        if self._sig_popup.isVisible() or not word_cursor.selectedText().strip().isidentifier():
            QToolTip.hideText()
            return
        doc = self._ctx.completion.help(CodePosition(self.toPlainText(), cursor.blockNumber() + 1, col, self._path))
        if doc:
            lines = doc.strip().splitlines()[:18]
            QToolTip.showText(e.globalPos(), "<pre>" + html.escape("\n".join(lines)) + "</pre>", self)
        else:
            QToolTip.hideText()

    def _resolve_image(self, name: str) -> Optional[str]:
        project = self._ctx.project
        candidates = ([os.path.join(project.root, name)] if project else []) + [
                      os.path.join(os.path.dirname(self._path), name),
                      os.path.join(os.path.dirname(self._path), "images", name)]
        if project:
            candidates += [os.path.join(d, name) for d in project.image_search_dirs()]
        for c in candidates:
            if os.path.isfile(c):
                return os.path.abspath(c)
        return None

