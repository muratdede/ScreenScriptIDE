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


class TextNotFound(FindFailed):
    """Aranan yazı süre içinde okunamadı. `seen`: son okumada bölgede görülen yazılar."""

    def __init__(self, query: str, timeout: float, seen: list):
        self.target = query
        self.timeout = timeout
        self.best_score = 0.0
        self.seen = list(seen)
        ScriptError.__init__(self, f"Yazı bulunamadı: {query} ({timeout:.1f} sn). Okunan: {self.seen}")


class ImageNotFound(ScriptError):
    """Pattern'in PNG dosyası diskte bulunamadı."""


class NotificationError(ScriptError):
    """push_notification gönderilemedi (ayar eksik, kullanıcı bulunamadı, ağ hatası...)."""
