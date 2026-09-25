"""Kod tamamlama, imza (signature) yardımı ve açıklama sağlayıcıları.

Yeni bir sağlayıcı eklemek için CompletionProvider'dan türetin ve EditorContext'e verin.
"""

from __future__ import annotations

import builtins
import keyword
import logging
import os
import re
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, List, Optional

import numpy as np

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompletionItem:
    name: str          # tam isim (ör. "exists")
    kind: str          # function, class, keyword, instance, module, param, ...
    detail: str = ""   # kısa açıklama / imza


@dataclass(frozen=True)
class SignatureInfo:
    label: str                 # "exists(target, timeout=None)"
    params: List[str]
    active: int                # imlecin olduğu parametre (-1 = yok)
    doc: str = ""


@dataclass(frozen=True)
class Definition:
    path: str
    line: int          # 1'den
    column: int        # 0'dan
    name: str = ""


@dataclass
class CodePosition:
    """line 1'den, column 0'dan başlar (jedi kuralı)."""
    source: str
    line: int
    column: int
    path: Optional[str] = None


class CompletionProvider(ABC):
    @abstractmethod
    def complete(self, pos: CodePosition) -> List[CompletionItem]: ...

    def signature(self, pos: CodePosition) -> Optional[SignatureInfo]:
        return None

    def help(self, pos: CodePosition) -> Optional[str]:
        return None

    def definition(self, pos: CodePosition) -> Optional[Definition]:
        """Ctrl+tık / Ctrl+B: imleçteki ismin tanımlandığı yer."""
        return None


# --------------------------------------------------------------------------- #
# Script API isim alanı (canlı cihaz olmadan)
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def script_namespace() -> Dict[str, object]:
    """Script'in çalışırken göreceği globallerin aynısı. Gerçek ScriptContext.namespace()
    kullanılır; cihaz yerine boş bir görüntü sağlayıcı verilir. Böylece API'ye eklenen her
    yeni isim editörde de otomatik görünür."""
    from screenscript import (Engine, ImageLibrary, ImagePath, ImageProvider, InputDriver,
                              ScriptContext, Settings, TemplateMatcher)

    class _BlankProvider(ImageProvider):
        def snapshot(self, fresh=True):
            return np.zeros((720, 1280, 3), np.uint8)

    class _NoInput(InputDriver):   # isim alanı hiç çalıştırılmaz; girdiler yok sayılır
        def tap(self, loc): pass
        def swipe(self, start, end, duration_ms=300): pass
        def type_text(self, text): pass
        def key(self, keycode): pass

    engine = Engine(_BlankProvider(), TemplateMatcher(), _NoInput(),
                    ImageLibrary(ImagePath()), Settings())
    ctx = ScriptContext(engine)
    ctx.open()
    return ctx.namespace({})


def api_names() -> List[str]:
    return sorted(script_namespace().keys())


# --------------------------------------------------------------------------- #
# Jedi tabanlı sağlayıcı
# --------------------------------------------------------------------------- #
class JediCompletionProvider(CompletionProvider):
    def __init__(self, project_root: Optional[str] = None, extra_sys_path: Optional[List[str]] = None):
        import jedi  # ImportError -> çağıran fallback kullanır
        self._jedi = jedi
        self._lock = threading.RLock()   # jedi thread-safe değil (arka plan ısıtma + UI)
        self._project = None
        self.set_project(project_root, extra_sys_path)

    def set_project(self, root: Optional[str], extra_sys_path: Optional[List[str]] = None) -> None:
        self._project = self._jedi.Project(root, added_sys_path=extra_sys_path or []) if root else None

    def _interpreter(self, pos: CodePosition):
        kwargs = {"path": pos.path} if pos.path else {}
        if self._project is not None:
            kwargs["project"] = self._project
        return self._jedi.Interpreter(pos.source, [script_namespace()], **kwargs)

    def complete(self, pos):
        with self._lock:
            return self._complete(pos)

    def _complete(self, pos):
        items = []
        for c in self._interpreter(pos).complete(pos.line, pos.column):
            items.append(CompletionItem(c.name, c.type, c.description))
        return items

    def signature(self, pos):
        with self._lock:
            return self._signature(pos)

    def _signature(self, pos):
        sigs = self._interpreter(pos).get_signatures(pos.line, pos.column)
        if not sigs:
            return None
        s = sigs[0]
        params = [p.description.replace("param ", "").replace("'", "") for p in s.params]
        doc = ""
        try:
            doc = s.docstring(raw=True).strip().split("\n\n")[0]
        except Exception:  # noqa: BLE001
            pass
        return SignatureInfo(f"{s.name}({', '.join(params)})", params,
                             s.index if s.index is not None else -1, doc)

    def help(self, pos):
        with self._lock:
            return self._help(pos)

    def definition(self, pos):
        with self._lock:
            return self._definition(pos)

    def _definition(self, pos):
        script = self._interpreter(pos)
        # goto: kaynağa en yakın tanım; script globalleri (click, SCREEN...) için dosya yolu vermez,
        # o zaman infer ile nesnenin tanımlandığı modüle gidilir
        for names in (script.goto(pos.line, pos.column, follow_imports=True),
                      script.infer(pos.line, pos.column)):
            for n in names:
                path = str(n.module_path) if n.module_path else None
                same_file = path and pos.path and os.path.normcase(path) == os.path.normcase(pos.path)
                if path and n.line and (same_file or os.path.isfile(path)):
                    return Definition(path, n.line, n.column or 0, n.name)
        return None

    def _help(self, pos):
        names = self._interpreter(pos).help(pos.line, pos.column)
        for n in names:
            doc = n.docstring().strip()
            if doc:
                return doc
        return None


# --------------------------------------------------------------------------- #
# Yedek sağlayıcı (jedi yoksa ya da hata verirse)
# --------------------------------------------------------------------------- #
class KeywordCompletionProvider(CompletionProvider):
    """Python anahtar kelimeleri + builtin'ler + API isimleri + dosyadaki kelimeler."""

    _WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

    def complete(self, pos):
        line_text = pos.source.splitlines()[pos.line - 1] if pos.source else ""
        m = re.search(r"[A-Za-z_][A-Za-z0-9_]*$", line_text[:pos.column])
        prefix = m.group(0) if m else ""
        items: Dict[str, CompletionItem] = {}
        for k in keyword.kwlist:
            items.setdefault(k, CompletionItem(k, "keyword"))
        for name, obj in script_namespace().items():
            items.setdefault(name, CompletionItem(name, "api", type(obj).__name__))
        for b in dir(builtins):
            if not b.startswith("_"):
                items.setdefault(b, CompletionItem(b, "builtin"))
        for w in set(self._WORD.findall(pos.source)):
            items.setdefault(w, CompletionItem(w, "word"))
        return [v for k, v in sorted(items.items()) if k.startswith(prefix) and k != prefix]


class FallbackCompletionProvider(CompletionProvider):
    """Önce birincil sağlayıcıyı dener; hata olursa yedeğe düşer (Chain of Responsibility)."""

    def __init__(self, primary: Optional[CompletionProvider], fallback: CompletionProvider):
        self._primary = primary
        self._fallback = fallback

    @property
    def primary(self) -> Optional[CompletionProvider]:
        return self._primary

    def _try(self, method: str, pos):
        if self._primary is not None:
            try:
                return getattr(self._primary, method)(pos)
            except Exception as e:  # noqa: BLE001
                log.debug("Birincil tamamlama hatası: %s", e)
        return getattr(self._fallback, method)(pos)

    def complete(self, pos):
        return self._try("complete", pos)

    def signature(self, pos):
        return self._try("signature", pos)

    def help(self, pos):
        return self._try("help", pos)

    def definition(self, pos):
        return self._try("definition", pos)


def create_default_provider(project_root: Optional[str] = None,
                            extra_sys_path: Optional[List[str]] = None) -> FallbackCompletionProvider:
    try:
        primary: Optional[CompletionProvider] = JediCompletionProvider(project_root, extra_sys_path)
    except ImportError:
        log.warning("jedi kurulu değil; basit tamamlama kullanılacak (pip install jedi)")
        primary = None
    return FallbackCompletionProvider(primary, KeywordCompletionProvider())
