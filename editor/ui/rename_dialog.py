"""File / folder rename dialog (F2 in the project tree).

For images it can also update the "images/old.png" and "old.png" strings in code."""

from __future__ import annotations

import html
import os
from typing import Dict, List, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QLabel, QLineEdit, QVBoxLayout)

from ..core import search
from ..core.context import EditorContext
from ..core.project import Project
from ..i18n import tr

INVALID_CHARS = set('\\/:*?"<>|')


def image_references(project: Project, path: str) -> Tuple[Dict[str, str], List[search.LineMatch]]:
    """Possible spellings of the image in code (relative path and bare name) and where they occur."""
    rel = project.relative(path).replace(os.sep, "/")
    forms = {rel: rel, os.path.basename(path): os.path.basename(path)}
    pattern = search.string_literal_pattern(list(forms))
    return forms, search.search_files(project.root, pattern, ["*.py"])


class RenameDialog(QDialog):
    def __init__(self, ctx: EditorContext, path: str, parent=None):
        super().__init__(parent)
        self._path = path
        self._dir = os.path.dirname(path)
        is_dir = os.path.isdir(path)
        project = ctx.project
        self.setWindowTitle(tr("Rename"))
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(8)
        rel = project.relative(path).replace(os.sep, "/") if project and project.contains(path) else path
        name_html = f"<b>{html.escape(os.path.basename(path))}</b>"
        title = QLabel(tr("Rename folder {name}", name=name_html) if is_dir else tr("Rename file {name}", name=name_html))
        layout.addWidget(title)
        where = QLabel(rel)
        where.setObjectName("hint")
        layout.addWidget(where)

        self.name_edit = QLineEdit(os.path.basename(path))
        self.name_edit.textChanged.connect(self._validate)
        layout.addWidget(self.name_edit)
        self._error = QLabel()
        self._error.setStyleSheet("color: #db5c5c;")
        layout.addWidget(self._error)

        # image references
        self.refs = QCheckBox()
        self.refs.setVisible(False)
        self._has_refs = False
        self._matches: List[search.LineMatch] = []
        if project and not is_dir and path.lower().endswith(Project.IMAGE_EXTENSIONS):
            _forms, self._matches = image_references(project, path)
            if self._matches:
                files = len({m.path for m in self._matches})
                self.refs.setText(tr("Also update references in code ({places} places, {files} files)",
                                      places=len(self._matches), files=files))
                self.refs.setToolTip("\n".join(f"{project.relative(m.path)}:{m.line}  {m.line_text.strip()}"
                                               for m in self._matches[:20]))
                self.refs.setChecked(True)
                self.refs.setVisible(True)
                self._has_refs = True
        layout.addWidget(self.refs)

        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._buttons.button(QDialogButtonBox.Ok).setText(tr("Rename"))
        self._buttons.button(QDialogButtonBox.Cancel).setText(tr("Cancel"))
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        # like IntelliJ: the name without the extension is preselected
        stem = os.path.splitext(os.path.basename(path))[0] if not is_dir else os.path.basename(path)
        self.name_edit.setSelection(0, len(stem) or len(self.name_edit.text()))
        self._validate()

    @property
    def new_name(self) -> str:
        return self.name_edit.text().strip()

    @property
    def new_path(self) -> str:
        return os.path.join(self._dir, self.new_name)

    @property
    def update_references(self) -> bool:
        return self._has_refs and self.refs.isChecked()

    def _validate(self) -> None:
        name = self.new_name
        error = ""
        if not name:
            error = tr("The name cannot be empty.")
        elif any(c in INVALID_CHARS for c in name):
            error = tr("The name cannot contain these characters: {chars}", chars="\\ / : * ? \" < > |")
        elif name in (".", ".."):
            error = tr("Invalid name.")
        elif name != os.path.basename(self._path) and os.path.exists(self.new_path) \
                and os.path.normcase(self.new_path) != os.path.normcase(self._path):
            error = tr("“{name}” already exists.", name=name)
        self._error.setText(error)
        self._error.setVisible(bool(error))
        self._buttons.button(QDialogButtonBox.Ok).setEnabled(not error and name != os.path.basename(self._path))
