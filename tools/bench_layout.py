"""Plate yerleşimi kıyaslaması: önbellekteki yönlendirilmiş parçalar (cache/work) üzerinde farklı yerleştiriciler.

Kullanım: python tools/bench_layout.py <kit> [<kit> ...]
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import trimesh

from slaprep.config import PRINTERS
from slaprep.io import simplify
from slaprep.layout import CELL, pack, silhouette

CONFIGS = {
    "eski (90°, sol-alt, alan)": dict(yaw_step=90, strategies=("bl",), orders=("area",), random_orders=0),
    "30° açı, sol-alt": dict(yaw_step=30, strategies=("bl",), orders=("area",), random_orders=0),
    "90°, temas": dict(yaw_step=90, strategies=("contact",), orders=("area",), random_orders=0),
    "yeni varsayılan (30°, temas+sol-alt, 5 sıra)": dict(),
    "15° açı, tam arama": dict(yaw_step=15),
}


def load_items(kit: Path, group_filter=None):
    o = kit / "_slaprep"
    d = json.loads((o / "prep.json").read_text(encoding="utf-8"))
    items = {}
    groups = {}
    for n, p in d["parts"].items():
        if not p.get("printed"):
            continue
        f = o / "cache" / "work" / f"{n}.npz"
        if not f.exists():
            continue
        z = np.load(f)
        m = trimesh.Trimesh(z["v"].astype(float), z["f"], process=False)
        items[n] = simplify(m, 30000)
        groups.setdefault(p.get("group") or "", []).append(n)
    return items, groups


def lower_bound(items, W, D, gap):
    g = int(np.ceil(gap / CELL))
    usable = (W / CELL - 2 * g) * (D / CELL - 2 * g)
    tot = 0.0
    for m in items.values():
        mk, _ = silhouette(m)
        # her parça etrafında yarım boşluk payı
        from scipy import ndimage

        r = g // 2
        yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
        tot += ndimage.binary_dilation(np.pad(mk, r), structure=xx * xx + yy * yy <= r * r).sum()
    return tot / usable


W, D, Z = PRINTERS["m7"]
rows = []
for k in sys.argv[1:]:
    kit = Path(k)
    items, groups = load_items(kit)
    for g, names in sorted(groups.items()):
        sub = {n: items[n] for n in names}
        lb = lower_bound(sub, W, D, 3.0)
        label = f"{kit.name}{'/' + g if g else ''}"
        print(f"\n== {label}: {len(sub)} parça, alan alt sınırı {lb:.2f} plate")
        for cname, cfg in CONFIGS.items():
            t = time.time()
            lay = pack(sub, (W, D), 3.0, None, **cfg)
            dt = time.time() - t
            print(f"   {cname:46s} plate={lay.n_plates}  Σyükseklik={lay.time_proxy:6.1f} mm  "
                  f"doluluk=%{lay.utilization*100:4.1f}  sığmayan={len(lay.unplaceable)}  "
                  f"[{lay.strategy}]  {dt:5.1f}s")
            rows.append((label, cname, lay.n_plates, lay.time_proxy, lay.utilization, dt))
