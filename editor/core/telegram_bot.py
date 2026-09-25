"""Telegram bot komutları: editör açıkken bota yazılan komutları arka plan thread'inde yanıtlar.

    /capture  — cihazın o anki ekran görüntüsünü fotoğraf olarak gönderir
    /help     — komut listesi

Yalnızca ayarlardaki kullanıcı (telegram_username; @ad ya da sayısal chat_id) yanıt alır; diğer
kişilerin mesajları yok sayılır. Servis getUpdates'i long polling ile tükettiği için script sürecindeki
TelegramNotifier kullanıcıyı getUpdates'te bulamayabilir; bu yüzden görülen chat_id önbelleğe yazılır.
Script de aynı anda getUpdates çağırırsa Telegram 409 (Conflict) döndürür; servis kısa bir beklemeyle
yeniden dener.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional, Tuple

import cv2
from PySide6.QtCore import QObject, Signal

from screenscript import TelegramNotifier

from .app_settings import AppConfig, telegram_cache_path
from .grabber import ScreenGrabber

log = logging.getLogger(__name__)


class TelegramCommandService(QObject):
    message = Signal(str)   # durum çubuğu için (thread'den yayılır; kuyruklu bağlantı)

    POLL_TIMEOUT = 25        # long polling süresi (sn)
    STALE_SECONDS = 60       # editör kapalıyken gelmiş eski komutlar yanıtlanmaz
    MAX_BACKOFF = 30
    HELP = ("ScreenScript IDE komutları:\n"
            "/capture — cihaz ekranının görüntüsünü gönderir\n"
            "/help — bu mesaj")

    def __init__(self, grabber: ScreenGrabber, parent=None):
        super().__init__(parent)
        self._grabber = grabber
        self._config: Optional[AppConfig] = None
        self._key: Optional[Tuple] = None
        self._stop: Optional[threading.Event] = None

    @property
    def running(self) -> bool:
        return self._stop is not None

    def apply_config(self, config: AppConfig) -> None:
        """Ayarlara göre başlatır / durdurur. Token ya da kullanıcı değiştiyse yeniden başlatır;
        yalnızca cihaz bağlantısı değiştiyse çalışan thread yeni ayarları bir sonraki komutta kullanır."""
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
            self._stop.set()   # süren long poll en geç POLL_TIMEOUT sonunda biter; thread daemon
        self._stop = None
        self._key = None

    # ---- thread ---------------------------------------------------------- #
    def _run(self, token: str, user: str, stop: threading.Event) -> None:
        bot = TelegramNotifier(token, user, cache_path=telegram_cache_path(),
                               timeout=self.POLL_TIMEOUT + 10)
        offset = 0
        backoff = 1
        log.info("Telegram komut servisi başladı (@%s)", user)
        while not stop.is_set():
            try:
                updates = bot.get_updates(offset, timeout=self.POLL_TIMEOUT)
                backoff = 1
            except Exception as e:  # noqa: BLE001 — ağ hatası, 409 Conflict, geçersiz token ...
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
                    log.warning("Telegram komutu işlenemedi: %s", e)
        log.info("Telegram komut servisi durdu")

    def _handle(self, bot: TelegramNotifier, token: str, user: str, msg: dict) -> None:
        text = (msg.get("text") or "").strip()
        chat = msg.get("chat") or {}
        sender = msg.get("from") or {}
        if not text.startswith("/") or chat.get("type") != "private":
            return
        if not self._authorized(user, chat, sender):
            log.info("Telegram: yetkisiz kullanıcıdan komut yok sayıldı (@%s, %s)",
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
            reply.send_text(self.HELP)

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
            reply.send_text(f"Ekran görüntüsü alınamadı: {e}")
            self.message.emit("Telegram /capture: ekran görüntüsü alınamadı")
            return
        ok, buf = cv2.imencode(".png", image)
        if not ok:
            reply.send_text("Ekran görüntüsü PNG'ye çevrilemedi")
            return
        h, w = image.shape[:2]
        reply.send_photo(buf.tobytes(), "screen.png",
                         caption=f"{config.host}:{config.port} — {w}x{h} — {time.strftime('%H:%M:%S')}")
        self.message.emit("Telegram /capture: ekran görüntüsü gönderildi")
