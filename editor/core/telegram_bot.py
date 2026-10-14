"""Telegram bot commands: answers commands sent to the bot in a background thread while the editor is open.

    /capture  — sends the device's current screenshot as a photo
    /console  — sends the last lines of the console (/console 50: the last 50)
    /pause, /resume, /stop — forwarded to the running script (its CONTROL); the script decides what
                to do and reports back itself
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
from collections import deque
from typing import Callable, List, Optional, Tuple

import cv2
from PySide6.QtCore import QObject, Signal

from ..i18n import tr
from ..plugins.screenscript import TelegramNotifier

from .app_settings import AppConfig, telegram_cache_path
from .grabber import ScreenGrabber

log = logging.getLogger(__name__)


class TelegramCommandService(QObject):
    message = Signal(str)   # for the status bar (emitted from the thread; queued connection)
    control_requested = Signal(str)   # pause / resume / stop for the running script (emitted from the thread)

    POLL_TIMEOUT = 25        # long polling duration (s)
    STALE_SECONDS = 60       # old commands that arrived while the editor was closed are not answered
    MAX_BACKOFF = 30
    CONSOLE_LINES = 20       # lines sent by /console without a number
    CONSOLE_MAX_LINES = 200  # console lines kept in memory (upper limit of /console N)

    def __init__(self, grabber: ScreenGrabber, script_running: Callable[[], bool] = lambda: False, parent=None):
        super().__init__(parent)
        self._grabber = grabber
        self._script_running = script_running
        self._config: Optional[AppConfig] = None
        self._key: Optional[Tuple] = None
        self._stop: Optional[threading.Event] = None
        self._console: deque = deque(maxlen=self.CONSOLE_MAX_LINES)
        self._console_partial = ""            # last line that has no newline yet
        self._console_lock = threading.Lock()

    @staticmethod
    def help_text() -> str:
        return tr("ScreenScript IDE commands:\n"
                  "/capture — sends a screenshot of the device screen\n"
                  "/console — sends the last 20 lines of the console (/console 50: the last 50)\n"
                  "/pause — pauses the running script (a bot docks first)\n"
                  "/resume — continues a paused script\n"
                  "/stop — stops the running script (a bot docks first)\n"
                  "/help — this message")

    # ---- console --------------------------------------------------------- #
    def record_output(self, text: str, kind: str = "out") -> None:
        """bus.output slot: keeps the last console lines for /console (called on the UI thread;
        the command thread reads them)."""
        with self._console_lock:
            parts = (self._console_partial + text).replace("\r\n", "\n").split("\n")
            self._console_partial = parts.pop()
            self._console.extend(parts)

    def console_tail(self, count: int) -> List[str]:
        """The last `count` console lines (including a line still being written)."""
        with self._console_lock:
            lines = list(self._console)
            if self._console_partial:
                lines.append(self._console_partial)
        return lines[-count:] if count > 0 else []

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

        words = text.split()
        command = words[0].split("@")[0].lower()
        reply = TelegramNotifier(token, str(chat_id))
        if command == "/capture":
            self._send_capture(reply)
        elif command == "/console":
            self._send_console(reply, words[1] if len(words) > 1 else "")
        elif command in ("/pause", "/resume", "/stop"):
            if not self._script_running():
                reply.send_text(tr("No script is running."))
                return
            self.control_requested.emit(command[1:])
            self.message.emit(tr("Telegram {command}: sent to the script", command=command))
        else:
            reply.send_text(self.help_text())

    @staticmethod
    def _authorized(user: str, chat: dict, sender: dict) -> bool:
        if user.lstrip("-").isdigit():
            return chat.get("id") == int(user)
        return (sender.get("username") or "").lower() == user.lower()

    def _send_console(self, reply: TelegramNotifier, arg: str) -> None:
        count = int(arg) if arg.isdigit() else self.CONSOLE_LINES
        count = max(1, min(count, self.CONSOLE_MAX_LINES))
        lines = self.console_tail(count)
        if not any(line.strip() for line in lines):
            reply.send_text(tr("The console is empty."))
        else:
            reply.send_text("\n".join(lines))
        self.message.emit(tr("Telegram /console: last {count} lines sent", count=len(lines)))

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
