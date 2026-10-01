"""Localization (Qt-free, stdlib only) — used by the editor, the script process and the plugins.

Source strings are written in English directly in the code and are also the catalog keys:

    from ..i18n import tr
    label.setText(tr("Settings"))
    tr("Found {count} matches", count=n)           # str.format placeholders
    log.warning(tr("Could not read %s"), path)     # %-style logging args keep working
    ntr("{n} error", "{n} errors", count)          # count dependent text

Catalogs live next to this module as `locales/<code>.json` ({"English source": "Translation"}).
English needs no catalog; a missing key falls back to the English source.

Strings defined at import time (class constants, module-level tables) must be marked with N_()
and translated where they are displayed, because the language is chosen after modules are imported:

    KINDS = {"unused": N_("Unused variable")}
    ...
    item.setText(tr(KINDS[kind]))

The language is resolved once: set_language() (editor startup, from AppConfig.language) wins;
otherwise the SCREENSCRIPT_LANG environment variable (the editor passes it to the script process),
otherwise the operating system language.

Check catalogs for missing / unused keys with:  python -m editor.i18n.check
"""

from __future__ import annotations

import json
import locale
import logging
import os
import sys
import threading
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

ENV_VAR = "SCREENSCRIPT_LANG"
DEFAULT_LANGUAGE = "en"
# (code, native name) — the native name is shown untranslated in the language picker
LANGUAGES: List[Tuple[str, str]] = [("en", "English"), ("tr", "Türkçe")]

LOCALES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locales")

_lock = threading.Lock()
_language: Optional[str] = None
_catalog: Dict[str, str] = {}


def N_(text: str) -> str:
    """Marks a string for translation without translating it (for import-time constants)."""
    return text


def supported(code: str) -> bool:
    return any(code == c for c, _ in LANGUAGES)


def system_language() -> str:
    """Operating system UI language if supported, else DEFAULT_LANGUAGE."""
    candidates = []
    if sys.platform == "win32":
        try:
            import ctypes
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            candidates.append(locale.windows_locale.get(lang_id, ""))
        except (AttributeError, OSError):
            pass
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        candidates.append(os.environ.get(var, ""))
    try:
        candidates.append(locale.getlocale()[0] or "")
    except ValueError:
        pass
    for name in candidates:
        code = name.split("_")[0].split("-")[0].split(".")[0].lower()
        if code == "turkish":
            code = "tr"
        if supported(code):
            return code
    return DEFAULT_LANGUAGE


def resolve(code: Optional[str]) -> str:
    """"" / None / unsupported -> environment variable, then system language."""
    code = (code or "").strip().lower()
    if supported(code):
        return code
    env = os.environ.get(ENV_VAR, "").strip().lower()
    if supported(env):
        return env
    return system_language()


def set_language(code: Optional[str]) -> str:
    """Activates a language ("" = automatic) and exports it to ENV_VAR so child processes
    (the script runner) use the same language. Returns the active language code."""
    global _language, _catalog
    active = resolve(code)
    catalog = _load_catalog(active)
    with _lock:
        _language, _catalog = active, catalog
    os.environ[ENV_VAR] = active
    return active


def language() -> str:
    """Active language code (resolves automatically on first use)."""
    if _language is None:
        set_language(None)
    return _language  # type: ignore[return-value]


def tr(text: str, **kwargs) -> str:
    """Translates an English source string; kwargs are applied with str.format."""
    if _language is None:
        set_language(None)
    result = _catalog.get(text, text)
    if kwargs:
        try:
            return result.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text.format(**kwargs)   # broken translation: fall back to the source
    return result


def ntr(singular: str, plural: str, n: int, **kwargs) -> str:
    """Count dependent text: tr(singular) when n == 1, else tr(plural); `n` is passed to format too.
    Both forms are separate catalog keys (Turkish usually translates them the same way)."""
    return tr(singular if n == 1 else plural, n=n, **kwargs)


def catalog_path(code: str) -> str:
    return os.path.join(LOCALES_DIR, f"{code}.json")


def _load_catalog(code: str) -> Dict[str, str]:
    if code == DEFAULT_LANGUAGE:
        return {}
    try:
        with open(catalog_path(code), encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): str(v) for k, v in data.items() if v}
    except (OSError, ValueError) as e:
        log.warning("Translation catalog could not be loaded: %s (%s)", catalog_path(code), e)
        return {}
