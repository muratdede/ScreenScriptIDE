"""Kod editörü: satır numarası/breakpoint oluğu, otomatik girinti, Ctrl+Space tamamlama,
imza yardımı ve üzerine gelince açıklama/görsel önizleme."""

from __future__ import annotations

import html
import os
import re
from functools import lru_cache
from typing import Optional

from PySide6.QtCore import QModelIndex, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (QColor, QGuiApplication, QIcon, QPainter, QPixmap, QStandardItem,
                           QStandardItemModel, QTextCursor, QTextDocument, QTextFormat)
from PySide6.QtWidgets import QCompleter, QLabel, QPlainTextEdit, QTextEdit, QToolTip, QWidget

from ..core.completion import CodePosition, api_names
from ..core.context import EditorContext
from .highlighter import PythonHighlighter
from .theme import THEME

INDENT = "    "
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp")
_IDENT_BEFORE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")
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
        self._debug_line = 0

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

    def save(self) -> None:
        text = self.toPlainText()
        if not text.endswith("\n"):
            text += "\n"
        with open(self._path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        self.document().setModified(False)

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

    def _update_gutter_width(self, *_):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _on_update_request(self, rect, dy):
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def paint_gutter(self, event):
        painter = QPainter(self._gutter)
        painter.fillRect(event.rect(), THEME.background)
        painter.setRenderHint(QPainter.Antialiasing)
        breakpoints = self._ctx.breakpoints.lines(self._path)
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
                    painter.setBrush(THEME.breakpoint)
                    painter.setPen(Qt.NoPen)
                    d = min(12, line_h - 4)
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
        self.setExtraSelections(selections)
        self._gutter.update()

    # ---- klavye ---------------------------------------------------------- #
    def keyPressEvent(self, event):
        popup = self._completer.popup()
        key, mods = event.key(), event.modifiers()

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
        candidates = [name, os.path.join(os.path.dirname(self._path), name),
                      os.path.join(os.path.dirname(self._path), "images", name)]
        if project:
            candidates += [os.path.join(d, name) for d in project.image_search_dirs()]
        for c in candidates:
            if os.path.isfile(c):
                return os.path.abspath(c)
        return None

