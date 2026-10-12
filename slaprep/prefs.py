"""Kite değil kullanıcıya ait tercihler: ~/.slaprep_prefs.json (reçine fiyatı, gece kuyruğu, düşük öncelik).

Her bölüm kendi anahtarında durur; bir bölümü yazmak diğerlerini korur.
"""
from __future__ import annotations

import json
from pathlib import Path

PREFS = Path.home() / ".slaprep_prefs.json"


def read(key: str, default=None):
    try:
        return json.loads(PREFS.read_text(encoding="utf-8")).get(key, default)
    except Exception:
        return default


def write(key: str, value):
    try:
        d = json.loads(PREFS.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            d = {}
    except Exception:
        d = {}
    d[key] = value
    tmp = PREFS.with_suffix(".tmp")          # yarım dosya kalmasın
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PREFS)
