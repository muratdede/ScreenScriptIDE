"""Ekran view'ı: cihaz ekranını yakala, alan seç, görsel kaydet / Region ekle, eşleşmeyi test et."""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFileDialog, QFrame, QGraphicsPixmapItem,
                               QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView,
                               QHBoxLayout, QInputDialog, QLabel, QMessageBox, QToolButton, QVBoxLayout,
                               QWidget)

from screenscript import ImageLibrary, ImagePath, Pattern

from ...agent.context_factory import ContextFactory
from .. import icons
from .base import DockView


def bgr_to_qimage(image: np.ndarray) -> QImage:
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, rgb.strides[0], QImage.Format_RGB888).copy()


class ScreenCanvas(QGraphicsView):
    """Görüntüyü gösterir; sürükleyerek alan seçilir. Ctrl+tekerlek = yakınlaştır."""

    selection_changed = Signal(object)   # (x, y, w, h) ya da None
    hovered = Signal(int, int, object)   # x, y, (b, g, r) ya da None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor("#1e1f22"))
        self.setDragMode(QGraphicsView.NoDrag)
        self.setMouseTracking(True)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self._image: Optional[np.ndarray] = None
        self._pixmap: Optional[QGraphicsPixmapItem] = None
        self._selection: Optional[QGraphicsRectItem] = None
        self._origin: Optional[QPointF] = None
        self._overlays = []

    @property
    def image(self) -> Optional[np.ndarray]:
        return self._image

    def set_image(self, image: np.ndarray, keep_overlays: bool = False) -> None:
        first = self._image is None or self._image.shape != image.shape
        self._image = image
        if not keep_overlays or first:
            self.clear_matches()
        if self._pixmap is None:
            self._pixmap = self.scene().addPixmap(QPixmap.fromImage(bgr_to_qimage(image)))
            self._pixmap.setZValue(0)
        else:
            self._pixmap.setPixmap(QPixmap.fromImage(bgr_to_qimage(image)))
        self.scene().setSceneRect(QRectF(0, 0, image.shape[1], image.shape[0]))
        if first:
            self.clear_selection()
            self.fit()

    def fit(self) -> None:
        if self._image is not None:
            self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)

    def actual_size(self) -> None:
        self.resetTransform()

    def selection(self) -> Optional[Tuple[int, int, int, int]]:
        if self._selection is None:
            return None
        r = self._selection.rect().toAlignedRect()
        return (r.x(), r.y(), r.width(), r.height()) if r.width() > 2 and r.height() > 2 else None

    def clear_selection(self) -> None:
        if self._selection is not None:
            self.scene().removeItem(self._selection)
            self._selection = None
        self.selection_changed.emit(None)

    def show_matches(self, matches: List[Tuple[Tuple[int, int, int, int], float]]) -> None:
        self.clear_matches()
        for (x, y, w, h), score in matches:
            rect = self.scene().addRect(QRectF(x, y, w, h), QPen(QColor("#e5534b"), 2))
            rect.setZValue(2)
            label = QGraphicsSimpleTextItem(f"{score:.3f}")
            label.setBrush(QBrush(QColor("#ffffff")))
            label.setPos(x, max(0, y - 16))
            label.setZValue(3)
            bg = self.scene().addRect(label.boundingRect().translated(label.pos()), QPen(Qt.NoPen),
                                      QBrush(QColor(229, 83, 75, 200)))
            bg.setZValue(2)
            self.scene().addItem(label)
            self._overlays += [rect, bg, label]

    def clear_matches(self) -> None:
        for item in self._overlays:
            self.scene().removeItem(item)
        self._overlays = []

    # ---- fare ------------------------------------------------------------ #
    def _clamped(self, pos) -> QPointF:
        p = self.mapToScene(pos)
        r = self.sceneRect()
        return QPointF(min(max(p.x(), 0), r.width()), min(max(p.y(), 0), r.height()))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self._image is not None:
            self._origin = self._clamped(e.position().toPoint())
            if self._selection is None:
                pen = QPen(QColor("#3574f0"), 0, Qt.DashLine)
                self._selection = self.scene().addRect(QRectF(), pen, QBrush(QColor(53, 116, 240, 50)))
                self._selection.setZValue(4)
            self._selection.setRect(QRectF(self._origin, self._origin))
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        p = self._clamped(e.position().toPoint())
        if self._image is not None:
            x, y = int(p.x()), int(p.y())
            h, w = self._image.shape[:2]
            color = tuple(int(c) for c in self._image[y, x]) if 0 <= x < w and 0 <= y < h else None
            self.hovered.emit(x, y, color)
        if self._origin is not None and self._selection is not None:
            self._selection.setRect(QRectF(self._origin, p).normalized())
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._origin is not None:
            self._origin = None
            self.selection_changed.emit(self.selection())
        super().mouseReleaseEvent(e)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            factor = 1.25 if e.angleDelta().y() > 0 else 0.8
            self.scale(factor, factor)
        else:
            super().wheelEvent(e)


class RefreshingComboBox(QComboBox):
    """Açılır liste açılmadan hemen önce about_to_show yayar; böylece liste her zaman günceldir."""

    about_to_show = Signal()

    def showPopup(self):
        self.about_to_show.emit()
        super().showPopup()


class ScreenView(DockView):
    view_id = "screen"
    title = "Ekran"
    icon_name = "phone"
    default_area = Qt.RightDockWidgetArea
    tab_group = "right"
    shortcut = "Alt+3"

    def build(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 1. satır: yakalama ve seçim eylemleri
        bar = QHBoxLayout()
        bar.setContentsMargins(8, 2, 8, 4)
        bar.setSpacing(2)
        self._live_btn = self._button(bar, "Yakala", "camera", "Cihaz ekranını canlı izle (Ctrl+Shift+G)",
                                      self.ctx.toggle_live_screen)
        self._shot_btn = self._button(bar, "", "camera", "Tek kare yakala", self.ctx.grab_screen)
        self._shot_btn.setIcon(icons.icon("refresh"))
        self._button(bar, "", "folder", "Kayıtlı bir ekran görüntüsünü aç", self._open_file)
        bar.addWidget(self._separator())
        self._button(bar, "", "fit", "Görüntüyü pencereye sığdır", lambda: self._canvas.fit())
        self._button(bar, "", "actual", "Gerçek boyut (1:1)", lambda: self._canvas.actual_size())
        bar.addWidget(self._separator())
        self._save_btn = self._button(bar, "Görsel kaydet", "crop",
                                      "Seçili alanı images/ klasörüne PNG olarak kaydet", self._save_selection)
        self._region_btn = self._button(bar, "Region", "region",
                                        "Seçili alanı Region(x, y, w, h) olarak koda ekle", self._insert_region)
        self._loc_btn = self._button(bar, "Konum", "location",
                                     "Seçimin merkezini Location(x, y) olarak koda ekle", self._insert_location)
        bar.addStretch()
        layout.addLayout(bar)

        # 2. satır: eşleşme testi
        test = QHBoxLayout()
        test.setContentsMargins(8, 0, 8, 6)
        test.setSpacing(4)
        self._pattern_box = RefreshingComboBox()
        self._pattern_box.setMinimumWidth(140)
        self._pattern_box.setPlaceholderText("Test edilecek görsel")
        self._pattern_box.setToolTip("Ekran görüntüsünde aranacak görsel")
        self._pattern_box.about_to_show.connect(lambda: self.ctx.images.refresh())
        test.addWidget(self._pattern_box, 1)
        label = QLabel("eşik")
        label.setObjectName("hint")
        test.addWidget(label)
        self._sim = QDoubleSpinBox(minimum=0.1, maximum=1.0, singleStep=0.05, decimals=2, value=0.8)
        test.addWidget(self._sim)
        self._button(test, "Göster", "match", "Seçili görseli bu ekran görüntüsünde ara", self._test_pattern)
        self._button(test, "", "trash", "İşaretleri kaldır", lambda: self._canvas.clear_matches())
        layout.addLayout(test)

        self._canvas = ScreenCanvas()
        self._canvas.selection_changed.connect(self._on_selection)
        self._canvas.hovered.connect(self._on_hover)
        layout.addWidget(self._canvas, 1)

        status = QHBoxLayout()
        status.setContentsMargins(10, 4, 10, 5)
        self._status = QLabel("Ekran görüntüsü yok. 'Yakala' ile cihazdan canlı alın.")
        self._status.setObjectName("hint")
        self._pixel = QLabel()
        self._pixel.setObjectName("hint")
        status.addWidget(self._status, 1)
        status.addWidget(self._pixel)
        layout.addLayout(status)

        bus = self.ctx.bus
        bus.screenshot_ready.connect(self._on_screenshot)
        bus.screenshot_failed.connect(self._on_failed)
        bus.project_opened.connect(self._on_project)
        bus.project_config_changed.connect(self._on_project)
        self.ctx.images.changed.connect(self._set_patterns)
        bus.pattern_test_requested.connect(self._on_test_requested)
        self.ctx.grabber.busy_changed.connect(lambda busy: self._shot_btn.setEnabled(not busy))
        self.ctx.grabber.live_changed.connect(self._on_live_changed)
        self._failed = False
        self._on_selection(None)
        self._on_live_changed(False)
        return w

    @staticmethod
    def _button(layout, text, icon_name, tip, slot) -> QToolButton:
        b = QToolButton(text=text)
        b.setIcon(icons.icon(icon_name))
        b.setToolTip(tip)
        b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon if text else Qt.ToolButtonIconOnly)
        b.clicked.connect(slot)
        layout.addWidget(b)
        return b

    @staticmethod
    def _separator() -> QFrame:
        line = QFrame()
        line.setFixedSize(1, 16)
        line.setStyleSheet("background: #43454a;")
        return line

    def _on_live_changed(self, live: bool) -> None:
        b = self._live_btn
        if live:
            b.setText("Durdur")
            b.setIcon(icons.icon("stop", "#ff8a8a"))
            b.setObjectName("live_on")
            b.setToolTip("Canlı görüntüyü durdur; son kare ekranda kalır (Ctrl+Shift+G)")
        else:
            has_image = self._canvas.image is not None
            b.setText("Devam" if has_image else "Yakala")
            b.setIcon(icons.icon("live", "#db5c5c") if has_image else icons.icon("camera"))
            b.setObjectName("")
            b.setToolTip("Canlı görüntüyü başlat (Ctrl+Shift+G)")
            if has_image and not self._failed:
                h, w = self._canvas.image.shape[:2]
                self._status.setText(f"Durduruldu · {w}×{h}. Alan seçmek için sürükleyin.")
        b.style().unpolish(b)
        b.style().polish(b)
        self._shot_btn.setEnabled(not live and not self.ctx.grabber.busy)

    # ---- olaylar --------------------------------------------------------- #
    def _on_project(self, project):
        self._sim.setValue(project.config.similarity)
        self._set_patterns(self.ctx.images.images)

    def _set_patterns(self, paths):
        """Listeyi günceller; seçili görsel hâlâ varsa seçili kalır."""
        box = self._pattern_box
        current = box.currentData()
        project = self.ctx.project
        box.blockSignals(True)
        box.clear()
        for path in paths:
            label = project.relative(path) if project else os.path.basename(path)
            box.addItem(icons.icon("image", "#d6ae58"), label.replace(os.sep, "/"), path)
        i = box.findData(current)
        box.setCurrentIndex(i if i >= 0 else (0 if paths else -1))
        box.blockSignals(False)

    def _on_screenshot(self, image):
        self._failed = False
        live = self.ctx.grabber.is_live
        first = self._canvas.image is None
        self._canvas.set_image(image, keep_overlays=live)
        h, w = image.shape[:2]
        if live:
            sel = self._canvas.selection()
            sel_txt = "  ·  seçim: x={} y={} w={} h={}".format(*sel) if sel else ""
            self._status.setText(f"● Canlı · {w}×{h} · {self.ctx.grabber.fps:.0f} FPS{sel_txt}")
        else:
            self._status.setText(f"Ekran: {w}×{h}. Alan seçmek için sürükleyin; Ctrl+tekerlek ile yakınlaştırın.")
        if first or not live:
            self.reveal()

    def _on_failed(self, message: str):
        self._failed = True
        self._status.setText(f"<span style='color:#f07178'>Yakalanamadı: {message}</span>")
        self.ctx.bus.output.emit(f"Ekran yakalanamadı: {message}\n", "err")

    def _on_selection(self, sel):
        enabled = sel is not None
        for b in (self._save_btn, self._region_btn, self._loc_btn):
            b.setEnabled(enabled)
        if sel and not self.ctx.grabber.is_live:
            x, y, w, h = sel
            self._status.setText(f"Seçim: x={x} y={y} w={w} h={h}")

    def _on_hover(self, x, y, color):
        if color is not None:
            b, g, r = color
            self._pixel.setText(f"({x}, {y})  RGB({r}, {g}, {b})")
        else:
            self._pixel.clear()

    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Ekran görüntüsü aç",
                                              self.ctx.project.root if self.ctx.project else "",
                                              "Görseller (*.png *.jpg *.jpeg *.bmp)")
        if path:
            img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                QMessageBox.warning(self, "Açılamadı", path)
            else:
                self._on_screenshot(img)

    # ---- seçim eylemleri ------------------------------------------------- #
    def _save_selection(self):
        sel, image, project = self._canvas.selection(), self._canvas.image, self.ctx.project
        if not (sel and image is not None and project):
            return
        name, ok = QInputDialog.getText(self, "Görsel kaydet", "Görsel adı:", text="button")
        if not ok or not name.strip():
            return
        x, y, w, h = sel
        path = project.unique_image_path(name.strip())
        ok, buf = cv2.imencode(".png", image[y:y + h, x:x + w])
        if not ok:
            QMessageBox.warning(self, "Kaydedilemedi", path)
            return
        buf.tofile(path)  # Türkçe karakterli yollar için imwrite yerine
        self.ctx.bus.images_changed.emit()
        self.ctx.bus.insert_text_requested.emit(f'"{os.path.basename(path)}"')
        self._status.setText(f"Kaydedildi: {project.relative(path)}")

    def _insert_region(self):
        sel = self._canvas.selection()
        if sel:
            self.ctx.bus.insert_text_requested.emit("Region(%d, %d, %d, %d)" % sel)

    def _insert_location(self):
        sel = self._canvas.selection()
        if sel:
            x, y, w, h = sel
            self.ctx.bus.insert_text_requested.emit(f"Location({x + w // 2}, {y + h // 2})")

    # ---- eşleşme testi --------------------------------------------------- #
    def _on_test_requested(self, path: str):
        i = self._pattern_box.findData(path)
        if i < 0:
            self.ctx.images.refresh(force=True)
            i = self._pattern_box.findData(path)
        self._pattern_box.setCurrentIndex(max(i, 0))
        self.reveal()
        self._test_pattern()

    def create_matcher(self):
        """Script'in kullandığı eşleştiricinin aynısı (her zaman çoklu ölçek)."""
        return ContextFactory.create_matcher(self.ctx.project.config)

    def _test_pattern(self):
        image, path = self._canvas.image, self._pattern_box.currentData()
        if image is None or not path or not self.ctx.project:
            self._status.setText("Önce ekran görüntüsü alın ve bir görsel seçin.")
            return
        needle = ImageLibrary(ImagePath()).load(Pattern(path))
        sel = self._canvas.selection()
        x0, y0 = (sel[0], sel[1]) if sel else (0, 0)
        haystack = image[y0:y0 + sel[3], x0:x0 + sel[2]] if sel else image
        matcher = self.create_matcher()
        threshold = self._sim.value()
        best = matcher.best(haystack, needle)
        found = matcher.find_all(haystack, needle, threshold, max_results=50)
        self._canvas.show_matches([((m.rect.x + x0, m.rect.y + y0, m.rect.w, m.rect.h), m.score) for m in found])
        where = " (seçili alanda)" if sel else ""
        best_txt = f"{best.score:.3f}" if best else "-"
        scale_txt = ""
        if best and best.rect.w != needle.shape[1]:
            scale_txt = f" (ölçek ≈{best.rect.w / needle.shape[1]:.2f}x)"
        color = "#98c379" if found else "#e5c07b"
        self._status.setText(f"<span style='color:{color}'>{os.path.basename(path)}: {len(found)} eşleşme"
                             f"{where}, en iyi skor {best_txt}{scale_txt}, eşik {threshold:.2f}</span>")
