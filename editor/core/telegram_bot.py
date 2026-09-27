"""Telegram bot commands: answers commands sent to the bot in a background thread while the editor is open.

    /capture  — sends the device's current screenshot as a photo
    /help     — command list

Only the user from the settings (telegram_username; @name or numeric chat_id) gets answers; messages
from other people are ignored. Since the service consumes getUpdates with long polling, the
TelegramNotifier in the script process may not find the user in getUpdates; therefore every seen
chat_id is written to the cache. If the script calls getUpdates at the same time, Telegram returns
409 (Conflict); the service retries after a short wait.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional, Tuple

import cv2
from PySide6.QtCore import QObject, Signal

from ..i18n import tr
from ..plugins.screenscript import TelegramNotifier

from .app_settings import AppConfig, telegram_cache_path
from .grabber import ScreenGrabber

log = logging.getLogger(__name__)


class TelegramCommandService(QObject):
    message = Signal(str)   # for the status bar (emitted from the thread; queued connection)

    POLL_TIMEOUT = 25        # long polling duration (s)
    STALE_SECONDS = 60       # old commands that arrived while the editor was closed are not answered
    MAX_BACKOFF = 30

    def __init__(self, grabber: ScreenGrabber, parent=None):
        super().__init__(parent)
        self._grabber = grabber
        self._config: Optional[AppConfig] = None
        self._key: Optional[Tuple] = None
        self._stop: Optional[threading.Event] = None

    @staticmethod
    def help_text() -> str:
        return tr("ScreenScript IDE commands:\n"
                  "/capture — sends a screenshot of the device screen\n"
                  "/help — this message")

    @property
    def running(self) -> bool:
        return self._stop is not None

    def apply_config(self, config: AppConfig) -> None:
        """Starts / stops according to the settings. Restarts if the token or user changed; if only
        the device connection changed, the running thread uses the new settings on the next command."""
        self._config = config
        token = config.effective_telegram_token()
        user = config.telegram_username.strip().lstrip("@")
        key = (token, user.lower()) if config.telegram_commands and token and user else None
        if key == self._key:
            return
        self.stop()
        self._key = key
        if key is None:
            return
        stop = threading.Event()
        self._stop = stop
        threading.Thread(target=self._run, args=(token, user, stop), daemon=True,
                         name="telegram-commands").start()

    def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()   # an ongoing long poll ends at most after POLL_TIMEOUT; the thread is a daemon
        self._stop = None
        self._key = None

    # ---- thread ---------------------------------------------------------- #
    def _run(self, token: str, user: str, stop: threading.Event) -> None:
        bot = TelegramNotifier(token, user, cache_path=telegram_cache_path(),
                               timeout=self.POLL_TIMEOUT + 10)
        offset = 0
        backoff = 1
        log.info(tr("Telegram command service started (@%s)"), user)
        while not stop.is_set():
            try:
                updates = bot.get_updates(offset, timeout=self.POLL_TIMEOUT)
                backoff = 1
            except Exception as e:  # noqa: BLE001 — network error, 409 Conflict, invalid token ...
                log.warning("Telegram getUpdates: %s", e)
                stop.wait(backoff)
                backoff = min(backoff * 2, self.MAX_BACKOFF)
                continue
            for update in updates:
                offset = max(offset, update.get("update_id", 0) + 1)
                if stop.is_set():
                    break
                try:
                    self._handle(bot, token, user, update.get("message") or {})
                except Exception as e:  # noqa: BLE001
                    log.warning(tr("Telegram command could not be processed: %s"), e)
        log.info(tr("Telegram command service stopped"))

    def _handle(self, bot: TelegramNotifier, token: str, user: str, msg: dict) -> None:
        text = (msg.get("text") or "").strip()
        chat = msg.get("chat") or {}
        sender = msg.get("from") or {}
        if not text.startswith("/") or chat.get("type") != "private":
            return
        if not self._authorized(user, chat, sender):
            log.info(tr("Telegram: command from an unauthorized user ignored (@%s, %s)"),
                     sender.get("username"), chat.get("id"))
            return
        chat_id = chat["id"]
        if sender.get("username"):
            bot.remember_chat(sender["username"], chat_id)
        if time.time() - msg.get("date", 0) > self.STALE_SECONDS:
            return

        command = text.split()[0].split("@")[0].lower()
        reply = TelegramNotifier(token, str(chat_id))
        if command == "/capture":
            self._send_capture(reply)
        else:
            reply.send_text(self.help_text())

    @staticmethod
    def _authorized(user: str, chat: dict, sender: dict) -> bool:
        if user.lstrip("-").isdigit():
            return chat.get("id") == int(user)
        return (sender.get("username") or "").lower() == user.lower()

    def _send_capture(self, reply: TelegramNotifier) -> None:
        config = self._config
        try:
            image = self._grabber.capture(config)
        except Exception as e:  # noqa: BLE001
            reply.send_text(tr("Screenshot could not be taken: {error}", error=e))
            self.message.emit(tr("Telegram /capture: screenshot could not be taken"))
            return
        ok, buf = cv2.imencode(".png", image)
        if not ok:
            reply.send_text(tr("Screenshot could not be converted to PNG"))
            return
        h, w = image.shape[:2]
        reply.send_photo(buf.tobytes(), "screen.png",
                         caption=f"{config.host}:{config.port} — {w}x{h} — {time.strftime('%H:%M:%S')}")
        self.message.emit(tr("Telegram /capture: screenshot sent"))
