"""pyflakes'in kapsamadığı IDE denetimleri (PyCharm'daki inceleme listesi örnek alınmıştır).

Qt bağımsızdır; derlenmiş ast ağacı üzerinde çalışır ve (satır, sütun, bitiş, önem, mesaj, kod)
demetleri üretir. Sütunlar Python str indeksidir.

Denetimler:
  - Çözümlenemeyen nitelik: self.x / modul.x / API_NESNESI.x (attributes.py)
  - Çağrı argümanları: projede tanımlı fonksiyon/sınıf/metotlar ve script API'si için eksik,
    fazla ya da beklenmeyen argümanlar (çalışınca TypeError verecek çağrılar)
  - Bulunamayan modül importları
  - Erişilemeyen kod (return/raise/break/continue sonrası)
  - Metodun ilk parametresi (self / cls)
  - Yerleşik ya da script API isimlerini gölgeleme
  - Değiştirilebilir varsayılan argüman, None/True/False ile == karşılaştırması
  - Çıplak `except:`, etkisiz ifade, kendine atama, modül düzeyinde `global`,
    `__init__` içinde değer döndürme
  - Derleyici uyarıları (geçersiz kaçış dizisi, …)
"""

from __future__ import annotations

import ast
import builtins as _builtins
import importlib.util
import inspect
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Set, Tuple

ERROR = "error"
WARNING = "warning"

Issue = tuple   # (satır, sütun, bitiş sütunu, önem, mesaj, kod[, önerilen isim])

BUILTIN_NAMES = frozenset(n for n in dir(_builtins) if not n.startswith("_"))
# gölgelenmesi sık ve zararsız olanlar (PyCharm da bunlar için sessiz kalabiliyor)
_SHADOW_OK = frozenset({"_", "copyright", "credits", "license", "exit", "quit", "help"})


def char_col(line_text: str, byte_col: int) -> int:
    """ast sütunu UTF-8 bayt ofsetidir; Python str indeksine çevir."""
    return len(line_text.encode("utf-8")[:max(0, byte_col)].decode("utf-8", errors="ignore"))


# --------------------------------------------------------------------------- #
# İmzalar
# --------------------------------------------------------------------------- #
@dataclass
class Signature:
    """Çağrı denetimi için sadeleştirilmiş imza."""
    positional: List[str] = field(default_factory=list)     # posonly + normal (sırayla)
    posonly: int = 0                                         # ilk kaç tanesi yalnızca konumsal
    required_positional: Set[str] = field(default_factory=set)
    kwonly: List[str] = field(default_factory=list)
    required_kwonly: Set[str] = field(default_factory=set)
    varargs: bool = False
    varkw: bool = False
    label: str = ""

    @classmethod
    def from_ast(cls, args: ast.arguments, label: str, drop_first: bool = False) -> "Signature":
        pos = [a.arg for a in args.posonlyargs + args.args]
        n_default = len(args.defaults)
        required = set(pos[:len(pos) - n_default]) if n_default else set(pos)
        sig = cls(pos, len(args.posonlyargs), required,
                  [a.arg for a in args.kwonlyargs],
                  {a.arg for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is None},
                  args.vararg is not None, args.kwarg is not None, label)
        return sig.without_first() if drop_first else sig

    @classmethod
    def from_callable(cls, obj, label: str) -> Optional["Signature"]:
        try:
            params = inspect.signature(obj).parameters.values()
        except (TypeError, ValueError):
            return None
        sig = cls(label=label)
        P = inspect.Parameter
        for p in params:
            if p.kind in (P.POSITIONAL_ONLY, P.POSITIONAL_OR_KEYWORD):
                sig.positional.append(p.name)
                if p.kind == P.POSITIONAL_ONLY:
                    sig.posonly += 1
                if p.default is P.empty:
                    sig.required_positional.add(p.name)
            elif p.kind == P.KEYWORD_ONLY:
                sig.kwonly.append(p.name)
                if p.default is P.empty:
                    sig.required_kwonly.add(p.name)
            elif p.kind == P.VAR_POSITIONAL:
                sig.varargs = True
            elif p.kind == P.VAR_KEYWORD:
                sig.varkw = True
        return sig

    def without_first(self) -> "Signature":
        if not self.positional:
            return self   # *args ile alan metot: dokunma
        first = self.positional[0]
        return Signature(self.positional[1:], max(0, self.posonly - 1), self.required_positional - {first},
                         self.kwonly, self.required_kwonly, self.varargs, self.varkw, self.label)


def _check_call(call: ast.Call, sig: Signature, lines: List[str]) -> List[Issue]:
    if any(isinstance(a, ast.Starred) for a in call.args) or any(k.arg is None for k in call.keywords):
        return []   # *seq / **map: argüman sayısı derleme anında bilinemez
    out: List[Issue] = []
    name = sig.label
    n = len(call.args)
    if n > len(sig.positional) and not sig.varargs:
        extra = call.args[len(sig.positional)]
        out.append(_issue(extra, lines, ERROR, f"{name}(): en fazla {len(sig.positional)} konumsal argüman "
                                               f"alır, {n} verildi", "TooManyArguments"))
    filled = set(sig.positional[:n])
    kw_names = set(sig.positional[sig.posonly:]) | set(sig.kwonly)
    for kw in call.keywords:
        if kw.arg in filled:
            out.append(_kw_issue(kw, lines, ERROR, f"{name}(): '{kw.arg}' parametresine birden fazla değer",
                                 "DuplicateArgumentValue"))
        elif kw.arg not in kw_names and not sig.varkw:
            out.append(_kw_issue(kw, lines, ERROR, f"{name}(): beklenmeyen argüman '{kw.arg}'",
                                 "UnexpectedArgument"))
        filled.add(kw.arg)
    missing = [p for p in sig.positional if p in sig.required_positional and p not in filled]
    missing += [p for p in sig.kwonly if p in sig.required_kwonly and p not in filled]
    if missing:
        names = ", ".join(f"'{p}'" for p in missing)
        out.append(_issue(call.func, lines, ERROR, f"{name}(): eksik argüman {names}", "MissingArgument",
                          name_only=True))
    return out


# --------------------------------------------------------------------------- #
# Konum yardımcıları
# --------------------------------------------------------------------------- #
def _line(lines: List[str], n: int) -> str:
    return lines[n - 1] if 0 < n <= len(lines) else ""


def _issue(node: ast.AST, lines: List[str], severity: str, message: str, code: str,
           name_only: bool = False) -> Issue:
    """Düğümün kapladığı aralık (çok satırlıysa ilk satırın sonuna kadar)."""
    if name_only and isinstance(node, ast.Attribute):   # a.b.metot(...) -> yalnızca 'metot'
        lt = _line(lines, node.end_lineno)
        end = char_col(lt, node.end_col_offset)
        return node.end_lineno, end - len(node.attr), end, severity, message, code
    lt = _line(lines, node.lineno)
    col = char_col(lt, node.col_offset)
    end = char_col(lt, node.end_col_offset) if node.end_lineno == node.lineno else len(lt.rstrip())
    return node.lineno, col, max(col, end), severity, message, code


def _kw_issue(kw: ast.keyword, lines: List[str], severity: str, message: str, code: str) -> Issue:
    lt = _line(lines, kw.lineno)
    col = char_col(lt, kw.col_offset)
    return kw.lineno, col, col + len(kw.arg or ""), severity, message, code


def _def_name_range(node, lines: List[str]) -> Tuple[int, int]:
    """`def isim` / `class isim` içindeki ismin sütun aralığı."""
    lt = _line(lines, node.lineno)
    m = re.compile(r"\b(?:def|class)\s+(" + re.escape(node.name) + r")\b").search(lt)
    if m:
        return m.start(1), m.end(1)
    col = char_col(lt, node.col_offset)
    return col, col


def _decorator_names(node) -> Set[str]:
    out = set()
    for d in node.decorator_list:
        target = d.func if isinstance(d, ast.Call) else d
        if isinstance(target, ast.Name):
            out.add(target.id)
        elif isinstance(target, ast.Attribute):
            out.add(target.attr)
    return out


# --------------------------------------------------------------------------- #
# Modül bulma (önbellekli)
# --------------------------------------------------------------------------- #
_MODULE_CACHE: Dict[str, bool] = {}


def clear_module_cache() -> None:
    _MODULE_CACHE.clear()


def _module_exists(name: str, search_dirs: Iterable[str]) -> bool:
    for d in search_dirs:   # proje içi modüller: önbelleğe alınmaz, dosya eklenince hemen bulunur
        if d and (os.path.isfile(os.path.join(d, name + ".py")) or
                  os.path.isfile(os.path.join(d, name, "__init__.py")) or
                  os.path.isdir(os.path.join(d, name))):
            return True
    if name in sys.builtin_module_names or name in sys.modules:
        return True
    cached = _MODULE_CACHE.get(name)
    if cached is None:
        try:
            cached = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError, AttributeError):
            cached = False
        _MODULE_CACHE[name] = cached
    return cached


# --------------------------------------------------------------------------- #
# Denetleyici
# --------------------------------------------------------------------------- #
def _binding_names(tree: ast.AST) -> Dict[str, int]:
    """Dosyanın herhangi bir yerinde bağlanan isimler ve kaç kez bağlandıkları."""
    counts: Dict[str, int] = {}

    def add(name: str):
        counts[name] = counts.get(name, 0) + 1

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            add(node.id)
        elif isinstance(node, ast.arg):
            add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                add((a.asname or a.name).split(".")[0])
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for n in node.names:
                add(n)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            add(node.name)
        elif isinstance(node, ast.MatchAs) and node.name:
            add(node.name)
        elif isinstance(node, ast.MatchStar) and node.name:
            add(node.name)
    return counts


class _Inspector(ast.NodeVisitor):
    def __init__(self, tree: ast.Module, lines: List[str], api: Mapping[str, object],
                 search_dirs: List[str]):
        self.lines = lines
        self.api = api
        self.search_dirs = search_dirs
        self.issues: List[Issue] = []
        self.bound = _binding_names(tree)
        self.functions: Dict[str, Signature] = {}          # tek tanımlı modül fonksiyonları
        self.classes: Dict[str, Optional[Signature]] = {}  # sınıf -> kurucu imzası (None: bilinmiyor)
        self.class_stack: List[ast.ClassDef] = []
        self.func_stack: List[ast.AST] = []
        self._collect(tree)

    def add(self, issue: Issue):
        self.issues.append(issue)

    # ---- tanımları topla ------------------------------------------------- #
    def _collect(self, tree: ast.Module):
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and self.bound.get(node.name) == 1 \
                    and not node.decorator_list and self._is_module_level(tree, node):
                self.functions[node.name] = Signature.from_ast(node.args, node.name)
            elif isinstance(node, ast.ClassDef) and self.bound.get(node.name) == 1 \
                    and self._is_module_level(tree, node):
                self.classes[node.name] = self._constructor(node)

    @staticmethod
    def _is_module_level(tree: ast.Module, node: ast.AST) -> bool:
        return node in tree.body or any(node in getattr(s, "body", []) or node in getattr(s, "orelse", [])
                                        for s in tree.body if isinstance(s, (ast.If, ast.Try)))

    @staticmethod
    def _constructor(cls: ast.ClassDef) -> Optional[Signature]:
        if cls.decorator_list or cls.keywords:   # @dataclass, metaclass=…: kurucu üretiliyor olabilir
            return None
        init = next((s for s in cls.body if isinstance(s, ast.FunctionDef) and s.name == "__init__"), None)
        if init is not None:
            return Signature.from_ast(init.args, cls.name, drop_first=True) if not init.decorator_list else None
        if any(not (isinstance(b, ast.Name) and b.id == "object") for b in cls.bases):
            return None   # kurucu üst sınıftan geliyor
        if any(isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)) and s.name == "__new__" for s in cls.body):
            return None
        return Signature(label=cls.name)

    def _method_signature(self, name: str) -> Optional[Signature]:
        """self.metot(...) için içinde bulunulan sınıftaki metodun imzası."""
        if not self.class_stack or not self.func_stack:
            return None
        func = self.func_stack[-1]
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)) or not func.args.args \
                or func.args.args[0].arg != "self":
            return None
        cls = self.class_stack[-1]
        defs = [s for s in cls.body if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)) and s.name == name]
        if len(defs) != 1:
            return None
        decorators = _decorator_names(defs[0])
        if decorators - {"staticmethod", "classmethod"}:
            return None   # property, özel dekoratörler
        return Signature.from_ast(defs[0].args, name, drop_first="staticmethod" not in decorators)

    # ---- gezinme --------------------------------------------------------- #
    def _in_class_body(self) -> bool:
        """Sınıf gövdesindeki isimler (metot, sınıf niteliği) yerleşikleri gölgelemez: obj.open gibi erişilir."""
        return bool(self.class_stack) and not self.func_stack

    def visit_ClassDef(self, node: ast.ClassDef):
        if not self._in_class_body():
            self._check_shadow(node.name, node, *_def_name_range(node, self.lines))
        self._check_body(node.body)
        self.class_stack.append(node)
        outer = self.func_stack
        self.func_stack = []
        self.generic_visit(node)
        self.func_stack = outer
        self.class_stack.pop()

    def visit_FunctionDef(self, node):
        col, end = _def_name_range(node, self.lines)
        in_class = self._in_class_body() and node in self.class_stack[-1].body
        if not self._in_class_body():
            self._check_shadow(node.name, node, col, end)
        if in_class:
            self._check_method_params(node, col, end)
        for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs + \
                [x for x in (node.args.vararg, node.args.kwarg) if x]:
            if not (in_class and a is (node.args.posonlyargs + node.args.args or [None])[0]):
                self._check_shadow(a.arg, a)
        for d in node.args.defaults + [d for d in node.args.kw_defaults if d is not None]:
            if isinstance(d, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)) or \
                    (isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id in ("list", "dict", "set")
                     and self.bound.get(d.func.id) is None):
                self.add(_issue(d, self.lines, WARNING, "Değiştirilebilir varsayılan argüman; çağrılar arasında "
                                                        "paylaşılır (None kullanıp içeride oluşturun)",
                                "MutableDefaultArgument"))
        if node.name == "__init__" and in_class:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and sub.value is not None and \
                        not (isinstance(sub.value, ast.Constant) and sub.value.value is None) and \
                        self._owner_function(node, sub):
                    self.add(_issue(sub, self.lines, ERROR, "__init__ değer döndürmemeli", "InitReturnsValue"))
        self._check_body(node.body)
        self.func_stack.append(node)
        self.generic_visit(node)
        self.func_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda):
        self.func_stack.append(node)
        self.generic_visit(node)
        self.func_stack.pop()

    @staticmethod
    def _owner_function(func, target) -> bool:
        """target, iç içe bir fonksiyona değil doğrudan func'a mı ait?"""
        stack = list(ast.iter_child_nodes(func))
        while stack:
            n = stack.pop()
            if n is target:
                return True
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                stack.extend(ast.iter_child_nodes(n))
        return False

    def _check_method_params(self, node, col: int, end: int):
        decorators = _decorator_names(node)
        if "staticmethod" in decorators:
            return
        params = node.args.posonlyargs + node.args.args
        expected = "cls" if ("classmethod" in decorators or
                             node.name in ("__new__", "__init_subclass__", "__class_getitem__")) else "self"
        if not params:
            if node.args.vararg is None:
                self.add((node.lineno, col, end, ERROR,
                          f"Metodun ilk parametresi olmalı (genellikle '{expected}')", "MethodWithoutSelf"))
            return
        first = params[0].arg
        if first != expected and not (expected == "self" and first in ("cls", "mcs", "metacls")):
            self.add(_issue(params[0], self.lines, WARNING,
                            f"Metodun ilk parametresi genellikle '{expected}' olarak adlandırılır",
                            "SelfParameterName"))

    # ---- deyimler -------------------------------------------------------- #
    def generic_visit(self, node):
        for name in ("body", "orelse", "finalbody"):
            body = getattr(node, name, None)
            if isinstance(body, list) and body and isinstance(body[0], ast.stmt) and \
                    not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self._check_body(body)   # modül, if/for/while/with/try, except ve case blokları
        super().generic_visit(node)

    def _check_body(self, body: List[ast.stmt]):
        """Aynı blokta return/raise/break/continue'dan sonra gelen deyim hiç çalışmaz."""
        for prev, stmt in zip(body, body[1:]):
            if isinstance(prev, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
                lt = _line(self.lines, stmt.lineno)
                col = char_col(lt, stmt.col_offset)
                end = len(lt.rstrip()) if stmt.end_lineno != stmt.lineno else char_col(lt, stmt.end_col_offset)
                self.add((stmt.lineno, col, max(col, end), WARNING, "Erişilemeyen kod", "UnreachableCode"))
                break

    def visit_Expr(self, node: ast.Expr):
        v = node.value
        if isinstance(v, ast.Constant):   # docstring / yorum yerine string ve ... serbest
            no_effect = not isinstance(v.value, str) and v.value is not Ellipsis
        else:   # içinde çağrı olan ifade (ör. `a.add(x) if k else a.discard(x)`) etkilidir
            no_effect = not any(isinstance(n, (ast.Call, ast.Await, ast.Yield, ast.YieldFrom, ast.NamedExpr))
                                for n in ast.walk(v))
        if no_effect:
            self.add(_issue(node, self.lines, WARNING, "İfadenin hiçbir etkisi yok", "StatementHasNoEffect"))
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign):
        if isinstance(node.value, ast.Name):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == node.value.id:
                    self.add(_issue(node, self.lines, WARNING, f"'{t.id}' kendisine atanıyor", "SelfAssignment"))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name):
        if isinstance(node.ctx, ast.Store) and not self._in_class_body():
            self._check_shadow(node.id, node)

    def visit_Global(self, node: ast.Global):
        if not self.func_stack and not self.class_stack:
            self.add(_issue(node, self.lines, WARNING, "Modül düzeyinde 'global' gereksiz", "GlobalAtModuleLevel"))

    def visit_ExceptHandler(self, node: ast.ExceptHandler):
        if node.type is None:
            lt = _line(self.lines, node.lineno)
            col = char_col(lt, node.col_offset)
            self.add((node.lineno, col, col + len("except"), WARNING,
                      "Çok geniş istisna yakalama: 'except:' KeyboardInterrupt/SystemExit'i de yutar "
                      "(except Exception: kullanın)", "BareExcept"))
        elif node.name:
            self._check_shadow(node.name, node)
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare):
        left = node.left
        for op, right in zip(node.ops, node.comparators):
            if isinstance(op, (ast.Eq, ast.NotEq)):
                for side in (left, right):
                    if isinstance(side, ast.Constant) and (side.value is None or isinstance(side.value, bool)):
                        suggestion = "is" if isinstance(op, ast.Eq) else "is not"
                        what = repr(side.value)
                        msg = (f"{what} ile karşılaştırmada '{suggestion}' kullanın" if side.value is None else
                               f"{what} ile == karşılaştırması gereksiz; koşulu doğrudan kullanın")
                        self.add(_issue(node, self.lines, WARNING, msg, "ComparisonWithSingleton"))
                        break
            left = right
        self.generic_visit(node)

    # ---- importlar -------------------------------------------------------- #
    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            self._check_module(node, alias.name, alias)
            if alias.asname:
                self._check_shadow(alias.asname, node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.level == 0 and node.module and node.module != "__future__":
            self._check_module(node, node.module, None)
        for alias in node.names:
            if alias.name != "*":
                self._check_shadow(alias.asname or alias.name, node)

    def _check_module(self, node, dotted: str, alias: Optional[ast.alias]):
        top = dotted.split(".")[0]
        if _module_exists(top, self.search_dirs):
            return
        lt = _line(self.lines, node.lineno)
        m = re.search(r"(?<![\w.])" + re.escape(dotted) + r"(?![\w])", lt)
        col, end = (m.start(), m.end()) if m else (char_col(lt, node.col_offset), len(lt.rstrip()))
        self.add((node.lineno, col, end, ERROR, f"'{top}' modülü bulunamadı", "UnresolvedImport"))

    # ---- gölgeleme -------------------------------------------------------- #
    def _check_shadow(self, name: str, node: ast.AST, col: Optional[int] = None, end: Optional[int] = None):
        if name in _SHADOW_OK:
            return
        if name in self.api:
            msg, code = f"Script API ismi '{name}' gölgeleniyor", "ShadowsApiName"
        elif name in BUILTIN_NAMES:
            msg, code = f"Yerleşik isim '{name}' gölgeleniyor", "ShadowsBuiltin"
        else:
            return
        lt = _line(self.lines, node.lineno)
        if col is None:
            m = re.compile(r"(?<![\w.])" + re.escape(name) + r"(?!\w)").search(lt, char_col(lt, node.col_offset))
            col, end = (m.start(), m.end()) if m else (char_col(lt, node.col_offset), char_col(lt, node.col_offset))
        self.add((node.lineno, col, end, WARNING, msg, code))

    # ---- çağrılar --------------------------------------------------------- #
    def visit_Call(self, node: ast.Call):
        sig = self._resolve(node.func)
        if sig is not None:
            for issue in _check_call(node, sig, self.lines):
                self.add(issue)
        self.generic_visit(node)

    def _resolve(self, func: ast.expr) -> Optional[Signature]:
        if isinstance(func, ast.Name):
            name = func.id
            if name in self.functions:
                return self.functions[name]
            if name in self.classes:
                return self.classes[name]
            if name in self.api and name not in self.bound:
                obj = self.api[name]
                if callable(obj):
                    return Signature.from_callable(obj, name)
            return None
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "self":
            return self._method_signature(func.attr)
        return None


_ESCAPE = re.compile(r"invalid escape sequence '(\\.)'")


def compiler_warnings(warnings_list, lines: List[str]) -> List[Issue]:
    """compile() sırasında yakalanan SyntaxWarning'ler (geçersiz kaçış dizisi, …)."""
    out: List[Issue] = []
    for w in warnings_list:
        if not issubclass(w.category, SyntaxWarning):
            continue
        text = str(w.message)
        lt = _line(lines, w.lineno or 1)
        m = _ESCAPE.match(text)
        if m:
            col = lt.find(m.group(1))
            col, end = (col, col + 2) if col >= 0 else (0, 0)
            out.append((w.lineno or 1, col, end, WARNING,
                        f"Geçersiz kaçış dizisi '{m.group(1)}' (ham string r\"…\" ya da '\\\\' kullanın)",
                        "InvalidEscapeSequence"))
        else:
            out.append((w.lineno or 1, 0, 0, WARNING, text, "SyntaxWarning"))
    return out


def inspect_tree(tree: ast.Module, lines: List[str], api: Mapping[str, object] = None,
                 search_dirs: Iterable[str] = ()) -> List[Issue]:
    inspector = _Inspector(tree, lines, api or {}, [d for d in search_dirs if d])
    inspector.visit(tree)
    from .attributes import check_attributes
    return inspector.issues + check_attributes(tree, lines, api or {}, inspector.search_dirs, inspector.bound)
