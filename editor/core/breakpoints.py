"""Breakpoint deposu: dosya -> satır kümesi."""

from __future__ import annotations

import os
from typing import Dict, List, Set

from PySide6.QtCore import QObject, Signal


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


class BreakpointStore(QObject):
    changed = Signal(str)   # değişen dosya yolu
    muted_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lines: Dict[str, Set[int]] = {}
        self._display: Dict[str, str] = {}
        self._muted = False

    # ---- geçici kapatma (IntelliJ'deki "Mute Breakpoints") ---------------- #
    @property
    def muted(self) -> bool:
        return self._muted

    def set_muted(self, muted: bool) -> None:
        muted = bool(muted)
        if muted != self._muted:
            self._muted = muted
            self.muted_changed.emit(muted)

    def lines(self, path: str) -> Set[int]:
        return set(self._lines.get(_key(path), set()))

    def toggle(self, path: str, line: int) -> bool:
        k = _key(path)
        s = self._lines.setdefault(k, set())
        self._display[k] = os.path.abspath(path)
        added = line not in s
        s.add(line) if added else s.discard(line)
        self.changed.emit(path)
        return added

    def set_lines(self, path: str, lines: Set[int]) -> None:
        k = _key(path)
        if self._lines.get(k, set()) == set(lines):
            return
        self._lines[k] = set(lines)
        self._display[k] = os.path.abspath(path)
        self.changed.emit(path)

    def remove(self, path: str, line: int) -> None:
        k = _key(path)
        if line in self._lines.get(k, set()):
            self._lines[k].discard(line)
            self.changed.emit(path)

    def clear(self) -> None:
        paths = [self._display[k] for k in self._lines]
        self._lines.clear()
        for p in paths:
            self.changed.emit(p)

    def as_dict(self) -> Dict[str, List[int]]:
        return {self._display[k]: sorted(v) for k, v in self._lines.items() if v}

    def active(self) -> Dict[str, List[int]]:
        """Debugger'a gönderilecek breakpoint'ler; kapalıyken boştur."""
        return {} if self._muted else self.as_dict()

    def all(self) -> List[tuple]:
        return [(self._display[k], line) for k, v in sorted(self._lines.items()) for line in sorted(v)]
