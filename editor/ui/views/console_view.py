"""Console: script output + expression evaluation while debugging / stdin while running."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

from ...core.session import SessionState
from ...i18n import N_, tr
from ..theme import THEME
from .base import DockView


class _HistoryLineEdit(QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._history, self._index = [], 0

    def push(self, text: str) -> None:
        if text and (not self._history or self._history[-1] != text):
            self._history.append(text)
        self._index = len(self._history)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Up and self._history:
            self._index = max(0, self._index - 1)
            self.setText(self._history[self._index])
        elif e.key() == Qt.Key_Down and self._history:
            self._index = min(len(self._history), self._index + 1)
            self.setText(self._history[self._index] if self._index < len(self._history) else "")
        else:
            super().keyPressEvent(e)


class ConsoleView(DockView):
    view_id = "console"
    title = N_("Console")
    icon_name = "console"
    stripe_bottom = True
    default_area = Qt.BottomDockWidgetArea
    tab_group = "bottom"
    shortcut = "Alt+4"

    def build(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._out = QPlainTextEdit(readOnly=True)
        self._out.setFont(THEME.code_font(10))
        self._out.setMaximumBlockCount(20000)
        self._out.setUndoRedoEnabled(False)
        layout.addWidget(self._out)

        row = QHBoxLayout()
        row.setContentsMargins(6, 3, 6, 3)
        self._prompt = QLabel()
        self._input = _HistoryLineEdit()
        self._input.setFont(THEME.code_font(10))
        self._input.returnPressed.connect(self._submit)
        self._prompt.setObjectName("hint")
        row.addWidget(self._prompt)
        row.addWidget(self._input, 1)
        layout.addLayout(row)

        self._formats = {k: self._fmt(c) for k, c in {
            "out": THEME.text, "sys": THEME.console_sys, "err": THEME.console_err,
            "warn": THEME.console_warn, "eval": THEME.console_eval, "echo": THEME.muted}.items()}

        bus = self.ctx.bus
        bus.output.connect(self.append)
        bus.eval_result.connect(self._on_eval)
        self._frame = 0
        bus.debug_paused.connect(lambda _m: setattr(self, "_frame", 0))
        bus.debug_frame_selected.connect(lambda i, _f: setattr(self, "_frame", i))
        self.ctx.session.state_changed.connect(self._update_prompt)
        self._update_prompt(SessionState.IDLE)
        return w

    def title_actions(self):
        return [self.make_action("trash", tr("Clear console"), lambda: self._out.clear())]

    @staticmethod
    def _fmt(color):
        f = QTextCharFormat()
        f.setForeground(color)
        return f

    # ---- output ---------------------------------------------------------- #
    def append(self, text: str, kind: str = "out") -> None:
        bar = self._out.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        cursor = self._out.textCursor()
        cursor.movePosition(QTextCursor.End)
        for line in text.splitlines(keepends=True):
            k = kind
            if kind == "out":
                if " ERROR " in line or "Traceback" in line or line.startswith(("  File ", "Error", "Exception")):
                    k = "err"
                elif " WARNING " in line:
                    k = "warn"
            cursor.insertText(line, self._formats.get(k, self._formats["out"]))
        if at_bottom:
            bar.setValue(bar.maximum())

    # ---- input ----------------------------------------------------------- #
    def _update_prompt(self, state) -> None:
        if state is SessionState.PAUSED:
            self._prompt.setText("debug »")
            self._input.setPlaceholderText(tr("Type an expression (e.g. m.score, SCREEN.best_score('x.png'))"))
            self._input.setEnabled(True)
        elif state is SessionState.RUNNING:
            self._prompt.setText("stdin »")
            self._input.setPlaceholderText(tr("Send input to the script (for input())"))
            self._input.setEnabled(True)
        else:
            self._prompt.setText("»")
            self._input.setPlaceholderText(tr("Used to evaluate expressions while debugging"))
            self._input.setEnabled(False)

    def _submit(self) -> None:
        text = self._input.text()
        self._input.push(text)
        self._input.clear()
        state = self.ctx.session.state
        if state is SessionState.PAUSED:
            if text.strip():
                self.append(f">>> {text}\n", "echo")
                self.ctx.session.evaluate(text, self._frame)
        elif state is SessionState.RUNNING:
            self.append(text + "\n", "echo")
            self.ctx.session.write_stdin(text + "\n")

    def _on_eval(self, msg: dict) -> None:
        if msg.get("value"):
            self.append(msg["value"] + "\n", "eval" if msg.get("ok") else "err")
