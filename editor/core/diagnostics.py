"""Kod denetimi (IDE'lerdeki gibi hata / uyarı listesi).

İki katman:
  - analyze_source() / check_source(): Qt bağımsız denetim. Sözdizimi hataları ve derleyici uyarıları
    (compile), pyflakes uyarıları (tanımsız isim, kullanılmayan import, …), ek IDE denetimleri
    (inspections: argüman sayısı, bulunamayan modül, erişilemeyen kod, …) ve projede bulunmayan
    görsel adları. analyze_source ayrıca anlamsal renklendirme bilgisini (semantic) döndürür.
  - DiagnosticsStore: dosya -> sorun listesi. Açık editörler kendi (kaydedilmemiş) metinlerinin
    sonucunu yayınlar; açık olmayan proje dosyaları diskten taranır ve değiştikçe yeniden denetlenir.

Bir satırın sonuna `# noqa` yazmak o satırdaki uyarıları gizler.
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

from . import inspections, search
from .semantic import Tokens, semantic_tokens

ERROR = "error"
WARNING = "warning"

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp")
_NOQA = re.compile(r"#\s*noqa\b", re.IGNORECASE)
_IDENT = re.compile(r"[^\W\d]\w*")

# pyflakes mesajı -> (önem, Türkçe şablon). Şablon mesajın message_args'ı ile doldurulur.
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
_TURKISH = {
    "UndefinedName": "Tanımsız isim '{0}'",
    "UndefinedLocal": "'{0}' yerel değişkeni atanmadan önce kullanılıyor",
    "UndefinedExport": "__all__ içinde tanımsız isim '{0}'",
    "UnusedImport": "'{0}' içe aktarıldı ama kullanılmıyor",
    "UnusedVariable": "'{0}' yerel değişkenine atandı ama hiç kullanılmıyor",
    "UnusedAnnotation": "'{0}' yerel değişkeni tiplendi ama hiç kullanılmıyor",
    "RedefinedWhileUnused": "Kullanılmayan '{0}' ({1}. satır) yeniden tanımlanıyor",
    "ImportShadowedByLoopVar": "'{0}' importu ({1}. satır) döngü değişkeniyle gölgeleniyor",
    "ImportStarUsed": "'from {0} import *' kullanıldı; tanımsız isimler denetlenemiyor",
    "ImportStarUsage": "'{0}' tanımsız olabilir ya da şuradan geliyor: {1}",
    "ReturnOutsideFunction": "Fonksiyon dışında 'return'",
    "YieldOutsideFunction": "Fonksiyon dışında 'yield'",
    "BreakOutsideLoop": "Döngü dışında 'break'",
    "ContinueOutsideLoop": "Döngü dışında 'continue'",
    "DuplicateArgument": "Fonksiyon tanımında tekrar eden argüman '{0}'",
    "FStringMissingPlaceholders": "f-string içinde {{…}} yer tutucu yok",
    "IsLiteral": "Sabitlerle karşılaştırmada 'is' yerine ==/!= kullanın",
    "IfTuple": "'if (…,)' her zaman doğru; fazladan virgül olabilir",
    "AssertTuple": "assert her zaman doğru; parantezleri kaldırın",
    "RaiseNotImplemented": "'raise NotImplemented' yerine 'raise NotImplementedError' olmalı",
    "MultiValueRepeatedKeyLiteral": "Sözlük anahtarı {0} farklı değerlerle tekrar ediyor",
    "DefaultExceptNotLast": "'except:' en sonda olmalı",
    "DoctestSyntaxError": "Doctest içinde sözdizimi hatası",
    "EagerUseOfLazyImport": "Tembel importlanan '{0}' ({1}. satır) erkenden kullanılıyor",
    "ForwardAnnotationSyntaxError": "İleri tip ipucunda sözdizimi hatası: {0}",
    "FutureFeatureNotDefined": "Tanımsız __future__ özelliği: {0}",
    "ImportStarNotPermitted": "'from {0} import *' yalnızca modül düzeyinde kullanılabilir",
    "InvalidPrintSyntax": "print fonksiyonunda >> kullanılamaz",
    "LateFutureImport": "from __future__ importları dosyanın en başında olmalı",
    "LazyImportNotAtModuleScope": "Tembel import yalnızca modül düzeyinde olabilir",
    "LazyImportStarNotPermitted": "'lazy from {0} import *' kullanılamaz",
    "MultiValueRepeatedKeyVariable": "Sözlük anahtarı değişkeni {0} farklı değerlerle tekrar ediyor",
    "PercentFormatExpectedMapping": "'…' % … sözlük bekliyor ama dizi verildi",
    "PercentFormatExpectedSequence": "'…' % … dizi bekliyor ama sözlük verildi",
    "PercentFormatExtraNamedArguments": "'…' % … kullanılmayan isimli argüman(lar): {0}",
    "PercentFormatInvalidFormat": "'…' % … geçersiz biçim dizesi: {0}",
    "PercentFormatMissingArgument": "'…' % … yer tutucu(lar) için argüman eksik: {0}",
    "PercentFormatMixedPositionalAndNamed": "'…' % … konumsal ve isimli yer tutucular karışık",
    "PercentFormatPositionalCountMismatch": "'…' % … {0} yer tutucu var ama {1} değer verildi",
    "PercentFormatStarRequiresSequence": "'…' % … '*' belirteci dizi gerektirir",
    "PercentFormatUnsupportedFormatCharacter": "'…' % … desteklenmeyen biçim karakteri {0}",
    "StringDotFormatExtraNamedArguments": "'…'.format(…) kullanılmayan isimli argüman(lar): {0}",
    "StringDotFormatExtraPositionalArguments": "'…'.format(…) kullanılmayan konumsal argüman(lar): {0}",
    "StringDotFormatInvalidFormat": "'…'.format(…) geçersiz biçim dizesi: {0}",
    "StringDotFormatMissingArgument": "'…'.format(…) yer tutucu(lar) için argüman eksik: {0}",
    "StringDotFormatMixingAutomatic": "'…'.format(…) otomatik ve elle numaralandırmayı karıştırıyor",
    "TStringMissingPlaceholders": "t-string içinde {{…}} yer tutucu yok",
    "TooManyExpressionsInStarredAssignment": "Yıldızlı atamada çok fazla ifade",
    "TwoStarredExpressions": "Atamada iki yıldızlı ifade",
    "UnusedIndirectAssignment": "'{0} {1}' gereksiz: isim bu kapsamda hiç atanmıyor",
}


@dataclass(frozen=True)
class WarningType:
    code: str
    group: str
    title: str
    description: str


# Ayarlardan kapatılabilen uyarılar (Ayarlar › Editör › Denetimler). Hatalar (sözdizimi, tanımsız isim,
# eksik argüman, bulunamayan modül, …) burada yoktur ve kapatılamaz. Yeni bir WARNING kodu eklerken buraya da ekleyin.
WARNING_TYPES: Tuple[WarningType, ...] = tuple(WarningType(*t) for t in (
    # kullanılmayan kod
    ("UnusedImport", "Kullanılmayan kod", "Kullanılmayan import", "İçe aktarılan isim dosyada hiç kullanılmıyor."),
    ("UnusedVariable", "Kullanılmayan kod", "Kullanılmayan yerel değişken",
     "Fonksiyon içinde atanan değişken hiç okunmuyor."),
    ("UnusedAnnotation", "Kullanılmayan kod", "Kullanılmayan tip ipucu",
     "Yerel değişken tiplenmiş ama hiç kullanılmıyor."),
    ("UnusedIndirectAssignment", "Kullanılmayan kod", "Gereksiz global/nonlocal",
     "global/nonlocal ile bildirilen isim bu kapsamda hiç atanmıyor."),
    ("RedefinedWhileUnused", "Kullanılmayan kod", "Kullanılmadan yeniden tanımlama",
     "Bir isim (import, fonksiyon, sınıf) kullanılmadan önce yeniden tanımlanıyor."),
    ("UnreachableCode", "Kullanılmayan kod", "Erişilemeyen kod",
     "return / raise / break / continue sonrasındaki kod hiç çalışmaz."),
    ("StatementHasNoEffect", "Kullanılmayan kod", "Etkisiz ifade",
     "Sonucu kullanılmayan ifade (ör. tek başına duran bir karşılaştırma)."),
    # olası hatalar
    ("MutableDefaultArgument", "Olası hatalar", "Değiştirilebilir varsayılan argüman",
     "Liste/sözlük gibi varsayılan değerler çağrılar arasında paylaşılır."),
    ("SelfAssignment", "Olası hatalar", "Kendisine atama", "x = x gibi etkisiz atama."),
    ("BareExcept", "Olası hatalar", "Çıplak except",
     "'except:' KeyboardInterrupt ve SystemExit dahil her şeyi yakalar."),
    ("IsLiteral", "Olası hatalar", "Sabitle 'is' karşılaştırması",
     "Sayı/metin sabitleriyle 'is' yerine == kullanılmalı."),
    ("IfTuple", "Olası hatalar", "Her zaman doğru if", "'if (x,)' demet olduğu için her zaman doğrudur."),
    ("AssertTuple", "Olası hatalar", "Her zaman doğru assert", "'assert (x, mesaj)' her zaman geçer."),
    ("RaiseNotImplemented", "Olası hatalar", "raise NotImplemented",
     "NotImplemented bir istisna değildir; NotImplementedError kullanın."),
    ("MultiValueRepeatedKeyLiteral", "Olası hatalar", "Tekrar eden sözlük anahtarı",
     "Aynı sabit anahtar sözlükte farklı değerlerle birden fazla kez yazılmış."),
    ("MultiValueRepeatedKeyVariable", "Olası hatalar", "Tekrar eden sözlük anahtarı (değişken)",
     "Aynı değişken anahtar sözlükte farklı değerlerle birden fazla kez yazılmış."),
    ("ImportShadowedByLoopVar", "Olası hatalar", "Döngü değişkeni importu gölgeliyor",
     "for döngüsünün değişkeni içe aktarılan bir ismi eziyor."),
    ("EagerUseOfLazyImport", "Olası hatalar", "Tembel importun erken kullanımı",
     "lazy import ile alınan isim modül yüklenirken kullanılıyor."),
    ("SyntaxWarning", "Olası hatalar", "Derleyici uyarısı",
     "Python derleyicisinin SyntaxWarning uyarıları (ör. 'is' ile sabit karşılaştırma)."),
    ("InvalidEscapeSequence", "Olası hatalar", "Geçersiz kaçış dizisi",
     "Metin içinde tanınmayan \\ kaçışı; raw string (r'…') kullanın."),
    # gölgeleme
    ("ShadowsBuiltin", "Gölgeleme", "Yerleşik isim gölgeleniyor",
     "list, id, type gibi yerleşik bir isim yeniden tanımlanıyor."),
    ("ShadowsApiName", "Gölgeleme", "Script API ismi gölgeleniyor",
     "click, find gibi script API isimlerinden biri yeniden tanımlanıyor."),
    # çözümleme
    ("UnresolvedAttribute", "Çözümleme", "Bulunamayan nitelik",
     "self.x ya da API nesnesinde tanımlı olmayan bir nitelik kullanılıyor."),
    ("UnresolvedReference", "Çözümleme", "Modülde bulunamayan isim",
     "İçe aktarılan modülde olmayan bir isim kullanılıyor (modul.x)."),
    ("ImportStarUsed", "Çözümleme", "import * kullanımı",
     "'from m import *' kullanıldığında tanımsız isimler denetlenemez."),
    ("ImportStarUsage", "Çözümleme", "import * kaynaklı olabilecek isim",
     "İsim tanımsız olabilir ya da bir 'import *' ile gelmiş olabilir."),
    # kod stili
    ("ComparisonWithSingleton", "Kod stili", "None/True/False ile == karşılaştırması",
     "None için 'is' kullanın; True/False ile karşılaştırma yerine koşulu doğrudan yazın."),
    ("SelfParameterName", "Kod stili", "self / cls parametre adı",
     "Metodun ilk parametresi geleneksel olarak 'self' (sınıf metodunda 'cls') olmalı."),
    ("GlobalAtModuleLevel", "Kod stili", "Modül düzeyinde global", "Modül düzeyinde 'global' bildirimi gereksiz."),
    ("FStringMissingPlaceholders", "Kod stili", "Yer tutucusuz f-string", "f-string içinde {…} yok; f öneki gereksiz."),
    ("TStringMissingPlaceholders", "Kod stili", "Yer tutucusuz t-string", "t-string içinde {…} yok."),
    # biçim dizeleri
    ("PercentFormatExtraNamedArguments", "Biçim dizeleri", "% biçiminde fazla isimli argüman",
     "'…' % {…} içinde kullanılmayan anahtarlar var."),
    ("StringDotFormatExtraNamedArguments", "Biçim dizeleri", ".format() fazla isimli argüman",
     ".format(…) çağrısında kullanılmayan isimli argümanlar var."),
    ("StringDotFormatExtraPositionalArguments", "Biçim dizeleri", ".format() fazla konumsal argüman",
     ".format(…) çağrısında kullanılmayan konumsal argümanlar var."),
    # proje
    ("MissingImage", "Proje", "Bulunamayan görsel",
     "Script'te adı geçen .png/.jpg görseli proje görsel klasörlerinde yok."),
))


@dataclass(frozen=True)
class Diagnostic:
    path: str
    line: int          # 1'den
    column: int        # satır içi karakter indeksi (Python str), 0'dan
    end_column: int    # dışlayan; column ile aynıysa satır sonuna kadar
    severity: str      # ERROR | WARNING
    message: str
    code: str = ""
    suggestion: str = ""   # hızlı düzeltme: [column, end_column) aralığının yerine yazılacak isim

    @property
    def is_error(self) -> bool:
        return self.severity == ERROR


# --------------------------------------------------------------------------- #
# Denetim
# --------------------------------------------------------------------------- #
_char_col = inspections.char_col   # ast/pyflakes sütunu UTF-8 bayt ofseti -> Python str indeksi


def _token_end(line_text: str, col: int) -> int:
    m = _IDENT.match(line_text, col)
    return m.end() if m else min(len(line_text), col + 1)


_NAMED = {"UnusedImport", "RedefinedWhileUnused", "ImportShadowedByLoopVar", "UnusedVariable", "UnusedAnnotation"}


def _find_name(line_text: str, name: str, start: int) -> Optional[Tuple[int, int]]:
    """'os.path' / 'numpy as np' gibi import adlarında satırda görünen ismi bul."""
    name = name.split(" as ")[-1].strip()
    m = re.compile(r"(?<![\w.])" + re.escape(name) + r"(?![\w])").search(line_text, start)
    return (m.start(), m.end()) if m else None


def _translate(msg) -> str:
    name = type(msg).__name__
    template = _TURKISH.get(name)
    if template:
        try:
            args = [str(a) for a in msg.message_args]
            return template.format(*args)
        except (IndexError, KeyError, ValueError, AttributeError):
            pass
    return msg.message % msg.message_args


def _suggest_name(name: str, tree: ast.AST, builtins: Iterable[str]) -> str:
    """Tanımsız isme en yakın bilinen isim (yazım hatası düzeltmesi için)."""
    pool = set(inspections._binding_names(tree)) | set(builtins) | inspections.BUILTIN_NAMES
    pool |= {"True", "False", "None"}
    pool.discard(name)
    match = difflib.get_close_matches(name, pool, n=1, cutoff=0.6)
    return match[0] if match else ""


def check_source(text: str, path: str = "", builtins: Iterable[str] = (),
                 image_exists: Optional[Callable[[str], bool]] = None,
                 api: Optional[Mapping[str, object]] = None, search_dirs: Iterable[str] = (),
                 disabled: Collection[str] = ()) -> List[Diagnostic]:
    """Kaynak metni denetler. image_exists verilirse string olarak geçen görsel adları da kontrol edilir."""
    return analyze_source(text, path, builtins, image_exists, api, search_dirs, semantic=False,
                          disabled=disabled)[0]


def analyze_source(text: str, path: str = "", builtins: Iterable[str] = (),
                   image_exists: Optional[Callable[[str], bool]] = None,
                   api: Optional[Mapping[str, object]] = None, search_dirs: Iterable[str] = (),
                   semantic: bool = True, disabled: Collection[str] = ()) -> Tuple[List[Diagnostic], Optional[Tokens]]:
    """(sorunlar, anlamsal belirteçler). Sözdizimi hatasında belirteçler None'dır (eskisi korunmalı).
    disabled: kapatılmış uyarı kodları (WARNING_TYPES); yalnızca uyarıları gizler, hatalar hep gösterilir."""
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
            col = max(0, (e.offset or 1) - 1)          # SyntaxError.offset karakter tabanlı, 1'den
            end = (e.end_offset - 1) if getattr(e, "end_offset", None) and e.end_lineno == n else col + 1
            col = min(col, len(lt))
            kind = "Girinti hatası" if isinstance(e, IndentationError) else "Sözdizimi hatası"
            return [Diagnostic(path, n, col, max(col, min(end, len(lt))), ERROR,
                               f"{kind}: {e.msg}", type(e).__name__)], None
        except ValueError as e:   # ör. kaynakta NUL karakteri
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
            if name in _NAMED and msg.message_args:   # konum deyimin başı; ismin kendisini işaretle
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
    except Exception as e:   # denetçideki bir hata editörü durdurmasın
        issues = [(1, 0, 0, WARNING, f"Denetim çalıştırılamadı: {e!r}", "InspectionFailed")]
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
                                      f"Görsel bulunamadı: {node.value}", "MissingImage"))

    out = [d for d in dict.fromkeys(out) if not _NOQA.search(line_text(d.line))   # tekrarları at
           and not (d.severity == WARNING and d.code in disabled)]
    out.sort(key=lambda d: (d.line, d.column, not d.is_error))

    tokens = None
    if semantic:
        try:
            tokens = semantic_tokens(tree, lines, api)
        except Exception:   # renklendirme hatası denetimi bozmasın
            tokens = {}
    return out, tokens


def counts(diagnostics: Iterable[Diagnostic]) -> Tuple[int, int]:
    """(hata, uyarı) sayısı."""
    errors = warnings = 0
    for d in diagnostics:
        if d.is_error:
            errors += 1
        else:
            warnings += 1
    return errors, warnings


# --------------------------------------------------------------------------- #
# Depo
# --------------------------------------------------------------------------- #
def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


class DiagnosticsStore(QObject):
    """Dosya -> sorun listesi. `changed(path)` bir dosyanın listesi değişince, `reset()` hepsi
    birden yenilenince yayılır."""

    changed = Signal(str)
    reset = Signal()

    SCAN_INTERVAL_MS = 1000

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._items: Dict[str, List[Diagnostic]] = {}
        self._display: Dict[str, str] = {}
        self._live: Dict[str, int] = {}                      # editörde açık dosyalar (sayaç)
        self._stamps: Dict[str, Tuple[int, int]] = {}        # diskten denetlenenlerin damgası
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

    # ---- sorgu ----------------------------------------------------------- #
    def get(self, path: str) -> List[Diagnostic]:
        return list(self._items.get(_key(path), []))

    def files(self) -> List[str]:
        """Sorunu olan dosyalar (görünen yollarıyla, sıralı)."""
        return sorted((self._display[k] for k, v in self._items.items() if v), key=str.lower)

    def totals(self) -> Tuple[int, int]:
        return counts(d for items in self._items.values() for d in items)

    # ---- denetim --------------------------------------------------------- #
    def builtins(self) -> frozenset:
        """Script'lerin çalışırken hazır bulduğu isimler (API) pyflakes için yerleşik sayılır."""
        if self._builtins is None:
            try:
                from .completion import api_names
                self._builtins = frozenset(api_names())
            except Exception:   # API yüklenemezse tanımsız isim uyarıları fazla çıkar, ama çalışır
                self._builtins = frozenset()
        return self._builtins

    def api(self) -> Mapping[str, object]:
        """Script API nesneleri (çağrı imzası denetimi ve renklendirme için)."""
        if self._api is None:
            try:
                from .completion import script_namespace
                self._api = dict(script_namespace())
            except Exception:
                self._api = {}
        return self._api

    def _search_dirs(self, path: str) -> List[str]:
        """Script çalışırken import yolunda olan proje klasörleri."""
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
        """Ayarlardan kapatılmış uyarı kodları."""
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

    # ---- açık editörler -------------------------------------------------- #
    def attach_live(self, path: str) -> None:
        k = _key(path)
        self._live[k] = self._live.get(k, 0) + 1

    def detach_live(self, path: str) -> None:
        """Editör kapandı: dosya artık diskten denetlenir (proje dışındaysa listeden çıkar)."""
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

    # ---- proje taraması --------------------------------------------------- #
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
        """Görseller/paketler değişti: 'görsel bulunamadı' ve 'modül bulunamadı' için her şeyi yeniden denetle."""
        inspections.clear_module_cache()
        self._stamps.clear()
        self.scan_project()
        if self._ctx.documents is not None:
            for editor in getattr(self._ctx.documents, "editors", lambda: [])():
                editor.schedule_diagnostics()

    def scan_project(self) -> None:
        """Açık olmayan .py dosyalarını diskten denetle; yalnızca değişenler yeniden işlenir."""
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
            self.remove(self._display.get(k, k))   # silinmiş dosyalar

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
