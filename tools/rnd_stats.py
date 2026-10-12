"""prep.json'lardan Ar-Ge özet istatistikleri: plate, reçine, delik, çanak/cep kapsaması."""
import json
import sys
from pathlib import Path

for k in sys.argv[1:]:
    d = json.loads((Path(k) / "_slaprep" / "prep.json").read_text(encoding="utf-8"))
    pr = [p for p in d["parts"].values() if p.get("printed")]
    plans = [pl for p in pr for pl in p.get("hole_plan", [])]
    cups = sum(pl.get("cups", 0) for pl in plans)
    cv = sum(pl.get("cups_vented", 0) for pl in plans)
    pk = sum(pl.get("pockets", 0) for pl in plans)
    pv = sum(pl.get("pockets_vented", 0) for pl in plans)
    holes = [h for p in pr for h in p.get("holes", [])]
    area = sum(3.14159 * (h["d"] / 2) ** 2 for h in holes)
    print(f"{Path(k).name:32s} plate={len(d['plates'])}  reçine {sum(p['vol_before_ml'] for p in pr):.0f}→"
          f"{sum(p['vol_after_ml'] for p in pr):.1f} ml  delik={len(holes)} (açıklık {area:.0f} mm²)  "
          f"vantuz çanağı {cv}/{cups} delikli  reçine cebi {pv}/{pk}")
