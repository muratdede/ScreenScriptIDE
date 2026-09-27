"""Ayar pencereleri: uygulama ayarları (tüm projeler için ortak) ve proje ayarları."""

from __future__ import annotations

import dataclasses
import os
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton, QSizePolicy, QSpinBox, QWidget)

from ..core import app_settings
from ..core.app_settings import AppConfig
from ..core.project import Project, ProjectConfig
from .inspections_panel import InspectionsPanel
from .settings_dialog import GroupedSettingsDialog, hint


class ProjectSettingsDialog(GroupedSettingsDialog):
    LOG_LEVELS = [("DEBUG", "DEBUG — her şey (arama skorları, adb komutları)"), ("INFO", "INFO — varsayılan"),
                  ("WARNING", "WARNING — sadece uyarı ve hatalar"), ("ERROR", "ERROR — sadece hatalar")]
    OCR_BACKENDS = [("auto", "Otomatik"), ("rapid", "RapidOCR (isabetli)"), ("windows", "Windows OCR (hızlı)")]

    def __init__(self, project: Project, parent=None):
        super().__init__(f"Proje Ayarları — {project.name}", parent)
        self._project = project
        c = project.config

        # eşleştirme
        self.similarity = QDoubleSpinBox(minimum=0.1, maximum=1.0, singleStep=0.05, decimals=2, value=c.similarity)
        self.timeout = QDoubleSpinBox(minimum=0.0, maximum=600, singleStep=0.5, decimals=1, value=c.timeout)
        self.scan_rate = QDoubleSpinBox(minimum=0.2, maximum=30, singleStep=0.5, decimals=1, value=c.scan_rate)
        self.scales = QLineEdit(", ".join(f"{v:g}" for v in c.scales))
        self.scales.setToolTip("Çoklu ölçekli arama her zaman açıktır. Denenecek ölçekler, virgülle.\n"
                               "Daha fazla ölçek = daha toleranslı ama daha yavaş arama. 1.0 her zaman eklenir.")
        self.grayscale = QCheckBox("Gri tonlamada eşleştir (daha hızlı)", checked=c.grayscale)
        self.ocr = QComboBox()
        for value, label in self.OCR_BACKENDS:
            self.ocr.addItem(label, value)
        self.ocr.setCurrentIndex(max(0, self.ocr.findData(c.ocr)))
        self.ocr.setToolTip("read_text / find_text için motor. Otomatik: kurulu olanlardan RapidOCR, yoksa Windows.")

        # çalıştırma
        self.main_script = QLineEdit(c.main_script)
        main_row = self._with_button(self.main_script, "Seç…", self._browse_main)
        self.humanize = QCheckBox("Tıklamalara küçük sapma/gecikme ekle", checked=c.humanize)
        self.tap_ms = QSpinBox(minimum=0, maximum=1000, singleStep=10, value=int(c.tap_ms), suffix=" ms")
        self.tap_ms.setToolTip("0: anlık dokunuş (input tap). >0: parmak bu kadar basılı kalıp kalkar;\n"
                               "oyun kısa dokunuşları kaçırıyorsa 60-120 ms deneyin.")
        self.save_on_fail = QCheckBox("Bulunamayınca ekranı debug/ klasörüne kaydet", checked=c.save_on_fail)
        self.log_level = QComboBox()
        for value, label in self.LOG_LEVELS:
            self.log_level.addItem(label, value)
        self.log_level.setCurrentIndex(max(0, self.log_level.findData(str(c.log_level).upper())))
        self.image_dirs = QLineEdit(", ".join(c.image_dirs))
        self.args = QPlainTextEdit("\n".join(f"{k}={v}" for k, v in c.script_args.items()))
        self.args.setPlaceholderText("anahtar=değer (her satıra bir tane) — script'te ARGS['anahtar']")

        self.args.setMinimumHeight(120)

        self.add_page(("Genel",), [
            ("Script", [("Ana script", main_row)]),
            ("Görseller", [("Görsel klasörleri", self.image_dirs),
                           ("", hint("Proje köküne göre, virgülle ayrılmış klasörler."))]),
            ("", [("", hint("Cihaz bağlantısı ve Telegram bildirim ayarları uygulama ayarlarındadır "
                            "(Ayarlar, Ctrl+Alt+S)."))])])
        self.add_page(("Eşleştirme", "Görsel Arama"), [
            ("Eşik ve süre", [("Varsayılan eşik", self.similarity), ("Bekleme süresi (sn)", self.timeout),
                              ("Tarama hızı (/sn)", self.scan_rate)]),
            ("Ölçek ve renk", [("Ölçekler (her zaman açık)", self.scales), ("", self.grayscale)])])
        self.add_page(("Eşleştirme", "Metin Okuma (OCR)"), [
            ("OCR", [("Motor", self.ocr),
                     ("", hint("read_text / find_text için kullanılır. Otomatik: kurulu olanlardan "
                               "RapidOCR, yoksa Windows OCR."))])])
        self.add_page(("Çalıştırma", "Girdi"), [
            ("Dokunuş", [("", self.humanize), ("Dokunuş süresi", self.tap_ms)])])
        self.add_page(("Çalıştırma", "Script Argümanları"), [
            ("ARGS", [("Argümanlar", self.args)])])
        self.add_page(("Çalıştırma", "Hata Ayıklama"), [
            ("Başarısız arama", [("", self.save_on_fail)]),
            ("Konsol", [("Log seviyesi", self.log_level),
                        ("", hint("Script çalışırken konsola yazılan log mesajlarının en düşük seviyesi. "
                                  "log() INFO seviyesindedir; WARNING seçilirse görünmez."))])])
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
        path, _ = QFileDialog.getOpenFileName(self, "Ana script", self._project.root, "Python (*.py)")
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
    """Uygulama ayarları: %LOCALAPPDATA%\\ScreenScript\\settings.json. Tüm projelerde geçerlidir."""

    _test_done = Signal(bool, str)

    def __init__(self, config: AppConfig, parent=None):
        super().__init__("Ayarlar", parent)
        self._config = config
        c = config

        self.host = QLineEdit(c.host)
        self.port = QSpinBox(minimum=1, maximum=65535, value=int(c.port))
        self.adb = QLineEdit(c.adb_path)
        self.adb.setPlaceholderText("Boş = otomatik bul (PATH / BlueStacks HD-Adb.exe)")
        adb_row = ProjectSettingsDialog._with_button(self.adb, "Gözat…", self._browse_adb)
        self.fmt = QComboBox()
        self.fmt.addItem("stream — H.264 akışı (hızlı, ~35 FPS)", "stream")
        self.fmt.addItem("raw — screencap (yavaş, ~3 FPS)", "raw")
        self.fmt.addItem("png — screencap PNG", "png")
        self.fmt.setCurrentIndex(max(0, self.fmt.findData(c.screencap_format)))
        self.background = QCheckBox("Script çalışırken arka planda sürekli yakala", checked=c.background)

        self.tg_token = QLineEdit(c.telegram_token)
        self.tg_token.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.tg_token.setPlaceholderText("BotFather token'ı (boş = TELEGRAM_BOT_TOKEN ortam değişkeni)")
        self.tg_user = QLineEdit(c.telegram_username)
        self.tg_user.setPlaceholderText("@kullanici — kişi bota bir kez Start demiş olmalı")
        self.tg_commands = QCheckBox("Editör açıkken bot komutlarını yanıtla (/capture: ekran görüntüsü gönder)",
                                     checked=c.telegram_commands)
        self._test_btn = QPushButton("Test mesajı gönder")
        self._test_btn.clicked.connect(self._send_test)
        self._test_result = QLabel()
        self._test_result.setWordWrap(True)
        self._test_done.connect(self._on_test_done)
        test_row = QWidget()
        tr = QHBoxLayout(test_row)
        tr.setContentsMargins(0, 0, 0, 0)
        tr.addWidget(self._test_btn)
        tr.addWidget(self._test_result, 1)

        folder = app_settings.app_data_dir()
        path_label = QLabel(folder)
        path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        path_label.setObjectName("hint")
        open_btn = QPushButton("Klasörü aç")
        open_btn.clicked.connect(lambda: app_settings.open_in_file_manager(folder))
        folder_row = QWidget()
        folder_row.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        fr = QHBoxLayout(folder_row)
        fr.setContentsMargins(0, 0, 0, 0)
        fr.addWidget(path_label, 1)
        fr.addWidget(open_btn)

        self.add_page(("Cihaz", "Bağlantı"), [
            ("ADB", [("Host", self.host), ("Port", self.port), ("adb yolu", adb_row)])])
        self.add_page(("Cihaz", "Ekran Yakalama"), [
            ("Yakalama", [("Format", self.fmt), ("", self.background)])])
        self.add_page(("Bildirimler", "Telegram"), [
            ("Bot", [("Bot token", self.tg_token), ("Kullanıcı adı", self.tg_user), ("", self.tg_commands)]),
            ("Deneme", [("", test_row)]),
            ("", [("", hint("Script'lerde push_notification() bu ayarlarla gönderilir."))])])
        self.inspections = InspectionsPanel(c.disabled_warnings)
        self.add_page(("Editör", "Denetimler"), [
            ("Uyarılar", [("", self.inspections)])])
        self.add_page(("Sistem", "Ayar Klasörü"), [
            ("Konum", [("", folder_row),
                       ("", hint("IDE durumu (ide.ini), uygulama ayarları (settings.json), Telegram önbelleği "
                                 "ve oturum dosyaları (*.session) burada tutulur; proje klasörlerine "
                                 "yazılmaz."))])])
        self.finish()

    def _browse_adb(self):
        path, _ = QFileDialog.getOpenFileName(self, "adb seç", r"C:\Program Files\BlueStacks_nxt"
                                              if os.name == "nt" else "", "adb (adb* HD-Adb*);;Tümü (*)")
        if path:
            self.adb.setText(path)

    def result_config(self) -> AppConfig:
        """Penceredeki değerlerle yeni AppConfig (pencerede olmayan alanlar korunur)."""
        return dataclasses.replace(
            self._config,
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
            self._on_test_done(False, "Token ve kullanıcı adı gerekli.")
            return
        self._test_btn.setEnabled(False)
        self._test_result.setText("Gönderiliyor…")

        def work():
            from ..plugins.screenscript import TelegramNotifier
            try:
                TelegramNotifier(token, user, cache_path=app_settings.telegram_cache_path()) \
                    .send_text("ScreenScript IDE: test bildirimi ✔")
                self._test_done.emit(True, "Gönderildi.")
            except Exception as e:  # noqa: BLE001
                self._test_done.emit(False, str(e))
        threading.Thread(target=work, daemon=True).start()

    def _on_test_done(self, ok: bool, message: str) -> None:
        self._test_btn.setEnabled(True)
        self._test_result.setText(f"<span style='color:{'#5fb865' if ok else '#db5c5c'}'>{message}</span>")