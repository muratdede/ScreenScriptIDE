"""Script API errors."""

from ...i18n import tr


class ScriptError(RuntimeError):
    """Base of all errors in the script API."""


class FindFailed(ScriptError):
    """The searched image was not found in time (like FindFailed in SikuliX)."""

    def __init__(self, target: str, timeout: float, best_score: float):
        self.target = target
        self.timeout = timeout
        self.best_score = best_score
        super().__init__(tr("Not found: {target} ({timeout:.1f} s, best score {score:.3f})",
                            target=target, timeout=timeout, score=best_score))


class TextNotFound(FindFailed):
    """The searched text could not be read in time. `seen`: texts seen in the region on the last read."""

    def __init__(self, query: str, timeout: float, seen: list):
        self.target = query
        self.timeout = timeout
        self.best_score = 0.0
        self.seen = list(seen)
        ScriptError.__init__(self, tr("Text not found: {query} ({timeout:.1f} s). Read: {seen}",
                                      query=query, timeout=timeout, seen=self.seen))


class ImageNotFound(ScriptError):
    """The Pattern's PNG file was not found on disk."""


class NotificationError(ScriptError):
    """push_notification could not be sent (missing setting, user not found, network error...)."""
