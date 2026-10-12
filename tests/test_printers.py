"""Yazıcı kütüphanesi: profiller tutarlı mı, kullanıcı kendi yazıcısını ekleyebiliyor mu?

Yanlış plate boyutu sessiz bir hatadır (parça sığmaz ya da gereksiz plate açılır), bu yüzden
kütüphanenin biçimi burada denetlenir. Değerlerin doğruluğu üreticiden gelir, test edilemez.
"""
from __future__ import annotations

import json

import pytest

from slaprep import config
from slaprep.config import (CUSTOM_PRINTER, DEFAULT_PRINTER, Settings, add_custom_printer, parse_build_volume,
                            printer_name, printers, resolve_printer)
from slaprep.resin import CURRENCIES, ResinPrefs, default_currency


def test_library_entries_are_well_formed():
    data = json.loads((config._PRINTER_FILE).read_text(encoding="utf-8"))
    entries = data["printers"]
    assert len(entries) >= 20, "kütüphane fazla küçük"
    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids)), "yinelenen yazıcı id'si"
    for e in entries:
        x, y, z = e["volume"]
        assert e["id"] == e["id"].lower() and " " not in e["id"], f"id biçimi: {e['id']}"
        assert e.get("name"), f"ad yok: {e['id']}"
        assert 50 < x < 600 and 30 < y < 400 and 50 < z < 600, f"akla yatkın olmayan plate: {e['id']}"
        assert x >= y, f"X kısa kenar olmalı (X/Y plate): {e['id']}"


def test_default_printer_exists_and_old_short_name_still_works():
    assert DEFAULT_PRINTER in printers()
    assert resolve_printer("m7") == DEFAULT_PRINTER          # eski kitlerde böyle kayıtlı
    assert resolve_printer("boyle-bir-yazici-yok") is None
    assert resolve_printer(None) is None


def test_settings_default_matches_default_printer():
    s = Settings()
    assert s.printer == DEFAULT_PRINTER and s.build_volume == printers()[DEFAULT_PRINTER]


def test_old_kit_yaml_with_short_printer_name_is_migrated():
    s = Settings.from_dict({"printer": "m7", "build_volume": [223.0, 126.0, 230.0]})
    assert s.printer == DEFAULT_PRINTER


@pytest.mark.parametrize("text,expected", [
    ("218x123x230", (218.0, 123.0, 230.0)),
    ("218X123X230", (218.0, 123.0, 230.0)),
    ("218*123*230", (218.0, 123.0, 230.0)),
    ("218,123,230", (218.0, 123.0, 230.0)),
    ("153.4x77x165", (153.4, 77.0, 165.0)),
])
def test_parse_build_volume(text, expected):
    assert parse_build_volume(text) == expected


@pytest.mark.parametrize("bad", ["218x123", "", "abc", "218x123x0", "-1x2x3", "218x123x230x5"])
def test_parse_build_volume_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        parse_build_volume(bad)


def test_user_can_add_own_printer(tmp_path):
    """conftest kullanıcı tercihlerini tmp dizine aldığı için gerçek ayarlar kirlenmez."""
    assert "my-printer" not in printers()
    add_custom_printer("my-printer", "Benim Yazıcım", (200.0, 100.0, 180.0))
    assert printers()["my-printer"] == (200.0, 100.0, 180.0)
    assert printer_name("my-printer") == "Benim Yazıcım"
    assert resolve_printer("my-printer") == "my-printer"


def test_custom_printer_id_is_not_translated():
    """Plate boyutu elle verildiğinde kaydedilen değer sabit bir anahtar olmalı (çeviri değil)."""
    assert CUSTOM_PRINTER == "custom"


def test_default_currency_is_sane():
    cur = default_currency()
    assert cur and cur.isalpha() and cur.upper() == cur
    assert ResinPrefs().currency == cur
    assert "USD" in CURRENCIES and "EUR" in CURRENCIES and "TL" in CURRENCIES
