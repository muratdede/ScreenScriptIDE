"""Uygulama (kullanıcı) düzeyindeki ayarlar ve dosyalar.

Hepsi tek klasörde durur: %LOCALAPPDATA%\\ScreenScript  (Linux/macOS: ~/.local/share/ScreenScript)
    ide.ini              — IDE durumu: pencere yerleşimi, son projeler, açık dosyalar (QSettings, INI)
    settings.json        — uygulama ayarları (cihaz bağlantısı, Telegram bot token'ı ...); Qt gerektirmez,
                           script süreci (editor.agent) de buradan okur
    telegram_chats.json  — kullanıcı adı -> chat_id önbelleği
    *.session            — Telegram oturum dosyaları (çalışma alanında tutulmaz)

Proje klasörlerinde gizli bilgi kalmaz. Eski sürümlerin bıraktıkları ilk açılışta buraya taşınır:
Windows kayıt defterindeki QSettings değerleri, proje .screenscript.json'undaki telegram_* ve cihaz
bağlantısı alanları,
proje kökündeki .telegram_chats.json ve çalışma alanındaki *.session dosyaları.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import shutil
from dataclasses import asdict, dataclass, fields
from typing import List, Optional

log = logging.getLogger(__name__)

APP_NAME = "ScreenScript"


def app_data_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def app_file(name: str) -> str:
    return os.path.join(app_data_dir(), name)


def telegram_cache_path() -> str:
    return app_file("telegram_chats.json")


def telegram_session_path(name: str = "oturum") -> str:
    """Telethon gibi kütüphaneler için oturum dosyası yolu (uzantısız verilirse .session eklenir)."""
    return app_file(name if name.endswith(".session") else name + ".session")


@dataclass
class AppConfig:
    """Tüm projelerde ortak kullanıcı ayarları. Yeni alan eklemek geriye dönük uyumludur."""

    # cihaz bağlantısı (bu bilgisayardaki emülatöre/cihaza bağlı; projeden bağımsız)
    host: str = "127.0.0.1"
    port: int = 5555
    adb_path: str = ""
    screencap_format: str = "stream"   # stream (H.264, hızlı) | raw | png
    background: bool = False           # script çalışırken arka planda sürekli yakala

    # push_notification: Telegram bot token'ı (boşsa TELEGRAM_BOT_TOKEN ortam değişkeni) ve
    # bildirim alacak kullanıcı adı. Kullanıcının bota bir kez Start demiş olması gerekir.
    telegram_token: str = ""
    telegram_username: str = ""
    telegram_commands: bool = True     # editör açıkken bota gelen /capture gibi komutları yanıtla

    FILE = "settings.json"

    @classmethod
    def path(cls) -> str:
        return app_file(cls.FILE)

    @classmethod
    def load(cls) -> "AppConfig":
        data = cls._load_raw()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def _load_raw(cls) -> dict:
        try:
            with open(cls.path(), encoding="utf-8") as f:
                return json.load(f) or {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            log.warning("Uygulama ayarları okunamadı: %s (%s)", cls.path(), e)
            return {}

    def save(self) -> None:
        tmp = self.path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path())

    def effective_telegram_token(self) -> str:
        return self.telegram_token.strip() or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


# --------------------------------------------------------------------------- #
# Eski konumlardan taşıma
# --------------------------------------------------------------------------- #
TELEGRAM_KEYS = ("telegram_token", "telegram_username")
DEVICE_KEYS = ("host", "port", "adb_path", "screencap_format", "background")
OLD_TELEGRAM_CACHE = ".telegram_chats.json"


def migrate_project(root: str, raw_config: dict) -> bool:
    """Proje ayarındaki telegram_* ve cihaz bağlantısı alanlarını, proje kökündeki önbelleği uygulama
    ayarlarına taşır. Proje ayar dosyasının yeniden yazılması gerekiyorsa True döndürür."""
    rewrite = False
    if any(k in raw_config for k in TELEGRAM_KEYS + DEVICE_KEYS):
        app = AppConfig.load()
        saved = AppConfig._load_raw()
        changed = False
        for key in TELEGRAM_KEYS:
            value = str(raw_config.get(key) or "").strip()
            if value and not getattr(app, key):   # kullanıcı ayarı zaten varsa ezilmez
                setattr(app, key, value)
                changed = True
        for key in DEVICE_KEYS:
            # varsayılanlar boş olmadığından "zaten ayarlı" = settings.json'da anahtar var;
            # böylece ilk taşınan projenin bağlantısı alınır, sonrakiler ezmez
            if key in raw_config and key not in saved:
                value = raw_config[key]
                if key == "port":
                    value = int(value)
                elif key == "background":
                    value = bool(value)
                else:
                    value = str(value or "").strip()
                setattr(app, key, value)
                changed = True
        if changed:
            app.save()
            log.info("Proje ayarlarındaki Telegram/cihaz bağlantısı alanları uygulama ayarlarına taşındı: %s",
                     root)
        rewrite = True
    _merge_telegram_cache(os.path.join(root, OLD_TELEGRAM_CACHE))
    move_session_files(root)
    return rewrite


def _merge_telegram_cache(old_path: str) -> None:
    if not os.path.isfile(old_path):
        return
    try:
        with open(old_path, encoding="utf-8") as f:
            old = json.load(f)
        new_path = telegram_cache_path()
        current = {}
        if os.path.isfile(new_path):
            with open(new_path, encoding="utf-8") as f:
                current = json.load(f)
        merged = {**old, **current}
        with open(new_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)
        os.remove(old_path)
    except (OSError, ValueError) as e:
        log.warning("Telegram önbelleği taşınamadı: %s (%s)", old_path, e)


def move_session_files(directory: str) -> List[str]:
    """Klasördeki *.session (ve -journal) dosyalarını uygulama klasörüne taşır. Hedefte aynı adda
    dosya varsa kaynak olduğu gibi bırakılır (veri kaybı olmasın)."""
    moved = []
    for src in glob.glob(os.path.join(directory, "*.session")) + \
            glob.glob(os.path.join(directory, "*.session-journal")):
        dst = app_file(os.path.basename(src))
        if os.path.exists(dst):
            log.warning("Oturum dosyası taşınmadı, hedefte zaten var: %s", dst)
            continue
        try:
            shutil.move(src, dst)
            moved.append(dst)
            log.info("Oturum dosyası taşındı: %s -> %s", src, dst)
        except OSError as e:
            log.warning("Oturum dosyası taşınamadı (kullanımda olabilir): %s (%s)", src, e)
    return moved


# --------------------------------------------------------------------------- #
# IDE durumu (Qt)
# --------------------------------------------------------------------------- #
def create_ide_settings(migrate_registry: bool = True):
    """%LOCALAPPDATA%\\ScreenScript\\ide.ini'ye yazan QSettings. İlk açılışta eski (kayıt defteri)
    değerleri kopyalanır ve eski konum temizlenir."""
    from PySide6.QtCore import QSettings

    settings = QSettings(app_file("ide.ini"), QSettings.IniFormat)
    if migrate_registry and not settings.allKeys():
        old = QSettings(APP_NAME, "IDE")   # eski: HKCU\Software\ScreenScript\IDE
        keys = old.allKeys()
        if keys:
            for key in keys:
                settings.setValue(key, old.value(key))
            settings.sync()
            old.clear()
            old.sync()
            log.info("IDE ayarları kayıt defterinden taşındı: %d anahtar", len(keys))
    return settings


def open_in_file_manager(path: Optional[str] = None) -> None:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    QDesktopServices.openUrl(QUrl.fromLocalFile(path or app_data_dir()))
