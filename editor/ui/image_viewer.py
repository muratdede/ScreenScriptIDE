"""Image tab: opens project images in the editor area like a file."""

from __future__ import annotations

import os

from PySide6.QtCore import QMimeData, QRectF, Qt, QUrl
from PySide6.QtGui import QBrush, QColor, QDrag, QPainter, QPixmap
from PySide6.QtWidgets import (QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QToolButton, QVBoxLayout,
                               QWidget)

from ..core.context import EditorContext
from ..i18n import tr
from . import icons


def _checker_brush() -> QBrush:
    pm = QPixmap(16, 16)
    pm.fill(QColor("#2b2d30"))
    p = QPainter(pm)
    p.fillRect(0, 0, 8, 8, QColor("#323438"))
    p.fillRect(8, 8, 8, 8, QColor("#323438"))
    p.end()
    return QBrush(pm)


class _ImageCanvas(QGraphicsView):
    """Ctrl+wheel = zoom. Dragging the image drops its relative path into code."""

    def __init__(self, viewer: "ImageViewer"):
        super().__init__(viewer)
        self._viewer = viewer
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QColor("#1e1f22"))
        self.setRenderHint(QPainter.SmoothPixmapTransform, False)   # show pixels sharp
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setAlignment(Qt.AlignCenter)
        self._press = None

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self._viewer.zoom(1.25 if e.angleDelta().y() > 0 else 0.8)
        else:
            super().wheelEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.position().toPoint()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._press is not None and (e.position().toPoint() - self._press).manhattanLength() > 8:
            self._press = None
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(self._viewer.path)])
            drag = QDrag(self)
            drag.setMimeData(mime)
            drag.setPixmap(self._viewer.thumbnail())
            drag.exec(Qt.CopyAction)
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._press = None
        super().mouseReleaseEvent(e)


class ImageViewer(QWidget):
    def __init__(self, path: str, ctx: EditorContext, parent=None):
        super().__init__(parent)
        self._path = os.path.abspath(path)
        self._ctx = ctx
        self._mtime = None
        self._pixmap = QPixmap()
        self._scale = 1.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar_host = QWidget()
        bar_host.setObjectName("dock_titlebar")
        bar = QHBoxLayout(bar_host)
        bar.setContentsMargins(8, 3, 8, 3)
        bar.setSpacing(2)
        self._button(bar, "fit", tr("Fit to window"), self.fit)
        self._button(bar, "actual", tr("Actual size (1:1)"), self.actual_size)
        self._button(bar, "add", tr("Zoom in (Ctrl+wheel)"), lambda: self.zoom(1.25))
        self._button(bar, "minimize", tr("Zoom out (Ctrl+wheel)"), lambda: self.zoom(0.8))
        bar.addSpacing(8)
        test = self._button(bar, "match", tr("Test on screenshot"), self._test)
        test.setText(tr("Test on screen"))
        test.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        bar.addStretch()
        self._info = QLabel()
        self._info.setObjectName("hint")
        bar.addWidget(self._info)
        layout.addWidget(bar_host)

        self._canvas = _ImageCanvas(self)
        self._canvas.setBackgroundBrush(_checker_brush())
        self._item = self._canvas.scene().addPixmap(QPixmap())
        layout.addWidget(self._canvas, 1)

        ctx.images.changed.connect(lambda _paths: self.reload_if_changed())

    @staticmethod
    def _button(layout, icon_name, tip, slot) -> QToolButton:
        b = QToolButton()
        b.setIcon(icons.icon(icon_name))
        b.setToolTip(tip)
        b.clicked.connect(slot)
        layout.addWidget(b)
        return b

    # ---- document interface (for EditorTabs) ----------------------------- #
    @property
    def path(self) -> str:
        return self._path

    def set_path(self, path: str) -> None:
        self._path = os.path.abspath(path)
        self._update_info()

    @property
    def is_dirty(self) -> bool:
        return False

    def load(self) -> None:
        pm = QPixmap(self._path)
        if pm.isNull():
            raise OSError(tr("Image could not be read: {path}", path=self._path))
        first = self._pixmap.isNull()
        self._pixmap = pm
        self._mtime = os.path.getmtime(self._path)
        self._item.setPixmap(pm)
        self._canvas.scene().setSceneRect(QRectF(pm.rect()))
        self._update_info()
        if first:
            # small templates open enlarged, large ones fitted
            self.actual_size()
            if pm.width() < 200 and pm.height() < 200:
                self.zoom(min(4.0, 240 / max(pm.width(), pm.height(), 1)))

    def reload_if_changed(self) -> None:
        try:
            if os.path.getmtime(self._path) != self._mtime:
                self.load()
        except OSError:
            pass

    def thumbnail(self) -> QPixmap:
        return self._pixmap.scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    # ---- view ------------------------------------------------------------ #
    def zoom(self, factor: float) -> None:
        self._scale = min(max(self._scale * factor, 0.05), 40.0)
        self._apply_scale()

    def actual_size(self) -> None:
        self._scale = 1.0
        self._apply_scale()

    def fit(self) -> None:
        if self._pixmap.isNull():
            return
        vp = self._canvas.viewport().rect()
        self._scale = min(vp.width() / self._pixmap.width(), vp.height() / self._pixmap.height()) * 0.95
        self._apply_scale()

    def _apply_scale(self) -> None:
        self._canvas.resetTransform()
        self._canvas.scale(self._scale, self._scale)
        self._update_info()

    def _update_info(self) -> None:
        project = self._ctx.project
        name = project.relative(self._path) if project and project.contains(self._path) else self._path
        try:
            kb = os.path.getsize(self._path) / 1024
        except OSError:
            kb = 0
        self._info.setText(f"{name.replace(os.sep, '/')}   ·   {self._pixmap.width()}×{self._pixmap.height()}"
                           f"   ·   {kb:.1f} KB   ·   %{self._scale * 100:.0f}")

    def _test(self) -> None:
        self._ctx.bus.pattern_test_requested.emit(self._path)
