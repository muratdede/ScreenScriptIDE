"""Script API hataları."""


class ScriptError(RuntimeError):
    """Script API'sindeki tüm hataların tabanı."""


class FindFailed(ScriptError):
    """Aranan görsel süre içinde bulunamadı (SikuliX'teki FindFailed gibi)."""

    def __init__(self, target: str, timeout: float, best_score: float):
        self.target = target
        self.timeout = timeout
        self.best_score = best_score
        super().__init__(f"Bulunamadı: {target} ({timeout:.1f} sn, en iyi skor {best_score:.3f})")


class ImageNotFound(ScriptError):
    """Pattern'in PNG dosyası diskte bulunamadı."""


class NotificationError(ScriptError):
    """push_notification gönderilemedi (ayar eksik, kullanıcı bulunamadı, ağ hatası...)."""
