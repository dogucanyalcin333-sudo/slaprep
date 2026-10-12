"""Bir kitin yönlendirilmiş parçalarının taban izi ve yüksekliklerini listeler."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from bench_layout import load_items  # noqa: E402
from slaprep.layout import CELL, silhouette  # noqa: E402
from slaprep.orient import min_area_rect  # noqa: E402

items, _ = load_items(Path(sys.argv[1]))
tot = 0
for n, m in sorted(items.items(), key=lambda kv: -kv[1].area):
    mk, _ = silhouette(m)
    a = mk.sum() * CELL * CELL
    tot += a
    w, d, _ = min_area_rect(m.vertices[:, :2])
    print(f"{n:22s} iz={a:7.0f} mm²  kutu={w:5.0f}×{d:4.0f}  yükseklik={m.bounds[1][2]:5.0f}")
print("toplam iz:", round(tot), "mm², M7 plate:", 223 * 126)
