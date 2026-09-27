"""Unresolved attribute inspection ("Unresolved attribute reference" in PyCharm).

  - `self.x` / `cls.x`: an attribute not defined in the class body, its methods (self.x = …) or its
    base classes. Base classes are resolved from the same file, modules inside the project (source is
    parsed), the script API and loaded / standard library modules. If there is an unresolvable base class,
    __getattr__, setattr(), an unknown decorator or a metaclass, the class is not inspected (silence instead of false alarms).
  - `module.x`: a name missing in the module, like `os.pathh` after `import os`.
  - `API_OBJECT.x`: an attribute missing on a script API object.

Results are inspections.Issue tuples; the 7th element is the closest correct name (for the quick fix).
"""

from __future__ import annotations

import ast
import builtins as _builtins
import difflib
import importlib
import importlib.util
import inspect
import os
import sys
from typing import Dict, Iterable, List, Mapping, Optional, Set, Tuple

from ..i18n import tr

WARNING = "warning"

# known class decorators that add no new attributes to the body
_SAFE_DECORATORS = {"dataclass", "total_ordering", "final", "unique", "runtime_checkable", "dataclass_json"}
_OBJECT_MEMBERS = frozenset(dir(object))

_SOURCE_CACHE: Dict[str, Tuple[Tuple[int, int], Optional[ast.Module]]] = {}


def _parse_file(path: str) -> Optional[ast.Module]:
    """Parses a source file (from the cache while the stamp is unchanged)."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    stamp = (st.st_mtime_ns, st.st_size)
    cached = _SOURCE_CACHE.get(path)
    if cached and cached[0] == stamp:
        return cached[1]
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            tree = ast.parse(f.read())
    except (SyntaxError, ValueError, OSError):
        tree = None
    _SOURCE_CACHE[path] = (stamp, tree)
    return tree


def _module_level(tree: ast.Module):
    """Module level statements (including if/try blocks)."""
    stack = list(tree.body)
    while stack:
        s = stack.pop(0)
        yield s
        if isinstance(s, (ast.If, ast.Try)):
            stack.extend(s.body + s.orelse + getattr(s, "finalbody", []) +
                         [x for h in getattr(s, "handlers", []) for x in h.body])


def _self_stores(cls: ast.ClassDef) -> Set[str]:
    """Attributes assigned through any name inside the class (self.x = …, cls.y = …)."""
    out = set()
    for n in ast.walk(cls):
        if isinstance(n, ast.Attribute) and isinstance(n.ctx, (ast.Store, ast.Del)) and isinstance(n.value, ast.Name):
            out.add(n.attr)
    return out


def _load_module(name: str):
    """Only already loaded or standard library modules are imported (user code is never executed)."""
    mod = sys.modules.get(name)
    if mod is not None:
        return mod
    if name.split(".")[0] in getattr(sys, "stdlib_module_names", ()):
        try:
            return importlib.import_module(name)
        except Exception:
            return None
    return None


def _python_class_stores(cls: type) -> Optional[Set[str]]:
    """Instance attributes assigned in __init__ etc. of a class written in Python (and its MRO)."""
    out: Set[str] = set()
    for klass in cls.__mro__:
        if klass is object or klass.__module__ == "builtins":
            continue
        try:
            path = inspect.getsourcefile(klass)
        except (TypeError, OSError):
            path = None
        if not path:
            continue   # C extension: instance attributes are visible via dir()
        tree = _parse_file(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == klass.__name__:
                out |= _self_stores(node)
    return out


def _runtime_class_members(obj) -> Optional[Set[str]]:
    if not isinstance(obj, type):
        return None
    if "__getattr__" in dir(obj):
        return None
    members = set(dir(obj)) | (_python_class_stores(obj) or set())
    for klass in obj.__mro__:
        members |= set(getattr(klass, "__annotations__", {}) or {})
        slots = getattr(klass, "__slots__", ())
        members |= {slots} if isinstance(slots, str) else set(slots)
    return members


class ModuleIndex:
    """Classes and imports of a file; resolves the attribute sets of base classes."""

    def __init__(self, tree: ast.Module, api: Mapping[str, object], search_dirs: List[str],
                 bound: Optional[Mapping[str, int]] = None, depth: int = 0):
        self.api = api
        self.search_dirs = search_dirs
        self.depth = depth
        self.classes: Dict[str, ast.ClassDef] = {}
        self.imports: Dict[str, Tuple[str, Optional[str]]] = {}   # local name -> (module, name|None)
        seen: Dict[str, int] = {}
        for s in _module_level(tree):
            if isinstance(s, ast.ClassDef):
                self.classes[s.name] = s
                seen[s.name] = seen.get(s.name, 0) + 1
            elif isinstance(s, ast.Import):
                for a in s.names:
                    if a.asname:
                        self.imports[a.asname] = (a.name, None)
                    else:
                        top = a.name.split(".")[0]
                        self.imports[top] = (top, None)
            elif isinstance(s, ast.ImportFrom) and (s.module or s.level):
                module = "." * s.level + (s.module or "")   # relative imports keep their dots: ".base"
                for a in s.names:
                    if a.name != "*":
                        self.imports[a.asname or a.name] = (module, a.name)
        # classes defined twice are ambiguous
        for name, n in seen.items():
            if n > 1 or (bound is not None and bound.get(name, 0) > 1):
                self.classes.pop(name, None)
        self._members: Dict[int, Optional[Set[str]]] = {}

    # ---- class members ---------------------------------------------------- #
    def members(self, cls: ast.ClassDef) -> Optional[Set[str]]:
        key = id(cls)
        if key in self._members:
            return self._members[key]
        self._members[key] = None   # guard against cyclic inheritance
        result = self._compute(cls)
        self._members[key] = result
        return result

    def _compute(self, cls: ast.ClassDef) -> Optional[Set[str]]:
        for d in cls.decorator_list:
            target = d.func if isinstance(d, ast.Call) else d
            name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", "")
            if name not in _SAFE_DECORATORS:
                return None
        if any(k.arg == "metaclass" for k in cls.keywords):
            return None
        names = set(_OBJECT_MEMBERS)
        for s in ast.walk(cls):
            if isinstance(s, ast.Call) and isinstance(s.func, ast.Name) and s.func.id in ("setattr", "vars"):
                return None
            if isinstance(s, ast.Attribute) and s.attr == "__dict__":
                return None
        stack = list(cls.body)
        while stack:
            s = stack.pop()
            if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if s.name in ("__getattr__", "__getattribute__"):
                    return None
                names.add(s.name)
            elif isinstance(s, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = s.targets if isinstance(s, ast.Assign) else [s.target]
                for t in targets:
                    for n in ast.walk(t):
                        if isinstance(n, ast.Name):
                            names.add(n.id)
                if isinstance(s, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__slots__"
                                                     for t in s.targets):
                    names |= {c.value for c in ast.walk(s.value)
                              if isinstance(c, ast.Constant) and isinstance(c.value, str)}
            elif isinstance(s, (ast.If, ast.Try, ast.With, ast.For)):
                stack.extend(s.body + getattr(s, "orelse", []) + getattr(s, "finalbody", []))
            elif isinstance(s, (ast.Import, ast.ImportFrom)):
                names |= {(a.asname or a.name).split(".")[0] for a in s.names}
        names |= _self_stores(cls)
        for base in cls.bases:
            m = self.base_members(base)
            if m is None:
                return None
            names |= m
        return names

    def base_members(self, expr: ast.expr) -> Optional[Set[str]]:
        if isinstance(expr, ast.Name):
            name = expr.id
            if name == "object":
                return set()
            if name in self.classes:
                return self.members(self.classes[name])
            if name in self.imports:
                return self._imported_members(*self.imports[name])
            if name in self.api:
                return _runtime_class_members(self.api[name])
            if hasattr(_builtins, name):
                return _runtime_class_members(getattr(_builtins, name))
            return None
        if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name) and expr.value.id in self.imports:
            module, attr = self.imports[expr.value.id]
            full = module if attr is None else f"{module}.{attr}"
            return self._imported_members(full, expr.attr)
        return None

    def _imported_members(self, module: str, name: Optional[str]) -> Optional[Set[str]]:
        if name is None:
            return None   # cannot derive from the module itself
        local = self._local_module(module)
        if local is not None:
            if self.depth > 4:
                return None
            tree = _parse_file(local)
            if tree is None:
                return None
            index = ModuleIndex(tree, {}, [os.path.dirname(local)] + self.search_dirs, depth=self.depth + 1)
            if name in index.classes:
                return index.members(index.classes[name])
            if name in index.imports:   # re-export
                return index._imported_members(*index.imports[name])
            return None
        mod = _load_module(module)
        return _runtime_class_members(getattr(mod, name, None)) if mod is not None else None

    def _local_module(self, module: str) -> Optional[str]:
        dirs = self.search_dirs
        if module.startswith("."):   # relative: (level-1) up from the file's folder
            level = len(module) - len(module.lstrip("."))
            base = self.search_dirs[0] if self.search_dirs else ""
            for _ in range(level - 1):
                base = os.path.dirname(base)
            module, dirs = module[level:], [base]
            if not module:
                return None
        rel = module.replace(".", os.sep)
        for d in dirs:
            for cand in (os.path.join(d, rel + ".py"), os.path.join(d, rel, "__init__.py")):
                if os.path.isfile(cand):
                    return cand
        return None

    def module_object(self, local_name: str):
        """Name bound with `import x` -> loaded module object (except modules inside the project)."""
        entry = self.imports.get(local_name)
        if entry is None:
            return None
        module, attr = entry
        full = module if attr is None else f"{module}.{attr}"
        if full.startswith(".") or self._local_module(full.split(".")[0]) is not None:
            return None
        if attr is None:
            obj = _load_module(full)
        else:   # from package import submodule
            parent = _load_module(module)
            obj = getattr(parent, attr, None) if parent is not None else None
        return obj if inspect.ismodule(obj) else None


def _suggest(name: str, candidates: Iterable[str]) -> str:
    pool = [c for c in candidates if not (c.startswith("__") and c.endswith("__"))]
    match = difflib.get_close_matches(name, pool, n=1, cutoff=0.6)
    return match[0] if match else ""


def _attr_range(node: ast.Attribute, lines: List[str]) -> Tuple[int, int, int]:
    from .inspections import char_col
    lt = lines[node.end_lineno - 1] if 0 < node.end_lineno <= len(lines) else ""
    end = char_col(lt, node.end_col_offset)
    return node.end_lineno, end - len(node.attr), end


def check_attributes(tree: ast.Module, lines: List[str], api: Mapping[str, object],
                     search_dirs: List[str], bound: Mapping[str, int]) -> list:
    index = ModuleIndex(tree, api, search_dirs, bound)
    out = []
    # attributes assigned from outside to any object in the file (obj.x = …) count as known
    stored_anywhere = {n.attr for n in ast.walk(tree)
                       if isinstance(n, ast.Attribute) and isinstance(n.ctx, (ast.Store, ast.Del))}

    # ---- self.x / cls.x --------------------------------------------------- #
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        if cls.name not in index.classes or index.classes[cls.name] is not cls:
            continue
        members = index.members(cls)
        if members is None:
            continue
        for method in cls.body:
            if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decorators = {getattr(d, "id", getattr(d, "attr", "")) for d in method.decorator_list}
            params = method.args.posonlyargs + method.args.args
            if "staticmethod" in decorators or not params:
                continue
            me = params[0].arg
            for node in _loads_of(method, me):
                attr = node.attr
                if attr in members or attr in stored_anywhere or (attr.startswith("__") and attr.endswith("__")):
                    continue
                line, col, end = _attr_range(node, lines)
                out.append((line, col, end, WARNING, tr("Attribute '{attr}' not found in class '{cls}'", attr=attr, cls=cls.name),
                            "UnresolvedAttribute", _suggest(attr, members)))

    # ---- module.x / API_OBJECT.x ------------------------------------------ #
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load) and isinstance(node.value, ast.Name)):
            continue
        name, attr = node.value.id, node.attr
        if bound.get(name, 0) > 1:
            continue
        if name in index.imports and bound.get(name, 0) == 1:
            mod = index.module_object(name)
            if mod is None or hasattr(mod, attr) or hasattr(mod, "__getattr__"):
                continue
            try:
                if importlib.util.find_spec(f"{mod.__name__}.{attr}") is not None:
                    continue   # submodule (may not be imported yet)
            except (ImportError, ValueError, AttributeError):
                pass
            line, col, end = _attr_range(node, lines)
            out.append((line, col, end, WARNING, tr("'{attr}' not found in module '{module}'", attr=attr, module=name),
                        "UnresolvedReference", _suggest(attr, dir(mod))))
        elif name in api and name not in bound:
            obj = api[name]
            if inspect.ismodule(obj) or hasattr(obj, attr) or hasattr(type(obj), "__getattr__"):
                continue
            line, col, end = _attr_range(node, lines)
            out.append((line, col, end, WARNING, tr("Attribute '{attr}' not found on object '{name}'", attr=attr, name=name),
                        "UnresolvedAttribute", _suggest(attr, dir(obj))))
    return out


def _loads_of(method, me: str):
    """`me.x` reads inside a method; inner functions rebinding `me` are not entered."""
    if any(isinstance(n, ast.Name) and n.id == me and isinstance(n.ctx, ast.Store) for n in ast.walk(method)):
        return   # self was reassigned: its type is unknown
    stack = list(method.body)
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = n.args
            if any(a.arg == me for a in args.posonlyargs + args.args + args.kwonlyargs):
                continue
        if isinstance(n, ast.ClassDef):
            continue
        if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Load) and isinstance(n.value, ast.Name) \
                and n.value.id == me:
            yield n
        stack.extend(ast.iter_child_nodes(n))
