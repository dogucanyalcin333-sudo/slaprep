"""Plate yerleşimi kıyaslaması — baskı süresi odaklı. Önceki hazırlıkların yönlendirilmiş parçaları (cache/work) üzerinde
eski yerleşimi (önce en az plate, yükseklik sıkıştırması yok) yeni yerleşimle karşılaştırır.

Kullanım: .venv\\Scripts\\python tools\\bench_plates.py "<kit>" ["<kit>" ...]
Süre tahmini: 0.05 mm katman × 6.5 sn + plate başına 15 dk (slaprep varsayılanları).
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slaprep.config import PRINTERS  # noqa: E402
from slaprep.io import simplify  # noqa: E402
from slaprep.layout import TimeModel, fmt_duration, pack  # noqa: E402

CONFIGS = {
    "eski (önce plate, sıkıştırmasız)": dict(goal="plates", compact=False),
    "yeni, hedef: en az plate": dict(goal="plates"),
    "yeni, hedef: en kısa süre": dict(goal="time"),
    "en kısa süre + derin arama (15°, 8 sıra)": dict(goal="time", yaw_step=15, random_orders=6),
}
if "--sadece-sure" in sys.argv:
    sys.argv.remove("--sadece-sure")
    CONFIGS = {k: v for k, v in CONFIGS.items() if "süre" in k}


def load_groups(kit: Path):
    o = kit / "_slaprep"
    d = json.loads((o / "prep.json").read_text(encoding="utf-8"))
    groups: dict[str, dict] = {}
    for n, p in d["parts"].items():
        f = o / "cache" / "work" / f"{n}.npz"
        if not p.get("printed") or not f.exists():
            continue
        z = np.load(f)
        m = trimesh.Trimesh(z["v"].astype(float), z["f"], process=False)
        groups.setdefault(p.get("group") or "", {})[n] = simplify(m, 30000)
    return groups


def run(groups: dict, cfg: dict, tm: TimeModel):
    W, D, _ = PRINTERS["m7"]
    plates, sec, heights = 0, 0.0, []
    for items in groups.values():
        lay = pack(items, (W, D), 3.0, **cfg, time_model=tm)
        plates += lay.n_plates
        sec += lay.seconds(tm)
        heights += [round(lay.heights[k]) for k in sorted(lay.heights)]
    return plates, sec, heights


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    tm = TimeModel()
    tot = {}
    for k in sys.argv[1:]:
        kit = Path(k)
        groups = load_groups(kit)
        variants = {"klasörlere göre": groups} if len(groups) > 1 else {"": groups}
        if len(groups) > 1:
            variants["tek grup (klasörsüz)"] = {"": {n: m for g in groups.values() for n, m in g.items()}}
        print(f"\n== {kit.name} ({sum(len(g) for g in groups.values())} parça)", flush=True)
        for vname, gr in variants.items():
            for cname, cfg in CONFIGS.items():
                t0 = time.time()
                n, sec, hs = run(gr, cfg, tm)
                label = f"{vname + ' / ' if vname else ''}{cname}"
                print(f"  {label:55s} {n} plate  {fmt_duration(sec):>12s}  yükseklikler {hs}  ({time.time() - t0:.0f}s)",
                      flush=True)
                tot.setdefault(label if not vname else f"{cname} [{vname}]", []).append((n, sec))
    print("\nToplam (tüm kitler, klasörlere göre/tek grup ayrı):")
    for label, v in tot.items():
        print(f"  {label:70s} {sum(x[0] for x in v):3d} plate  {fmt_duration(sum(x[1] for x in v))}")
