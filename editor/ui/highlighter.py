"""Python sözdizimi renklendirici (script API isimleri ayrı renkte).

Kelime kurallarının üstüne, kod denetiminin ürettiği anlamsal belirteçler (kullanıcının sınıfları,
fonksiyonları, parametreleri, modülleri) set_semantic() ile uygulanır."""

from __future__ import annotations

import builtins
import keyword
import re
from typing import Callable, Dict, Iterable, List, Tuple

from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat

from ..core.semantic import CLASS, FUNCTION, MODULE, PARAMETER
from .theme import THEME


def _fmt(color: QColor, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(color)
    if bold:
        f.setFontWeight(QFont.Bold)
    f.setFontItalic(italic)
    return f


def utf16_mapper(text: str) -> Callable[[int], int]:
    """Python str indeksini QTextDocument (UTF-16) konumuna çevirir. Emoji gibi BMP dışı karakter
    yoksa ikisi aynıdır."""
    if all(ord(ch) < 0x10000 for ch in text):
        return lambda i: i
    prefix = [0]
    for ch in text:
        prefix.append(prefix[-1] + (2 if ord(ch) >= 0x10000 else 1))
    return lambda i: prefix[min(max(i, 0), len(text))]


class PythonHighlighter(QSyntaxHighlighter):
    STATE_NONE, STATE_SINGLE, STATE_DOUBLE = 0, 1, 2
    _TRIPLE = {STATE_SINGLE: "'''", STATE_DOUBLE: '"""'}

    def __init__(self, document, api_names: Iterable[str] = ()):
        super().__init__(document)
        self._rules: List[Tuple[re.Pattern, QTextCharFormat, int]] = []
        words = lambda ws: r"\b(?:" + "|".join(map(re.escape, ws)) + r")\b"
        builtin_names = [b for b in dir(builtins) if not b.startswith("_")]
        api = [n for n in api_names if n.isidentifier()]

        self._add(words(builtin_names), _fmt(THEME.builtin))
        if api:
            self._add(words(api), _fmt(THEME.api, bold=True))
        self._add(words(keyword.kwlist), _fmt(THEME.keyword, bold=True))
        self._add(r"\bself\b", _fmt(THEME.self_))
        self._add(r"\b(?:def|class)\s+([A-Za-z_]\w*)", _fmt(THEME.definition), group=1)
        self._add(r"@[\w.]+", _fmt(THEME.decorator))
        self._add(r"\b(?:0[xX][0-9a-fA-F_]+|\d[\d_]*\.?\d*(?:[eE][+-]?\d+)?)\b", _fmt(THEME.number))
        self._string_fmt = _fmt(THEME.string)
        self._comment_fmt = _fmt(THEME.comment, italic=True)
        # string/yorum/üçlü tırnak tek regex'le sırayla taranır; böylece '#' string içinde
        # yorum sayılmaz, yorumdaki tırnak da string başlatmaz
        self._token = re.compile(
            r"""(?P<tri>[rbfuRBFU]{0,2}(?:\'\'\'|\"\"\"))"""
            r"""|(?P<str>[rbfuRBFU]{0,2}(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'))"""
            r"""|(?P<com>\#.*)""")
        self._semantic: Dict[int, List[Tuple[int, int, str, str]]] = {}   # satır (1'den) -> belirteçler
        self._semantic_fmt = {CLASS: _fmt(THEME.class_ref), FUNCTION: _fmt(THEME.function_call),
                              PARAMETER: _fmt(THEME.parameter), MODULE: _fmt(THEME.module)}

    def set_semantic(self, tokens: Dict[int, List[Tuple[int, int, str, str]]]) -> None:
        """Yeni anlamsal belirteçler: yalnızca değişen satırlar yeniden boyanır."""
        old, self._semantic = self._semantic, tokens
        doc = self.document()
        for line in set(old) | set(tokens):
            if old.get(line) != tokens.get(line):
                block = doc.findBlockByNumber(line - 1)
                if block.isValid():
                    self.rehighlightBlock(block)

    def _add(self, pattern: str, fmt: QTextCharFormat, group: int = 0) -> None:
        self._rules.append((re.compile(pattern), fmt, group))

    def highlightBlock(self, text: str) -> None:
        for regex, fmt, group in self._rules:
            for m in regex.finditer(text):
                self.setFormat(m.start(group), m.end(group) - m.start(group), fmt)
        tokens = self._semantic.get(self.currentBlock().blockNumber() + 1)
        if tokens:
            to16 = utf16_mapper(text)
            for start, end, name, kind in tokens:
                if text[start:end] == name:   # satırlar kaymışsa (yazarken) bir sonraki denetime kadar atla
                    self.setFormat(to16(start), to16(end) - to16(start), self._semantic_fmt[kind])

        pos = self._continue_multiline(text)
        if pos < 0:
            return  # tüm satır çok satırlı string'in içinde
        while pos < len(text):
            m = self._token.search(text, pos)
            if m is None:
                break
            if m.group("tri"):
                delim = m.group("tri")[-3:]
                end = text.find(delim, m.end())
                if end == -1:
                    self.setFormat(m.start(), len(text) - m.start(), self._string_fmt)
                    self.setCurrentBlockState(self.STATE_SINGLE if delim == "\'\'\'" else self.STATE_DOUBLE)
                    return
                self.setFormat(m.start(), end + 3 - m.start(), self._string_fmt)
                pos = end + 3
                continue
            self.setFormat(m.start(), m.end() - m.start(),
                           self._string_fmt if m.group("str") else self._comment_fmt)
            pos = m.end()
        self.setCurrentBlockState(self.STATE_NONE)

    def _continue_multiline(self, text: str) -> int:
        prev = self.previousBlockState()
        if prev not in self._TRIPLE:
            return 0
        end = text.find(self._TRIPLE[prev])
        if end == -1:
            self.setFormat(0, len(text), self._string_fmt)
            self.setCurrentBlockState(prev)
            return -1
        self.setFormat(0, end + 3, self._string_fmt)
        return end + 3
