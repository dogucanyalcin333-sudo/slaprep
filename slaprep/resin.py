"""Reçine miktarı ve maliyeti: plate başına / kit toplamı, şişe sayısı.

Fiyatlar kite değil kullanıcıya aittir (her kitte yeniden girilmesin): ~/.slaprep_prefs.json. Hesap prep.json'daki
hazır (hollow + delikli) parça hacimlerinden yapılır; destek ve kayıp için yüzde pay eklenir. "Ayrı plate" grubu
(ör. şeffaf reçine) ayrı satır ve isterse ayrı fiyatla gösterilir.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, fields

from . import prefs
from .config import SEPARATE_GROUP

SEPARATE_PREFIX = SEPARATE_GROUP + "/"     # plate etiketi: "ayrı/plate_01"


@dataclass
class ResinPrefs:
    price_l: float = 0.0             # 1 litre fiyatı; 0 = maliyet gösterilmez, yalnız ml
    bottle_ml: float = 1000.0
    extra_pct: float = 15.0          # destek + kayıp payı (CHITUBOX'ta destek eklendikten sonra hacmi oradan okuyun)
    separate_price_l: float = 0.0    # "ayrı plate" reçinesinin litre fiyatı; 0 = ana reçineyle aynı
    currency: str = "TL"


def load_prefs() -> ResinPrefs:
    d = prefs.read("resin", {})
    known = {f.name for f in fields(ResinPrefs)}
    try:
        return ResinPrefs(**{k: v for k, v in (d if isinstance(d, dict) else {}).items() if k in known})
    except TypeError:
        return ResinPrefs()


def save_prefs(p: ResinPrefs):
    prefs.write("resin", asdict(p))


def _ml(p: dict) -> float:
    return float(p.get("vol_after_ml", p.get("volume_ml", 0.0)) or 0.0)


def resin_summary(d: dict, prefs: ResinPrefs) -> dict:
    """prep.json sözlüğünden reçine özeti.

    Dönüş: plates {etiket: {net_ml, ml, cost}}, groups {"ana"|"ayrı": {net_ml, ml, bottles, cost}}, total {...}.
    ml = net × (1 + pay); cost None (fiyat girilmemişse)."""
    k = 1.0 + max(prefs.extra_pct, 0.0) / 100.0
    printed = {n: p for n, p in (d.get("parts") or {}).items() if p.get("printed")}

    def price(label: str) -> float:
        if label.startswith(SEPARATE_PREFIX) and prefs.separate_price_l > 0:
            return prefs.separate_price_l
        return prefs.price_l

    def cost(ml: float, pl: float):
        return round(ml / 1000.0 * pl, 2) if pl > 0 else None

    plates: dict[str, dict] = {}
    for n, p in printed.items():
        label = p.get("plate") or ""
        e = plates.setdefault(label, dict(net_ml=0.0, parts=[]))
        e["net_ml"] += _ml(p)
        e["parts"].append(n)
    groups: dict[str, dict] = {}
    for label, e in plates.items():
        e["net_ml"] = round(e["net_ml"], 1)
        e["ml"] = round(e["net_ml"] * k, 1)
        e["cost"] = cost(e["ml"], price(label))
        g = groups.setdefault(SEPARATE_GROUP if label.startswith(SEPARATE_PREFIX) else "ana", dict(net_ml=0.0, ml=0.0))
        g["net_ml"] += e["net_ml"]
        g["ml"] += e["ml"]
    for name, g in groups.items():
        g["net_ml"], g["ml"] = round(g["net_ml"], 1), round(g["ml"], 1)
        g["bottles"] = math.ceil(g["ml"] / prefs.bottle_ml - 1e-9) if prefs.bottle_ml > 0 and g["ml"] > 0 else 0
        g["cost"] = cost(g["ml"], prefs.separate_price_l if name == SEPARATE_GROUP and prefs.separate_price_l > 0
                         else prefs.price_l)
    costs = [g["cost"] for g in groups.values()]
    total = dict(net_ml=round(sum(g["net_ml"] for g in groups.values()), 1),
                 ml=round(sum(g["ml"] for g in groups.values()), 1),
                 cost=round(sum(costs), 2) if costs and all(c is not None for c in costs) else None)
    return dict(plates=plates, groups=groups, total=total, extra_pct=prefs.extra_pct, currency=prefs.currency,
                bottle_ml=prefs.bottle_ml)


def run_summary(run, prefs: ResinPrefs | None = None) -> dict:
    """Pipeline Run nesnesinden (rapor yazılırken) aynı özet."""
    d = {"parts": {n: dict(printed=r.printed, vol_after_ml=r.vol_after, plate=r.plate)
                   for n, r in run.results.items()}}
    return resin_summary(d, prefs or load_prefs())


def resin_cell(e: dict | None, currency: str) -> str:
    """Tablo hücresi: "≈ 85 ml · 102.00 TL"."""
    if not e:
        return "–"
    return f"≈ {e['ml']:.0f} ml" + (f" · {e['cost']:.2f} {currency}" if e.get("cost") is not None else "")


def summary_lines(rs: dict) -> list[str]:
    """Özet / konsol için kısa satırlar."""
    cur = rs["currency"]
    t = rs["total"]
    if not t["net_ml"]:
        return []
    out = [f"Reçine: {t['net_ml']:.0f} ml parça + %{rs['extra_pct']:g} destek/kayıp ≈ {t['ml']:.0f} ml"
           + (f" ≈ {t['cost']:.2f} {cur}" if t["cost"] is not None else "")]
    for name, g in rs["groups"].items():
        if len(rs["groups"]) > 1 or name == SEPARATE_GROUP:
            out.append(f"  {'ayrı plate reçinesi' if name == SEPARATE_GROUP else 'ana reçine'}: ≈ {g['ml']:.0f} ml, "
                       f"{g['bottles']} şişe ({rs['bottle_ml']:g} ml)"
                       + (f", ≈ {g['cost']:.2f} {cur}" if g["cost"] is not None else ""))
        else:
            out[0] += f" — {g['bottles']} şişe ({rs['bottle_ml']:g} ml)"
    if t["cost"] is None:
        out.append("  (maliyet için uygulamada Reçine kartına litre fiyatını girin)")
    return out
