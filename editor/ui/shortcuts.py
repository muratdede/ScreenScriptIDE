"""Scoped keyboard shortcut helper.

Note: in PySide6 the QShortcut(key, parent, slot, context=...) constructor ignores the context argument
and the shortcut becomes window-wide (WindowShortcut). Then e.g. the "Return" shortcut of the project
tree also swallows Enter in the editor. Always set the scope with this function, via setContext.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut


def shortcut(key: str, parent, slot, context: Qt.ShortcutContext = Qt.WidgetShortcut) -> QShortcut:
    sc = QShortcut(QKeySequence(key), parent)
    sc.setContext(context)
    sc.activated.connect(slot)
    return sc
