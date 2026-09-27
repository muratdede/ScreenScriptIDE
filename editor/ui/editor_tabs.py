"""Open file tabs. Implements the DocumentService interface.
Code files open with CodeEditor, images with ImageViewer; both provide path / is_dirty / load / set_path."""

from __future__ import annotations

import os
from typing import List, Optional, Tuple, Union

from PySide6.QtCore import QEvent, QFileSystemWatcher, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QMessageBox, QTabBar, QTabWidget, QToolButton

from ..core.context import DocumentService, EditorContext
from ..i18n import tr
from . import icons
from .code_editor import IMAGE_EXTENSIONS, CodeEditor, file_stamp
from .image_viewer import ImageViewer
from .theme import THEME

Document = Union[CodeEditor, ImageViewer]


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


class EditorTabs(QTabWidget, DocumentService):
    cursor_moved = Signal(int, int)   # line, column

    TEXT_EXTENSIONS = (".py", ".txt", ".md", ".json", ".cfg", ".ini", ".yaml", ".yml", ".csv", ".log")
    POLL_INTERVAL_MS = 500

    def __init__(self, ctx: EditorContext, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._debug_location: Optional[Tuple[str, int]] = None
        self.setObjectName("editor_tabs")
        self.setMovable(True)
        self.setDocumentMode(True)
        self.setElideMode(Qt.ElideNone)
        self.setUsesScrollButtons(True)
        self.tabBar().setExpanding(False)
        self.tabBar().installEventFilter(self)   # middle click = close
        self.currentChanged.connect(self._on_current_changed)

        # open files changed externally: the watcher reports instantly, polling catches what it misses
        self._asking = False
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(lambda _p: self._sync_timer.start())
        self._sync_timer = QTimer(self, singleShot=True, interval=100)   # wait for atomic writes
        self._sync_timer.timeout.connect(self.check_external_changes)
        self._poll = QTimer(self, interval=self.POLL_INTERVAL_MS)
        self._poll.timeout.connect(self.check_external_changes)
        self._poll.start()
        app = QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(
                lambda s: self._sync_timer.start() if s == Qt.ApplicationActive else None)

        bus = ctx.bus
        bus.open_file_requested.connect(self.open_path)
        bus.insert_text_requested.connect(self.insert_text)
        bus.file_renamed.connect(self.rename_path)
        bus.debug_paused.connect(self._on_paused)
        bus.debug_resumed.connect(self._clear_debug_line)
        bus.run_finished.connect(lambda _c: self._clear_debug_line())

    # ---- DocumentService ------------------------------------------------- #
    def current_editor(self) -> Optional[CodeEditor]:
        w = self.currentWidget()
        return w if isinstance(w, CodeEditor) else None

    def editors(self) -> List[CodeEditor]:
        return [w for w in self.documents() if isinstance(w, CodeEditor)]

    def documents(self) -> List[Document]:
        return [self.widget(i) for i in range(self.count())
                if isinstance(self.widget(i), (CodeEditor, ImageViewer))]

    def current_path(self) -> Optional[str]:
        e = self.current_editor()
        return e.path if e else None

    def current_document_path(self) -> Optional[str]:
        w = self.currentWidget()
        return w.path if isinstance(w, (CodeEditor, ImageViewer)) else None

    def open_paths(self) -> List[str]:
        return [d.path for d in self.documents()]

    def find_document(self, path: str) -> Optional[Document]:
        key = _norm(path)
        return next((d for d in self.documents() if _norm(d.path) == key), None)

    def find_editor(self, path: str) -> Optional[CodeEditor]:
        doc = self.find_document(path)
        return doc if isinstance(doc, CodeEditor) else None

    def open_path(self, path: str, line: int = 0) -> Optional[Document]:
        if not os.path.isfile(path):
            return None
        doc = self.find_document(path)
        if doc is None:
            is_image = path.lower().endswith(IMAGE_EXTENSIONS)
            doc = ImageViewer(path, self._ctx) if is_image else CodeEditor(path, self._ctx)
            try:
                doc.load()
            except OSError as e:
                QMessageBox.warning(self, tr("Could not be opened"), str(e))
                doc.deleteLater()
                return None
            index = self.addTab(doc, icons.file_icon(path), os.path.basename(path))
            self.setTabToolTip(index, path)
            self._add_close_button(doc)
            if isinstance(doc, CodeEditor):
                doc.document().modificationChanged.connect(lambda _m, ed=doc: self._update_title(ed))
                doc.cursorPositionChanged.connect(lambda ed=doc: self._emit_cursor(ed))
                self._apply_debug_line(doc)
                if doc.path.endswith(".py"):
                    self._ctx.diagnostics.attach_live(doc.path)
                    doc.run_diagnostics()
        self.setCurrentWidget(doc)
        if line and isinstance(doc, CodeEditor):
            doc.go_to_line(line)
        doc.setFocus()
        return doc

    def save_current(self) -> bool:
        e = self.current_editor()
        return self._save(e) if e else True

    def save_all(self) -> bool:
        return all(self._save(e) for e in self.editors() if e.is_dirty)

    def insert_text(self, text: str) -> None:
        e = self.current_editor()
        if e is None:
            return
        e.insertPlainText(text)
        e.setFocus()

    # ---- tabs ------------------------------------------------------------ #
    def _add_close_button(self, doc) -> None:
        btn = QToolButton()
        btn.setIcon(icons.icon("close", "#9da0a8", 14))
        btn.setToolTip(tr("Close (Ctrl+W)"))
        btn.setFixedSize(18, 18)
        btn.setStyleSheet("QToolButton { padding: 0; border-radius: 4px; }")
        btn.clicked.connect(lambda: self.close_tab(self.indexOf(doc)))
        self.tabBar().setTabButton(self.indexOf(doc), QTabBar.RightSide, btn)

    def eventFilter(self, obj, event):
        if obj is self.tabBar() and event.type() == QEvent.MouseButtonRelease \
                and event.button() == Qt.MiddleButton:
            index = self.tabBar().tabAt(event.position().toPoint())
            if index >= 0:
                self.close_tab(index)
                return True
        return super().eventFilter(obj, event)

    def close_tab(self, index: int) -> bool:
        doc = self.widget(index)
        if doc is None:
            return True
        if isinstance(doc, CodeEditor) and doc.is_dirty:
            answer = QMessageBox.question(
                self, tr("Unsaved changes"), tr("Save {name}?", name=os.path.basename(doc.path)),
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if answer == QMessageBox.Cancel:
                return False
            if answer == QMessageBox.Save and not self._save(doc):
                return False
        self.removeTab(index)
        if isinstance(doc, CodeEditor) and doc.path.endswith(".py"):
            self._ctx.diagnostics.detach_live(doc.path)
        doc.deleteLater()
        if not self.count():
            self._ctx.bus.active_file_changed.emit("")
        return True

    def close_all(self) -> bool:
        while self.count():
            if not self.close_tab(0):
                return False
        return True

    def rename_path(self, old: str, new: str) -> None:
        """A file or folder was renamed: update the paths of the affected tabs."""
        old_key = _norm(old)
        for doc in self.documents():
            key = _norm(doc.path)
            if key == old_key:
                target = new
            elif key.startswith(old_key + os.sep):
                target = os.path.join(new, os.path.abspath(doc.path)[len(os.path.abspath(old)) + 1:])
            else:
                continue
            if isinstance(doc, CodeEditor) and doc.path.endswith(".py"):
                self._ctx.diagnostics.detach_live(doc.path)
            doc.set_path(target)
            if isinstance(doc, CodeEditor) and target.endswith(".py"):
                self._ctx.diagnostics.attach_live(target)
                doc.run_diagnostics()
            i = self.indexOf(doc)
            dirty = isinstance(doc, CodeEditor) and doc.is_dirty
            self.setTabText(i, os.path.basename(target) + (" ●" if dirty else ""))
            self.setTabToolTip(i, target)
            self.setTabIcon(i, icons.file_icon(target))
        self._on_current_changed(self.currentIndex())

    # ---- external changes ------------------------------------------------ #
    def check_external_changes(self) -> None:
        """Reloads open files that changed on disk. Asks for editors with unsaved changes."""
        self._rewatch()
        if self._asking:
            return
        for doc in self.documents():
            if isinstance(doc, ImageViewer):
                doc.reload_if_changed()
                continue
            stamp = file_stamp(doc.path)
            if stamp is None or stamp == doc.disk_stamp:
                continue   # deleted (maybe in the middle of an atomic save) or unchanged
            if not doc.is_dirty:
                self._reload(doc)
            elif QApplication.applicationState() == Qt.ApplicationActive:
                self._ask_reload(doc)

    def _reload(self, editor: CodeEditor) -> None:
        try:
            editor.reload_from_disk()
        except OSError:
            pass

    def _ask_reload(self, editor: CodeEditor) -> None:
        self._asking = True
        try:
            self.setCurrentWidget(editor)
            answer = QMessageBox.question(
                self, tr("File changed externally"),
                tr("{name} changed on disk, and the editor has unsaved changes.\n\n"
                   "Load the version on disk? (No: the editor's version is kept)", name=os.path.basename(editor.path)),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer == QMessageBox.Yes:
                self._reload(editor)
            else:
                editor.disk_stamp = file_stamp(editor.path)   # don't ask again for this change
        finally:
            self._asking = False

    def _rewatch(self) -> None:
        wanted = {d.path for d in self.documents() if os.path.isfile(d.path)}
        current = set(self._watcher.files())
        if current - wanted:
            self._watcher.removePaths(list(current - wanted))
        if wanted - current:
            self._watcher.addPaths(list(wanted - current))

    def _save(self, editor: CodeEditor) -> bool:
        try:
            editor.save()
            return True
        except OSError as e:
            QMessageBox.warning(self, tr("Could not be saved"), str(e))
            return False

    def _update_title(self, editor: CodeEditor) -> None:
        i = self.indexOf(editor)
        if i >= 0:
            self.setTabText(i, os.path.basename(editor.path) + (" ●" if editor.is_dirty else ""))

    def _on_current_changed(self, _index: int) -> None:
        path = self.current_document_path()
        self._ctx.bus.active_file_changed.emit(path or "")
        e = self.current_editor()
        if e:
            self._emit_cursor(e)
        else:
            self.cursor_moved.emit(0, 0)

    def _emit_cursor(self, editor: CodeEditor) -> None:
        if editor is self.current_editor():
            c = editor.textCursor()
            self.cursor_moved.emit(c.blockNumber() + 1, c.positionInBlock() + 1)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.count():
            return
        # empty editor area: shortcut hints like in IntelliJ
        p = QPainter(self)
        p.fillRect(self.rect(), THEME.background)
        hints = [(tr("Open project"), "Ctrl+Shift+O"), (tr("New script"), "Ctrl+N"),
                 (tr("Find in files"), "Ctrl+Shift+F"), (tr("Run"), "Shift+F10"), (tr("Debug"), "F5"),
                 (tr("Capture the screen live"), "Ctrl+Shift+G"), (tr("Panels"), "Alt+1 … Alt+6")]
        line_h = 28
        top = self.height() // 2 - len(hints) * line_h // 2
        mid = self.width() // 2
        for i, (label, key) in enumerate(hints):
            y = top + i * line_h
            p.setPen(THEME.muted)
            p.drawText(QRect(0, y, mid - 8, line_h), Qt.AlignRight | Qt.AlignVCenter, label)
            p.setPen(QColor("#548af7"))
            p.drawText(QRect(mid + 8, y, mid, line_h), Qt.AlignLeft | Qt.AlignVCenter, key)
        p.end()

    # ---- debug line ------------------------------------------------------ #
    def _on_paused(self, msg: dict) -> None:
        self._debug_location = (_norm(msg["file"]), int(msg["line"]))
        for e in self.editors():
            self._apply_debug_line(e)
        self.open_path(msg["file"], int(msg["line"]))

    def _clear_debug_line(self) -> None:
        self._debug_location = None
        for e in self.editors():
            e.set_debug_line(0)

    def _apply_debug_line(self, editor: CodeEditor) -> None:
        loc = self._debug_location
        editor.set_debug_line(loc[1] if loc and loc[0] == _norm(editor.path) else 0)
