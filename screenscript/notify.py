"""Script'ten bildirim gönderme (push_notification).

Notifier         : Bildirim gönderen soyut arayüz. send(content, caption) — metin ya da görsel.
NullNotifier     : Yapılandırılmamış proje için; her gönderimde NotificationError fırlatır.
TelegramNotifier : Telegram Bot API. Kullanıcı adını, bota yazmış kişiler arasından chat_id'ye
                   çevirir (getUpdates) ve bulunan ID'yi önbellek dosyasına yazar.

Telegram'da bir bot, kendisine hiç yazmamış birine mesaj atamaz. Bildirim alacak kişi bir kez
botu açıp Start'a basmalıdır.
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

from .errors import NotificationError

log = logging.getLogger("script")

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".webp")


class Notifier(ABC):
    """Bildirim kanalı. Metin için sendMessage, görsel için sendPhoto benzeri davranır."""

    def send(self, content: Any, caption: Optional[str] = None) -> None:
        """content: str (metin), görsel dosya yolu, np.ndarray (BGR), PNG/JPEG bytes ya da
        capture() metodu olan bir nesne (Region, Match, SCREEN)."""
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
        """İçerik bir görselse (bayt verisi, dosya adı) döndürür; metinse None."""
        if hasattr(content, "capture") and callable(content.capture):
            content = content.capture()
        if isinstance(content, np.ndarray):
            ok, buf = cv2.imencode(".png", content)
            if not ok:
                raise NotificationError("Görsel PNG'ye çevrilemedi")
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
                raise NotificationError(f"Görsel okunamadı: {path} ({e})") from e
        return None


class NullNotifier(Notifier):
    """Bildirim ayarı yapılmamış projelerde kullanılır."""

    MESSAGE = ("Bildirim yapılandırılmamış: proje ayarlarında Telegram bot token'ı ve "
               "kullanıcı adını girin")

    def send_text(self, text: str) -> None:
        raise NotificationError(self.MESSAGE)

    def send_photo(self, data: bytes, filename: str, caption: Optional[str] = None) -> None:
        raise NotificationError(self.MESSAGE)


class TelegramNotifier(Notifier):
    """Telegram Bot API ile tek bir kullanıcıya bildirim gönderir.

    username  : "@kullanici" ya da "kullanici"; doğrudan sayısal chat_id de verilebilir.
    cache_path: Bulunan chat_id'lerin saklandığı JSON dosyası. getUpdates sadece son ~24 saatin
                mesajlarını döndürdüğü için, ID bir kez bulununca buradan okunur.
    """

    API = "https://api.telegram.org/bot{token}/{method}"
    CAPTION_LIMIT = 1024
    TEXT_LIMIT = 4096

    def __init__(self, token: str, username: str, cache_path: Optional[str] = None,
                 timeout: float = 15.0):
        if not token:
            raise NotificationError("Telegram bot token'ı boş")
        if not username:
            raise NotificationError("Telegram kullanıcı adı boş")
        self._token = token
        self._username = username.strip().lstrip("@")
        self._cache_path = cache_path
        self._timeout = timeout
        self._chat_id: Optional[int] = None
        self._lock = threading.Lock()

    # ---- gönderim ------------------------------------------------------- #
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
        """getUpdates (long polling). timeout > 0 ise bağlantı zaman aşımı bundan uzun olmalıdır."""
        fields: Dict[str, Any] = {"timeout": timeout, "allowed_updates": '["message"]'}
        if offset:
            fields["offset"] = offset
        return self._call("getUpdates", fields) or []

    # ---- chat_id çözümleme ---------------------------------------------- #
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
            known = ", ".join("@" + n for n in sorted(found)) or "yok"
            raise NotificationError(
                f"@{self._username} botla konuşma başlatmamış. Kullanıcının Telegram'da botu açıp "
                f"Start'a basması gerekiyor. (Son 24 saatte bota yazanlar: {known})")
        cache.update(found)
        self._save_cache(cache)
        log.info("Telegram: @%s -> chat_id %s", self._username, found[key])
        return found[key]

    def _chats_from_updates(self) -> Dict[str, int]:
        """Bota yazmış kişiler: {küçük harf kullanıcı adı: chat_id}."""
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
        """Bulunan chat_id'yi önbelleğe yazar (getUpdates'i başkası tüketiyorsa çözümleme buradan yapılır)."""
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
            log.warning("Telegram önbelleği okunamadı: %s", self._cache_path)
            return {}

    def _save_cache(self, cache: Dict[str, int]) -> None:
        if not self._cache_path:
            return
        try:
            with open(self._cache_path, "w", encoding="utf-8") as f:
                json.dump(cache, f, indent=2, ensure_ascii=False)
        except OSError as e:
            log.warning("Telegram önbelleği yazılamadı: %s (%s)", self._cache_path, e)

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
            raise NotificationError(f"Telegram'a bağlanılamadı ({method}): {e}") from None
        if not payload.get("ok"):
            desc = payload.get("description", "bilinmeyen hata")
            if payload.get("error_code") in (401, 404):
                desc += " — bot token'ını kontrol edin"
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
