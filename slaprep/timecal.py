"""Baskı süresi ayarı: kullanıcının ölçtüğü gerçek plate sürelerinden süre modelinin katsayıları.

Model (layout.TimeModel): süre = (plate yüksekliği / katman kalınlığı) × katman süresi + plate başına ek süre.
Tek ölçümle iki bilinmeyen ayrılamaz: ek süre kullanıcının değerinde tutulur, katman süresi bulunur. Yükseklikleri
belirgin farklı en az iki ölçüm olunca ikisi birlikte (en küçük kareler) bulunur. Ölçümler yazıcıya/reçineye aittir,
kite değil: ~/.slaprep_prefs.json ("time_calibration").

İlk ölçüm (2026-10-07): Rango Bottle tek başına (ayrı plate), 82.7 mm, gerçek 3 sa 31 dk — model 6.5 sn/katman +
15 dk ile 3 sa 14 dk diyordu (%8 kısa).
"""
from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field

from . import prefs
from .i18n import t

KEY = "time_calibration"


@dataclass
class CalPoint:
    height_mm: float               # plate'in en yüksek noktası (destek yüksekliği dahil)
    minutes: float                 # gerçek baskı süresi
    layer_height: float = 0.05
    label: str = ""                # ör. "Rango_STL_Repaired / ayrı/plate_01"
    added: float = field(default_factory=time.time)

    @property
    def layers(self) -> float:
        return self.height_mm / self.layer_height


def parse_duration(text: str) -> float | None:
    """'3:31', '3 sa 31 dk', '3s 31d', '211', '211 dk', '3.5 sa' → dakika; anlaşılmazsa None."""
    s = str(text).strip().lower().replace(",", ".")
    if not s:
        return None
    m = re.fullmatch(r"(\d+):(\d{1,2})(?::(\d{1,2}))?", s)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2)) + (int(m.group(3)) / 60 if m.group(3) else 0)
    m = re.fullmatch(r"(?:(\d+(?:\.\d+)?)\s*(?:sa|saat|s|h)\.?)?\s*(?:(\d+(?:\.\d+)?)\s*(?:dk|dakika|d|m|min)\.?)?", s)
    if m and (m.group(1) or m.group(2)):
        return float(m.group(1) or 0) * 60 + float(m.group(2) or 0)
    try:
        return float(s)                                  # yalın sayı: dakika
    except ValueError:
        return None


def load_points() -> list[CalPoint]:
    out = []
    for d in prefs.read(KEY, []) or []:
        try:
            out.append(CalPoint(**{k: d[k] for k in ("height_mm", "minutes", "layer_height", "label", "added")
                                   if k in d}))
        except (TypeError, KeyError):
            continue
    return out


def save_points(points: list[CalPoint]):
    prefs.write(KEY, [asdict(p) for p in points])


def add_point(p: CalPoint) -> list[CalPoint]:
    """Aynı etiketli eski ölçümün yerine geçer (aynı plate'i yeniden girince çift sayılmasın)."""
    pts = [q for q in load_points() if not (p.label and q.label == p.label)] + [p]
    save_points(pts)
    return pts


def fit(points: list[CalPoint], overhead_min: float) -> tuple[float, float] | None:
    """(katman süresi sn, plate başına ek dk). Tek ölçüm ya da benzer yükseklikler: ek süre sabit, katman süresi
    bulunur. Farklı yükseklikler: ikisi birlikte; ek süre makul aralık dışına (0–45 dk) düşerse sabit tutulur."""
    pts = [p for p in points if p.height_mm > 0 and p.minutes > 0 and p.layer_height > 0]
    if not pts:
        return None
    L = [p.layers for p in pts]
    M = [p.minutes for p in pts]

    def fixed_overhead(ov):
        # en küçük kareler, kesişim sabit: lt = Σ L·(M−ov) / Σ L²  (dk/katman → sn)
        lt = sum(l * (m - ov) for l, m in zip(L, M)) / sum(l * l for l in L) * 60.0
        return lt, ov

    if len(pts) >= 2 and max(L) > 1.25 * min(L):
        n = len(pts)
        ml, mm = sum(L) / n, sum(M) / n
        slope = sum((l - ml) * (m - mm) for l, m in zip(L, M)) / sum((l - ml) ** 2 for l in L)
        ov = mm - slope * ml
        if 0.0 <= ov <= 45.0 and slope > 0:
            return slope * 60.0, ov
    return fixed_overhead(overhead_min)


def describe(points: list[CalPoint], result: tuple[float, float] | None) -> str:
    if not points or result is None:
        return t("Süre ayarı yok: bir plate'in gerçek baskı süresini girerseniz tahminler ona göre ayarlanır.")
    lt, ov = result
    return (t("Süre ayarı {n} ölçümden: katman {layer} sn, plate başına +{over} dk").format(
        n=len(points), layer=f"{lt:.2f}", over=f"{ov:.0f}")
        + (t(" (tek ölçüm: ek süre sabit tutuldu)") if len(points) == 1 else ""))


def calibrated(overhead_min: float) -> tuple[float, float] | None:
    """Kayıtlı ölçümlerden (katman sn, ek dk); ölçüm yoksa None."""
    return fit(load_points(), overhead_min)
