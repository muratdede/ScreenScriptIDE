"""Proje ağacı."""

from __future__ import annotations

import os

from PySide6.QtCore import QDir, QFile, QModelIndex, Qt, QUrl
from typing import List

from PySide6.QtGui import QDesktopServices, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QFileSystemModel, QInputDialog, QLabel, QMenu, QMessageBox, QStackedWidget,
                               QTreeView)

from ...core import search
from .. import icons
from ..find_in_files import apply_text_change
from ..rename_dialog import RenameDialog, image_references
from .base import DockView


TEXT_EXTENSIONS = (".py", ".txt", ".md", ".json", ".cfg", ".ini", ".yaml", ".yml", ".csv", ".log")


class ProjectFileModel(QFileSystemModel):
    """Ana script'i kalın gösterir."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_script = ""

    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.FontRole and self.main_script and \
                os.path.normcase(self.filePath(index)) == os.path.normcase(self.main_script):
            f = QFont()
            f.setBold(True)
            return f
        return super().data(index, role)


class ProjectView(DockView):
    view_id = "project"
    title = "Proje"
    icon_name = "folder"
    default_area = Qt.LeftDockWidgetArea
    tab_group = "left"
    shortcut = "Alt+1"

    HIDDEN = ["__pycache__", "*.pyc", ".git", ".idea", ".vscode", "debug"]

    def build(self):
        self._stack = QStackedWidget()
        self._empty = QLabel("Proje açık değil.\nProje Aç (Ctrl+Shift+O)", alignment=Qt.AlignCenter)
        self._empty.setObjectName("hint")
        self._empty.setWordWrap(True)
        self._model = ProjectFileModel(self)
        self._model.setFilter(QDir.AllEntries | QDir.NoDotAndDotDot | QDir.AllDirs)
        self._model.setNameFilters([])
        self._tree = QTreeView()
        self._tree.setModel(self._model)
        for col in (1, 2, 3):
            self._tree.hideColumn(col)
        self._tree.setHeaderHidden(True)
        self._tree.setIndentation(14)
        self._tree.setSelectionMode(QAbstractItemView.ExtendedSelection)   # Ctrl/Shift ile çoklu seçim
        self._tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tree.setDragEnabled(True)                     # görseli koda sürükle -> göreli yol
        self._tree.setDragDropMode(QAbstractItemView.DragOnly)
        self._tree.setDefaultDropAction(Qt.CopyAction)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._context_menu)
        self._tree.doubleClicked.connect(self._activate)
        for key, slot in (("F2", self.rename_current), ("Delete", self.delete_selected),
                          ("Return", self.open_selected), ("Enter", self.open_selected)):
            QShortcut(QKeySequence(key), self._tree, slot, context=Qt.WidgetShortcut)
        self._stack.addWidget(self._empty)
        self._stack.addWidget(self._tree)
        bus = self.ctx.bus
        bus.project_opened.connect(self._on_project)
        bus.project_config_changed.connect(self._on_project_config)
        return self._stack

    def _on_project(self, project):
        root = self._model.setRootPath(project.root)
        self._tree.setRootIndex(root)
        self._on_project_config(project)
        self._stack.setCurrentWidget(self._tree)
        self.setWindowTitle("Proje")

    def _on_project_config(self, project):
        self._model.main_script = project.main_script_path or ""
        self._tree.viewport().update()

    # ---- seçim ----------------------------------------------------------- #
    def selected_paths(self) -> List[str]:
        """Seçili öğeler; seçili bir klasörün içindekiler ayrıca listelenmez."""
        paths = []
        for index in self._tree.selectionModel().selectedRows(0):
            paths.append(os.path.normpath(self._model.filePath(index)))
        paths = sorted(set(paths))
        return [p for p in paths if not any(p != q and p.startswith(q + os.sep) for q in paths)]

    def _current_path(self) -> str:
        index = self._tree.currentIndex()
        return os.path.normpath(self._model.filePath(index)) if index.isValid() else ""

    # ---- etkileşim ------------------------------------------------------- #
    def _activate(self, index: QModelIndex):
        self._open_path(self._model.filePath(index))

    def _open_path(self, path: str):
        if os.path.isdir(path):
            return
        if path.lower().endswith(TEXT_EXTENSIONS + (".png", ".jpg", ".jpeg", ".bmp")):
            self.ctx.bus.open_file_requested.emit(path, 0)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def open_selected(self):
        paths = self.selected_paths()
        dirs = [p for p in paths if os.path.isdir(p)]
        if len(paths) == 1 and dirs:   # tek klasör: aç/kapat
            index = self._tree.currentIndex()
            self._tree.setExpanded(index, not self._tree.isExpanded(index))
            return
        for p in paths:
            self._open_path(p)

    def _selected_dir(self, index: QModelIndex) -> str:
        if not index.isValid():
            return self.ctx.project.root
        path = self._model.filePath(index)
        return path if os.path.isdir(path) else os.path.dirname(path)

    def _context_menu(self, pos):
        if self.ctx.project is None:
            return
        index = self._tree.indexAt(pos)
        path = os.path.normpath(self._model.filePath(index)) if index.isValid() else ""
        selected = self.selected_paths()
        menu = QMenu(self)
        if path and len(selected) > 1 and path in selected:
            menu.addAction(f"{len(selected)} öğe seçili").setEnabled(False)
            menu.addSeparator()
            menu.addAction("Aç\tEnter", self.open_selected)
            menu.addAction(icons.icon("trash"), "Sil…\tDelete", self.delete_selected)
            menu.exec(self._tree.viewport().mapToGlobal(pos))
            return
        menu.addAction(icons.icon("python", "#548af7"), "Yeni script…",
                       lambda: self._new_script(self._selected_dir(index)))
        menu.addAction(icons.icon("folder"), "Yeni klasör…", lambda: self._new_folder(self._selected_dir(index)))
        if path and os.path.isfile(path) and path.endswith(".py"):
            menu.addSeparator()
            menu.addAction("Ana script yap", lambda: self._set_main(path))
            menu.addAction(icons.icon("run", "#5fb865"), "Çalıştır", lambda: self._run(path, False))
            menu.addAction(icons.icon("bug", "#5fb865"), "Debug", lambda: self._run(path, True))
        if path:
            menu.addSeparator()
            menu.addAction("Yeniden adlandır…\tF2", lambda: self.rename(path))
            menu.addAction(icons.icon("trash"), "Sil…\tDelete", lambda: self.delete([path]))
            menu.addAction("Klasörde göster", lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(path if os.path.isdir(path) else os.path.dirname(path))))
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _new_script(self, directory: str):
        name, ok = QInputDialog.getText(self, "Yeni script", "Dosya adı:", text="yeni_script.py")
        if ok and name.strip():
            try:
                path = self.ctx.project.create_script(name.strip(), directory)
                self.ctx.bus.open_file_requested.emit(path, 0)
            except OSError as e:
                QMessageBox.warning(self, "Oluşturulamadı", str(e))

    def _new_folder(self, directory: str):
        name, ok = QInputDialog.getText(self, "Yeni klasör", "Klasör adı:")
        if ok and name.strip():
            os.makedirs(os.path.join(directory, name.strip()), exist_ok=True)

    def _set_main(self, path: str):
        self.ctx.project.config.main_script = self.ctx.project.relative(path)
        self.ctx.save_project_config()

    def _run(self, path: str, debug: bool):
        self.ctx.bus.run_requested.emit(path, debug)

    # ---- yeniden adlandır (F2) ------------------------------------------- #
    def rename_current(self):
        path = self._current_path()
        if path and self.ctx.project and os.path.normcase(path) != os.path.normcase(self.ctx.project.root):
            self.rename(path)

    def rename(self, path: str):
        project = self.ctx.project
        dialog = RenameDialog(self.ctx, path, self)
        if not dialog.exec():
            return
        new = dialog.new_path
        forms = image_references(project, path)[0] if dialog.update_references else None
        try:
            os.rename(path, new)
        except OSError as e:
            QMessageBox.warning(self, "Yeniden adlandırılamadı", str(e))
            return
        if forms:
            self._update_references(forms, path, new)
        main = project.main_script_path
        if main and (os.path.normcase(main) == os.path.normcase(path)
                     or os.path.normcase(main).startswith(os.path.normcase(path) + os.sep)):
            project.config.main_script = project.relative(new + main[len(path):])
            self.ctx.save_project_config()
        self.ctx.bus.file_renamed.emit(path, new)
        self.ctx.bus.images_changed.emit()
        index = self._model.index(new)
        if index.isValid():
            self._tree.setCurrentIndex(index)

    def _update_references(self, forms, old: str, new: str):
        project = self.ctx.project
        new_rel = project.relative(new).replace(os.sep, "/")
        mapping = {}
        for form in forms:
            mapping[form] = new_rel if "/" in form else os.path.basename(new)
        pattern = search.string_literal_pattern(list(mapping))
        total = files = 0
        for path in search.iter_files(project.root, ["*.py"]):
            n = apply_text_change(self.ctx, path, lambda text: pattern.subn(
                lambda m: m.group(1) + mapping[m.group(2)] + m.group(1), text))
            if n:
                total, files = total + n, files + 1
        self.ctx.bus.status_message.emit(f"{files} dosyada {total} referans güncellendi")

    # ---- sil (Delete) ---------------------------------------------------- #
    def delete_selected(self):
        paths = self.selected_paths()
        if self.ctx.project:
            paths = [p for p in paths if os.path.normcase(p) != os.path.normcase(self.ctx.project.root)]
        if paths:
            self.delete(paths)

    def delete(self, paths: List[str]):
        if len(paths) == 1:
            question = f"“{os.path.basename(paths[0])}” çöp kutusuna taşınsın mı?"
        else:
            names = "\n".join("  • " + os.path.basename(p) for p in paths[:12])
            more = f"\n  … ve {len(paths) - 12} öğe daha" if len(paths) > 12 else ""
            question = f"{len(paths)} öğe çöp kutusuna taşınsın mı?\n\n{names}{more}"
        if QMessageBox.question(self, "Sil", question) != QMessageBox.Yes:
            return
        failed = [p for p in paths if not QFile.moveToTrash(p)]
        if failed:
            QMessageBox.warning(self, "Silinemedi", "\n".join(failed))
        self.ctx.bus.images_changed.emit()