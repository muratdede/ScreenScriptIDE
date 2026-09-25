"""Vektör ikonlar (16x16 SVG). Dosya gerektirmez; QIcon olarak ya da QSS için dosyaya yazılarak kullanılır.

Yeni ikon eklemek: _ICONS sözlüğüne 16x16 viewBox içinde SVG gövdesi ekleyin. Gövdede renk yerine
{c} kullanın; çizgiler varsayılan olarak {c} ile, dolgular fill="{c}" ile boyanır.
"""

from __future__ import annotations

import os
import tempfile
from functools import lru_cache
from typing import Optional

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_DOTS = '<circle cx="{x}" cy="{y}" r="1.15" fill="{c}" stroke="none"/>'

_ICONS = {
    "menu": '<path d="M2.5 4h11M2.5 8h11M2.5 12h11"/>',
    "folder": '<path d="M1.5 4.5a1 1 0 0 1 1-1h3.6l1.5 1.5h5.9a1 1 0 0 1 1 1v6.5a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1z"/>',
    "image": '<rect x="1.5" y="2.5" width="13" height="11" rx="1.5"/><circle cx="5.3" cy="6" r="1.2"/>'
             '<path d="M2 12l4-3.5 3 2.5 2-2 3 3"/>',
    "phone": '<rect x="4" y="1.5" width="8" height="13" rx="1.6"/><path d="M7 12.3h2"/>',
    "console": '<rect x="1.5" y="2.5" width="13" height="11" rx="1.5"/><path d="M4.5 6l2 2-2 2M8 10.5h3.5"/>',
    "bug": '<path d="M5.6 5.4a2.4 2.4 0 0 1 4.8 0"/><rect x="4.8" y="5.4" width="6.4" height="8.2" rx="3.2"/>'
           '<path d="M8 7.6v4.6M2.6 8.8h2.2M11.2 8.8h2.2M3 12.8l1.9-1.1M13 12.8l-1.9-1.1M3 4.6l1.9 1.3'
           'M13 4.6l-1.9 1.3"/>',
    "run": '<path d="M4.5 2.9v10.2a.7.7 0 0 0 1.05.6l8.1-5.1a.7.7 0 0 0 0-1.2L5.55 2.3a.7.7 0 0 0-1.05.6z" '
           'fill="{c}" stroke="none"/>',
    "stop": '<rect x="3.5" y="3.5" width="9" height="9" rx="1.6" fill="{c}" stroke="none"/>',
    "pause": '<rect x="4" y="3" width="2.6" height="10" rx=".8" fill="{c}" stroke="none"/>'
             '<rect x="9.4" y="3" width="2.6" height="10" rx=".8" fill="{c}" stroke="none"/>',
    "resume": '<rect x="2.5" y="3" width="2.2" height="10" rx=".7" fill="{c}" stroke="none"/>'
              '<path d="M7 3.4v9.2a.6.6 0 0 0 .95.5l6.3-4.6a.6.6 0 0 0 0-1L7.95 2.9A.6.6 0 0 0 7 3.4z" '
              'fill="{c}" stroke="none"/>',
    "step_over": '<path d="M2.5 9.5a5.5 5.5 0 0 1 10.4-2.6"/><path d="M13.3 3.3v3.8H9.5"/>'
                 '<circle cx="8" cy="13" r="1.4" fill="{c}" stroke="none"/>',
    "step_into": '<path d="M8 1.8v7.6M4.8 6.4L8 9.6l3.2-3.2"/>'
                 '<circle cx="8" cy="13.2" r="1.4" fill="{c}" stroke="none"/>',
    "step_out": '<path d="M8 10V2M4.8 5.2L8 2l3.2 3.2"/>'
                '<circle cx="8" cy="13.2" r="1.4" fill="{c}" stroke="none"/>',
    "entry": '<path d="M2 8h8M7 5l3 3-3 3M13.5 3v10"/>',
    "variables": '<path d="M5 2.5c-1.4 0-2 .6-2 2v1.4c0 1-.5 2-1.4 2.1.9.1 1.4 1.1 1.4 2.1v1.4c0 1.4.6 2 2 2'
                 'M11 2.5c1.4 0 2 .6 2 2v1.4c0 1 .5 2 1.4 2.1-.9.1-1.4 1.1-1.4 2.1v1.4c0 1.4-.6 2-2 2'
                 'M6.4 6l3.2 4M9.6 6l-3.2 4"/>',
    "frames": '<path d="M8 2l6 3-6 3-6-3z"/><path d="M2 8l6 3 6-3M2 11l6 3 6-3"/>',
    "breakpoint": '<circle cx="8" cy="8" r="4.6" fill="{c}" stroke="none"/>',
    "breakpoints": '<circle cx="6" cy="6" r="3.4" fill="{c}" stroke="none"/><circle cx="10.5" cy="10.5" r="3.4"/>',
    "settings": '<path d="M6.9 1.6h2.2l.4 1.9 1.3.7 1.8-.7 1.1 1.9-1.4 1.3v1.5l1.4 1.3-1.1 1.9-1.8-.7-1.3.7'
                '-.4 1.9H6.9l-.4-1.9-1.3-.7-1.8.7-1.1-1.9 1.4-1.3V7.2L2.3 5.9l1.1-1.9 1.8.7 1.3-.7z"/>'
                '<circle cx="8" cy="8" r="2"/>',
    "search": '<circle cx="7" cy="7" r="4.5"/><path d="M10.4 10.4l3.6 3.6"/>',
    "save": '<path d="M3 2.5h8l2.5 2.5v8.5a1 1 0 0 1-1 1h-9.5a1 1 0 0 1-1-1v-10a1 1 0 0 1 1-1z"/>'
            '<path d="M5 2.5V6h5V2.5M5 14.5v-4h6v4"/>',
    "camera": '<path d="M2 5.5a1 1 0 0 1 1-1h2l1.2-1.8h3.6L11 4.5h2a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1z"/>'
              '<circle cx="8" cy="8.8" r="2.4"/>',
    "live": '<circle cx="8" cy="8" r="3.6" fill="{c}" stroke="none"/><circle cx="8" cy="8" r="6.2"/>',
    "fit": '<path d="M2 5.5V2h3.5M10.5 2H14v3.5M14 10.5V14h-3.5M5.5 14H2v-3.5"/>',
    "actual": '<path d="M3.5 5L5 4v8M11 5l1.5-1v8"/><circle cx="8" cy="6.5" r=".9" fill="{c}" stroke="none"/>'
              '<circle cx="8" cy="10.5" r=".9" fill="{c}" stroke="none"/>',
    "crop": '<path d="M4 1.5V12h10.5M1.5 4H12v10.5"/>',
    "region": '<rect x="2.5" y="2.5" width="11" height="11" rx="1" stroke-dasharray="2.2 1.6"/>',
    "location": '<circle cx="8" cy="8" r="4.5"/><path d="M8 1v3M8 12v3M1 8h3M12 8h3"/>',
    "match": '<rect x="1.5" y="1.5" width="9" height="9" rx="1"/><circle cx="10.5" cy="10.5" r="3" '
             'fill="none"/><path d="M12.7 12.7l1.8 1.8"/>',
    "close": '<path d="M4.5 4.5l7 7M11.5 4.5l-7 7"/>',
    "chevron_down": '<path d="M4 6.2l4 4 4-4"/>',
    "chevron_up": '<path d="M4 9.8l4-4 4 4"/>',
    "chevron_right": '<path d="M6.2 4l4 4-4 4"/>',
    "check": '<path d="M3.5 8.5l3 3 6-7" stroke-width="1.8"/>',
    "trash": '<path d="M3 4.5h10M6 4.5V3h4v1.5M4.5 4.5l.7 9h5.6l.7-9"/>',
    "refresh": '<path d="M13 8a5 5 0 1 1-1.5-3.6"/><path d="M13 2.5v3h-3"/>',
    "more": "".join(_DOTS.replace("{x}", "8").replace("{y}", str(y)) for y in (3.5, 8, 12.5)),
    "minimize": '<path d="M3.5 8h9"/>',
    "float": '<rect x="2.5" y="5" width="8.5" height="8.5" rx="1"/><path d="M5.5 2.5h8v8"/>',
    "file": '<path d="M3.5 1.5h6l3 3v10h-9z"/><path d="M9.5 1.5v3h3"/>',
    "python": '<path d="M8 1.8c-2.8 0-3 1.2-3 2.1v1.4h3.1v.6H3.8C2.6 5.9 1.6 6.8 1.6 8.7c0 1.9.9 2.9 2.1 2.9H5V9.9'
              'c0-1.2 1-2.2 2.2-2.2h3c1 0 1.8-.8 1.8-1.8V3.9c0-1-.9-2.1-4-2.1z" fill="{c}" stroke="none"/>'
              '<path d="M8 14.2c2.8 0 3-1.2 3-2.1v-1.4H7.9v-.6h4.3c1.2 0 2.2-.9 2.2-2.8 0-1.9-.9-2.9-2.1-2.9H11'
              'v1.7c0 1.2-1 2.2-2.2 2.2h-3c-1 0-1.8.8-1.8 1.8v2c0 1 .9 2.1 4 2.1z" fill="{c}" stroke="none" '
              'opacity=".7"/>',
    "add": '<path d="M8 3v10M3 8h10"/>',
    "pointer": '<path d="M3.5 2.2l9.3 5.4-4 1.1 2.4 4.3-1.7 1-2.4-4.3-3 2.9z"/>',
    "back": '<path d="M5.5 4L2 7.5 5.5 11"/><path d="M2 7.5h7.5a4 4 0 0 1 0 8H8"/>',
    "home": '<path d="M2.5 7.5L8 2.5l5.5 5V14h-4v-4h-3v4h-4z"/>',
    "recents": '<rect x="2.5" y="4.5" width="8" height="9" rx="1"/><path d="M5.5 2.5h7a1 1 0 0 1 1 1v8"/>',
    "eval": '<rect x="2" y="2.5" width="12" height="11" rx="1.5"/><path d="M5 6.5h2.5M6.25 5.25v2.5M9 6.5h2.5'
            'M5 10h2.5M9 9.3h2.5M9 10.7h2.5"/>',
}

_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="none" stroke="{c}" '
        'stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')

DEFAULT_COLOR = "#ced0d6"
DISABLED_COLOR = "#5a5d63"


def svg(name: str, color: str = DEFAULT_COLOR) -> str:
    return _SVG.replace("{body}", _ICONS[name]).replace("{c}", color)


def _render(name: str, color: str, size: int, dpr: float) -> QPixmap:
    px = max(1, round(size * dpr))
    img = QImage(px, px, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    renderer = QSvgRenderer(QByteArray(svg(name, color).encode("utf-8")))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    renderer.render(p, QRectF(0, 0, px, px))
    p.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    return pm


@lru_cache(maxsize=256)
def icon(name: str, color: str = DEFAULT_COLOR, size: int = 16, disabled: Optional[str] = DISABLED_COLOR) -> QIcon:
    ic = QIcon()
    for dpr in (1.0, 1.5, 2.0):
        ic.addPixmap(_render(name, color, size, dpr), QIcon.Normal)
        if disabled:
            ic.addPixmap(_render(name, disabled, size, dpr), QIcon.Disabled)
    return ic


@lru_cache(maxsize=32)
def badge(letter: str, color: str, size: int = 20) -> QIcon:
    """IntelliJ'deki proje rozeti: renkli yuvarlak kare içinde baş harf."""
    ic = QIcon()
    for dpr in (1.0, 2.0):
        px = round(size * dpr)
        pm = QPixmap(px, px)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(color))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(0, 0, px, px), 5 * dpr, 5 * dpr)
        f = QFont()
        f.setBold(True)
        f.setPixelSize(round(11 * dpr))
        p.setFont(f)
        p.setPen(QColor("#ffffff"))
        p.drawText(QRectF(0, 0, px, px), Qt.AlignCenter, (letter[:1] or "?").upper())
        p.end()
        pm.setDevicePixelRatio(dpr)
        ic.addPixmap(pm)
    return ic


_FILE_DIR = os.path.join(tempfile.gettempdir(), "screenscript-ide-icons")


def icon_file(name: str, color: str = DEFAULT_COLOR) -> str:
    """QSS'te url(...) ile kullanmak için ikonu diske yazar; '/' ayraçlı yol döndürür."""
    os.makedirs(_FILE_DIR, exist_ok=True)
    path = os.path.join(_FILE_DIR, f"{name}_{color.lstrip('#')}.svg")
    data = svg(name, color)
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == data:
                return path.replace("\\", "/")
    except OSError:
        pass
    with open(path, "w", encoding="utf-8") as f:
        f.write(data)
    return path.replace("\\", "/")


def file_icon(path: str) -> QIcon:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".py":
        return icon("python", "#548af7")
    if ext in (".png", ".jpg", ".jpeg", ".bmp"):
        return icon("image", "#d6ae58")
    return icon("file", "#9da0a8")
