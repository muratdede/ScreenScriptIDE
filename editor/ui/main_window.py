"""Main window (IntelliJ "New UI" layout):
  - main toolbar at the top: ☰ menu, project selector, run target, Run / Debug / Stop, settings
  - tool window stripes on the left and right; only one window of the same group is visible
  - editor tabs in the middle, a status bar at the bottom showing the path (breadcrumb) + cursor position
"""

from __future__ import annotations

import hashlib
import os
from typing import Dict, Iterable, List, Optional, Type

from PySide6.QtCore import QByteArray, QPointF, QSize, Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QPainter, QPen, QColor
from PySide6.QtWidgets import (QFileDialog, QInputDialog, QLabel, QMainWindow, QMenu, QMessageBox,
                               QSizePolicy, QToolBar, QToolButton, QWidget)

from ..core.context import EditorContext
from ..core.run_controller import RunController
from ..core.session import SessionState
from ..i18n import tr
from . import icons
from .dialogs import ProjectSettingsDialog
from .editor_tabs import EditorTabs
from .problems_badge import ProblemsBadge
from .theme import THEME
from .views import DEFAULT_VIEWS, DockView

_PROJECT_COLORS = ["#7b61d6", "#3d8f58", "#c75450", "#3574f0", "#c7812d", "#2a9d99"]


class DropDownButton(QToolButton):
    """Text + a small arrow on the right; opens a menu on click (like the project / run selector in IntelliJ)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dropdown")
        self.setPopupMode(QToolButton.InstantPopup)
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setIconSize(QSize(18, 18))

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#9da0a8"), 1.3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        x, y = self.width() - 14, self.height() / 2 - 1
        p.drawPolyline([QPointF(x - 3.5, y - 1.5), QPointF(x, y + 2), QPointF(x + 3.5, y - 1.5)])
        p.end()


def _spacer(width: int = 0) -> QWidget:
    w = QWidget()
    if width:
        w.setFixedWidth(width)
    else:
        w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    return w


class MainWindow(QMainWindow):
    STATE_VERSION = 2   # v1 = old tabbed dock layout; ignored

    def __init__(self, ctx: EditorContext, view_classes: Optional[Iterable[Type[DockView]]] = None):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle("ScreenScript IDE")
        self.resize(1400, 900)
        self.setDockNestingEnabled(True)
        self.setCorner(Qt.BottomLeftCorner, Qt.BottomDockWidgetArea)
        self.setCorner(Qt.BottomRightCorner, Qt.RightDockWidgetArea)

        self.tabs = EditorTabs(ctx)
        ctx.documents = self.tabs
        self.setCentralWidget(self.tabs)
        self._problems_badge = ProblemsBadge(ctx, self.tabs)   # top right of the code area: error/warning summary
        self._problems_badge.clicked.connect(self._show_problems)
        self.run_controller = RunController(ctx, self)

        self._actions: Dict[str, QAction] = {}
        self._find_dialog = None
        self._create_actions()
        self.views: Dict[str, DockView] = {}
        self._view_actions: Dict[str, QAction] = {}
        self._create_views(view_classes or DEFAULT_VIEWS)
        self._create_menu()
        self._create_main_toolbar()
        self._create_stripes()
        self._create_statusbar()
        self.menuBar().hide()   # menus live in the ☰ button; shortcuts keep working since they are added to the window

        bus = ctx.bus
        bus.project_opened.connect(self._on_project_opened)
        bus.project_config_changed.connect(self._on_project_config)
        bus.app_config_changed.connect(self._on_app_config)
        self._on_app_config(ctx.app_config)
        bus.active_file_changed.connect(self._on_active_file)
        bus.run_requested.connect(lambda path, debug: self.run_controller.run(debug=debug, script=path))
        bus.run_started.connect(lambda *_: self.views["console"].reveal() if "console" in self.views else None)
        bus.debug_paused.connect(self._on_paused)
        bus.status_message.connect(lambda text: self.statusBar().showMessage(text, 5000))
        ctx.session.state_changed.connect(self._update_run_actions)
        ctx.grabber.live_changed.connect(self._on_live_changed)
        self._update_run_actions(SessionState.IDLE)
        self._update_run_target()
        self._restore_layout()

    # ---- views ----------------------------------------------------------- #
    def _create_views(self, classes: Iterable[Type[DockView]]) -> None:
        groups: Dict[str, List[DockView]] = {}
        for cls in classes:
            view = cls(self.ctx, self)
            self.views[view.view_id] = view
            self.addDockWidget(view.default_area, view)
            view.attach_actions(self._actions)
            if view.tab_group:
                groups.setdefault(view.tab_group, []).append(view)

            act = QAction(icons.icon(view.icon_name), view.title, self)
            act.setCheckable(True)
            act.setToolTip(f"{view.title} ({view.shortcut})" if view.shortcut else view.title)
            if view.shortcut:
                act.setShortcut(QKeySequence(view.shortcut))
            act.triggered.connect(lambda _checked, v=view: self.toggle_view(v))
            view.visibilityChanged.connect(lambda _vis, v=view, a=act: a.setChecked(v.isVisible()))
            view.windowTitleChanged.connect(lambda _t, v=view, a=act: a.setText(v.title))
            self.addAction(act)
            self._view_actions[view.view_id] = act

        for members in groups.values():
            for other in members[1:]:
                other.hide()
        if "console" in self.views:
            self.resizeDocks([self.views["console"]], [260], Qt.Vertical)
        if "project" in self.views:
            self.resizeDocks([self.views["project"]], [260], Qt.Horizontal)
        if "screen" in self.views:
            self.resizeDocks([self.views["screen"]], [520], Qt.Horizontal)

    def show_view(self, view: DockView) -> None:
        """Shows the view; hides the other (docked) views of the same group."""
        if view.tab_group and not view.isFloating():
            for other in self.views.values():
                if other is not view and other.tab_group == view.tab_group and not other.isFloating():
                    other.hide()
        view.show()
        view.raise_()

    def toggle_view(self, view: DockView) -> None:
        if view.isVisible():
            view.hide()
        else:
            self.show_view(view)
            view.widget().setFocus()

    def _enforce_groups(self) -> None:
        seen = set()
        for view in self.views.values():
            if view.isVisible() and view.tab_group and not view.isFloating():
                if view.tab_group in seen:
                    view.hide()
                seen.add(view.tab_group)

    # ---- actions --------------------------------------------------------- #
    def _action(self, key: str, text: str, slot, shortcut=None, tip: str = "", checkable=False,
                icon: Optional[str] = None, color: str = icons.DEFAULT_COLOR) -> QAction:
        a = QAction(text, self)
        if icon:
            a.setIcon(icons.icon(icon, color))
        if shortcut:
            a.setShortcuts([QKeySequence(s) for s in (shortcut if isinstance(shortcut, (list, tuple)) else [shortcut])])
        a.setToolTip(f"{text} ({a.shortcut().toString()})" if a.shortcut() else text)
        if tip:
            a.setStatusTip(tip)
        a.setCheckable(checkable)
        a.triggered.connect(slot)
        self.addAction(a)   # so the shortcut works even though the menu bar is hidden
        self._actions[key] = a
        return a

    def _create_actions(self) -> None:
        rc = self.run_controller
        A = self._action
        green, red = "#5fb865", "#db5c5c"
        A("open_project", tr("Open Project…"), self.open_project_dialog, "Ctrl+Shift+O", icon="folder")
        A("new_script", tr("New Script…"), self.new_script, "Ctrl+N", icon="add")
        A("save", tr("Save"), self.tabs.save_current, "Ctrl+S", icon="save")
        A("save_all", tr("Save All"), self.tabs.save_all, "Ctrl+Shift+S")
        A("close_tab", tr("Close Tab"), lambda: self.tabs.close_tab(self.tabs.currentIndex()), "Ctrl+W")
        A("quit", tr("Exit"), self.close, "Ctrl+Q")
        A("settings", tr("Project Settings…"), self.edit_settings, "Ctrl+Alt+Shift+S", icon="settings")
        A("app_settings", tr("Settings…"), self.edit_app_settings, "Ctrl+Alt+S", icon="settings")
        A("grab", tr("Capture Screen Live / Stop"), self.ctx.toggle_live_screen, "Ctrl+Shift+G", icon="camera")
        A("grab_once", tr("Capture Single Frame"), self.ctx.grab_screen, icon="refresh")

        A("complete", tr("Complete"), lambda: self._editor_call("show_completions"), None,
          tr("Also opens with Ctrl+Space inside the editor"))
        A("signature", tr("Parameter Info"), lambda: self._editor_call("show_signature", True), None,
          tr("Also opens with Ctrl+Shift+Space inside the editor"))
        A("comment", tr("Toggle Comment"), lambda: self._editor_call("toggle_comment"), None)
        A("goto", tr("Go to Line…"), self.goto_line, "Ctrl+G")
        A("goto_def", tr("Go to Definition"), lambda: self._editor_call("go_to_definition"), ["Ctrl+B", "F12"],
          tr("Ctrl+click works too"))
        A("find", tr("Find…"), lambda: self._editor_call("show_find", False), "Ctrl+F", icon="search")
        A("replace", tr("Replace…"), lambda: self._editor_call("show_find", True), "Ctrl+R")
        A("find_next", tr("Find Next"), lambda: self._editor_call("find_next"), "F3")
        A("find_prev", tr("Find Previous"), lambda: self._editor_call("find_previous"), "Shift+F3")
        A("next_problem", tr("Next Problem"), lambda: self._editor_call("go_to_problem", True), "F8")
        A("prev_problem", tr("Previous Problem"), lambda: self._editor_call("go_to_problem", False), "Shift+F8")
        A("find_files", tr("Find in Files…"), lambda: self.find_in_files(False), "Ctrl+Shift+F", icon="search")
        A("replace_files", tr("Replace in Files…"), lambda: self.find_in_files(True), "Ctrl+Shift+R")

        A("run", tr("Run"), lambda: rc.run(debug=False), ["Shift+F10", "Ctrl+F5"], tr("Run without debugging"),
          icon="run", color=green)
        A("debug", tr("Debug"), self._debug, ["F5", "Shift+F9"], tr("Start or continue debugging"), icon="bug",
          color=green)
        A("stop", tr("Stop"), rc.stop, ["Shift+F5", "Ctrl+F2"], icon="stop", color=red)
        A("pause", tr("Pause"), rc.pause, "F6", icon="pause")
        A("step_over", tr("Step Over"), rc.step_over, "F10", icon="step_over")
        A("step_into", tr("Step Into"), rc.step_into, "F11", icon="step_into")
        A("step_out", tr("Step Out"), rc.step_out, "Shift+F11", icon="step_out")
        A("toggle_bp", tr("Toggle Breakpoint"), lambda: self._editor_call("toggle_breakpoint"), ["F9", "Ctrl+F8"],
          icon="breakpoint", color=red)
        A("mute_bps", tr("Mute Breakpoints"), self.ctx.breakpoints.set_muted, "Ctrl+F9",
          tr("Breakpoints are disabled without being deleted"), checkable=True, icon="mute_breakpoints", color=red)
        self.ctx.breakpoints.muted_changed.connect(self._actions["mute_bps"].setChecked)
        A("stop_on_entry", tr("Stop at First Line When Debugging"), lambda: None, checkable=True, icon="entry")

    def _debug(self) -> None:
        self.run_controller.run(debug=True, stop_on_entry=self._actions["stop_on_entry"].isChecked())

    def _create_menu(self) -> None:
        a = self._actions
        self._main_menu = root = QMenu(self)
        m = root.addMenu(tr("File"))
        m.addActions([a["new_script"], a["open_project"]])
        self._recent_menu = m.addMenu(tr("Recent Projects"))
        self._recent_menu.aboutToShow.connect(lambda: self._fill_recent(self._recent_menu))
        m.addSeparator()
        m.addActions([a["save"], a["save_all"], a["close_tab"]])
        m.addSeparator()
        m.addActions([a["app_settings"], a["settings"]])
        m.addSeparator()
        m.addAction(a["quit"])

        m = root.addMenu(tr("Edit"))
        m.addActions([a["find"], a["replace"], a["find_next"], a["find_prev"]])
        m.addSeparator()
        m.addActions([a["find_files"], a["replace_files"]])
        m.addSeparator()
        m.addActions([a["complete"], a["signature"], a["comment"]])

        m = root.addMenu(tr("Navigate"))
        m.addActions([a["goto_def"], a["goto"], a["next_problem"], a["prev_problem"]])

        m = root.addMenu(tr("Run"))
        m.addActions([a["run"], a["debug"], a["stop"], a["pause"]])
        m.addSeparator()
        m.addActions([a["step_over"], a["step_into"], a["step_out"]])
        m.addSeparator()
        m.addActions([a["toggle_bp"], a["mute_bps"], a["stop_on_entry"]])

        m = root.addMenu(tr("Project"))
        m.addActions([a["settings"], a["grab"], a["grab_once"]])

        m = root.addMenu(tr("View"))
        m.addActions(list(self._view_actions.values()))
        m.addSeparator()
        m.addAction(tr("Reset Layout"), self._reset_layout)

        # keep the old menu bar filled too (not shown with Alt, only for accessibility)
        for menu_action in root.actions():
            self.menuBar().addAction(menu_action)

    def _create_main_toolbar(self) -> None:
        a = self._actions
        tb = QToolBar(tr("Main toolbar"))
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setIconSize(QSize(18, 18))
        tb.toggleViewAction().setVisible(False)
        tb.setContextMenuPolicy(Qt.PreventContextMenu)

        menu_btn = QToolButton()
        menu_btn.setIcon(icons.icon("menu"))
        menu_btn.setToolTip(tr("Main menu"))
        menu_btn.setMenu(self._main_menu)
        menu_btn.setPopupMode(QToolButton.InstantPopup)
        tb.addWidget(menu_btn)
        tb.addWidget(_spacer(4))

        self._project_btn = DropDownButton()
        self._project_btn.setText(tr("No project"))
        self._project_btn.setIcon(icons.badge("?", "#5a5d63"))
        pmenu = QMenu(self)
        pmenu.addAction(a["open_project"])
        recent = pmenu.addMenu(tr("Recent Projects"))
        recent.aboutToShow.connect(lambda: self._fill_recent(recent))
        pmenu.addSeparator()
        pmenu.addAction(a["settings"])
        self._project_btn.setMenu(pmenu)
        tb.addWidget(self._project_btn)

        tb.addWidget(_spacer())

        self._target_btn = DropDownButton()
        self._target_btn.setIconSize(QSize(16, 16))
        tmenu = QMenu(self)
        group = QActionGroup(self)
        self._target_current = tmenu.addAction(tr("Active file"), lambda: self._set_target(RunController.TARGET_CURRENT))
        self._target_main = tmenu.addAction(tr("Main script"), lambda: self._set_target(RunController.TARGET_MAIN))
        for act in (self._target_current, self._target_main):
            act.setCheckable(True)
            group.addAction(act)
        tmenu.addSeparator()
        tmenu.addAction(tr("Change main script…"), self.edit_settings)
        tmenu.aboutToShow.connect(self._update_run_target)
        self._target_btn.setMenu(tmenu)
        tb.addWidget(self._target_btn)
        tb.addActions([a["run"], a["debug"], a["stop"]])

        more = QToolButton()
        more.setIcon(icons.icon("more"))
        more.setToolTip(tr("More run options"))
        mmenu = QMenu(self)
        mmenu.addActions([a["stop_on_entry"], a["toggle_bp"], a["mute_bps"]])
        mmenu.addSeparator()
        mmenu.addActions([a["grab"], a["grab_once"]])
        mmenu.addSeparator()
        mmenu.addAction(a["settings"])
        more.setMenu(mmenu)
        more.setPopupMode(QToolButton.InstantPopup)
        tb.addWidget(more)

        tb.addWidget(_spacer(24))
        search_btn = QToolButton()
        search_btn.setDefaultAction(a["find_files"])
        tb.addWidget(search_btn)
        tb.addAction(a["app_settings"])
        self.addToolBar(Qt.TopToolBarArea, tb)

    def _create_stripes(self) -> None:
        left = self._stripe("stripe_left", Qt.LeftToolBarArea)
        right = self._stripe("stripe_right", Qt.RightToolBarArea)
        bottom_items = []
        for view in self.views.values():
            act = self._view_actions[view.view_id]
            if view.default_area == Qt.RightDockWidgetArea:
                right.addAction(act)
            elif view.stripe_bottom or view.default_area == Qt.BottomDockWidgetArea:
                bottom_items.append(act)
            else:
                left.addAction(act)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        left.addWidget(spacer)
        left.addActions(bottom_items)

    def _stripe(self, name: str, area) -> QToolBar:
        tb = QToolBar(name)
        tb.setObjectName(name)
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setOrientation(Qt.Vertical)
        tb.setIconSize(QSize(18, 18))
        tb.toggleViewAction().setVisible(False)
        tb.setContextMenuPolicy(Qt.PreventContextMenu)
        self.addToolBar(area, tb)
        return tb

    def _create_statusbar(self) -> None:
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        self._crumb_label = QLabel()
        self._state_label = QLabel()
        self._device_label = QLabel()
        self._device_label.setToolTip(tr("ADB target (Settings, Ctrl+Alt+S)"))
        self._pos_label = QLabel()
        self._enc_label = QLabel(tr("UTF-8   4 spaces"))
        self._problems_label = QLabel()
        self._problems_label.setToolTip(tr("Problems (Alt+6) — click"))
        self._problems_label.setCursor(Qt.PointingHandCursor)
        self._problems_label.mousePressEvent = lambda _e: self._show_problems()
        sb.addWidget(self._crumb_label, 1)
        for w in (self._problems_label, self._state_label, self._device_label, self._pos_label, self._enc_label):
            sb.addPermanentWidget(w)
        self.tabs.cursor_moved.connect(self._on_cursor)
        self.ctx.diagnostics.changed.connect(self._update_problems_label)
        self.ctx.diagnostics.reset.connect(self._update_problems_label)
        self._update_problems_label()

    def _update_problems_label(self, *_):
        errors, warnings = self.ctx.diagnostics.totals()
        parts = []
        if errors:
            parts.append(f"<span style='color:{THEME.error.name()}'>●</span> {errors}")
        if warnings:
            parts.append(f"<span style='color:{THEME.warning.name()}'>▲</span> {warnings}")
        self._problems_label.setText("&nbsp;&nbsp;".join(parts))

    def _show_problems(self):
        if "problems" in self.views:
            self.views["problems"].reveal()

    def _on_cursor(self, line: int, col: int) -> None:
        self._pos_label.setText(f"{line}:{col}" if line else "")
        self._enc_label.setVisible(bool(line))

    def find_in_files(self, replace: bool) -> None:
        from .find_in_files import FindInFilesDialog
        if self._find_dialog is None:
            self._find_dialog = FindInFilesDialog(self.ctx, self)
        editor = self.tabs.current_editor()
        selected = editor.textCursor().selectedText() if editor else ""
        self._find_dialog.open_with(selected if selected and " " not in selected else "", replace)

    def _editor_call(self, method: str, *args) -> None:
        editor = self.tabs.current_editor()
        if editor:
            getattr(editor, method)(*args)

    # ---- state ----------------------------------------------------------- #
    def _update_run_actions(self, state: SessionState) -> None:
        a = self._actions
        idle, paused = state is SessionState.IDLE, state is SessionState.PAUSED
        running = state is SessionState.RUNNING
        debugging = self.ctx.session.is_debugging
        a["run"].setEnabled(idle)
        a["debug"].setEnabled(idle or paused)
        a["debug"].setText(tr("Resume") if paused else tr("Debug"))
        a["debug"].setIcon(icons.icon("resume" if paused else "bug", "#5fb865"))
        a["debug"].setToolTip(f"{a['debug'].text()} (F5)")
        a["stop"].setEnabled(not idle)
        a["pause"].setEnabled(running and debugging)
        for k in ("step_over", "step_into", "step_out"):
            a[k].setEnabled(paused)
        text = {SessionState.IDLE: "",
                SessionState.RUNNING: "<span style='color:#5fb865'>●</span> " + tr("Running"),
                SessionState.PAUSED: "<span style='color:#f2c55c'>‖</span> " + tr("Paused")}[state]
        self._state_label.setText(text)
        self._target_btn.setEnabled(idle)

    def _on_paused(self, msg: dict) -> None:
        reason = {"breakpoint": tr("breakpoint"), "step": tr("step"), "pause": tr("pause"),
                  "entry": tr("entry")}.get(msg.get("reason"), msg.get("reason"))
        self.statusBar().showMessage(tr("Stopped ({reason}): {file}:{line}", reason=reason,
                                        file=os.path.basename(msg['file']), line=msg['line']), 5000)
        if "debug" in self.views:
            self.views["debug"].reveal()
        self.activateWindow()

    def _on_live_changed(self, live: bool) -> None:
        a = self._actions["grab"]
        a.setIcon(icons.icon("stop", "#ff8a8a") if live else icons.icon("camera"))
        a.setToolTip(tr("Stop the live view (Ctrl+Shift+G)") if live else tr("Capture the screen live (Ctrl+Shift+G)"))

    def _set_target(self, mode: str) -> None:
        self.run_controller.set_target_mode(mode)
        self._update_run_target()

    def _update_run_target(self, *_):
        rc = self.run_controller
        project = self.ctx.project
        main = project.config.main_script if project else "main.py"
        self._target_main.setText(tr("Main script ({name})", name=main))
        (self._target_current if rc.target_mode == rc.TARGET_CURRENT else self._target_main).setChecked(True)
        target = rc.target_script()
        if target:
            self._target_btn.setText(os.path.basename(target))
            self._target_btn.setIcon(icons.file_icon(target))
            self._target_btn.setToolTip(tr("To run: {path}", path=target))
        else:
            self._target_btn.setText(tr("No script"))
            self._target_btn.setIcon(icons.icon("file", "#6f737a"))
            self._target_btn.setToolTip(tr("No script to run"))

    def _on_active_file(self, path: str) -> None:
        self._update_run_target()
        project = self.ctx.project
        if not path:
            self._crumb_label.setText(project.name if project else "")
            return
        if project and project.contains(path):
            parts = [project.name] + project.relative(path).split(os.sep)
        else:
            parts = [path]
        self._crumb_label.setText("  ›  ".join(parts))

    # ---- project --------------------------------------------------------- #
    def open_project_dialog(self) -> None:
        start = self.ctx.project.root if self.ctx.project else os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(self, tr("Select project folder"), start)
        if path:
            self.open_project(path)

    def open_project(self, path: str) -> bool:
        if self.ctx.session.is_active:
            QMessageBox.information(self, tr("Running"), tr("Stop the running script first."))
            return False
        if not self.tabs.close_all():
            return False
        try:
            self.ctx.open_project(path)
            return True
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("Project could not be opened"), str(e))
            return False

    def _on_project_opened(self, project) -> None:
        self.setWindowTitle(f"{project.name} – ScreenScript IDE")
        color = _PROJECT_COLORS[int(hashlib.md5(project.name.encode("utf-8")).hexdigest(), 16)
                                % len(_PROJECT_COLORS)]
        self._project_btn.setText(project.name)
        self._project_btn.setIcon(icons.badge(project.name[:1], color))
        self._project_btn.setToolTip(project.root)
        self._on_project_config(project)
        opened = self.ctx.settings.value(self._open_files_key(project.root), []) or []
        if isinstance(opened, str):
            opened = [opened]
        for path in opened:
            self.tabs.open_path(path)
        if not self.tabs.count() and project.main_script_path:
            self.tabs.open_path(project.main_script_path)
        self._on_active_file(self.tabs.current_document_path() or "")

    def _on_project_config(self, project) -> None:
        self._update_run_target()

    def _on_app_config(self, config) -> None:
        self._device_label.setText(f"{config.host}:{config.port}")

    @staticmethod
    def _open_files_key(root: str) -> str:
        # \\ and / count as separators in QSettings keys; hash the path
        return "open_files/" + hashlib.md5(os.path.normcase(root).encode("utf-8")).hexdigest()

    def _fill_recent(self, menu: QMenu) -> None:
        menu.clear()
        for root in self.ctx.recent_projects():
            act = menu.addAction(root, lambda r=root: self.open_project(r))
            act.setEnabled(os.path.isdir(root))
        if menu.isEmpty():
            menu.addAction(tr("(empty)")).setEnabled(False)

    def new_script(self) -> None:
        if not self.ctx.project:
            QMessageBox.information(self, tr("No project"), tr("Open a project folder first."))
            return
        name, ok = QInputDialog.getText(self, tr("New script"), tr("File name:"), text=tr("new_script.py"))
        if ok and name.strip():
            try:
                self.tabs.open_path(self.ctx.project.create_script(name.strip()))
            except OSError as e:
                QMessageBox.warning(self, tr("Could not be created"), str(e))

    def edit_settings(self) -> None:
        if not self.ctx.project:
            QMessageBox.information(self, tr("No project"), tr("Open a project folder first."))
            return
        dialog = ProjectSettingsDialog(self.ctx.project, self)
        dialog.applied.connect(self.ctx.save_project_config)   # OK and Apply
        dialog.exec()

    def edit_app_settings(self) -> None:
        from .dialogs import AppSettingsDialog
        dialog = AppSettingsDialog(self.ctx.app_config, self)

        def save():
            old_language = self.ctx.app_config.language
            config = dialog.result_config()
            self.ctx.save_app_config(config)
            self.statusBar().showMessage(tr("Settings saved"), 3000)
            if config.language != old_language:
                QMessageBox.information(self, tr("Language"),
                                        tr("The new interface language will be used after restarting ScreenScript IDE."))
        dialog.applied.connect(save)   # OK and Apply
        dialog.exec()

    def goto_line(self) -> None:
        editor = self.tabs.current_editor()
        if editor:
            line, ok = QInputDialog.getInt(self, tr("Go to line"), tr("Line:"), editor.current_line(), 1,
                                           editor.blockCount())
            if ok:
                editor.go_to_line(line)

    # ---- layout ---------------------------------------------------------- #
    def _restore_layout(self) -> None:
        self._default_state = self.saveState(self.STATE_VERSION)   # for "Reset Layout"
        geo = self.ctx.settings.value("window/geometry")
        state = self.ctx.settings.value("window/state")
        if isinstance(geo, QByteArray):
            self.restoreGeometry(geo)
        if isinstance(state, QByteArray):
            self.restoreState(state, self.STATE_VERSION)
        self._enforce_groups()

    def _reset_layout(self) -> None:
        if self._default_state is not None:
            self.restoreState(self._default_state, self.STATE_VERSION)
        self._enforce_groups()

    def closeEvent(self, event) -> None:
        if self.ctx.session.is_active:
            if QMessageBox.question(self, tr("Running"), tr("A script is running. Stop it and exit?")) \
                    != QMessageBox.Yes:
                event.ignore()
                return
            self.ctx.session.stop()
        if self.ctx.project:
            self.ctx.settings.setValue(self._open_files_key(self.ctx.project.root), self.tabs.open_paths())
        if not self.tabs.close_all():
            event.ignore()
            return
        self.ctx.telegram.stop()
        self.ctx.grabber.shutdown()   # screenrecord processes must close too
        self.ctx.settings.setValue("window/geometry", self.saveGeometry())
        self.ctx.settings.setValue("window/state", self.saveState(self.STATE_VERSION))
        event.accept()
