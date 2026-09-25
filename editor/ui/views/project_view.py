"""Proje ağacı."""

from __future__ import annotations

import os

from PySide6.QtCore import QDir, QFile, QModelIndex, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (QAbstractItemView, QFileSystemModel, QInputDialog, QLabel, QMenu, QMessageBox, QStackedWidget,
                               QTreeView)

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
        self._tree.setDragEnabled(True)                     # görseli koda sürükle -> göreli yol
        self._tree.setDragDropMode(QAbstractItemView.DragOnly)
        self._tree.setDefaultDropAction(Qt.CopyAction)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._context_menu)
        self._tree.doubleClicked.connect(self._activate)
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

    # ---- etkileşim ------------------------------------------------------- #
    def _activate(self, index: QModelIndex):
        path = self._model.filePath(index)
        if os.path.isdir(path):
            return
        if path.lower().endswith(TEXT_EXTENSIONS):
            self.ctx.bus.open_file_requested.emit(path, 0)
        elif path.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")):
            self.ctx.bus.open_file_requested.emit(path, 0)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _selected_dir(self, index: QModelIndex) -> str:
        if not index.isValid():
            return self.ctx.project.root
        path = self._model.filePath(index)
        return path if os.path.isdir(path) else os.path.dirname(path)

    def _context_menu(self, pos):
        if self.ctx.project is None:
            return
        index = self._tree.indexAt(pos)
        path = self._model.filePath(index) if index.isValid() else ""
        menu = QMenu(self)
        menu.addAction("Yeni script…", lambda: self._new_script(self._selected_dir(index)))
        menu.addAction("Yeni klasör…", lambda: self._new_folder(self._selected_dir(index)))
        if path and os.path.isfile(path) and path.endswith(".py"):
            menu.addSeparator()
            menu.addAction("Ana script yap", lambda: self._set_main(path))
            menu.addAction("Çalıştır", lambda: self._run(path, False))
            menu.addAction("Debug", lambda: self._run(path, True))
        if path:
            menu.addSeparator()
            menu.addAction("Yeniden adlandır…", lambda: self._rename(path))
            menu.addAction("Sil", lambda: self._delete(path))
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

    def _rename(self, path: str):
        name, ok = QInputDialog.getText(self, "Yeniden adlandır", "Yeni ad:", text=os.path.basename(path))
        if not ok or not name.strip() or name == os.path.basename(path):
            return
        new = os.path.join(os.path.dirname(path), name.strip())
        if os.path.exists(new):
            QMessageBox.warning(self, "Var", f"{name} zaten var.")
            return
        os.rename(path, new)
        self.ctx.bus.file_renamed.emit(path, new)
        self.ctx.bus.images_changed.emit()

    def _delete(self, path: str):
        if QMessageBox.question(self, "Sil", f"{os.path.basename(path)} çöp kutusuna taşınsın mı?") \
                != QMessageBox.Yes:
            return
        if not QFile.moveToTrash(path):
            QMessageBox.warning(self, "Silinemedi", path)
        self.ctx.bus.images_changed.emit()
