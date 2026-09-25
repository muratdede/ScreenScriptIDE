"""Dosya / klasör yeniden adlandırma penceresi (proje ağacında F2).

Görseller için koddaki "images/eski.png" ve "eski.png" string'lerini de güncelleyebilir."""

from __future__ import annotations

import os
from typing import Dict, List, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QLabel, QLineEdit, QVBoxLayout)

from ..core import search
from ..core.context import EditorContext
from ..core.project import Project

INVALID_CHARS = set('\\/:*?"<>|')


def image_references(project: Project, path: str) -> Tuple[Dict[str, str], List[search.LineMatch]]:
    """Görselin koddaki olası yazımları (göreli yol ve yalın ad) ve bulunduğu yerler."""
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
        self.setWindowTitle("Yeniden Adlandır")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(8)
        kind = "klasörü" if is_dir else "dosyasını"
        rel = project.relative(path).replace(os.sep, "/") if project and project.contains(path) else path
        title = QLabel(f"<b>{os.path.basename(path)}</b> {kind} yeniden adlandır")
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

        # görsel referansları
        self.refs = QCheckBox()
        self.refs.setVisible(False)
        self._has_refs = False
        self._matches: List[search.LineMatch] = []
        if project and not is_dir and path.lower().endswith(Project.IMAGE_EXTENSIONS):
            _forms, self._matches = image_references(project, path)
            if self._matches:
                files = len({m.path for m in self._matches})
                self.refs.setText(f"Koddaki referansları da güncelle ({len(self._matches)} yer, {files} dosya)")
                self.refs.setToolTip("\n".join(f"{project.relative(m.path)}:{m.line}  {m.line_text.strip()}"
                                               for m in self._matches[:20]))
                self.refs.setChecked(True)
                self.refs.setVisible(True)
                self._has_refs = True
        layout.addWidget(self.refs)

        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._buttons.button(QDialogButtonBox.Ok).setText("Yeniden Adlandır")
        self._buttons.button(QDialogButtonBox.Cancel).setText("İptal")
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        # IntelliJ gibi: uzantı hariç ad seçili gelir
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
            error = "Ad boş olamaz."
        elif any(c in INVALID_CHARS for c in name):
            error = "Ad şu karakterleri içeremez: \\ / : * ? \" < > |"
        elif name in (".", ".."):
            error = "Geçersiz ad."
        elif name != os.path.basename(self._path) and os.path.exists(self.new_path) \
                and os.path.normcase(self.new_path) != os.path.normcase(self._path):
            error = f"“{name}” zaten var."
        self._error.setText(error)
        self._error.setVisible(bool(error))
        self._buttons.button(QDialogButtonBox.Ok).setEnabled(not error and name != os.path.basename(self._path))
