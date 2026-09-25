"""Kapsamlı klavye kısayolu yardımcısı.

Dikkat: PySide6'da QShortcut(key, parent, slot, context=...) yapıcısı context argümanını yok sayar ve
kısayol pencere geneli (WindowShortcut) olur. Böylece örneğin proje ağacındaki "Return" kısayolu
editördeki Enter'ı da yutar. Kapsamı her zaman bu fonksiyonla, setContext ile verin.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut


def shortcut(key: str, parent, slot, context: Qt.ShortcutContext = Qt.WidgetShortcut) -> QShortcut:
    sc = QShortcut(QKeySequence(key), parent)
    sc.setContext(context)
    sc.activated.connect(slot)
    return sc
