"""Catalog consistency check: every literal passed to tr() / N_() / ntr() should have a translation.

    python -m editor.i18n.check            # report missing and unused keys for every catalog
    python -m editor.i18n.check --strict   # exit code 1 if anything is missing (for CI)
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import string
import sys
from typing import Dict, List, Set, Tuple

from . import LANGUAGES, DEFAULT_LANGUAGE, catalog_path

EDITOR_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKERS = {"tr", "N_", "ntr"}


def collect_keys(root: str = EDITOR_ROOT) -> Dict[str, List[Tuple[str, int]]]:
    """{source string: [(file, line), ...]} for all tr("...") / N_("...") / ntr("...", "...") literals under root."""
    keys: Dict[str, List[Tuple[str, int]]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8") as f:
                try:
                    tree = ast.parse(f.read(), path)
                except SyntaxError as e:
                    print(f"syntax error: {path}: {e}", file=sys.stderr)
                    continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                func = node.func
                fname = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
                args = node.args[:2] if fname == "ntr" else node.args[:1]
                if fname not in MARKERS:
                    continue
                for arg in args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        keys.setdefault(arg.value, []).append((os.path.relpath(path, root), node.lineno))
    return keys


def _fields(text: str) -> Set[str]:
    try:
        return {f for _, f, _, _ in string.Formatter().parse(text) if f}
    except ValueError:
        return {"<invalid>"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="editor.i18n.check")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    keys = collect_keys()
    failed = False
    for code, name in LANGUAGES:
        if code == DEFAULT_LANGUAGE:
            continue
        with open(catalog_path(code), encoding="utf-8") as f:
            catalog: Dict[str, str] = json.load(f)
        missing = sorted(k for k in keys if not catalog.get(k))
        unused = sorted(k for k in catalog if k not in keys)
        mismatched = sorted(k for k in keys if catalog.get(k) and _fields(k) != _fields(catalog[k])
                            or catalog.get(k) and k.count("%") != catalog[k].count("%"))
        print(f"[{code}] {name}: {len(keys)} keys, {len(missing)} missing, {len(unused)} unused, "
              f"{len(mismatched)} placeholder mismatches")
        for k in missing:
            file, line = keys[k][0]
            print(f"  missing   {file}:{line}  {k!r}")
        for k in mismatched:
            print(f"  mismatch  {k!r} -> {catalog[k]!r}")
        for k in unused:
            print(f"  unused    {k!r}")
        failed |= bool(missing or mismatched)
    return 1 if failed and args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
