"""Deneme turu: daha önce denenmemiş kitlerde tam hazırlığı (prep) sırayla çalıştırır, sonuçları özetler.

Regresyon setinden (regress.py) farkı: beklenen değer yok; amaç yeni kitlerde çökme, bellek taşması, montaj
bulunamaması, onarım/hollow başarısızlığı gibi sorunları toplu görmek. Her kit ayrı süreçte çalışır (bir kitin
çökmesi diğerlerini durdurmaz); süre ve en yüksek bellek (süreç ağacı) ölçülür.

Kullanım (slaprep klasöründen):
    .venv\\Scripts\\python tools\\trial_kits.py tools\\trial_kits.yaml
    .venv\\Scripts\\python tools\\trial_kits.py tools\\trial_kits.yaml --only frieren,griffith

YAML: kits: [{id, path, args: [ek prep argümanları], note}]
Çıktı: tools/trial_out/<zaman>.json ve .md (kit başına: çıkış kodu, süre, bellek, montaj, boy, plate, süre
tahmini, hollow/onarım sayıları, uyarılar) + kit başına günlük (tools/trial_out/logs/<id>.log).
Kitlerin kendi çıktıları her zamanki gibi <kit>/_slaprep/ altındadır (report.html).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "trial_out"
EXE = ROOT / ".venv" / "Scripts" / "slaprep.exe"


def _watch(proc: subprocess.Popen, stats: dict, stop: threading.Event):
    """Süreç ağacının toplam RSS'ini ve sistemdeki en düşük boş belleği örnekle."""
    try:
        root = psutil.Process(proc.pid)
    except psutil.Error:
        return
    while not stop.is_set():
        try:
            rss = root.memory_info().rss
            for c in root.children(recursive=True):
                try:
                    rss += c.memory_info().rss
                except psutil.Error:
                    pass
        except psutil.Error:
            break
        stats["peak_gb"] = max(stats.get("peak_gb", 0.0), rss / 2**30)
        stats["min_free_gb"] = min(stats.get("min_free_gb", 1e9), psutil.virtual_memory().available / 2**30)
        stop.wait(2.0)


def run_kit(e: dict, logdir: Path) -> dict:
    kit = Path(e["path"])
    cmd = [str(EXE), "prep", str(kit), "--no-open", *[str(a) for a in e.get("args") or []]]
    had_settings = (kit / "_slaprep" / "slaprep.yaml").exists()
    log_f = logdir / f"{e['id']}.log"
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONWARNINGS="ignore")
    stats: dict = {}
    t0 = time.time()
    with open(log_f, "w", encoding="utf-8", errors="replace") as fh:
        proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, env=env, cwd=str(ROOT))
        stop = threading.Event()
        th = threading.Thread(target=_watch, args=(proc, stats, stop), daemon=True)
        th.start()
        code = proc.wait()
        stop.set()
        th.join()
    res = dict(id=e["id"], path=str(kit), args=e.get("args") or [], exit=code, seconds=round(time.time() - t0),
               peak_gb=round(stats.get("peak_gb", 0.0), 1), min_free_gb=round(stats.get("min_free_gb", 0.0), 1),
               had_settings=had_settings, note=e.get("note", ""))
    tail = log_f.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
    pj = kit / "_slaprep" / "prep.json"
    if code != 0 or not pj.exists() or pj.stat().st_mtime < t0:
        res["error_tail"] = tail
        return res
    res.update(summarize(json.loads(pj.read_text(encoding="utf-8"))))
    return res


def summarize(d: dict) -> dict:
    parts = d.get("parts") or {}
    asm = d.get("assembly") or {}
    printed = [n for n, p in parts.items() if p.get("printed")]
    plates = d.get("plates") or {}
    warn = d.get("warnings") or []
    return dict(
        mode=asm.get("mode"), up=f"{'+' if asm.get('up_sign', 1) > 0 else '-'}{asm.get('up_axis')}",
        n_parts=len(parts), n_printed=len(printed),
        unplaced=sorted(n for n in asm.get("selected") or [] if parts.get(n, {}).get("placed") is False),
        unattached=asm.get("unattached_groups") or [],
        unselected=sorted(set(parts) - set(asm.get("selected") or parts)),
        height_mm=asm.get("height_mm"), height_after_mm=d.get("height_after_mm"), scale=d.get("scale"),
        max_pen_mm=asm.get("max_penetration_mm"),
        n_plates=len(plates), print_h=round((d.get("print_time_min") or 0) / 60, 1),
        hollowed=sum(1 for n in printed if parts[n].get("hollowed")),
        solid=sorted(n for n in printed if not parts[n].get("hollowed")),
        repaired=sorted(n for n, p in parts.items() if p.get("repaired")),
        repair_failed=sorted(n for n, p in parts.items() if p.get("repair_failed")),
        junk=asm.get("junk_parts") or [], reference=asm.get("reference_parts") or [],
        not_watertight_out=sorted(n for n in printed if parts[n].get("watertight_out") is False),
        holes=sum(len(parts[n].get("holes") or []) for n in printed),
        part_warnings=sum(len(parts[n].get("warnings") or []) for n in printed),
        warnings=warn,
        timings=d.get("timings_s"),
    )


def to_md(results: list[dict]) -> str:
    out = ["# Deneme turu", "", f"_{time.strftime('%Y-%m-%d %H:%M')}_", "",
           "| Kit | Sonuç | Süre | Bellek | Montaj | Boy (mm) | Parça (basılan) | Hollow | Onarılan | Plate | Baskı |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        ok = "✔" if r["exit"] == 0 and "error_tail" not in r else f"✘ ({r['exit']})"
        if "mode" not in r:
            out.append(f"| {r['id']} | {ok} | {r['seconds'] // 60} dk | {r['peak_gb']} GB | – | – | – | – | – | – | – |")
            continue
        out.append(f"| {r['id']} | {ok} | {r['seconds'] // 60} dk | {r['peak_gb']} GB | {r['mode']} {r['up']} | "
                   f"{r['height_mm']} | {r['n_parts']} ({r['n_printed']}) | {r['hollowed']} | {len(r['repaired'])} | "
                   f"{r['n_plates']} | {r['print_h']} sa |")
    for r in results:
        out += ["", f"## {r['id']}", "", f"`{r['path']}` {' '.join(r['args'])}"]
        if r.get("note"):
            out.append(f"Not: {r['note']}")
        if "error_tail" in r:
            out += ["", "```", *r["error_tail"], "```"]
            continue
        for k, label in (("unplaced", "Yerleşmeyen"), ("unselected", "Seçilmeyen (alternatif/referans/reklam)"),
                         ("junk", "Reklam/filigran sayılan"), ("reference", "Ölçek referansı"),
                         ("repair_failed", "Onarılamayan (orijinal + dolu)"), ("solid", "Dolu basılan"),
                         ("not_watertight_out", "Çıktısı su geçirmez değil")):
            if r.get(k):
                out.append(f"- {label}: {', '.join(r[k])}")
        if r["warnings"]:
            out.append("- Uyarılar:")
            out += [f"  - {w}" for w in r["warnings"]]
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kits")
    ap.add_argument("--only", help="virgülle kit id'leri")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    kits = yaml.safe_load(Path(a.kits).read_text(encoding="utf-8"))["kits"]
    only = {x.strip() for x in a.only.split(",")} if a.only else None
    logdir = OUT / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M")
    results = []
    for e in kits:
        if only and e["id"] not in only:
            continue
        if not Path(e["path"]).exists():
            print(f"[{e['id']}] ATLANDI: klasör yok")
            continue
        print(f"[{e['id']}] çalışıyor…", flush=True)
        r = run_kit(e, logdir)
        results.append(r)
        print(f"[{e['id']}] çıkış {r['exit']}, {r['seconds']} s, en yüksek bellek {r['peak_gb']} GB"
              + (f", montaj {r.get('mode')}, {r.get('n_plates')} plate" if "mode" in r else ""), flush=True)
        (OUT / f"{stamp}.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
        (OUT / f"{stamp}.md").write_text(to_md(results), encoding="utf-8")
    print(f"Özet: {OUT / (stamp + '.md')}")


if __name__ == "__main__":
    main()
