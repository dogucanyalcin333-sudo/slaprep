"""Regresyon seti: bilinen kitlerde montaj sonucunu beklenenle karşılaştırır.

Montaj/varyant/otomatik montaj kodunda her değişiklikten sonra çalıştırın; daha önce doğru çözülen bir kit
bozulduysa burada görünür.

Kullanım (slaprep klasöründen):
    .venv\\Scripts\\python tools\\regress.py                 # hepsi
    .venv\\Scripts\\python tools\\regress.py --quick         # yavaş kitler (slow: true) hariç
    .venv\\Scripts\\python tools\\regress.py --only radahn,rango

Sadece yükleme + montaj adımı çalışır (önbellekli, ayar başına ayrı önbellek dosyası); kitlerin analysis.json,
rapor ve plate çıktılarına dokunmaz. Sonuçlar tools/regress_out/ altına yazılır (son.json + zaman damgalı kopya);
bir önceki çalıştırmaya göre değişen ölçümler de listelenir. Beklenenler tools/regress_kits.yaml'da.
Çıkış kodu: tüm kontroller geçtiyse 0, değilse 1.
"""
from __future__ import annotations

import argparse
import copy
import gc
import json
import sys
import time
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from slaprep.assembly import AXES  # noqa: E402
from slaprep.config import Settings  # noqa: E402
from slaprep.pipeline import Run  # noqa: E402

OUT = Path(__file__).resolve().parent / "regress_out"


def _run(entry: dict, log) -> Run:
    s = Settings()
    for k, v in (entry.get("settings") or {}).items():
        setattr(s, k, v)
    run = Run(Path(entry["path"]), s, log=log)
    run.load()
    run.assemble()
    return run


def measure_assembly(entry: dict, log) -> dict:
    run = _run(entry, log)
    asm = run.asm
    sel = set(asm.selected)
    return dict(
        mode=asm.mode,
        up=("+" if asm.up_sign > 0 else "-") + AXES[asm.up_axis],
        parts=len(run.parts),
        placed=sum(1 for n in asm.selected if asm.placed.get(n)),
        height=round(run.height_before, 2),
        figure_height=round(run.figure_height_before, 2),
        selected=sorted(sel),
        unselected=sorted(set(run.parts) - sel),
        unplaced=sorted(n for n in asm.selected if not asm.placed.get(n)) if asm.mode != "bilinmiyor" else [],
        base=list(asm.base_parts),
        max_penetration=round(asm.max_penetration, 2),
        groups=[sorted(g) for g in getattr(asm, "groups", None) or []],
        junk=list(getattr(asm, "junk_parts", None) or []),
        repaired=sorted(n for n, p in run.parts.items() if p.repaired),
        repair_failed=sorted(n for n, p in run.parts.items() if p.repair_failed),
        seconds=run.timings.get("montaj"),
    )


def measure_auto_synthetic(entry: dict, log) -> dict:
    """Montajı bilinen kiti her parça orijinde olacak şekilde boz, otomatik montajla geri bulmayı dene."""
    from slaprep.autoassemble import auto_assemble
    from slaprep.geom import DistanceField, sample_surface

    run = _run(entry, log)
    asm = run.asm
    names = [n for n in asm.selected if asm.placed[n]]
    parts, shift = {}, {}
    for n in names:
        p = copy.copy(run.parts[n])
        m = p.lite.copy()
        m.apply_translation(asm.offsets[n])                       # gerçek montaj konumu
        c = (m.bounds[0] + m.bounds[1]) / 2
        s = -np.array([c[0], c[1], m.bounds[0][2]])               # orijine taşı (XY merkez, Z taban)
        m.apply_translation(s)
        p.lite, p.bounds = m, m.bounds.copy()
        parts[n], shift[n] = p, s
    fields = {n: DistanceField(parts[n].lite) for n in names}
    samples = {n: sample_surface(parts[n].lite) for n in names}
    t0 = time.time()
    res = auto_assemble(parts, fields, samples, names, log=lambda m: None, up=asm.up_vector)
    r = res.root
    errs = {}
    for n in names:
        if res.placed[n]:
            errs[n] = float(np.linalg.norm(res.offsets[n] + (shift[n] - shift[r])))
    e = list(errs.values())
    return dict(parts=len(names), placed=len(errs), median_err=round(float(np.median(e)), 2) if e else None,
                max_err=round(float(np.max(e)), 2) if e else None,
                worst=sorted(errs, key=lambda n: -errs[n])[:3], unplaced=sorted(set(names) - set(errs)),
                seconds=round(time.time() - t0, 1))


def check(entry: dict, got: dict) -> list[tuple[str, bool, str]]:
    """[(kontrol adı, geçti mi, açıklama)]"""
    ex = entry.get("expect") or {}
    out = []

    def add(name, ok, detail):
        out.append((name, bool(ok), detail))

    if entry.get("check") == "auto_synthetic":
        if "placed_min" in ex:
            add("yerleşen", got["placed"] >= ex["placed_min"], f"{got['placed']}/{got['parts']} (≥ {ex['placed_min']})")
        if "median_err_max" in ex and got["median_err"] is not None:
            add("medyan hata", got["median_err"] <= ex["median_err_max"], f"{got['median_err']} mm (≤ {ex['median_err_max']})")
        if "max_err" in ex and got["max_err"] is not None:
            add("en büyük hata", got["max_err"] <= ex["max_err"],
                f"{got['max_err']} mm (≤ {ex['max_err']}; en kötü: {', '.join(got['worst'])})")
        return out
    for k in ("mode", "up", "parts"):
        if k in ex:
            add(k, got[k] == ex[k], f"{got[k]} (beklenen {ex[k]})")
    if "placed_min" in ex:
        add("yerleşen", got["placed"] >= ex["placed_min"], f"{got['placed']} (≥ {ex['placed_min']})"
            + (f"; yerleşmeyen: {', '.join(got['unplaced'])}" if got["unplaced"] else ""))
    for k, label in (("height", "boy"), ("figure_height", "figür boyu")):
        if k in ex:
            v, tol = ex[k]
            add(label, abs(got[k] - v) <= tol, f"{got[k]} mm (beklenen {v} ± {tol})")
    if "selected" in ex:
        miss = [n for n in ex["selected"] if n not in got["selected"]]
        add("seçili", not miss, "tamam" if not miss else f"seçili değil: {', '.join(miss)}")
    if "unselected" in ex:
        wrong = [n for n in ex["unselected"] if n in got["selected"]]
        add("alternatif", not wrong, "tamam" if not wrong else f"seçili olmamalıydı: {', '.join(wrong)}")
    if "base" in ex:
        miss = [n for n in ex["base"] if n not in got["base"]]
        add("kaide", not miss, "tamam" if not miss else f"kaide sayılmadı: {', '.join(miss)}")
    if "max_penetration" in ex:
        add("iç içe geçme", got["max_penetration"] <= ex["max_penetration"],
            f"{got['max_penetration']} mm (≤ {ex['max_penetration']})")
    if "repair_failed" in ex:          # onarımı şekli bozacağı için yapılmayan (dolu basılan) parçalar
        add("onarılamayan", sorted(got["repair_failed"]) == sorted(ex["repair_failed"]),
            f"{', '.join(got['repair_failed']) or 'yok'} (beklenen {', '.join(ex['repair_failed']) or 'yok'})")
    if "junk" in ex:
        add("reklam dosyası", sorted(got["junk"]) == sorted(ex["junk"]),
            f"{', '.join(got['junk']) or 'yok'} (beklenen {', '.join(ex['junk']) or 'yok'})")
    return out


def diff(prev: dict, got: dict) -> list[str]:
    keys = ("mode", "up", "placed", "height", "figure_height", "selected", "base", "groups", "junk", "repaired",
            "repair_failed", "median_err", "max_err")
    out = []
    for k in keys:
        if k not in got or k not in prev or prev.get(k) == got.get(k):
            continue
        a, b = prev.get(k), got.get(k)
        if isinstance(a, list) and isinstance(b, list) and all(isinstance(x, str) for x in a + b):
            plus, minus = sorted(set(b) - set(a)), sorted(set(a) - set(b))
            out.append(f"{k}: " + " ".join([f"+{x}" for x in plus] + [f"-{x}" for x in minus]))
        else:
            out.append(f"{k}: {a} → {b}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="virgülle kit id'leri")
    ap.add_argument("--quick", action="store_true", help="yavaş kitleri atla")
    ap.add_argument("--kits", default=str(Path(__file__).with_name("regress_kits.yaml")))
    ap.add_argument("-v", "--verbose", action="store_true", help="montaj günlüğünü göster")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    kits = yaml.safe_load(Path(a.kits).read_text(encoding="utf-8"))["kits"]
    only = {x.strip() for x in a.only.split(",")} if a.only else None
    OUT.mkdir(exist_ok=True)
    last_f = OUT / "son.json"
    prev = json.loads(last_f.read_text(encoding="utf-8")) if last_f.exists() else {}
    results = dict(prev)
    log = print if a.verbose else (lambda m: None)
    n_fail = 0
    for e in kits:
        if (only and e["id"] not in only) or (a.quick and e.get("slow") and not only):
            continue
        if not Path(e["path"]).exists():
            print(f"[{e['id']}] ATLANDI: klasör yok ({e['path']})")
            continue
        print(f"[{e['id']}] çalışıyor…", flush=True)
        t0 = time.time()
        try:
            got = (measure_auto_synthetic if e.get("check") == "auto_synthetic" else measure_assembly)(e, log)
            rows = check(e, got)
        except Exception as ex:  # bir kit çökerse diğerleri yine çalışsın
            got, rows = {"hata": repr(ex)}, [("çalıştı", False, repr(ex))]
        got["toplam_s"] = round(time.time() - t0, 1)
        bad = [r for r in rows if not r[1]]
        n_fail += bool(bad)
        print(f"[{e['id']}] {'GEÇTİ' if not bad else 'KALDI'} ({got['toplam_s']} s)")
        for name, ok, detail in rows:
            print(f"    {'✔' if ok else '✘'} {name}: {detail}")
        ch = diff(prev.get(e["id"], {}), got)
        if ch:
            print("    önceki çalıştırmaya göre değişen: " + "; ".join(ch))
        results[e["id"]] = {**got, "geçti": not bad, "zaman": time.strftime("%Y-%m-%d %H:%M")}
        gc.collect()
    txt = json.dumps(results, indent=1, ensure_ascii=False)
    last_f.write_text(txt, encoding="utf-8")
    (OUT / f"{time.strftime('%Y%m%d_%H%M')}.json").write_text(txt, encoding="utf-8")
    print(f"\n{'Tüm kontroller geçti.' if not n_fail else f'{n_fail} kit kaldı.'}  Sonuçlar: {last_f}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
