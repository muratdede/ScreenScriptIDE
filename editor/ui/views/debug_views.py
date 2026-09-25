"""Debug paneli (IntelliJ'deki Debug penceresi gibi): üstte debug kontrolleri,
altta Çerçeveler | Değişkenler (+ ifade değerlendirme) | Breakpoint'ler."""

from __future__ import annotations

import os
from typing import Dict, List, Tuple

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QBrush, QColor
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QSplitter,
                               QToolBar, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ...core.session import SessionState
from .. import icons
from .base import DockView


def _pane(title: str, body: QWidget, buttons=()) -> Tuple[QWidget, QLabel]:
    """Başlıklı küçük bölme."""
    w = QWidget()
    layout = QVBoxLayout(w)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    head = QHBoxLayout()
    head.setContentsMargins(10, 4, 6, 4)
    label = QLabel(title)
    label.setObjectName("hint")
    head.addWidget(label)
    head.addStretch()
    for b in buttons:
        head.addWidget(b)
    layout.addLayout(head)
    layout.addWidget(body, 1)
    return w, label


def _small_button(icon_name: str, tip: str, slot, color: str = icons.DEFAULT_COLOR) -> QToolButton:
    b = QToolButton()
    b.setIcon(icons.icon(icon_name, color, 14))
    b.setToolTip(tip)
    b.setAutoRaise(True)
    b.clicked.connect(slot)
    return b


class DebugView(DockView):
    view_id = "debug"
    title = "Debug"
    icon_name = "bug"
    default_area = Qt.BottomDockWidgetArea
    tab_group = "bottom"
    stripe_bottom = True
    shortcut = "Alt+5"

    MAX_EVALS = 8

    def build(self):
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- kontroller (attach_actions ile doldurulur) --------------------
        top = QHBoxLayout()
        top.setContentsMargins(6, 0, 10, 0)
        self._toolbar = QToolBar()
        self._toolbar.setIconSize(QSize(16, 16))
        self._toolbar.setStyleSheet("QToolBar { padding: 2px 0; }")
        top.addWidget(self._toolbar)
        top.addStretch()
        self._state = QLabel()
        self._state.setObjectName("hint")
        top.addWidget(self._state)
        root.addLayout(top)

        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet("background: #1e1f22;")
        root.addWidget(line)

        # --- çerçeveler ----------------------------------------------------
        self._frames = QListWidget()
        self._frames.currentRowChanged.connect(self._select_frame)
        frames_pane, self._frames_label = _pane("Çerçeveler", self._frames)

        # --- değişkenler + ifade değerlendirme ------------------------------
        self._eval_input = QLineEdit()
        self._eval_input.setPlaceholderText("İfade değerlendir (Enter) — ör. m.score, SCREEN.best_score('x.png')")
        self._eval_input.returnPressed.connect(self._submit_eval)
        self._eval_input.setClearButtonEnabled(True)
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["İsim", "Tip", "Değer"])
        self._tree.setRootIsDecorated(False)
        self._tree.setColumnWidth(0, 180)
        self._tree.setColumnWidth(1, 110)
        self._tree.setUniformRowHeights(True)
        vars_body = QWidget()
        vb = QVBoxLayout(vars_body)
        vb.setContentsMargins(6, 0, 6, 0)
        vb.setSpacing(4)
        vb.addWidget(self._eval_input)
        vb.addWidget(self._tree, 1)
        vars_pane, self._vars_label = _pane("Değişkenler", vars_body, [
            _small_button("trash", "Değerlendirme sonuçlarını temizle", self._clear_evals)])

        # --- breakpoint'ler -------------------------------------------------
        self._bps = QListWidget()
        self._bps.itemDoubleClicked.connect(self._open_breakpoint)
        bp_pane, self._bp_label = _pane("Breakpoint'ler", self._bps, [
            _small_button("minimize", "Seçili breakpoint'i kaldır", self._remove_breakpoint),
            _small_button("trash", "Tüm breakpoint'leri kaldır", self.ctx.breakpoints.clear)])

        split = QSplitter(Qt.Horizontal)
        split.addWidget(frames_pane)
        split.addWidget(vars_pane)
        split.addWidget(bp_pane)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 5)
        split.setStretchFactor(2, 2)
        split.setSizes([260, 700, 260])
        split.setChildrenCollapsible(False)
        root.addWidget(split, 1)

        self._stack: List[dict] = []
        self._frame = 0
        self._evals: List[Tuple[str, str, bool]] = []   # (ifade, değer, başarılı)
        self._pending: Dict[int, str] = {}              # eval id -> ifade

        bus = self.ctx.bus
        bus.debug_paused.connect(self._on_paused)
        bus.debug_resumed.connect(self._on_resumed)
        bus.run_finished.connect(self._on_finished)
        bus.eval_result.connect(self._on_eval)
        self.ctx.breakpoints.changed.connect(lambda _p: self._refresh_breakpoints())
        self.ctx.breakpoints.muted_changed.connect(lambda _m: self._refresh_breakpoints())
        self.ctx.session.state_changed.connect(self._update_state)
        self._update_state(SessionState.IDLE)
        return w

    # ---- kontroller ------------------------------------------------------ #
    def attach_actions(self, actions: Dict[str, QAction]) -> None:
        tb = self._toolbar
        s = self.ctx.session

        # F5 hem "başlat" hem "devam"dır; burada ikisi ayrı düğme olarak gösterilir
        self._start = QAction(icons.icon("bug", "#5fb865"), "Debug başlat (F5)", self)
        self._start.triggered.connect(actions["debug"].trigger)
        self._resume = QAction(icons.icon("resume", "#5fb865"), "Devam (F5)", self)
        self._resume.triggered.connect(actions["debug"].trigger)
        tb.addAction(self._start)
        tb.addAction(self._resume)
        tb.addAction(actions["pause"])
        tb.addAction(actions["stop"])
        tb.addSeparator()
        tb.addAction(actions["step_over"])
        tb.addAction(actions["step_into"])
        tb.addAction(actions["step_out"])
        tb.addSeparator()
        tb.addAction(actions["mute_bps"])
        tb.addAction(actions["stop_on_entry"])
        s.state_changed.connect(self._update_proxy_actions)
        self._update_proxy_actions(s.state)

    def _update_proxy_actions(self, state) -> None:
        self._start.setEnabled(state is SessionState.IDLE)
        self._resume.setEnabled(state is SessionState.PAUSED)

    def _update_state(self, state) -> None:
        debugging = self.ctx.session.is_debugging
        if state is SessionState.IDLE:
            text = "Çalışmıyor — F5 ile debug başlatın"
        elif state is SessionState.PAUSED:
            text = self._location_text()
        else:
            text = "● Çalışıyor…" if debugging else "● Çalışıyor (debug olmadan)"
        self._state.setText(text)
        paused = state is SessionState.PAUSED
        self._eval_input.setEnabled(paused)
        self._frames.setEnabled(paused)
        self._tree.setEnabled(paused)

    def _location_text(self) -> str:
        if not self._stack:
            return "‖ Duraklatıldı"
        f = self._stack[0]
        return f"‖ Duraklatıldı · {os.path.basename(f['file'])}:{f['line']}"

    # ---- olaylar --------------------------------------------------------- #
    def _on_paused(self, msg):
        self._stack = msg.get("stack", [])
        self._frame = 0
        self._frames.blockSignals(True)
        self._frames.clear()
        for f in self._stack:
            item = QListWidgetItem(icons.icon("frames", "#9da0a8", 14),
                                   f"{f['function']}  ·  {os.path.basename(f['file'])}:{f['line']}")
            item.setToolTip(f"{f['file']}:{f['line']}")
            self._frames.addItem(item)
        self._frames.setCurrentRow(0)
        self._frames.blockSignals(False)
        self._frames_label.setText(f"Çerçeveler ({len(self._stack)})")
        if self._stack:
            self._show_variables(self._stack[0])
        self._update_state(SessionState.PAUSED)

    def _on_resumed(self):
        self._frames.setEnabled(False)
        self._tree.setEnabled(False)

    def _on_finished(self, _code):
        self._stack = []
        self._frames.clear()
        self._tree.clear()
        self._evals.clear()
        self._pending.clear()
        self._frames_label.setText("Çerçeveler")
        self._vars_label.setText("Değişkenler")

    def _select_frame(self, row):
        if 0 <= row < len(self._stack):
            self._frame = row
            frame = self._stack[row]
            self.ctx.bus.debug_frame_selected.emit(row, frame)
            self.ctx.bus.open_file_requested.emit(frame["file"], int(frame["line"]))
            self._show_variables(frame)

    # ---- değişkenler ----------------------------------------------------- #
    def _show_variables(self, frame):
        self._tree.clear()
        for expr, value, ok in self._evals:
            item = QTreeWidgetItem([expr, "=", value])
            item.setIcon(0, icons.icon("eval", "#98c379" if ok else "#f07178", 14))
            item.setForeground(2, QBrush(QColor("#98c379" if ok else "#f07178")))
            item.setToolTip(2, value)
            self._tree.addTopLevelItem(item)
        variables = frame.get("variables", [])
        for v in variables:
            item = QTreeWidgetItem([v["name"], v["type"], v["value"]])
            item.setIcon(0, icons.icon("variables", "#9da0a8", 14))
            item.setForeground(1, QBrush(QColor("#868a91")))
            item.setToolTip(2, v["value"])
            self._tree.addTopLevelItem(item)
        self._vars_label.setText(f"Değişkenler ({len(variables)})")

    def _submit_eval(self):
        text = self._eval_input.text().strip()
        if not text or self.ctx.session.state is not SessionState.PAUSED:
            return
        self._pending[self.ctx.session.evaluate(text, self._frame)] = text
        self.ctx.bus.output.emit(f">>> {text}\n", "echo")
        self._eval_input.selectAll()

    def _on_eval(self, msg):
        expr = self._pending.pop(msg.get("id"), None)
        if expr is not None:
            value = (msg.get("value") or "").strip() or "(değer yok)"
            self._evals.insert(0, (expr, value, bool(msg.get("ok"))))
            del self._evals[self.MAX_EVALS:]
        if self._stack:
            frame = self._stack[self._frame]
            if msg.get("variables") is not None:   # exec ile değişken değişmiş olabilir
                frame = dict(frame, variables=msg["variables"])
                self._stack[self._frame] = frame
            self._show_variables(frame)

    def _clear_evals(self):
        self._evals.clear()
        if self._stack:
            self._show_variables(self._stack[self._frame])

    # ---- breakpoint'ler -------------------------------------------------- #
    def _refresh_breakpoints(self):
        self._bps.clear()
        project = self.ctx.project
        muted = self.ctx.breakpoints.muted
        bp_icon = icons.icon("mute_breakpoints" if muted else "breakpoint", "#db5c5c", 14)
        items = list(self.ctx.breakpoints.all())
        for path, line in items:
            name = project.relative(path) if project and project.contains(path) else path
            item = QListWidgetItem(bp_icon, f"{name.replace(os.sep, '/')} : {line}")
            item.setData(Qt.UserRole, (path, line))
            if muted:
                item.setForeground(QBrush(QColor("#6f737a")))
            self._bps.addItem(item)
        label = f"Breakpoint'ler ({len(items)})" if items else "Breakpoint'ler"
        self._bp_label.setText(f"{label} — kapalı" if muted else label)

    def _open_breakpoint(self, item):
        path, line = item.data(Qt.UserRole)
        self.ctx.bus.open_file_requested.emit(path, line)

    def _remove_breakpoint(self):
        item = self._bps.currentItem()
        if item:
            path, line = item.data(Qt.UserRole)
            self.ctx.breakpoints.remove(path, line)
