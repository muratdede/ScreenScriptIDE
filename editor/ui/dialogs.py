"""Settings windows: application settings (shared by all projects) and project settings."""

from __future__ import annotations

import dataclasses
import os
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton, QSizePolicy, QSpinBox, QWidget)

from .. import i18n
from ..core import app_settings
from ..core.app_settings import AppConfig
from ..core.project import Project, ProjectConfig
from ..i18n import N_, tr
from .inspections_panel import InspectionsPanel
from .settings_dialog import GroupedSettingsDialog, hint


class ProjectSettingsDialog(GroupedSettingsDialog):
    LOG_LEVELS = [("DEBUG", N_("DEBUG — everything (search scores, adb commands)")), ("INFO", N_("INFO — default")),
                  ("WARNING", N_("WARNING — only warnings and errors")), ("ERROR", N_("ERROR — only errors"))]
    OCR_BACKENDS = [("auto", N_("Automatic")), ("rapid", N_("RapidOCR (accurate)")),
                    ("windows", N_("Windows OCR (fast)"))]

    def __init__(self, project: Project, parent=None):
        super().__init__(tr("Project Settings — {name}", name=project.name), parent)
        self._project = project
        c = project.config

        # matching
        self.similarity = QDoubleSpinBox(minimum=0.1, maximum=1.0, singleStep=0.05, decimals=2, value=c.similarity)
        self.timeout = QDoubleSpinBox(minimum=0.0, maximum=600, singleStep=0.5, decimals=1, value=c.timeout)
        self.scan_rate = QDoubleSpinBox(minimum=0.2, maximum=30, singleStep=0.5, decimals=1, value=c.scan_rate)
        self.scales = QLineEdit(", ".join(f"{v:g}" for v in c.scales))
        self.scales.setToolTip(tr("Multi-scale search is always on. Scales to try, comma separated.\n"
                                  "More scales = more tolerant but slower search. 1.0 is always added."))
        self.grayscale = QCheckBox(tr("Match in grayscale (faster)"), checked=c.grayscale)
        self.ocr = QComboBox()
        for value, label in self.OCR_BACKENDS:
            self.ocr.addItem(tr(label), value)
        self.ocr.setCurrentIndex(max(0, self.ocr.findData(c.ocr)))
        self.ocr.setToolTip(tr("Engine for read_text / find_text. Automatic: RapidOCR if installed, otherwise Windows."))

        # running
        self.main_script = QLineEdit(c.main_script)
        main_row = self._with_button(self.main_script, tr("Choose…"), self._browse_main)
        self.humanize = QCheckBox(tr("Add a small jitter/delay to clicks"), checked=c.humanize)
        self.tap_ms = QSpinBox(minimum=0, maximum=1000, singleStep=10, value=int(c.tap_ms), suffix=" ms")
        self.tap_ms.setToolTip(tr("0: instant tap (input tap). >0: the finger stays down this long, then lifts;\n"
                                  "try 60-120 ms if the game misses short taps."))
        self.save_on_fail = QCheckBox(tr("Save the screen to the debug/ folder when not found"), checked=c.save_on_fail)
        self.log_level = QComboBox()
        for value, label in self.LOG_LEVELS:
            self.log_level.addItem(tr(label), value)
        self.log_level.setCurrentIndex(max(0, self.log_level.findData(str(c.log_level).upper())))
        self.image_dirs = QLineEdit(", ".join(c.image_dirs))
        self.args = QPlainTextEdit("\n".join(f"{k}={v}" for k, v in c.script_args.items()))
        self.args.setPlaceholderText(tr("key=value (one per line) — ARGS['key'] in the script"))

        self.args.setMinimumHeight(120)

        self.add_page((tr("General"),), [
            (tr("Script"), [(tr("Main script"), main_row)]),
            (tr("Images"), [(tr("Image folders"), self.image_dirs),
                            ("", hint(tr("Folders relative to the project root, comma separated.")))]),
            ("", [("", hint(tr("Device connection and Telegram notification settings are in the application "
                               "settings (Settings, Ctrl+Alt+S).")))])])
        self.add_page((tr("Matching"), tr("Image Search")), [
            (tr("Threshold and time"), [(tr("Default threshold"), self.similarity),
                                        (tr("Wait time (s)"), self.timeout),
                                        (tr("Scan rate (/s)"), self.scan_rate)]),
            (tr("Scale and color"), [(tr("Scales (always on)"), self.scales), ("", self.grayscale)])])
        self.add_page((tr("Matching"), tr("Text Recognition (OCR)")), [
            ("OCR", [(tr("Engine"), self.ocr),
                     ("", hint(tr("Used for read_text / find_text. Automatic: RapidOCR if installed, "
                                  "otherwise Windows OCR.")))])])
        self.add_page((tr("Running"), tr("Input")), [
            (tr("Tap"), [("", self.humanize), (tr("Tap duration"), self.tap_ms)])])
        self.add_page((tr("Running"), tr("Script Arguments")), [
            ("ARGS", [(tr("Arguments"), self.args)])])
        self.add_page((tr("Running"), tr("Debugging")), [
            (tr("Failed search"), [("", self.save_on_fail)]),
            (tr("Console"), [(tr("Log level"), self.log_level),
                             ("", hint(tr("The lowest level of log messages written to the console while the "
                                          "script runs. log() is at INFO level; it is hidden if WARNING is "
                                          "selected.")))])])
        self.finish()

    @staticmethod
    def _with_button(edit, text, slot) -> QWidget:
        w = QWidget()
        w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit, 1)
        b = QPushButton(text)
        b.clicked.connect(slot)
        row.addWidget(b)
        return w

    def _browse_main(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Main script"), self._project.root, "Python (*.py)")
        if path:
            self.main_script.setText(self._project.relative(path))

    def apply(self) -> None:
        c = self._project.config
        c.similarity = round(self.similarity.value(), 2)
        c.timeout = self.timeout.value()
        c.scan_rate = self.scan_rate.value()
        c.scales = ProjectConfig.normalize_scales(self.scales.text().replace(";", ",").split(","))
        c.grayscale = self.grayscale.isChecked()
        c.ocr = self.ocr.currentData()
        c.main_script = self.main_script.text().strip()
        c.humanize = self.humanize.isChecked()
        c.tap_ms = self.tap_ms.value()
        c.save_on_fail = self.save_on_fail.isChecked()
        c.log_level = self.log_level.currentData()
        c.image_dirs = [d.strip() for d in self.image_dirs.text().split(",") if d.strip()] or ["images"]
        args = {}
        for line in self.args.toPlainText().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                if k.strip():
                    args[k.strip()] = v.strip()
        c.script_args = args


class AppSettingsDialog(GroupedSettingsDialog):
    """Application settings: %LOCALAPPDATA%\\ScreenScript\\settings.json. Valid for all projects."""

    _test_done = Signal(bool, str)

    def __init__(self, config: AppConfig, parent=None):
        super().__init__(tr("Settings"), parent)
        self._config = config
        c = config

        self.language = QComboBox()
        self.language.addItem(tr("Automatic (system language)"), "")
        for code, native_name in i18n.LANGUAGES:
            self.language.addItem(native_name, code)   # native names are never translated
        self.language.setCurrentIndex(max(0, self.language.findData(c.language)))

        self.host = QLineEdit(c.host)
        self.port = QSpinBox(minimum=1, maximum=65535, value=int(c.port))
        self.adb = QLineEdit(c.adb_path)
        self.adb.setPlaceholderText(tr("Empty = find automatically (PATH / platform-tools / LDPlayer / BlueStacks)"))
        adb_row = ProjectSettingsDialog._with_button(self.adb, tr("Browse…"), self._browse_adb)
        self.fmt = QComboBox()
        self.fmt.addItem(tr("stream — H.264 stream (fast, ~35 FPS)"), "stream")
        self.fmt.addItem(tr("raw — screencap (slow, ~3 FPS)"), "raw")
        self.fmt.addItem(tr("png — screencap PNG"), "png")
        self.fmt.setCurrentIndex(max(0, self.fmt.findData(c.screencap_format)))
        self.background = QCheckBox(tr("Capture continuously in the background while a script runs"),
                                    checked=c.background)

        self.tg_token = QLineEdit(c.telegram_token)
        self.tg_token.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.tg_token.setPlaceholderText(tr("BotFather token (empty = TELEGRAM_BOT_TOKEN environment variable)"))
        self.tg_user = QLineEdit(c.telegram_username)
        self.tg_user.setPlaceholderText(tr("@user — the person must have pressed Start in the bot once"))
        self.tg_commands = QCheckBox(tr("Answer bot commands while the editor is open (/capture: send a screenshot)"),
                                     checked=c.telegram_commands)
        self._test_btn = QPushButton(tr("Send test message"))
        self._test_btn.clicked.connect(self._send_test)
        self._test_result = QLabel()
        self._test_result.setWordWrap(True)
        self._test_done.connect(self._on_test_done)
        test_row = QWidget()
        test_layout = QHBoxLayout(test_row)
        test_layout.setContentsMargins(0, 0, 0, 0)
        test_layout.addWidget(self._test_btn)
        test_layout.addWidget(self._test_result, 1)

        folder = app_settings.app_data_dir()
        path_label = QLabel(folder)
        path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        path_label.setObjectName("hint")
        open_btn = QPushButton(tr("Open folder"))
        open_btn.clicked.connect(lambda: app_settings.open_in_file_manager(folder))
        folder_row = QWidget()
        folder_row.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        fr = QHBoxLayout(folder_row)
        fr.setContentsMargins(0, 0, 0, 0)
        fr.addWidget(path_label, 1)
        fr.addWidget(open_btn)

        self.add_page((tr("Appearance"), tr("Language")), [
            (tr("Interface"), [(tr("Language"), self.language),
                               ("", hint(tr("Applies to menus, messages, logs and Telegram messages. "
                                            "Takes effect after restarting the IDE.")))])])
        self.add_page((tr("Device"), tr("Connection")), [
            ("ADB", [(tr("Host"), self.host), (tr("Port"), self.port), (tr("adb path"), adb_row)])])
        self.add_page((tr("Device"), tr("Screen Capture")), [
            (tr("Capturing"), [(tr("Format"), self.fmt), ("", self.background)])])
        self.add_page((tr("Notifications"), "Telegram"), [
            (tr("Bot"), [(tr("Bot token"), self.tg_token), (tr("Username"), self.tg_user), ("", self.tg_commands)]),
            (tr("Test"), [("", test_row)]),
            ("", [("", hint(tr("push_notification() in scripts is sent with these settings.")))])])
        self.inspections = InspectionsPanel(c.disabled_warnings)
        self.add_page((tr("Editor"), tr("Inspections")), [
            (tr("Warnings"), [("", self.inspections)])])
        self.add_page((tr("System"), tr("Settings Folder")), [
            (tr("Location"), [("", folder_row),
                              ("", hint(tr("IDE state (ide.ini), application settings (settings.json), the "
                                           "Telegram cache and session files (*.session) are kept here; nothing "
                                           "is written to project folders.")))])])
        self.finish()

    def _browse_adb(self):
        start = os.path.dirname(self.adb.text()) if self.adb.text() else ""
        path, _ = QFileDialog.getOpenFileName(self, tr("Select adb"), start, tr("adb (adb* HD-Adb*);;All (*)"))
        if path:
            self.adb.setText(path)

    def result_config(self) -> AppConfig:
        """A new AppConfig with the values in the window (fields not in the window are kept)."""
        return dataclasses.replace(
            self._config,
            language=self.language.currentData(),
            host=self.host.text().strip() or "127.0.0.1",
            port=self.port.value(),
            adb_path=self.adb.text().strip(),
            screencap_format=self.fmt.currentData(),
            background=self.background.isChecked(),
            telegram_token=self.tg_token.text().strip(),
            telegram_username=self.tg_user.text().strip(),
            telegram_commands=self.tg_commands.isChecked(),
            disabled_warnings=self.inspections.disabled_codes())

    def _send_test(self) -> None:
        cfg = self.result_config()
        token, user = cfg.effective_telegram_token(), cfg.telegram_username
        if not token or not user:
            self._on_test_done(False, tr("Token and username are required."))
            return
        self._test_btn.setEnabled(False)
        self._test_result.setText(tr("Sending…"))

        def work():
            from ..plugins.screenscript import TelegramNotifier
            try:
                TelegramNotifier(token, user, cache_path=app_settings.telegram_cache_path()) \
                    .send_text(tr("ScreenScript IDE: test notification ✔"))
                self._test_done.emit(True, tr("Sent."))
            except Exception as e:  # noqa: BLE001
                self._test_done.emit(False, str(e))
        threading.Thread(target=work, daemon=True).start()

    def _on_test_done(self, ok: bool, message: str) -> None:
        self._test_btn.setEnabled(True)
        self._test_result.setText(f"<span style='color:{'#5fb865' if ok else '#db5c5c'}'>{message}</span>")
