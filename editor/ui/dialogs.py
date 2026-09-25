"""Proje ayarları penceresi."""

from __future__ import annotations

import os

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLineEdit,
                               QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ..core.project import Project, ProjectConfig


class ProjectSettingsDialog(QDialog):
    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Proje Ayarları — {project.name}")
        self.setMinimumWidth(520)
        self._project = project
        c = project.config

        # bağlantı
        self.host = QLineEdit(c.host)
        self.port = QSpinBox(minimum=1, maximum=65535, value=int(c.port))
        self.adb = QLineEdit(c.adb_path)
        self.adb.setPlaceholderText("Boş = otomatik bul (PATH / BlueStacks HD-Adb.exe)")
        adb_row = self._with_button(self.adb, "Gözat…", self._browse_adb)
        self.fmt = QComboBox()
        self.fmt.addItems(["raw", "png"])
        self.fmt.setCurrentText(c.screencap_format)

        # eşleştirme
        self.similarity = QDoubleSpinBox(minimum=0.1, maximum=1.0, singleStep=0.05, decimals=2, value=c.similarity)
        self.timeout = QDoubleSpinBox(minimum=0.0, maximum=600, singleStep=0.5, decimals=1, value=c.timeout)
        self.scan_rate = QDoubleSpinBox(minimum=0.2, maximum=30, singleStep=0.5, decimals=1, value=c.scan_rate)
        self.scales = QLineEdit(", ".join(f"{v:g}" for v in c.scales))
        self.scales.setToolTip("Çoklu ölçekli arama her zaman açıktır. Denenecek ölçekler, virgülle.\n"
                               "Daha fazla ölçek = daha toleranslı ama daha yavaş arama. 1.0 her zaman eklenir.")
        self.grayscale = QCheckBox("Gri tonlamada eşleştir (daha hızlı)", checked=c.grayscale)

        # çalıştırma
        self.main_script = QLineEdit(c.main_script)
        main_row = self._with_button(self.main_script, "Seç…", self._browse_main)
        self.dry_run = QCheckBox("Dry-run: tıklama gönderme, sadece logla", checked=c.dry_run)
        self.humanize = QCheckBox("Tıklamalara küçük sapma/gecikme ekle", checked=c.humanize)
        self.background = QCheckBox("Arka planda sürekli yakala", checked=c.background)
        self.save_on_fail = QCheckBox("Bulunamayınca ekranı debug/ klasörüne kaydet", checked=c.save_on_fail)
        self.image_dirs = QLineEdit(", ".join(c.image_dirs))
        self.args = QPlainTextEdit("\n".join(f"{k}={v}" for k, v in c.script_args.items()))
        self.args.setPlaceholderText("anahtar=değer (her satıra bir tane) — script'te ARGS['anahtar']")
        self.args.setMaximumHeight(90)

        # bildirim
        self.tg_token = QLineEdit(c.telegram_token)
        self.tg_token.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.tg_token.setPlaceholderText("BotFather token'ı (boş = TELEGRAM_BOT_TOKEN ortam değişkeni)")
        self.tg_user = QLineEdit(c.telegram_username)
        self.tg_user.setPlaceholderText("@kullanici — kişi bota bir kez Start demiş olmalı")

        layout = QVBoxLayout(self)
        layout.addWidget(self._group("Cihaz bağlantısı", [
            ("Host", self.host), ("Port", self.port), ("adb yolu", adb_row), ("Yakalama formatı", self.fmt)]))
        layout.addWidget(self._group("Eşleştirme", [
            ("Varsayılan eşik", self.similarity), ("Bekleme süresi (sn)", self.timeout),
            ("Tarama hızı (/sn)", self.scan_rate), ("Ölçekler (her zaman açık)", self.scales), ("", self.grayscale)]))
        layout.addWidget(self._group("Çalıştırma", [
            ("Ana script", main_row), ("Görsel klasörleri", self.image_dirs), ("", self.dry_run),
            ("", self.humanize), ("", self.background), ("", self.save_on_fail), ("Script argümanları", self.args)]))
        layout.addWidget(self._group("Bildirim (Telegram) — push_notification()", [
            ("Bot token", self.tg_token), ("Kullanıcı adı", self.tg_user)]))
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _group(title, rows):
        box = QGroupBox(title)
        form = QFormLayout(box)
        for label, widget in rows:
            form.addRow(label, widget)
        return box

    @staticmethod
    def _with_button(edit, text, slot) -> QWidget:
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit, 1)
        b = QPushButton(text)
        b.clicked.connect(slot)
        row.addWidget(b)
        return w

    def _browse_adb(self):
        path, _ = QFileDialog.getOpenFileName(self, "adb seç", r"C:\Program Files\BlueStacks_nxt"
                                              if os.name == "nt" else "", "adb (adb* HD-Adb*);;Tümü (*)")
        if path:
            self.adb.setText(path)

    def _browse_main(self):
        path, _ = QFileDialog.getOpenFileName(self, "Ana script", self._project.root, "Python (*.py)")
        if path:
            self.main_script.setText(self._project.relative(path))

    def apply(self) -> None:
        c = self._project.config
        c.host = self.host.text().strip() or "127.0.0.1"
        c.port = self.port.value()
        c.adb_path = self.adb.text().strip()
        c.screencap_format = self.fmt.currentText()
        c.similarity = round(self.similarity.value(), 2)
        c.timeout = self.timeout.value()
        c.scan_rate = self.scan_rate.value()
        c.scales = ProjectConfig.normalize_scales(self.scales.text().replace(";", ",").split(","))
        c.grayscale = self.grayscale.isChecked()
        c.main_script = self.main_script.text().strip()
        c.dry_run = self.dry_run.isChecked()
        c.humanize = self.humanize.isChecked()
        c.background = self.background.isChecked()
        c.save_on_fail = self.save_on_fail.isChecked()
        c.image_dirs = [d.strip() for d in self.image_dirs.text().split(",") if d.strip()] or ["images"]
        args = {}
        for line in self.args.toPlainText().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                if k.strip():
                    args[k.strip()] = v.strip()
        c.script_args = args
        c.telegram_token = self.tg_token.text().strip()
        c.telegram_username = self.tg_user.text().strip()
