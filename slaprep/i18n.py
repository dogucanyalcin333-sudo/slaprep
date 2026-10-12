"""Arayüz dili: metinler Türkçe yazılır, çeviri sözlüğünden İngilizceye çevrilir.

Kullanım::

    from .i18n import t
    console.print(t("Klasör bulunamadı"))
    label = t("{n} parça").format(n=3)

Anahtar, metnin Türkçesidir: çeviri bulunamazsa Türkçesi görünür (yani çevrilmemiş bir metin
hataya değil, eski davranışa düşer). Çeviriler ``slaprep/locale/<dil>.json`` dosyalarında
``{"Türkçe metin": "English text"}`` biçiminde durur.

Dil sırası: ``set_lang()`` (CLI ``--lang``) → ``SLAPREP_LANG`` ortam değişkeni →
kullanıcı tercihi (``prefs``) → işletim sistemi dili → Türkçe.

Çeviri ilerlemesini ölçmek için ``SLAPREP_I18N_DEBUG=1``: çevirisi olmayan metinler
``missing_translations.txt`` dosyasına yazılır (bkz. ``tools/i18n_report.py``).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import prefs

LANGS = ("tr", "en")
DEFAULT_LANG = "tr"
LOCALE_DIR = Path(__file__).parent / "locale"

_lang: str | None = None
_catalog: dict[str, str] = {}
_missing: set[str] = set()


def _os_lang() -> str | None:
    """İşletim sisteminin arayüz dili (tr/en); bulunamazsa None."""
    try:
        import locale

        code = locale.getdefaultlocale()[0] or ""
    except Exception:
        code = ""
    code = (code or os.environ.get("LANG", "")).lower()
    for lang in LANGS:
        if code.startswith(lang):
            return lang
    return "en" if code else None


def resolve_lang() -> str:
    """Kullanılacak dili bul (set_lang çağrılmadıysa)."""
    env = (os.environ.get("SLAPREP_LANG") or "").strip().lower()[:2]
    if env in LANGS:
        return env
    saved = prefs.read("lang")
    if saved in LANGS:
        return saved
    return _os_lang() or DEFAULT_LANG


def set_lang(lang: str | None, *, save: bool = False) -> str:
    """Dili ayarla (None: yeniden çöz). ``save=True`` ise kullanıcı tercihine yazar."""
    global _lang, _catalog
    _lang = lang if lang in LANGS else resolve_lang()
    _catalog = _load_catalog(_lang)
    if save:
        prefs.write("lang", _lang)
    return _lang


def lang() -> str:
    """Yürürlükteki dil."""
    if _lang is None:
        set_lang(None)
    return _lang or DEFAULT_LANG


def _load_catalog(lang: str) -> dict[str, str]:
    if lang == DEFAULT_LANG:
        return {}
    try:
        data = json.loads((LOCALE_DIR / f"{lang}.json").read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str) and v}


def t(text: str) -> str:
    """Metni yürürlükteki dile çevir; çevirisi yoksa Türkçesini döndür."""
    if _lang is None:
        set_lang(None)
    if not _catalog:
        return text
    out = _catalog.get(text)
    if out is None:
        if os.environ.get("SLAPREP_I18N_DEBUG"):
            _note_missing(text)
        return text
    return out


def _note_missing(text: str) -> None:
    if text in _missing:
        return
    _missing.add(text)
    try:
        with (Path.cwd() / "missing_translations.txt").open("a", encoding="utf-8") as f:
            f.write(text.replace("\n", "\\n") + "\n")
    except Exception:
        pass


def catalog_stats(lang: str = "en") -> tuple[int, int]:
    """(çevrilmiş, sözlükteki toplam) — ilerleme raporu için."""
    cat = _load_catalog(lang)
    return len(cat), len(cat)
