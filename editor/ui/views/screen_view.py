"""Ekran view'ı: cihaz ekranını yakala, alan seç, görsel kaydet / Region ekle, eşleşmeyi test et."""

from __future__ import annotations

import os
import time
from typing import List, Optional, Tuple

import cv2
import numpy as np
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFileDialog, QFrame, QGraphicsEllipseItem,
                               QGraphicsItem, QGraphicsPixmapItem,
                               QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView,
                               QHBoxLayout, QInputDialog, QLabel, QMessageBox, QToolButton, QVBoxLayout,
                               QWidget)

from screenscript import ImageLibrary, ImagePath, Key, Pattern

from ...agent.context_factory import ContextFactory
from .. import icons
from .base import DockView


def bgr_to_qimage(image: np.ndarray) -> QImage:
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, rgb.strides[0], QImage.Format_RGB888).copy()


class ScreenCanvas(QGraphicsView):
    """Görüntüyü gösterir; sürükleyerek alan seçilir. Ctrl+tekerlek = yakınlaştır.
    Tıklayıcı modunda fare ve klavye cihaza girdi olarak gider: tık = dokunma, sürükle = kaydırma,
    basılı tut = uzun basma, tekerlek = kaydırma, yazı/Enter/Backspace/Esc(=Geri) = tuşlar."""

    selection_changed = Signal(object)   # (x, y, w, h) ya da None
    hovered = Signal(int, int, object)   # x, y, (b, g, r) ya da None
    # tıklayıcı modu (koordinatlar cihaz pikseli)
    tapped = Signal(int, int)
    swiped = Signal(int, int, int, int, int)   # x1, y1, x2, y2, süre ms
    long_pressed = Signal(int, int, int)       # x, y, süre ms
    text_typed = Signal(str)
    key_sent = Signal(int, str)                # keycode, açıklama

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
        self.clicker = False
        self._press = None
        self._trail = None

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

    # ---- tıklayıcı modu: fare/klavye -> cihaz girdisi ------------------- #
    TAP_SLOP_PX = 8          # ekran pikseli; bundan az hareket = dokunma
    LONG_PRESS_MS = 500

    def set_clicker(self, on: bool) -> None:
        self.clicker = on
        self._press = None
        self.viewport().setCursor(Qt.PointingHandCursor if on else Qt.ArrowCursor)
        self.setFocusPolicy(Qt.StrongFocus if on else Qt.WheelFocus)
        if on:
            self.clear_selection()
            self.setFocus()

    def _device_point(self, pos) -> Tuple[int, int]:
        p = self._clamped(pos)
        h, w = self._image.shape[:2]
        return min(int(p.x()), w - 1), min(int(p.y()), h - 1)

    def _clicker_press(self, e) -> None:
        self._press = (e.position().toPoint(), self._device_point(e.position().toPoint()), time.monotonic())
        pen = QPen(QColor(95, 184, 101, 220), 3)
        pen.setCosmetic(True)
        self._trail = self.scene().addLine(QLineF(), pen)
        self._trail.setZValue(6)

    def _clicker_move(self, e) -> None:
        if self._press and self._trail is not None:
            x0, y0 = self._press[1]
            x1, y1 = self._device_point(e.position().toPoint())
            self._trail.setLine(QLineF(x0, y0, x1, y1))

    def _clicker_release(self, e) -> None:
        if not self._press:
            return
        view_start, (x0, y0), t0 = self._press
        self._press = None
        if self._trail is not None:
            self.scene().removeItem(self._trail)
            self._trail = None
        x1, y1 = self._device_point(e.position().toPoint())
        ms = int((time.monotonic() - t0) * 1000)
        moved = (e.position().toPoint() - view_start).manhattanLength() > self.TAP_SLOP_PX
        if moved:
            self.swiped.emit(x0, y0, x1, y1, min(max(ms, 80), 3000))
            self._ripple(x1, y1, QColor(95, 184, 101))
        elif ms >= self.LONG_PRESS_MS:
            self.long_pressed.emit(x0, y0, ms)
            self._ripple(x0, y0, QColor(242, 197, 92))
        else:
            self.tapped.emit(x0, y0)
            self._ripple(x0, y0, QColor(53, 116, 240))

    def _ripple(self, x: int, y: int, color: QColor) -> None:
        """Dokunulan yerde kısa süre görünen halka (sabit ekran boyutunda)."""
        item = QGraphicsEllipseItem(-14, -14, 28, 28)
        item.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        item.setPos(x, y)
        item.setPen(QPen(color, 2))
        fill = QColor(color)
        fill.setAlpha(80)
        item.setBrush(QBrush(fill))
        item.setZValue(7)
        self.scene().addItem(item)
        steps = [0]

        def fade():
            steps[0] += 1
            if steps[0] >= 10 or item.scene() is None:
                timer.stop()
                if item.scene() is not None:
                    self.scene().removeItem(item)
                return
            item.setOpacity(1 - steps[0] / 10)
            item.setScale(1 + steps[0] * 0.08)
        timer = QTimer(self, interval=35)
        timer.timeout.connect(fade)
        timer.start()

    def keyPressEvent(self, e):
        if not self.clicker:
            return super().keyPressEvent(e)
        keys = {Qt.Key_Backspace: (Key.DEL, "Sil"), Qt.Key_Return: (Key.ENTER, "Enter"),
                Qt.Key_Enter: (Key.ENTER, "Enter"), Qt.Key_Tab: (Key.TAB, "Tab"), Qt.Key_Escape: (Key.BACK, "Geri"),
                Qt.Key_Home: (Key.HOME, "Ana ekran")}
        if e.key() in keys:
            self.key_sent.emit(int(keys[e.key()][0]), keys[e.key()][1])
        elif e.text() and e.text().isprintable() and not (e.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
            self.text_typed.emit(e.text())
        else:
            super().keyPressEvent(e)

    def focusNextPrevChild(self, _next):
        return not self.clicker and super().focusNextPrevChild(_next)   # Tab cihaza gitsin

    # ---- fare ------------------------------------------------------------ #
    def _clamped(self, pos) -> QPointF:
        p = self.mapToScene(pos)
        r = self.sceneRect()
        return QPointF(min(max(p.x(), 0), r.width()), min(max(p.y(), 0), r.height()))

    def mousePressEvent(self, e):
        if self.clicker and e.button() == Qt.LeftButton and self._image is not None:
            self.setFocus()
            self._clicker_press(e)
            return
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
        if self.clicker:
            self._clicker_move(e)
            return
        if self._origin is not None and self._selection is not None:
            self._selection.setRect(QRectF(self._origin, p).normalized())
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self.clicker and e.button() == Qt.LeftButton:
            self._clicker_release(e)
            return
        if self._origin is not None:
            self._origin = None
            self.selection_changed.emit(self.selection())
        super().mouseReleaseEvent(e)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            factor = 1.25 if e.angleDelta().y() > 0 else 0.8
            self.scale(factor, factor)
        elif self.clicker and self._image is not None:
            # tekerlek = cihazda kaydırma (liste/ sayfa gezinme)
            x, y = self._device_point(e.position().toPoint())
            h = self._image.shape[0]
            dy = int(h * 0.18) * (1 if e.angleDelta().y() > 0 else -1)
            self.swiped.emit(x, y, x, min(max(y + dy, 0), h - 1), 200)
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
        self._clicker_btn = self._button(bar, "Tıklayıcı", "pointer",
                                         "Tıklayıcı modu: ekrana tıklayınca cihaza dokunma gönderilir, sürükle = "
                                         "kaydır, basılı tut = uzun bas, tekerlek = kaydır, klavye = yazı", None)
        self._clicker_btn.setCheckable(True)
        self._clicker_btn.toggled.connect(self._set_clicker)
        self._nav_buttons = [
            self._button(bar, "", "back", "Geri (Esc)", lambda: self._send_key(Key.BACK, "Geri")),
            self._button(bar, "", "home", "Ana ekran", lambda: self._send_key(Key.HOME, "Ana ekran")),
            self._button(bar, "", "recents", "Son uygulamalar", lambda: self._send_key(Key.APP_SWITCH, "Son uygulamalar")),
        ]
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
        c, cfg = self._canvas, lambda: self.ctx.app_config
        c.tapped.connect(lambda x, y: self._input(lambda: self.ctx.device_input.tap(cfg(), x, y)))
        c.swiped.connect(lambda x1, y1, x2, y2, ms: self._input(
            lambda: self.ctx.device_input.swipe(cfg(), x1, y1, x2, y2, ms)))
        c.long_pressed.connect(lambda x, y, ms: self._input(lambda: self.ctx.device_input.long_press(cfg(), x, y, ms)))
        c.key_sent.connect(self._send_key)
        c.text_typed.connect(self._queue_text)
        self._text_buffer = ""
        self._text_timer = QTimer(self, singleShot=True, interval=150)   # hızlı yazılan harfleri tek komutta gönder
        self._text_timer.timeout.connect(self._flush_text)
        self.ctx.device_input.sent.connect(self._on_input_sent)
        self.ctx.device_input.failed.connect(self._on_input_failed)
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
        self._set_clicker(False)
        return w

    @staticmethod
    def _button(layout, text, icon_name, tip, slot) -> QToolButton:
        b = QToolButton(text=text)
        b.setIcon(icons.icon(icon_name))
        b.setToolTip(tip)
        b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon if text else Qt.ToolButtonIconOnly)
        if slot is not None:
            b.clicked.connect(slot)
        layout.addWidget(b)
        return b

    @staticmethod
    def _separator() -> QFrame:
        line = QFrame()
        line.setFixedSize(1, 16)
        line.setStyleSheet("background: #43454a;")
        return line

    # ---- tıklayıcı modu -------------------------------------------------- #
    def _set_clicker(self, on: bool) -> None:
        if on and not self.ctx.project:
            self._clicker_btn.setChecked(False)
            return
        self._canvas.set_clicker(on)
        for b in self._nav_buttons:
            b.setVisible(on)
        for b in (self._save_btn, self._region_btn, self._loc_btn):
            b.setVisible(not on)
        if on:
            if not self.ctx.grabber.is_live:
                self.ctx.toggle_live_screen()     # sonucu görmek için canlı görüntü
            self._pixel.setText("Tıklayıcı açık — girdiler cihaza gider")

    def _input(self, send) -> None:
        if self.ctx.project and self._canvas.clicker:
            self._flush_text()
            send()

    def _send_key(self, keycode: int, label: str = "") -> None:
        self._input(lambda: self.ctx.device_input.key(self.ctx.app_config, int(keycode), label))

    def _queue_text(self, text: str) -> None:
        self._text_buffer += text
        self._text_timer.start()

    def _flush_text(self) -> None:
        self._text_timer.stop()
        if self._text_buffer and self.ctx.project:
            text, self._text_buffer = self._text_buffer, ""
            self.ctx.device_input.text(self.ctx.app_config, text)

    def _on_input_sent(self, label: str) -> None:
        if self._canvas.clicker:
            self._pixel.setText(f"→ {label}")

    def _on_input_failed(self, message: str) -> None:
        self._pixel.setText(f"<span style='color:#f07178'>Girdi gönderilemedi</span>")
        self.ctx.bus.output.emit(f"Girdi gönderilemedi: {message}\n", "err")

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
        if self._canvas.clicker:
            return
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
