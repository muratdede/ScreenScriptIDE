"""Code inspection (an error / warning list like in IDEs).

Two layers:
  - analyze_source() / check_source(): Qt-independent inspection. Syntax errors and compiler warnings
    (compile), pyflakes warnings (undefined name, unused import, …), extra IDE inspections
    (inspections: argument count, unresolved module, unreachable code, …) and image names missing
    from the project. analyze_source also returns the semantic highlighting info (semantic).
  - DiagnosticsStore: file -> list of problems. Open editors publish the result for their own (unsaved)
    text; project files that are not open are scanned from disk and re-inspected when they change.

Writing `# noqa` at the end of a line hides the warnings on that line.
"""

from __future__ import annotations

import ast
import difflib
import os
import re
import warnings
from dataclasses import dataclass
from typing import Callable, Collection, Dict, Iterable, List, Mapping, Optional, Tuple

from PySide6.QtCore import QObject, QTimer, Signal

from ..i18n import N_, tr
from . import inspections, search
from .semantic import Tokens, semantic_tokens

ERROR = "error"
WARNING = "warning"

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp")
_NOQA = re.compile(r"#\s*noqa\b", re.IGNORECASE)
_IDENT = re.compile(r"[^\W\d]\w*")

# pyflakes message -> severity and message template. The template is filled with the message_args.
_ERRORS = {"UndefinedName", "UndefinedLocal", "UndefinedExport", "ReturnOutsideFunction", "YieldOutsideFunction",
           "BreakOutsideLoop", "ContinueOutsideLoop", "DuplicateArgument", "TwoStarredExpressions",
           "TooManyExpressionsInStarredAssignment", "DefaultExceptNotLast", "LateFutureImport",
           "FutureFeatureNotDefined", "ImportStarNotPermitted", "PercentFormatMissingArgument",
           "StringDotFormatMissingArgument", "PercentFormatPositionalCountMismatch", "InvalidPrintSyntax", "DoctestSyntaxError",
           "ForwardAnnotationSyntaxError", "LazyImportNotAtModuleScope", "LazyImportStarNotPermitted",
           "PercentFormatExpectedMapping", "PercentFormatExpectedSequence", "PercentFormatInvalidFormat",
           "PercentFormatMixedPositionalAndNamed", "PercentFormatStarRequiresSequence",
           "PercentFormatUnsupportedFormatCharacter", "StringDotFormatInvalidFormat",
           "StringDotFormatMixingAutomatic"}
_MESSAGES = {
    "UndefinedName": N_("Undefined name '{0}'"),
    "UndefinedLocal": N_("Local variable '{0}' is used before assignment"),
    "UndefinedExport": N_("Undefined name '{0}' in __all__"),
    "UnusedImport": N_("'{0}' imported but unused"),
    "UnusedVariable": N_("Local variable '{0}' is assigned to but never used"),
    "UnusedAnnotation": N_("Local variable '{0}' is annotated but never used"),
    "RedefinedWhileUnused": N_("Redefinition of unused '{0}' (line {1})"),
    "ImportShadowedByLoopVar": N_("Import '{0}' (line {1}) is shadowed by a loop variable"),
    "ImportStarUsed": N_("'from {0} import *' used; unable to detect undefined names"),
    "ImportStarUsage": N_("'{0}' may be undefined, or defined from star imports: {1}"),
    "ReturnOutsideFunction": N_("'return' outside function"),
    "YieldOutsideFunction": N_("'yield' outside function"),
    "BreakOutsideLoop": N_("'break' outside loop"),
    "ContinueOutsideLoop": N_("'continue' outside loop"),
    "DuplicateArgument": N_("Duplicate argument '{0}' in function definition"),
    "FStringMissingPlaceholders": N_("f-string is missing placeholders {{…}}"),
    "IsLiteral": N_("Use ==/!= instead of 'is' to compare with literals"),
    "IfTuple": N_("'if (…,)' is always true; perhaps a stray comma"),
    "AssertTuple": N_("assert is always true; remove the parentheses"),
    "RaiseNotImplemented": N_("'raise NotImplemented' should be 'raise NotImplementedError'"),
    "MultiValueRepeatedKeyLiteral": N_("Dictionary key {0} repeated with different values"),
    "DefaultExceptNotLast": N_("'except:' must be last"),
    "DoctestSyntaxError": N_("Syntax error in doctest"),
    "EagerUseOfLazyImport": N_("Lazily imported '{0}' (line {1}) is used eagerly"),
    "ForwardAnnotationSyntaxError": N_("Syntax error in forward annotation: {0}"),
    "FutureFeatureNotDefined": N_("Future feature is not defined: {0}"),
    "ImportStarNotPermitted": N_("'from {0} import *' only allowed at module level"),
    "InvalidPrintSyntax": N_("Use of >> is invalid with the print function"),
    "LateFutureImport": N_("from __future__ imports must occur at the beginning of the file"),
    "LazyImportNotAtModuleScope": N_("Lazy import is only allowed at module level"),
    "LazyImportStarNotPermitted": N_("'lazy from {0} import *' is not allowed"),
    "MultiValueRepeatedKeyVariable": N_("Dictionary key variable {0} repeated with different values"),
    "PercentFormatExpectedMapping": N_("'…' % … expected a mapping but got a sequence"),
    "PercentFormatExpectedSequence": N_("'…' % … expected a sequence but got a mapping"),
    "PercentFormatExtraNamedArguments": N_("'…' % … has unused named argument(s): {0}"),
    "PercentFormatInvalidFormat": N_("'…' % … has an invalid format string: {0}"),
    "PercentFormatMissingArgument": N_("'…' % … is missing argument(s) for placeholder(s): {0}"),
    "PercentFormatMixedPositionalAndNamed": N_("'…' % … has mixed positional and named placeholders"),
    "PercentFormatPositionalCountMismatch": N_("'…' % … has {0} placeholder(s) but {1} substitution(s)"),
    "PercentFormatStarRequiresSequence": N_("'…' % … the '*' specifier requires a sequence"),
    "PercentFormatUnsupportedFormatCharacter": N_("'…' % … has an unsupported format character {0}"),
    "StringDotFormatExtraNamedArguments": N_("'…'.format(…) has unused named argument(s): {0}"),
    "StringDotFormatExtraPositionalArguments": N_("'…'.format(…) has unused positional argument(s): {0}"),
    "StringDotFormatInvalidFormat": N_("'…'.format(…) has an invalid format string: {0}"),
    "StringDotFormatMissingArgument": N_("'…'.format(…) is missing argument(s) for placeholder(s): {0}"),
    "StringDotFormatMixingAutomatic": N_("'…'.format(…) mixes automatic and manual numbering"),
    "TStringMissingPlaceholders": N_("t-string is missing placeholders {{…}}"),
    "TooManyExpressionsInStarredAssignment": N_("Too many expressions in star-unpacking assignment"),
    "TwoStarredExpressions": N_("Two starred expressions in assignment"),
    "UnusedIndirectAssignment": N_("'{0} {1}' is unnecessary: the name is never assigned in this scope"),
}


@dataclass(frozen=True)
class WarningType:
    """group, title and description are English source strings; display them with tr()."""
    code: str
    group: str
    title: str
    description: str


# Warnings that can be disabled in the settings (Settings › Editor › Inspections). Errors (syntax,
# undefined name, missing argument, unresolved module, …) are not here and cannot be disabled.
# When adding a new WARNING code, add it here too.
WARNING_TYPES: Tuple[WarningType, ...] = tuple(WarningType(*t) for t in (
    # unused code
    ("UnusedImport", N_("Unused code"), N_("Unused import"), N_("The imported name is never used in the file.")),
    ("UnusedVariable", N_("Unused code"), N_("Unused local variable"),
     N_("A variable assigned inside a function is never read.")),
    ("UnusedAnnotation", N_("Unused code"), N_("Unused annotation"),
     N_("A local variable is annotated but never used.")),
    ("UnusedIndirectAssignment", N_("Unused code"), N_("Unnecessary global/nonlocal"),
     N_("A name declared with global/nonlocal is never assigned in this scope.")),
    ("RedefinedWhileUnused", N_("Unused code"), N_("Redefinition while unused"),
     N_("A name (import, function, class) is redefined before it is used.")),
    ("UnreachableCode", N_("Unused code"), N_("Unreachable code"),
     N_("Code after return / raise / break / continue never runs.")),
    ("StatementHasNoEffect", N_("Unused code"), N_("Statement without effect"),
     N_("An expression whose result is not used (e.g. a comparison standing alone).")),
    # probable bugs
    ("MutableDefaultArgument", N_("Probable bugs"), N_("Mutable default argument"),
     N_("Default values like lists/dicts are shared between calls.")),
    ("SelfAssignment", N_("Probable bugs"), N_("Self assignment"), N_("An assignment without effect like x = x.")),
    ("BareExcept", N_("Probable bugs"), N_("Bare except"),
     N_("'except:' catches everything, including KeyboardInterrupt and SystemExit.")),
    ("IsLiteral", N_("Probable bugs"), N_("'is' comparison with a literal"),
     N_("Use == instead of 'is' with number/text literals.")),
    ("IfTuple", N_("Probable bugs"), N_("Always-true if"), N_("'if (x,)' is always true because it is a tuple.")),
    ("AssertTuple", N_("Probable bugs"), N_("Always-true assert"), N_("'assert (x, message)' always passes.")),
    ("RaiseNotImplemented", N_("Probable bugs"), N_("raise NotImplemented"),
     N_("NotImplemented is not an exception; use NotImplementedError.")),
    ("MultiValueRepeatedKeyLiteral", N_("Probable bugs"), N_("Repeated dictionary key"),
     N_("The same literal key is written more than once in a dict with different values.")),
    ("MultiValueRepeatedKeyVariable", N_("Probable bugs"), N_("Repeated dictionary key (variable)"),
     N_("The same variable key is written more than once in a dict with different values.")),
    ("ImportShadowedByLoopVar", N_("Probable bugs"), N_("Loop variable shadows an import"),
     N_("The variable of a for loop overwrites an imported name.")),
    ("EagerUseOfLazyImport", N_("Probable bugs"), N_("Eager use of a lazy import"),
     N_("A name imported with lazy import is used while the module is loading.")),
    ("SyntaxWarning", N_("Probable bugs"), N_("Compiler warning"),
     N_("SyntaxWarnings of the Python compiler (e.g. comparing a literal with 'is').")),
    ("InvalidEscapeSequence", N_("Probable bugs"), N_("Invalid escape sequence"),
     N_("An unrecognized \\ escape in a string; use a raw string (r'…').")),
    # shadowing
    ("ShadowsBuiltin", N_("Shadowing"), N_("Built-in name is shadowed"),
     N_("A built-in name like list, id, type is redefined.")),
    ("ShadowsApiName", N_("Shadowing"), N_("Script API name is shadowed"),
     N_("One of the script API names like click, find is redefined.")),
    # resolution
    ("UnresolvedAttribute", N_("Resolution"), N_("Unresolved attribute"),
     N_("An attribute not defined on self.x or an API object is used.")),
    ("UnresolvedReference", N_("Resolution"), N_("Name not found in module"),
     N_("A name that does not exist in the imported module is used (module.x).")),
    ("ImportStarUsed", N_("Resolution"), N_("import * usage"),
     N_("Undefined names cannot be checked when 'from m import *' is used.")),
    ("ImportStarUsage", N_("Resolution"), N_("Name possibly from import *"),
     N_("The name may be undefined or may come from an 'import *'.")),
    # code style
    ("ComparisonWithSingleton", N_("Code style"), N_("== comparison with None/True/False"),
     N_("Use 'is' for None; write the condition directly instead of comparing with True/False.")),
    ("SelfParameterName", N_("Code style"), N_("self / cls parameter name"),
     N_("The first parameter of a method should conventionally be 'self' ('cls' in a class method).")),
    ("GlobalAtModuleLevel", N_("Code style"), N_("global at module level"),
     N_("A 'global' declaration at module level is unnecessary.")),
    ("FStringMissingPlaceholders", N_("Code style"), N_("f-string without placeholders"),
     N_("There is no {…} in the f-string; the f prefix is unnecessary.")),
    ("TStringMissingPlaceholders", N_("Code style"), N_("t-string without placeholders"),
     N_("There is no {…} in the t-string.")),
    # format strings
    ("PercentFormatExtraNamedArguments", N_("Format strings"), N_("Extra named argument in % format"),
     N_("There are unused keys in '…' % {…}.")),
    ("StringDotFormatExtraNamedArguments", N_("Format strings"), N_(".format() extra named argument"),
     N_("There are unused named arguments in the .format(…) call.")),
    ("StringDotFormatExtraPositionalArguments", N_("Format strings"), N_(".format() extra positional argument"),
     N_("There are unused positional arguments in the .format(…) call.")),
    # project
    ("MissingImage", N_("Project"), N_("Missing image"),
     N_("A .png/.jpg image named in the script does not exist in the project image folders.")),
))


@dataclass(frozen=True)
class Diagnostic:
    path: str
    line: int          # 1-based
    column: int        # character index within the line (Python str), 0-based
    end_column: int    # exclusive; if equal to column, up to the end of the line
    severity: str      # ERROR | WARNING
    message: str
    code: str = ""
    suggestion: str = ""   # quick fix: name to write in place of the [column, end_column) range

    @property
    def is_error(self) -> bool:
        return self.severity == ERROR


# --------------------------------------------------------------------------- #
# Inspection
# --------------------------------------------------------------------------- #
_char_col = inspections.char_col   # ast/pyflakes column UTF-8 byte offset -> Python str index


def _token_end(line_text: str, col: int) -> int:
    m = _IDENT.match(line_text, col)
    return m.end() if m else min(len(line_text), col + 1)


_NAMED = {"UnusedImport", "RedefinedWhileUnused", "ImportShadowedByLoopVar", "UnusedVariable", "UnusedAnnotation"}


def _find_name(line_text: str, name: str, start: int) -> Optional[Tuple[int, int]]:
    """Finds the name visible on the line for import names like 'os.path' / 'numpy as np'."""
    name = name.split(" as ")[-1].strip()
    m = re.compile(r"(?<![\w.])" + re.escape(name) + r"(?![\w])").search(line_text, start)
    return (m.start(), m.end()) if m else None


def _translate(msg) -> str:
    name = type(msg).__name__
    template = _MESSAGES.get(name)
    if template:
        try:
            args = [str(a) for a in msg.message_args]
            return tr(template).format(*args)
        except (IndexError, KeyError, ValueError, AttributeError):
            pass
    return msg.message % msg.message_args


def _suggest_name(name: str, tree: ast.AST, builtins: Iterable[str]) -> str:
    """The known name closest to an undefined name (for fixing typos)."""
    pool = set(inspections._binding_names(tree)) | set(builtins) | inspections.BUILTIN_NAMES
    pool |= {"True", "False", "None"}
    pool.discard(name)
    match = difflib.get_close_matches(name, pool, n=1, cutoff=0.6)
    return match[0] if match else ""


def check_source(text: str, path: str = "", builtins: Iterable[str] = (),
                 image_exists: Optional[Callable[[str], bool]] = None,
                 api: Optional[Mapping[str, object]] = None, search_dirs: Iterable[str] = (),
                 disabled: Collection[str] = ()) -> List[Diagnostic]:
    """Inspects source text. If image_exists is given, image names in strings are checked too."""
    return analyze_source(text, path, builtins, image_exists, api, search_dirs, semantic=False,
                          disabled=disabled)[0]


def analyze_source(text: str, path: str = "", builtins: Iterable[str] = (),
                   image_exists: Optional[Callable[[str], bool]] = None,
                   api: Optional[Mapping[str, object]] = None, search_dirs: Iterable[str] = (),
                   semantic: bool = True, disabled: Collection[str] = ()) -> Tuple[List[Diagnostic], Optional[Tokens]]:
    """(problems, semantic tokens). On a syntax error the tokens are None (keep the old ones).
    disabled: disabled warning codes (WARNING_TYPES); only hides warnings, errors are always shown."""
    lines = text.splitlines()

    def line_text(n: int) -> str:
        return lines[n - 1] if 0 < n <= len(lines) else ""

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            tree = compile(text, path or "<script>", "exec", ast.PyCF_ONLY_AST)
        except SyntaxError as e:
            n = e.lineno or 1
            lt = line_text(n)
            col = max(0, (e.offset or 1) - 1)          # SyntaxError.offset is character based, 1-based
            end = (e.end_offset - 1) if getattr(e, "end_offset", None) and e.end_lineno == n else col + 1
            col = min(col, len(lt))
            kind = tr("Indentation error") if isinstance(e, IndentationError) else tr("Syntax error")
            return [Diagnostic(path, n, col, max(col, min(end, len(lt))), ERROR,
                               f"{kind}: {e.msg}", type(e).__name__)], None
        except ValueError as e:   # e.g. a NUL character in the source
            return [Diagnostic(path, 1, 0, 0, ERROR, str(e), "ValueError")], None

    out: List[Diagnostic] = []
    for issue in inspections.compiler_warnings(caught, lines):
        out.append(Diagnostic(path, *issue))

    try:
        from pyflakes import checker
    except ImportError:
        checker = None
    if checker is not None:
        w = checker.Checker(tree, filename=path or "<script>", builtins=set(builtins))
        for msg in sorted(w.messages, key=lambda m: (m.lineno, m.col)):
            lt = line_text(msg.lineno)
            col = _char_col(lt, msg.col)
            name = type(msg).__name__
            end = _token_end(lt, col)
            if name in _NAMED and msg.message_args:   # position is the statement start; mark the name itself
                found = _find_name(lt, str(msg.message_args[0]), col)
                if found:
                    col, end = found
            suggestion = ""
            if name in ("UndefinedName", "UndefinedLocal") and msg.message_args:
                suggestion = _suggest_name(str(msg.message_args[0]), tree, builtins)
            out.append(Diagnostic(path, msg.lineno, col, end,
                                  ERROR if name in _ERRORS else WARNING, _translate(msg), name, suggestion))

    try:
        issues = inspections.inspect_tree(tree, lines, api, search_dirs)
    except Exception as e:   # an error in the inspector must not stop the editor
        issues = [(1, 0, 0, WARNING, tr("Inspection could not be run: {error!r}", error=e), "InspectionFailed")]
    for issue in issues:
        out.append(Diagnostic(path, *issue))

    if image_exists is not None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and node.value.lower().endswith(IMAGE_EXTENSIONS) and "\n" not in node.value \
                    and not image_exists(node.value):
                lt = line_text(node.lineno)
                col = _char_col(lt, node.col_offset)
                end = _char_col(lt, node.end_col_offset) if node.end_lineno == node.lineno else len(lt)
                out.append(Diagnostic(path, node.lineno, col, end, WARNING,
                                      tr("Image not found: {name}", name=node.value), "MissingImage"))

    out = [d for d in dict.fromkeys(out) if not _NOQA.search(line_text(d.line))   # drop duplicates
           and not (d.severity == WARNING and d.code in disabled)]
    out.sort(key=lambda d: (d.line, d.column, not d.is_error))

    tokens = None
    if semantic:
        try:
            tokens = semantic_tokens(tree, lines, api)
        except Exception:   # a highlighting error must not break the inspection
            tokens = {}
    return out, tokens


def counts(diagnostics: Iterable[Diagnostic]) -> Tuple[int, int]:
    """(errors, warnings) count."""
    errors = warnings = 0
    for d in diagnostics:
        if d.is_error:
            errors += 1
        else:
            warnings += 1
    return errors, warnings


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #
def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


class DiagnosticsStore(QObject):
    """File -> list of problems. `changed(path)` is emitted when one file's list changes, `reset()`
    when all of them are renewed at once."""

    changed = Signal(str)
    reset = Signal()

    SCAN_INTERVAL_MS = 1000

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._items: Dict[str, List[Diagnostic]] = {}
        self._display: Dict[str, str] = {}
        self._live: Dict[str, int] = {}                      # files open in an editor (counter)
        self._stamps: Dict[str, Tuple[int, int]] = {}        # stamps of the files inspected from disk
        self._builtins: Optional[frozenset] = None
        self._api: Optional[Dict[str, object]] = None
        self._scan_timer = QTimer(self, interval=self.SCAN_INTERVAL_MS)
        self._scan_timer.timeout.connect(self.scan_project)
        self._disabled = frozenset(getattr(ctx.app_config, "disabled_warnings", ()) or ())
        ctx.images.changed.connect(lambda _paths: self.recheck_all())
        ctx.bus.app_config_changed.connect(self._on_app_config)

    def _on_app_config(self, config) -> None:
        disabled = frozenset(getattr(config, "disabled_warnings", ()) or ())
        if disabled != self._disabled:
            self._disabled = disabled
            self.recheck_all()

    # ---- query ----------------------------------------------------------- #
    def get(self, path: str) -> List[Diagnostic]:
        return list(self._items.get(_key(path), []))

    def files(self) -> List[str]:
        """Files with problems (with their display paths, sorted)."""
        return sorted((self._display[k] for k, v in self._items.items() if v), key=str.lower)

    def totals(self) -> Tuple[int, int]:
        return counts(d for items in self._items.values() for d in items)

    # ---- inspection ------------------------------------------------------ #
    def builtins(self) -> frozenset:
        """Names scripts find ready at runtime (the API) count as builtins for pyflakes."""
        if self._builtins is None:
            try:
                from .completion import api_names
                self._builtins = frozenset(api_names())
            except Exception:   # if the API cannot load there are extra undefined-name warnings, but it works
                self._builtins = frozenset()
        return self._builtins

    def api(self) -> Mapping[str, object]:
        """Script API objects (for call signature checks and highlighting)."""
        if self._api is None:
            try:
                from .completion import script_namespace
                self._api = dict(script_namespace())
            except Exception:
                self._api = {}
        return self._api

    def _search_dirs(self, path: str) -> List[str]:
        """Project folders that are on the import path while a script runs."""
        project = self._ctx.project
        return [os.path.dirname(path)] + ([project.root] if project else [])

    def analyze(self, path: str, text: str) -> Tuple[List[Diagnostic], Optional[Tokens]]:
        project = self._ctx.project
        exists = (lambda name: project.find_image(name, os.path.dirname(path)) is not None) if project else None
        return analyze_source(text, path, self.builtins(), exists, self.api(), self._search_dirs(path),
                              disabled=self.disabled())

    def check(self, path: str, text: str) -> List[Diagnostic]:
        project = self._ctx.project
        exists = (lambda name: project.find_image(name, os.path.dirname(path)) is not None) if project else None
        return check_source(text, path, self.builtins(), exists, self.api(), self._search_dirs(path),
                            disabled=self.disabled())

    def disabled(self) -> frozenset:
        """Warning codes disabled in the settings."""
        return self._disabled

    def publish(self, path: str, diagnostics: List[Diagnostic]) -> None:
        k = _key(path)
        old = self._items.get(k)
        self._display[k] = os.path.abspath(path)
        self._items[k] = list(diagnostics)
        if old != self._items[k]:
            self.changed.emit(self._display[k])

    def remove(self, path: str) -> None:
        k = _key(path)
        self._stamps.pop(k, None)
        if self._items.pop(k, None):
            self.changed.emit(self._display.get(k, path))

    # ---- open editors ---------------------------------------------------- #
    def attach_live(self, path: str) -> None:
        k = _key(path)
        self._live[k] = self._live.get(k, 0) + 1

    def detach_live(self, path: str) -> None:
        """The editor was closed: the file is now inspected from disk (removed from the list if outside the project)."""
        k = _key(path)
        n = self._live.get(k, 0) - 1
        if n > 0:
            self._live[k] = n
            return
        self._live.pop(k, None)
        self._stamps.pop(k, None)
        project = self._ctx.project
        if project is None or not project.contains(path) or not os.path.isfile(path):
            self.remove(path)
        else:
            self._check_disk(path)

    # ---- project scan ---------------------------------------------------- #
    def set_project(self, project) -> None:
        self._items.clear()
        self._display.clear()
        self._stamps.clear()
        self.reset.emit()
        if project is None:
            self._scan_timer.stop()
            return
        self.scan_project()
        self._scan_timer.start()

    def recheck_all(self) -> None:
        """Images/packages changed: re-inspect everything for 'image not found' and 'module not found'."""
        inspections.clear_module_cache()
        self._stamps.clear()
        self.scan_project()
        if self._ctx.documents is not None:
            for editor in getattr(self._ctx.documents, "editors", lambda: [])():
                editor.schedule_diagnostics()

    def scan_project(self) -> None:
        """Inspects .py files that are not open from disk; only changed ones are processed again."""
        project = self._ctx.project
        if project is None:
            return
        seen = set()
        for path in search.iter_files(project.root, ["*.py"]):
            k = _key(path)
            seen.add(k)
            if k not in self._live:
                self._check_disk(path)
        for k in [k for k in self._items if k not in seen and k not in self._live]:
            self.remove(self._display.get(k, k))   # deleted files

    def _check_disk(self, path: str) -> None:
        k = _key(path)
        try:
            st = os.stat(path)
        except OSError:
            self.remove(path)
            return
        stamp = (st.st_mtime_ns, st.st_size)
        if self._stamps.get(k) == stamp:
            return
        self._stamps[k] = stamp
        text = search.read_text(path)
        if text is None:
            return
        self.publish(path, self.check(path, text.replace("\r\n", "\n")))
