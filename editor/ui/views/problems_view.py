"""Sorunlar: projedeki hata ve uyarıların dosyalara göre listesi (IntelliJ'deki Problems)."""

from __future__ import annotations

import os
from typing import Dict

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QStackedWidget, QTreeWidget, QTreeWidgetItem

from ...core.diagnostics import Diagnostic, counts
from .. import icons
from ..code_editor import CodeEditor
from ..theme import THEME
from .base import DockView

_PATH_ROLE = Qt.UserRole
_DIAG_ROLE = Qt.UserRole + 1


class ProblemsView(DockView):
    view_id = "problems"
    title = "Sorunlar"
    icon_name = "warning"
    default_area = Qt.BottomDockWidgetArea
    tab_group = "bottom"
    stripe_bottom = True
    shortcut = "Alt+6"

    def build(self):
        self._stack = QStackedWidget()
        self._empty = QLabel("Sorun bulunamadı", alignment=Qt.AlignCenter)
        self._empty.setObjectName("hint")
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setIndentation(14)
        self._tree.itemClicked.connect(lambda item, _c: self._activate(item, focus=False))   # tek tık: satıra git
        self._tree.itemActivated.connect(lambda item, _c: self._activate(item, focus=True))  # çift tık / Enter
        self._stack.addWidget(self._empty)
        self._stack.addWidget(self._tree)
        self._files: Dict[str, QTreeWidgetItem] = {}   # normcase yol -> dosya öğesi
        self._show_warnings = True

        store = self.ctx.diagnostics
        store.changed.connect(self._update_file)
        store.reset.connect(self._rebuild)
        self._rebuild()
        return self._stack

    def title_actions(self):
        self._warnings_action = self.make_action("warning", "Uyarıları göster", self._toggle_warnings,
                                                 THEME.warning.name())
        self._warnings_action.setCheckable(True)
        self._warnings_action.setChecked(True)
        return [self._warnings_action,
                self.make_action("refresh", "Projeyi yeniden denetle", self.ctx.diagnostics.recheck_all)]

    # ---- liste ----------------------------------------------------------- #
    def _toggle_warnings(self, checked: bool):
        self._show_warnings = checked
        self._rebuild()

    def _rebuild(self):
        self._tree.clear()
        self._files.clear()
        for path in self.ctx.diagnostics.files():
            self._update_file(path, refresh_title=False)
        self._refresh_title()

    def _visible(self, path: str):
        items = self.ctx.diagnostics.get(path)
        return items if self._show_warnings else [d for d in items if d.is_error]

    def _update_file(self, path: str, refresh_title: bool = True):
        key = os.path.normcase(path)
        items = self._visible(path)
        file_item = self._files.get(key)
        if not items:
            if file_item is not None:
                self._tree.takeTopLevelItem(self._tree.indexOfTopLevelItem(file_item))
                del self._files[key]
        else:
            if file_item is None:
                file_item = QTreeWidgetItem()
                file_item.setData(0, _PATH_ROLE, path)
                bold = QFont(file_item.font(0))
                bold.setBold(True)
                file_item.setFont(0, bold)
                self._insert_sorted(file_item, path)
                file_item.setExpanded(True)
                self._files[key] = file_item
            self._fill(file_item, path, items)
        if refresh_title:
            self._refresh_title()

    def _insert_sorted(self, item: QTreeWidgetItem, path: str):
        keys = [self._tree.topLevelItem(i).data(0, _PATH_ROLE).lower() for i in range(self._tree.topLevelItemCount())]
        index = next((i for i, k in enumerate(keys) if k > path.lower()), len(keys))
        self._tree.insertTopLevelItem(index, item)

    def _fill(self, file_item: QTreeWidgetItem, path: str, items):
        project = self.ctx.project
        rel = project.relative(os.path.dirname(path)) if project and project.contains(path) else os.path.dirname(path)
        errors, warnings = counts(items)
        summary = ", ".join(s for s in (f"{errors} hata" if errors else "", f"{warnings} uyarı" if warnings else "") if s)
        where = "" if rel in (".", "") else f"   {rel}"
        file_item.setText(0, f"{os.path.basename(path)}{where}   —   {summary}")
        file_item.setIcon(0, icons.file_icon(path))
        file_item.takeChildren()
        for d in items:
            child = QTreeWidgetItem([f"{d.message}   [{d.line}:{d.column + 1}]"])
            child.setIcon(0, icons.icon("error", THEME.error.name()) if d.is_error
                          else icons.icon("warning", THEME.warning.name()))
            child.setData(0, _PATH_ROLE, path)
            child.setData(0, _DIAG_ROLE, d)
            child.setToolTip(0, d.code)
            file_item.addChild(child)

    def _refresh_title(self):
        errors, warnings = self.ctx.diagnostics.totals()
        parts = [f"{errors} hata" if errors else "", f"{warnings} uyarı" if warnings else ""]
        detail = ", ".join(p for p in parts if p)
        self.setWindowTitle(f"Sorunlar ({detail})" if detail else "Sorunlar")
        self._stack.setCurrentWidget(self._tree if self._files else self._empty)

    # ---- gezinme --------------------------------------------------------- #
    def _activate(self, item: QTreeWidgetItem, focus: bool = True):
        """Sorunun satırına git. Tek tıkta odak listede kalır (ok tuşlarıyla gezmeye devam edilebilir)."""
        diag: Diagnostic = item.data(0, _DIAG_ROLE)
        if diag is None:   # dosya satırı: aç/kapat ağacın kendi davranışı (ok / çift tık)
            return
        docs = self.ctx.documents
        editor = docs.open_path(item.data(0, _PATH_ROLE)) if docs else None
        if isinstance(editor, CodeEditor):
            editor.select_match_at(diag.line, diag.column, max(0, diag.end_column - diag.column))
            if focus:
                editor.setFocus()
            else:
                self._tree.setFocus()
