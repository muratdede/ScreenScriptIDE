"""Anlamsal renklendirme: isimlerin ne olduğuna (sınıf, fonksiyon, parametre, modül) göre sınıflandırılması.

Sözdizimi renklendiricisi yalnızca kelimelere bakar; burada ast üzerinden kullanıcının tanımladığı
sınıflar/fonksiyonlar, importlanan modüller, fonksiyon parametreleri ve çağrılan her şey bulunur.
Qt bağımsızdır; sonuç satır -> [(başlangıç, bitiş, isim, tür)] sözlüğüdür (sütunlar Python str indeksi).
"""

from __future__ import annotations

import ast
import re
from typing import Dict, List, Mapping, Optional, Set, Tuple

from .inspections import BUILTIN_NAMES, char_col

CLASS = "class"
FUNCTION = "function"
PARAMETER = "parameter"
MODULE = "module"

Token = Tuple[int, int, str, str]          # başlangıç, bitiş, isim, tür
Tokens = Dict[int, List[Token]]


def _is_class_name(name: str) -> bool:
    """PascalCase isimler (ALL_CAPS sabitler hariç) sınıf kabul edilir."""
    return name[:1].isupper() and not name.isupper() and any(c.islower() for c in name)


def _locals(func) -> Set[str]:
    """Fonksiyonun kendi kapsamında atanan isimler (iç fonksiyonlar hariç)."""
    out: Set[str] = set()
    stack = list(ast.iter_child_nodes(func))
    while stack:
        n = stack.pop()
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            out.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
            continue
        elif isinstance(n, ast.Lambda):
            continue
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            out.update((a.asname or a.name).split(".")[0] for a in n.names)
        stack.extend(ast.iter_child_nodes(n))
    return out


def _params(args: ast.arguments) -> List[ast.arg]:
    return args.posonlyargs + args.args + args.kwonlyargs + [a for a in (args.vararg, args.kwarg) if a]


class _Scope:
    def __init__(self, params: Set[str], local_names: Set[str]):
        self.params = params
        self.locals = local_names


class _Collector(ast.NodeVisitor):
    def __init__(self, lines: List[str], api: Mapping[str, object]):
        self.lines = lines
        self.api = api
        self.tokens: Tokens = {}
        self.kinds: Dict[str, str] = {}   # dosya genelinde tanımlı isimler
        self.scopes: List[_Scope] = []
        self.called: Set[int] = set()     # çağrılan Name/Attribute düğümlerinin id'leri

    # ---- kayıt ----------------------------------------------------------- #
    def emit(self, line: int, col: int, name: str, kind: str):
        if 0 < line <= len(self.lines) and self.lines[line - 1][col:col + len(name)] == name:
            self.tokens.setdefault(line, []).append((col, col + len(name), name, kind))

    def emit_node(self, node: ast.AST, name: str, kind: str):
        lt = self.lines[node.lineno - 1] if 0 < node.lineno <= len(self.lines) else ""
        self.emit(node.lineno, char_col(lt, node.col_offset), name, kind)

    def emit_attr(self, node: ast.Attribute, kind: str):
        lt = self.lines[node.end_lineno - 1] if 0 < node.end_lineno <= len(self.lines) else ""
        end = char_col(lt, node.end_col_offset)
        self.emit(node.end_lineno, end - len(node.attr), node.attr, kind)

    # ---- tanımlar -------------------------------------------------------- #
    def collect(self, tree: ast.Module):
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                self.kinds[node.name] = CLASS
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.kinds.setdefault(node.name, FUNCTION)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    self.kinds.setdefault((a.asname or a.name).split(".")[0], MODULE)
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    name = a.asname or a.name
                    if name != "*" and _is_class_name(name):
                        self.kinds.setdefault(name, CLASS)

    def kind_of(self, name: str) -> Optional[str]:
        for scope in reversed(self.scopes):
            if name in scope.params:
                return PARAMETER if name != "self" else None   # self'in kendi rengi var
            if name in scope.locals:
                return None                                    # yerel değişken
        if name in self.api or (name in BUILTIN_NAMES and name not in self.kinds):
            return None   # API ve yerleşikler sözdizimi renklendiricisinde
        return self.kinds.get(name)

    # ---- gezinme --------------------------------------------------------- #
    def visit_ClassDef(self, node: ast.ClassDef):
        for d in node.decorator_list:
            self.visit(d)
        for b in node.bases + [k.value for k in node.keywords]:
            self.visit(b)
            if isinstance(b, ast.Name) and self.kind_of(b.id) is None and _is_class_name(b.id) \
                    and b.id not in self.api and b.id not in BUILTIN_NAMES:
                self.emit_node(b, b.id, CLASS)
            elif isinstance(b, ast.Attribute) and _is_class_name(b.attr):
                self.emit_attr(b, CLASS)
        outer = self.scopes
        self.scopes = []   # sınıf gövdesi dış fonksiyonun yerellerini görmez gibi davran (yeterli yaklaşım)
        for s in node.body:
            self.visit(s)
        self.scopes = outer

    def visit_FunctionDef(self, node):
        for d in node.decorator_list:
            self.visit(d)
        params = _params(node.args)
        for a in params:
            if a.arg != "self":
                self.emit_node(a, a.arg, PARAMETER)
            if a.annotation:
                self.visit(a.annotation)
        for d in node.args.defaults + [d for d in node.args.kw_defaults if d is not None]:
            self.visit(d)
        if node.returns:
            self.visit(node.returns)
        self.scopes.append(_Scope({a.arg for a in params}, _locals(node) - {a.arg for a in params}))
        for s in node.body:
            self.visit(s)
        self.scopes.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda):
        params = _params(node.args)
        for a in params:
            self.emit_node(a, a.arg, PARAMETER)
        for d in node.args.defaults + [d for d in node.args.kw_defaults if d is not None]:
            self.visit(d)
        self.scopes.append(_Scope({a.arg for a in params}, set()))
        self.visit(node.body)
        self.scopes.pop()

    def visit_Import(self, node: ast.Import):
        for a in node.names:
            self._emit_import(node, a.asname or a.name.split(".")[0], MODULE)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        for a in node.names:
            if a.name != "*":
                name = a.asname or a.name
                self._emit_import(node, name, self.kinds.get(name) or (CLASS if _is_class_name(name) else None))

    def _emit_import(self, node: ast.stmt, name: str, kind: Optional[str]):
        if not kind:
            return
        for line in range(node.lineno, (node.end_lineno or node.lineno) + 1):
            lt = self.lines[line - 1] if 0 < line <= len(self.lines) else ""
            m = re.search(r"(?<![\w.])" + re.escape(name) + r"(?!\w)", lt, max(0, lt.find("import")) if line == node.lineno else 0)
            if m:
                self.emit(line, m.start(), name, kind)
                return

    def visit_Call(self, node: ast.Call):
        self.called.add(id(node.func))
        for kw in node.keywords:   # isimli argüman: f(zaman_asimi=3)
            if kw.arg:
                lt = self.lines[kw.lineno - 1] if 0 < kw.lineno <= len(self.lines) else ""
                self.emit(kw.lineno, char_col(lt, kw.col_offset), kw.arg, PARAMETER)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name):
        kind = self.kind_of(node.id)
        if kind is None and id(node) in self.called and not any(node.id in s.locals for s in self.scopes) \
                and node.id not in self.api and node.id not in BUILTIN_NAMES:
            kind = CLASS if _is_class_name(node.id) else FUNCTION   # başka yerden gelen çağrılabilir
        if kind:
            self.emit_node(node, node.id, kind)

    def visit_Attribute(self, node: ast.Attribute):
        self.visit(node.value)
        if id(node) in self.called:
            self.emit_attr(node, CLASS if _is_class_name(node.attr) else FUNCTION)


def semantic_tokens(tree: ast.Module, lines: List[str], api: Mapping[str, object] = None) -> Tokens:
    c = _Collector(lines, api or {})
    c.collect(tree)
    c.visit(tree)
    for items in c.tokens.values():
        items.sort()
    return c.tokens
