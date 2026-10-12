"""Application (user) level settings and files.

Everything lives in one folder: %LOCALAPPDATA%\\ScreenScript  (Linux/macOS: ~/.local/share/ScreenScript)
    ide.ini              — IDE state: window layout, recent projects, open files (QSettings, INI)
    settings.json        — application settings (device connection, Telegram bot token, language ...);
                           does not need Qt, the script process (editor.agent.runner) reads it too
    telegram_chats.json  — username -> chat_id cache
    *.session            — Telegram session files (not kept in the workspace)

No secrets stay in project folders. Leftovers from older versions are moved here on first start:
QSettings values in the Windows registry, telegram_* and device connection fields in the project's
.screenscript.json, .telegram_chats.json in the project root and *.session files in the workspace.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import shutil
from dataclasses import asdict, dataclass, field, fields
from typing import List, Optional

from ..i18n import tr

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
    """Session file path for libraries like Telethon (.session is appended when missing).
    The default name is kept from older versions so existing session files are still found."""
    return app_file(name if name.endswith(".session") else name + ".session")


@dataclass
class AppConfig:
    """User settings shared by all projects. Adding new fields is backwards compatible."""

    # interface language: "" = automatic (system language), otherwise a code from i18n.LANGUAGES
    language: str = ""

    # device connection (tied to the emulator/device on this computer; independent of the project)
    host: str = "127.0.0.1"
    port: int = 5555
    adb_path: str = ""
    screencap_format: str = "stream"   # stream (H.264, fast) | raw | png
    background: bool = False           # capture continuously in the background while a script runs

    # RapidOCR on the GPU (DirectML / CUDA) when the installed onnxruntime build supports it
    ocr_gpu: bool = True

    # push_notification: Telegram bot token (TELEGRAM_BOT_TOKEN environment variable when empty) and
    # the username receiving notifications. The user must have pressed Start in the bot once.
    telegram_token: str = ""
    telegram_username: str = ""
    telegram_commands: bool = True     # answer commands like /capture sent to the bot while the editor is open
    # notify when the emulator screen stays unchanged this long (or an adb command times out) while a script runs
    freeze_alert: bool = True
    freeze_seconds: int = 60

    # code inspection: disabled warning codes (diagnostics.WARNING_TYPES); errors cannot be disabled
    disabled_warnings: List[str] = field(default_factory=list)

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
            log.warning(tr("Application settings could not be read: %s (%s)"), cls.path(), e)
            return {}

    def save(self) -> None:
        tmp = self.path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path())

    def effective_telegram_token(self) -> str:
        return self.telegram_token.strip() or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


# --------------------------------------------------------------------------- #
# Migration from old locations
# --------------------------------------------------------------------------- #
TELEGRAM_KEYS = ("telegram_token", "telegram_username")
DEVICE_KEYS = ("host", "port", "adb_path", "screencap_format", "background")
OLD_TELEGRAM_CACHE = ".telegram_chats.json"


def migrate_project(root: str, raw_config: dict) -> bool:
    """Moves telegram_* and device connection fields of the project config, and the cache in the
    project root, into the application settings. Returns True if the project config must be rewritten."""
    rewrite = False
    if any(k in raw_config for k in TELEGRAM_KEYS + DEVICE_KEYS):
        app = AppConfig.load()
        saved = AppConfig._load_raw()
        changed = False
        for key in TELEGRAM_KEYS:
            value = str(raw_config.get(key) or "").strip()
            if value and not getattr(app, key):   # an existing user setting is not overwritten
                setattr(app, key, value)
                changed = True
        for key in DEVICE_KEYS:
            # defaults are not empty, so "already set" = the key exists in settings.json;
            # this way the first migrated project's connection is taken and later ones don't overwrite it
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
            log.info(tr("Telegram/device connection fields in the project settings were moved to the "
                        "application settings: %s"), root)
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
        log.warning(tr("Telegram cache could not be moved: %s (%s)"), old_path, e)


def move_session_files(directory: str) -> List[str]:
    """Moves *.session (and -journal) files in the folder to the application folder. If a file with
    the same name exists at the destination, the source is left as is (to avoid data loss)."""
    moved = []
    for src in glob.glob(os.path.join(directory, "*.session")) + \
            glob.glob(os.path.join(directory, "*.session-journal")):
        dst = app_file(os.path.basename(src))
        if os.path.exists(dst):
            log.warning(tr("Session file not moved, it already exists at the destination: %s"), dst)
            continue
        try:
            shutil.move(src, dst)
            moved.append(dst)
            log.info(tr("Session file moved: %s -> %s"), src, dst)
        except OSError as e:
            log.warning(tr("Session file could not be moved (it may be in use): %s (%s)"), src, e)
    return moved


# --------------------------------------------------------------------------- #
# IDE state (Qt)
# --------------------------------------------------------------------------- #
def create_ide_settings(migrate_registry: bool = True):
    """QSettings writing to %LOCALAPPDATA%\\ScreenScript\\ide.ini. On first start the old (registry)
    values are copied and the old location is cleared."""
    from PySide6.QtCore import QSettings

    settings = QSettings(app_file("ide.ini"), QSettings.IniFormat)
    if migrate_registry and not settings.allKeys():
        old = QSettings(APP_NAME, "IDE")   # old: HKCU\Software\ScreenScript\IDE
        keys = old.allKeys()
        if keys:
            for key in keys:
                settings.setValue(key, old.value(key))
            settings.sync()
            old.clear()
            old.sync()
            log.info(tr("IDE settings were moved from the registry: %d keys"), len(keys))
    return settings


def open_in_file_manager(path: Optional[str] = None) -> None:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    QDesktopServices.openUrl(QUrl.fromLocalFile(path or app_data_dir()))
