"""Text search/replace engine (Qt-independent). Find in file, find in files and reference updates
when renaming use it."""

from __future__ import annotations

import bisect
import fnmatch
import os
import re
from dataclasses import dataclass
from typing import Callable, Iterator, List, Optional, Sequence

SKIP_DIRS = {"__pycache__", ".git", ".idea", ".vscode", "debug", "node_modules", ".venv", "venv"}
TEXT_EXTENSIONS = (".py", ".txt", ".md", ".json", ".cfg", ".ini", ".yaml", ".yml", ".csv", ".log", ".toml")
MAX_FILE_SIZE = 2 * 1024 * 1024
_NEWLINE = re.compile("\n")


@dataclass(frozen=True)
class SearchQuery:
    text: str
    case_sensitive: bool = False
    whole_word: bool = False
    regex: bool = False

    def compile(self) -> Optional[re.Pattern]:
        """Raises re.error for an invalid regex; returns None for an empty query."""
        if not self.text:
            return None
        body = self.text if self.regex else re.escape(self.text)
        if self.whole_word:
            body = rf"(?<![A-Za-z0-9_]){body}(?![A-Za-z0-9_])"
        flags = re.MULTILINE | (0 if self.case_sensitive else re.IGNORECASE)
        return re.compile(body, flags)

    def expand(self, match: re.Match, replacement: str) -> str:
        """In regex mode expands groups like \\1, \\g<name>; otherwise returns the text as is."""
        return match.expand(replacement) if self.regex else replacement


@dataclass(frozen=True)
class LineMatch:
    path: str
    line: int          # 1-based
    column: int        # character index within the line (Python str)
    length: int
    line_text: str


def find_in_text(pattern: re.Pattern, text: str, path: str = "", limit: int = 5000) -> List[LineMatch]:
    out: List[LineMatch] = []
    line_starts = [0] + [m.end() for m in _NEWLINE.finditer(text)]
    for m in pattern.finditer(text):
        if m.end() == m.start():
            continue   # skip empty matches (e.g. ^ or a*)
        ln = bisect.bisect_right(line_starts, m.start()) - 1
        start = line_starts[ln]
        end = text.find("\n", start)
        line_text = text[start:] if end < 0 else text[start:end]
        length = min(m.end(), start + len(line_text)) - m.start()   # a multi-line match is cut at the first line
        out.append(LineMatch(path, ln + 1, m.start() - start, max(1, length), line_text))
        if len(out) >= limit:
            break
    return out


def replace_in_text(pattern: re.Pattern, query: SearchQuery, text: str, replacement: str) -> tuple:
    """(new text, number of replacements)"""
    return pattern.subn(lambda m: query.expand(m, replacement), text)


def iter_files(root: str, masks: Sequence[str] = ("*",), text_only: bool = True) -> Iterator[str]:
    masks = [m.strip() for m in masks if m.strip()] or ["*"]
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            if text_only and not name.lower().endswith(TEXT_EXTENSIONS):
                continue
            if not any(fnmatch.fnmatch(name.lower(), m.lower()) for m in masks):
                continue
            path = os.path.join(dirpath, name)
            try:
                if os.path.getsize(path) > MAX_FILE_SIZE:
                    continue
            except OSError:
                continue
            yield path


def read_text(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8", errors="replace", newline="") as f:
            return f.read().replace("\r\n", "\n")
    except OSError:
        return None


def search_files(root: str, pattern: re.Pattern, masks: Sequence[str] = ("*",),
                 text_for: Optional[Callable[[str], Optional[str]]] = None, limit: int = 2000) -> List[LineMatch]:
    """text_for(path): provides the (unsaved) text of an open editor; read from disk if it returns None."""
    results: List[LineMatch] = []
    for path in iter_files(root, masks):
        text = (text_for(path) if text_for else None)
        if text is None:
            text = read_text(path)
        if text is None:
            continue
        results.extend(find_in_text(pattern, text, path, limit - len(results)))
        if len(results) >= limit:
            break
    return results


def string_literal_pattern(values: Sequence[str]) -> re.Pattern:
    """Finds string literals of the form "value" or 'value' (group 1 quote, group 2 value)."""
    alts = "|".join(re.escape(v) for v in sorted(set(values), key=len, reverse=True))
    return re.compile(rf"""(["'])({alts})\1""")
