"""Çevrilecek metinleri koddan çıkarır ve çeviri sözlüğünü günceller.

    python tools\\i18n_extract.py                 # rapor: modül başına çevrilmiş / kalan
    python tools\\i18n_extract.py --write         # locale\\en.json'a eksik anahtarları "" olarak ekler
    python tools\\i18n_extract.py --list gui.py   # bir modülde çevrilmemiş metinleri yaz

Docstring'ler ve yorumlar ayıklanır (ast ile): sadece çalışma anında kullanıcıya görünebilecek
metin sabitleri sayılır. f-string'ler çeviri sözlüğüne giremez (içinde değişken var); onlar
``--fstrings`` ile ayrı listelenir, elle ``t("...").format(...)`` biçimine çevrilmeleri gerekir.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "slaprep"
LOCALE = PKG / "locale" / "en.json"

TR = re.compile(r"[çğıöşüÇĞİÖŞÜ]")
# Türkçe harfi olmayan tek sözcüklü metinler (durum, etiket): bunlar da kullanıcıya görünebilir
TR_WORDS = re.compile(r"\b(?:yok|var|dolu|hata|bitti|bekliyor|parca|plate|kavite|montaj|destek|delik|"
                      r"analiz|hazir|kit|olcek|sure|recine|duvar|taban|kaide)\b", re.I)
SKIP_EXACT = {"", " ", "\n", "utf-8", "replace", "w", "r", "a", "rb", "wb", "ignore", "strict"}
IGNORE_FILE = Path(__file__).parent / "i18n_ignore.txt"


def load_ignore() -> set[str]:
    """Çevrilmemesi gereken metinler: veri anahtarları, klasör adları, biçim kodları."""
    try:
        lines = IGNORE_FILE.read_text(encoding="utf-8").splitlines()
    except Exception:
        return set()
    return {ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")}


IGNORE = load_ignore()


def is_candidate(s: str) -> bool:
    """Kullanıcıya görünen metin olabilir mi? (Gevşek filtre: elle gözden geçirilir.)"""
    if s in SKIP_EXACT or s in IGNORE or len(s) < 2:
        return False
    if s.startswith(("--", "<", "{", "[", "#")) or s.endswith((".py", ".json", ".yaml", ".stl", ".html")):
        return False
    if not re.search(r"[A-Za-zçğıöşüÇĞİÖŞÜ]{2}", s):
        return False                                   # sayı / simge / biçim kodu
    if re.fullmatch(r"[\w.\-/\\%:]+", s) and not TR.search(s) and not TR_WORDS.search(s):
        return False                                   # anahtar / yol / tek sözcüklü İngilizce kod
    return bool(TR.search(s) or TR_WORDS.search(s) or " " in s.strip())


class Collector(ast.NodeVisitor):
    """Metin sabitlerini toplar; docstring'leri ve t() içindekileri ayırır."""

    def __init__(self) -> None:
        self.strings: list[tuple[int, str]] = []       # çevrilecek (henüz t() içinde değil)
        self.wrapped: list[tuple[int, str]] = []       # t("...") içinde
        self.fstrings: list[tuple[int, str]] = []      # f"..." — elle çevrilecek
        self._docstrings: set[int] = set()

    def _mark_docstring(self, node) -> None:
        body = getattr(node, "body", None)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            self._docstrings.add(id(body[0].value))

    def visit_Module(self, node):
        self._mark_docstring(node)
        self.generic_visit(node)

    visit_ClassDef = visit_FunctionDef = visit_AsyncFunctionDef = lambda self, node: (
        self._mark_docstring(node), self.generic_visit(node))[1]

    def visit_Call(self, node):
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
        if name == "t" and node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str):
            self.wrapped.append((node.lineno, node.args[0].value))
            for arg in node.args[1:]:
                self.visit(arg)
            for kw in node.keywords:
                self.visit(kw)
            return
        self.generic_visit(node)

    def visit_Constant(self, node):
        if isinstance(node.value, str) and id(node) not in self._docstrings and is_candidate(node.value):
            self.strings.append((node.lineno, node.value))

    def visit_JoinedStr(self, node):
        src = "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
        if is_candidate(src):
            self.fstrings.append((node.lineno, src.strip()))
        for v in node.values:
            if not isinstance(v, ast.Constant):
                self.visit(v)


def collect(path: Path) -> Collector:
    c = Collector()
    c.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    return c


def load_catalog() -> dict[str, str]:
    try:
        return json.loads(LOCALE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> int:
    ap = argparse.ArgumentParser(description="i18n metin çıkarma")
    ap.add_argument("--write", action="store_true", help="eksik anahtarları en.json'a boş değerle ekle")
    ap.add_argument("--list", metavar="MODUL", help="bu modülde çevrilmemiş metinleri listele")
    ap.add_argument("--fstrings", action="store_true", help="f-string'leri listele (elle çevrilecek)")
    args = ap.parse_args()

    cat = load_catalog()
    files = sorted(PKG.glob("*.py"))
    total_todo = total_wrapped = 0
    rows = []
    missing_all: list[str] = []

    for f in files:
        if f.name in ("i18n.py",):
            continue
        c = collect(f)
        todo = [s for _, s in c.strings]
        done = [s for _, s in c.wrapped if cat.get(s)]
        # t() ile sarılmış ama çevirisi olmayanlar da sözlüğe girmeli
        missing_all += [s for s in todo if s not in cat] + [s for _, s in c.wrapped if s not in cat]
        total_todo += len(todo)
        total_wrapped += len(c.wrapped)
        if todo or c.wrapped or c.fstrings:
            rows.append((f.name, len(c.wrapped), len(done), len(todo), len(c.fstrings)))

        if args.list and f.name == args.list:
            for ln, s in c.strings:
                print(f"{f.name}:{ln}: {s!r}")
        if args.fstrings and c.fstrings:
            for ln, s in c.fstrings:
                print(f"{f.name}:{ln}: f{s!r}")

    if not args.list and not args.fstrings:
        print(f"{'modül':<18}{'t() ile':>9}{'çevrili':>9}{'kalan':>8}{'f-string':>10}")
        for name, wrapped, done, todo, fs in rows:
            print(f"{name:<18}{wrapped:>9}{done:>9}{todo:>8}{fs:>10}")
        print(f"{'TOPLAM':<18}{total_wrapped:>9}{len(cat):>9}{total_todo:>8}")

    if args.write:
        for s in dict.fromkeys(missing_all):
            cat.setdefault(s, "")
        LOCALE.parent.mkdir(parents=True, exist_ok=True)
        LOCALE.write_text(json.dumps(cat, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\n{LOCALE.relative_to(ROOT)}: {len(cat)} anahtar "
              f"({sum(1 for v in cat.values() if not v)} çevrilmemiş)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
