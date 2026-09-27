"""Project images: thumbnails. Double click = open in a new tab; drag into code = insert the relative path.
The list comes from the ImageRepository."""

from __future__ import annotations

import os
from typing import Dict, List, Tuple

from PySide6.QtCore import QMimeData, QSize, Qt, QUrl
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QListView, QListWidget, QListWidgetItem, QMenu

from ...i18n import N_, tr
from .. import icons
from .base import DockView


class _ImageList(QListWidget):
    """Dragged items carry a file URL; the code editor inserts it as "images/x.png"."""

    def mimeData(self, items):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(it.data(Qt.UserRole)) for it in items])
        return mime

    def mimeTypes(self):
        return ["text/uri-list"]


class ImagesView(DockView):
    view_id = "images"
    title = N_("Images")
    icon_name = "image"
    default_area = Qt.LeftDockWidgetArea
    tab_group = "left"
    shortcut = "Alt+2"

    THUMB = QSize(88, 64)

    def build(self):
        self._list = _ImageList()
        self._list.setViewMode(QListView.IconMode)
        self._list.setIconSize(self.THUMB)
        self._list.setGridSize(QSize(110, 100))
        self._list.setResizeMode(QListView.Adjust)
        self._list.setMovement(QListView.Static)
        self._list.setWordWrap(True)
        self._list.setSpacing(4)
        self._list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._list.setDragEnabled(True)
        self._list.setDragDropMode(QAbstractItemView.DragOnly)
        self._list.setDefaultDropAction(Qt.CopyAction)
        self._list.itemDoubleClicked.connect(self._open)
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._menu)
        self._thumbs: Dict[str, Tuple[float, QIcon, str]] = {}   # path -> (mtime, icon, size text)
        self.ctx.images.changed.connect(self._show)
        self._show(self.ctx.images.images)
        return self._list

    def title_actions(self):
        return [self.make_action("refresh", tr("Refresh"), lambda: self.ctx.images.refresh(force=True))]

    def _show(self, paths: List[str]) -> None:
        selected = self._list.currentItem().data(Qt.UserRole) if self._list.currentItem() else None
        self._list.clear()
        project = self.ctx.project
        for path in paths:
            icon, size_text = self._thumbnail(path)
            label = project.relative(path) if project else os.path.basename(path)
            item = QListWidgetItem(icon, os.path.basename(path))
            item.setData(Qt.UserRole, path)
            item.setToolTip(f"{label}  ({size_text})\n" + tr("Double click: open in a tab · Drag into code: insert its path"))
            self._list.addItem(item)
            if path == selected:
                self._list.setCurrentItem(item)
        self.setWindowTitle(f"{self.title} ({len(paths)})")

    def _thumbnail(self, path: str):
        """Don't regenerate thumbnails of unchanged files."""
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0
        cached = self._thumbs.get(path)
        if cached and cached[0] == mtime:
            return cached[1], cached[2]
        pm = QPixmap(path)
        icon = QIcon(pm.scaled(self.THUMB, Qt.KeepAspectRatio, Qt.SmoothTransformation)) if not pm.isNull() else QIcon()
        size_text = f"{pm.width()}x{pm.height()}" if not pm.isNull() else tr("unreadable")
        self._thumbs[path] = (mtime, icon, size_text)
        return icon, size_text

    def _open(self, item) -> None:
        self.ctx.bus.open_file_requested.emit(item.data(Qt.UserRole), 0)

    def _reference(self, path: str) -> str:
        project = self.ctx.project
        rel = project.relative(path) if project and project.contains(path) else path
        return rel.replace("\\", "/")

    def _insert(self, item, as_pattern: bool):
        ref = self._reference(item.data(Qt.UserRole))
        sim = self.ctx.project.config.similarity if self.ctx.project else 0.7
        text = f'Pattern("{ref}").similar({sim:.2f})' if as_pattern else f'"{ref}"'
        self.ctx.bus.insert_text_requested.emit(text)

    def _menu(self, pos):
        item = self._list.itemAt(pos)
        menu = QMenu(self)
        if item:
            menu.addAction(icons.icon("image"), tr("Open in tab"), lambda: self._open(item))
            menu.addAction(tr("Insert into code (path)"), lambda: self._insert(item, False))
            menu.addAction(tr("Insert into code (Pattern + threshold)"), lambda: self._insert(item, True))
            menu.addAction(icons.icon("match"), tr("Test on screenshot"),
                           lambda: self.ctx.bus.pattern_test_requested.emit(item.data(Qt.UserRole)))
            menu.addSeparator()
        menu.addAction(icons.icon("refresh"), tr("Refresh"), lambda: self.ctx.images.refresh(force=True))
        menu.exec(self._list.viewport().mapToGlobal(pos))
