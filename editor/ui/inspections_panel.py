"""Settings › Editor › Inspections: list of warning types that can be disabled (like IntelliJ "Inspections").
Only warnings are listed; errors (syntax, undefined name, …) are always shown."""

from __future__ import annotations

from typing import Iterable, List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core.diagnostics import WARNING_TYPES
from ..i18n import tr
from . import icons
from .settings_dialog import hint
from .theme import THEME

_CODE_ROLE = Qt.UserRole


class InspectionsPanel(QWidget):
    def __init__(self, disabled: Iterable[str], parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        disabled = set(disabled)
        warning_icon = icons.icon("warning", THEME.warning.name())

        self._filter = QLineEdit()
        self._filter.setPlaceholderText(tr("Search inspections"))
        self._filter.setClearButtonEnabled(True)
        self._filter.addAction(icons.icon("search", "#9da0a8"), QLineEdit.LeadingPosition)
        self._filter.textChanged.connect(self._apply_filter)
        enable_all = QPushButton(tr("Enable all"))
        enable_all.clicked.connect(lambda: self._set_all(True))
        disable_all = QPushButton(tr("Disable all"))
        disable_all.clicked.connect(lambda: self._set_all(False))

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumHeight(240)
        self.tree.setIndentation(16)
        groups = {}
        for wt in WARNING_TYPES:
            group = groups.get(wt.group)
            if group is None:
                group = groups[wt.group] = QTreeWidgetItem(self.tree, [tr(wt.group)])
                group.setFlags(group.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
                font = group.font(0)
                font.setBold(True)
                group.setFont(0, font)
            item = QTreeWidgetItem(group, [tr(wt.title)])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setIcon(0, warning_icon)
            item.setData(0, _CODE_ROLE, wt.code)
            item.setToolTip(0, f"{tr(wt.description)}\n({wt.code})")
            item.setCheckState(0, Qt.Unchecked if wt.code in disabled else Qt.Checked)
        self.tree.expandAll()
        self.tree.currentItemChanged.connect(self._show_description)

        self._description = QLabel()
        self._description.setObjectName("hint")
        self._description.setWordWrap(True)
        self._description.setMinimumHeight(36)
        self._description.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self._filter, 1)
        top.addWidget(enable_all)
        top.addWidget(disable_all)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(hint(tr("Unchecked warnings are not shown in the editor and the Problems window. "
                                 "Errors (syntax, undefined name, missing argument, unresolved module, …) "
                                 "cannot be disabled. For a single line you can write # noqa at the end of the line.")))
        layout.addSpacing(4)
        layout.addLayout(top)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self._description)

    def _items(self) -> List[QTreeWidgetItem]:
        return [self.tree.topLevelItem(i).child(j) for i in range(self.tree.topLevelItemCount())
                for j in range(self.tree.topLevelItem(i).childCount())]

    def disabled_codes(self) -> List[str]:
        return [it.data(0, _CODE_ROLE) for it in self._items() if it.checkState(0) != Qt.Checked]

    def _set_all(self, enabled: bool) -> None:
        state = Qt.Checked if enabled else Qt.Unchecked
        for it in self._items():
            if not it.isHidden():   # only the visible ones when filtered
                it.setCheckState(0, state)

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().casefold()
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i)
            group_match = needle in group.text(0).casefold()
            any_visible = False
            for j in range(group.childCount()):
                it = group.child(j)
                hay = f"{it.text(0)} {it.toolTip(0)}".casefold()
                visible = not needle or group_match or needle in hay
                it.setHidden(not visible)
                any_visible |= visible
            group.setHidden(not any_visible)

    def _show_description(self, item, _previous=None) -> None:
        if item is None or item.data(0, _CODE_ROLE) is None:
            self._description.setText("")
            return
        self._description.setText(item.toolTip(0).replace("\n", "  "))

