"""Sending notifications from a script (push_notification).

Notifier         : Abstract interface that sends notifications. send(content, caption) — text or image.
NullNotifier     : For unconfigured projects; raises NotificationError on every send.
TelegramNotifier : Telegram Bot API. Resolves the username to a chat_id among the people who wrote to
                   the bot (getUpdates) and writes the found ID to the cache file.

In Telegram a bot cannot message someone who has never written to it. The person receiving
notifications must open the bot once and press Start.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np

from ...i18n import tr
from .errors import NotificationError

log = logging.getLogger("script")

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".webp")


class Notifier(ABC):
    """Notification channel. Behaves like sendMessage for text and sendPhoto for images."""

    def send(self, content: Any, caption: Optional[str] = None) -> None:
        """content: str (text), image file path, np.ndarray (BGR), PNG/JPEG bytes or an object
        with a capture() method (Region, Match, SCREEN)."""
        photo = self._as_photo(content)
        if photo is None:
            text = str(content) if caption is None else f"{caption}\n{content}"
            self.send_text(text)
        else:
            self.send_photo(*photo, caption=caption)

    @abstractmethod
    def send_text(self, text: str) -> None: ...

    @abstractmethod
    def send_photo(self, data: bytes, filename: str, caption: Optional[str] = None) -> None: ...

    @staticmethod
    def _as_photo(content: Any) -> Optional[Tuple[bytes, str]]:
        """Returns (byte data, file name) if the content is an image; None if it is text."""
        if hasattr(content, "capture") and callable(content.capture):
            content = content.capture()
        if isinstance(content, np.ndarray):
            ok, buf = cv2.imencode(".png", content)
            if not ok:
                raise NotificationError(tr("Image could not be converted to PNG"))
            return buf.tobytes(), "screen.png"
        if isinstance(content, (bytes, bytearray)):
            return bytes(content), "image.png"
        if isinstance(content, os.PathLike) or (
                isinstance(content, str) and content.lower().endswith(IMAGE_EXTENSIONS)
                and os.path.isfile(content)):
            path = os.fspath(content)
            try:
                with open(path, "rb") as f:
                    return f.read(), os.path.basename(path)
            except OSError as e:
                raise NotificationError(tr("Image could not be read: {path} ({error})", path=path, error=e)) from e
        return None


class NullNotifier(Notifier):
    """Used by projects without notification settings."""

    @staticmethod
    def message() -> str:
        return tr("Notifications are not configured: enter the Telegram bot token and username "
                  "in the settings")

    def send_text(self, text: str) -> None:
        raise NotificationError(self.message())

    def send_photo(self, data: bytes, filename: str, caption: Optional[str] = None) -> None:
        raise NotificationError(self.message())


class TelegramNotifier(Notifier):
    """Sends notifications to a single user via the Telegram Bot API.

    username  : "@user" or "user"; a numeric chat_id can be given directly as well.
    cache_path: JSON file storing found chat_ids. Since getUpdates only returns messages of the last
                ~24 hours, once found the ID is read from here.
    """

    API = "https://api.telegram.org/bot{token}/{method}"
    CAPTION_LIMIT = 1024
    TEXT_LIMIT = 4096

    def __init__(self, token: str, username: str, cache_path: Optional[str] = None,
                 timeout: float = 15.0):
        if not token:
            raise NotificationError(tr("Telegram bot token is empty"))
        if not username:
            raise NotificationError(tr("Telegram username is empty"))
        self._token = token
        self._username = username.strip().lstrip("@")
        self._cache_path = cache_path
        self._timeout = timeout
        self._chat_id: Optional[int] = None
        self._lock = threading.Lock()

    # ---- sending --------------------------------------------------------- #
    def send_text(self, text: str) -> None:
        for i in range(0, max(len(text), 1), self.TEXT_LIMIT):
            self._call("sendMessage", {"chat_id": self.chat_id(), "text": text[i:i + self.TEXT_LIMIT]})

    def send_photo(self, data: bytes, filename: str, caption: Optional[str] = None) -> None:
        fields: Dict[str, Any] = {"chat_id": self.chat_id()}
        rest = None
        if caption:
            fields["caption"] = caption[:self.CAPTION_LIMIT]
            rest = caption[self.CAPTION_LIMIT:]
        self._call("sendPhoto", fields, files={"photo": (filename, data)})
        if rest:
            self.send_text(rest)

    def get_updates(self, offset: int = 0, timeout: int = 0) -> list:
        """getUpdates (long polling). If timeout > 0 the connection timeout must be longer than it."""
        fields: Dict[str, Any] = {"timeout": timeout, "allowed_updates": '["message"]'}
        if offset:
            fields["offset"] = offset
        return self._call("getUpdates", fields) or []

    # ---- chat_id resolution ---------------------------------------------- #
    def chat_id(self) -> int:
        with self._lock:
            if self._chat_id is None:
                self._chat_id = self._resolve_chat_id()
            return self._chat_id

    def _resolve_chat_id(self) -> int:
        if self._username.lstrip("-").isdigit():
            return int(self._username)
        key = self._username.lower()
        cache = self._load_cache()
        if key in cache:
            return int(cache[key])

        found = self._chats_from_updates()
        if key not in found:
            known = ", ".join("@" + n for n in sorted(found)) or tr("none")
            raise NotificationError(tr(
                "@{user} has not started a conversation with the bot. The user needs to open the bot in "
                "Telegram and press Start. (People who wrote to the bot in the last 24 hours: {known})",
                user=self._username, known=known))
        cache.update(found)
        self._save_cache(cache)
        log.info("Telegram: @%s -> chat_id %s", self._username, found[key])
        return found[key]

    def _chats_from_updates(self) -> Dict[str, int]:
        """People who wrote to the bot: {lowercase username: chat_id}."""
        out: Dict[str, int] = {}
        for update in self._call("getUpdates", {}):
            for kind in ("message", "edited_message", "my_chat_member", "callback_query"):
                item = update.get(kind)
                if not item:
                    continue
                chat = item.get("chat") or item.get("message", {}).get("chat")
                user = item.get("from") or {}
                if chat and chat.get("type") == "private" and user.get("username"):
                    out[user["username"].lower()] = chat["id"]
        return out

    def remember_chat(self, username: str, chat_id: int) -> None:
        """Writes a found chat_id to the cache (resolution uses it when someone else consumes getUpdates)."""
        cache = self._load_cache()
        key = username.strip().lstrip("@").lower()
        if cache.get(key) != chat_id:
            cache[key] = chat_id
            self._save_cache(cache)

    def _load_cache(self) -> Dict[str, int]:
        if not self._cache_path or not os.path.isfile(self._cache_path):
            return {}
        try:
            with open(self._cache_path, encoding="utf-8") as f:
                data = json.load(f)
            return {str(k).lower(): int(v) for k, v in data.items()}
        except (OSError, ValueError, TypeError, AttributeError):
            log.warning(tr("Telegram cache could not be read: %s"), self._cache_path)
            return {}

    def _save_cache(self, cache: Dict[str, int]) -> None:
        if not self._cache_path:
            return
        try:
            with open(self._cache_path, "w", encoding="utf-8") as f:
                json.dump(cache, f, indent=2, ensure_ascii=False)
        except OSError as e:
            log.warning(tr("Telegram cache could not be written: %s (%s)"), self._cache_path, e)

    # ---- HTTP ------------------------------------------------------------ #
    def _call(self, method: str, fields: Dict[str, Any],
              files: Optional[Dict[str, Tuple[str, bytes]]] = None) -> Any:
        url = self.API.format(token=self._token, method=method)
        if files:
            body, content_type = _multipart(fields, files)
        else:
            body = urllib.parse.urlencode(fields).encode()
            content_type = "application/x-www-form-urlencoded"
        request = urllib.request.Request(url, data=body, headers={"Content-Type": content_type})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as e:
            try:
                payload = json.load(e)
            except ValueError:
                raise NotificationError(f"Telegram {method}: HTTP {e.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise NotificationError(tr("Could not connect to Telegram ({method}): {error}",
                                       method=method, error=e)) from None
        if not payload.get("ok"):
            desc = payload.get("description", tr("unknown error"))
            if payload.get("error_code") in (401, 404):
                desc += tr(" — check the bot token")
            raise NotificationError(f"Telegram {method}: {desc}")
        return payload.get("result")


def _multipart(fields: Dict[str, Any], files: Dict[str, Tuple[str, bytes]]) -> Tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                     f'{value}\r\n'.encode())
    for name, (filename, data) in files.items():
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                     f'filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'.encode())
        parts.append(data)
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"
