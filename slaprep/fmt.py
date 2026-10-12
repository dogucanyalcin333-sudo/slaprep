"""Hafif biçimlendirme yardımcıları (uygulama açılırken yüklenir: ağır kütüphane içe aktarmaz)."""
from __future__ import annotations

import math


def fmt_duration(sec: float) -> str:
    """Saniye → '5 sa 07 dk' / '42 dk'."""
    m = int(round(sec / 60))
    return f"{m // 60} sa {m % 60:02d} dk" if m >= 60 else f"{m} dk"


def nice_step(total_mm: float, target_ticks: int = 8) -> float:
    """Cetvel için okunaklı etiket aralığı (1/2/5 × 10^k mm), ~target_ticks etiket olacak şekilde."""
    raw = max(total_mm, 1e-6) / target_ticks
    k = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if raw <= m * k:
            return float(m * k)
    return float(10 * k)
