"""Dil katmanı: çeviri sözlüğü kodla tutarlı mı?

En önemli kontrol, yer tutucular ({n}, {mm}…): çeviride eksik ya da fazla yer tutucu olursa
uygulama çalışma anında KeyError / IndexError ile patlar. Bu test onu yayından önce yakalar.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from slaprep import i18n

PKG = Path(i18n.__file__).parent
PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z_0-9]*)[^}]*\}")


def _catalog(lang: str = "en") -> dict[str, str]:
    return json.loads((PKG / "locale" / f"{lang}.json").read_text(encoding="utf-8"))


def _wrapped_keys() -> set[str]:
    """Kodda t("...") ile sarılmış bütün metinler."""
    keys: set[str] = set()
    for f in PKG.glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            if name == "t" and node.args and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                keys.add(node.args[0].value)
    return keys


def test_en_catalog_is_valid_json_without_empty_values():
    cat = _catalog()
    assert cat, "en.json boş"
    empty = [k for k, v in cat.items() if not str(v).strip()]
    assert not empty, f"çevirisi boş kalan anahtarlar: {empty[:5]}"


@pytest.mark.parametrize("lang", ["en"])
def test_placeholders_match_between_source_and_translation(lang):
    for src, dst in _catalog(lang).items():
        assert set(PLACEHOLDER.findall(src)) == set(PLACEHOLDER.findall(dst)), \
            f"yer tutucular uyuşmuyor:\n  {src!r}\n  {dst!r}"


def test_catalog_has_no_keys_that_code_never_uses():
    """Sözlükte kodda olmayan anahtar varsa metin değişmiş ve çeviri boşa düşmüş demektir."""
    unused = sorted(set(_catalog()) - _wrapped_keys())
    assert not unused, f"kodda kullanılmayan çeviri anahtarları: {unused[:5]}"


def test_translation_is_applied_and_falls_back():
    i18n.set_lang("en")
    try:
        assert i18n.t("bitti") == "done"
        assert i18n.t("böyle bir metin yok") == "böyle bir metin yok"      # çevirisi yok: Türkçesi
    finally:
        i18n.set_lang("tr")
    assert i18n.t("bitti") == "bitti"


def test_data_keys_are_not_translated():
    """Veri anahtarları (JSON'a yazılan, kodda karşılaştırılan değerler) çevrilmemeli."""
    cat = _catalog()
    ignore = (Path(__file__).parent.parent / "tools" / "i18n_ignore.txt").read_text(encoding="utf-8")
    data_keys = {ln.strip() for ln in ignore.splitlines() if ln.strip() and not ln.startswith("#")}
    # batch/scan durumları sözlükte var (ekranda gösterilir) ama kodda sabit olarak kalmalı:
    # burada sadece klasör adı ve yöntem değerleri gibi gerçekten çevrilmemesi gerekenleri kontrol ediyoruz
    forbidden = {"ayrı", "düzlemsel", "temas+duvar", "küme", "kök", "ek yüzeyi", "kenetlenme"} & data_keys
    assert not (forbidden & set(cat)), \
        f"veri anahtarı çeviri sözlüğüne girmiş: {sorted(forbidden & set(cat))}"
