"""Komut satırı: slaprep analyze | prep | list"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from rich.console import Console

from .config import PRINTERS, Settings, load_settings, out_dir, save_settings

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
console = Console()


def _csv(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def _kit(path: str) -> Path:
    p = Path(path.strip().strip('"')).expanduser().resolve()
    if not p.is_dir():
        console.print(f"[red]Klasör bulunamadı:[/red] {p}")
        sys.exit(2)
    return p


def _join(s: str) -> list:
    """ÇAPA:PARÇA ya da ÇAPA:PARÇA:SIRA (sıra 2, 3…: en iyi konum yanlışsa sonraki aday)."""
    bits = [x.strip() for x in s.split(":")]
    if len(bits) not in (2, 3) or not all(bits[:2]) or (len(bits) == 3 and not bits[2].isdigit()):
        raise argparse.ArgumentTypeError("biçim: ÇAPA:PARÇA ya da ÇAPA:PARÇA:SIRA (ör. Head_v2:Miquella_Torso, "
                                         "Torso:Hand_L:2)")
    out = bits[:2]
    if len(bits) == 3 and int(bits[2]) > 1:
        out.append(int(bits[2]) - 1)              # içeride 0'dan sayılır
    return out


def _fix_args(p):
    """Kullanıcının montaj düzeltmeleri (otomatik montaj yanlış yaptıysa)."""
    p.add_argument("--detach", type=_csv, help="yanlış yere oturan parçalar: montaja alınmaz, ayrı basılır")
    p.add_argument("--join", action="append", type=_join, default=None,
                   help="ÇAPA:PARÇA — parça çapaya bağlanır (tekrarlanabilir; sadece otomatik montajda)")
    p.add_argument("--clear-fixes", action="store_true", help="kayıtlı montaj düzeltmelerini sil")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="slaprep", description="SLA baskı öncesi otomatik hazırlık")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="sadece analiz: montaj, yükseklik, varyantlar, rapor")
    a.add_argument("kit")
    a.add_argument("--variant", action="append", default=None)
    a.add_argument("--up-axis", choices=["x", "y", "z", "-x", "-y", "-z"])
    a.add_argument("--front-axis", choices=["x", "y", "z", "-x", "-y", "-z"],
                   help="figürün baktığı yön (önizlemede 'önden' sırtı gösteriyorsa); destekler arkaya/alta yönlenir")
    a.add_argument("--base", type=_csv)
    a.add_argument("--exclude", type=_csv, help="yok sayılacak parçalar: ad, * deseni veya alt klasör adı")
    a.add_argument("--wall-mount", dest="wall_mount", action="store_true", default=None)
    a.add_argument("--reset", action="store_true", help="kayıtlı slaprep.yaml ayarlarını yok say")
    a.add_argument("--hole", type=float, help="güvenli bölge hesabı için delik çapı")
    a.add_argument("--no-open", action="store_true", help="raporu tarayıcıda açma")
    _fix_args(a)

    p = sub.add_parser("prep", help="tüm pipeline")
    p.add_argument("kit")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--height", type=float, help="hedef montaj yüksekliği (mm)")
    g.add_argument("--scale", type=float, help="doğrudan ölçek")
    p.add_argument("--height-mode", choices=["all", "figure"])
    p.add_argument("--orig-height", type=float,
                   help="montaj bulunamazsa modelin bilinen orijinal boyu (mm); --height ile ölçek = hedef/orijinal")
    p.add_argument("--base", type=_csv, help="kaide parçaları: A,B")
    p.add_argument("--variant", action="append", default=None, help="alternatif seçimi (tekrarlanabilir)")
    p.add_argument("--print-all-variants", action="store_true", default=None)
    p.add_argument("--print-reference", action="store_true", default=None,
                   help="ölçek/boy referans parçalarını (ör. HEIGHT_PILLAR) ve reklam/filigran sanılan "
                        "dosyaları da bas")
    p.add_argument("--wall", type=float)
    p.add_argument("--hole", type=float, help="sabit delik çapı (mm); verilmezse kavite boyutuna göre otomatik")
    p.add_argument("--hole-max", type=float, help="otomatik modda en büyük delik çapı (varsayılan 5)")
    p.add_argument("--holes", type=int, help="kavite başına en az delik sayısı")
    p.add_argument("--auto-hole", action="store_true", help="kayıtlı sabit çapı bırak, otomatik çapa dön")
    p.add_argument("--solid", type=_csv, help="dolu basılacak parçalar (ad, * deseni ya da klasör)")
    p.add_argument("--hollow", type=_csv, help="eşiğin altında olsa da hollow edilecek parçalar")
    p.add_argument("--separate", type=_csv,
                   help="diğerleriyle karışmadan kendi plate'lerine dizilecek parçalar (ör. şeffaf reçine)")
    p.add_argument("--allow-visible-holes", action="store_true", default=None)
    p.add_argument("--holes-on-bottom", dest="hole_on_bottom", action="store_true", default=None,
                   help="kaidenin alt yüzüne de delik aç (dış yüzey: baskıdan sonra tıkanmalı)")
    p.add_argument("--holes-on-wall-back", dest="hole_on_wall", action="store_true", default=None,
                   help="duvar modunda arka yüze de delik aç (dış yüzey: baskıdan sonra tıkanmalı)")
    p.add_argument("--up-axis", choices=["x", "y", "z", "-x", "-y", "-z"])
    p.add_argument("--front-axis", choices=["x", "y", "z", "-x", "-y", "-z"],
                   help="figürün baktığı yön (önizlemede 'önden' sırtı gösteriyorsa); destekler arkaya/alta yönlenir")
    wm = p.add_mutually_exclusive_group()
    wm.add_argument("--wall-mount", dest="wall_mount", action="store_true", default=None,
                    help="duvara asılacak: duvara bakan yüzler gizli sayılır, zemin tabanı yok")
    wm.add_argument("--no-wall-mount", dest="wall_mount", action="store_false")
    p.add_argument("--wall-side", choices=["x", "y", "z", "-x", "-y", "-z"], help="duvar yönü (otomatik bulunamazsa)")
    p.add_argument("--tilt", help="eğim aralığı, ör. 30-45")
    p.add_argument("--gap", type=float)
    p.add_argument("--printer", choices=sorted(PRINTERS))
    p.add_argument("--plates", type=int)
    pf = p.add_mutually_exclusive_group()
    pf.add_argument("--plates-by-folder", dest="plates_by_folder", action="store_true", default=None,
                    help="plate'leri kaynak alt klasörlere göre ayır (birden çok alt klasör varsa varsayılan)")
    pf.add_argument("--no-plates-by-folder", dest="plates_by_folder", action="store_false",
                    help="tüm parçaları ortak plate'lere diz")
    p.add_argument("--plate-goal", choices=["time", "plates"],
                   help="time: en kısa toplam baskı süresi (uzun parçalar aynı plate'te; varsayılan), plates: en az plate")
    p.add_argument("--layer-height", type=float, help="katman kalınlığı mm (süre tahmini; varsayılan 0.05)")
    p.add_argument("--layer-time", type=float, help="katman başına süre sn (pozlama + kaldırma; varsayılan 6.5)")
    p.add_argument("--plate-overhead", dest="plate_overhead_min", type=float,
                   help="plate başına ek süre dk (alt katmanlar, hazırlık, temizlik; varsayılan 15)")
    p.add_argument("--steps", choices=["all", "plates", "hollow"],
                   help="all: hollow + delik + plate (varsayılan); plates: sadece yönlendir ve plate'e diz (parçalar "
                        "dolu); hollow: sadece hollow + delik (plate yok, parçalar orijinal duruşta)")
    p.add_argument("--only", type=_csv)
    p.add_argument("--exclude", type=_csv, help="kitle ilgisiz dosyaları yok say (logo, reklam vb.)")
    p.add_argument("--reset", action="store_true", help="kayıtlı slaprep.yaml ayarlarını yok say")
    p.add_argument("--no-open", action="store_true", help="raporu tarayıcıda açma")
    _fix_args(p)

    sub.add_parser("list", help="son çalıştırılan kitlerin özeti")

    sc = sub.add_parser("scan", help="klasör ağacındaki kitleri hafifçe tara ve montaj durumuna göre sınıflandır")
    sc.add_argument("root")
    sc.add_argument("--json", help="sonuçları bu JSON dosyasına yaz")
    return ap


def settings_from_args(kit: Path, args) -> Settings:
    s = Settings() if getattr(args, "reset", False) else load_settings(kit)
    if getattr(args, "height", None) is not None:
        s.height, s.scale = args.height, None
    if getattr(args, "scale", None) is not None:
        s.scale, s.height = args.scale, None
    simple = ["height_mode", "orig_height", "base", "wall", "hole", "solid", "hollow", "separate", "up_axis",
              "front_axis", "gap",
              "plates", "only",
              "exclude", "print_all_variants", "print_reference", "allow_visible_holes", "plates_by_folder", "wall_mount", "wall_side",
              "hole_on_bottom", "hole_on_wall", "plate_goal", "layer_height", "layer_time", "plate_overhead_min",
              "steps"]
    for k in simple:
        v = getattr(args, k, None)
        if v is not None:
            setattr(s, k, v)
    if getattr(args, "hole", None) is not None and getattr(args, "cmd", "") == "prep":
        s.hole_auto = False                      # kullanıcı çapı açıkça verdi: sabit
    if getattr(args, "auto_hole", False):
        s.hole_auto = True
        s.hole = 3.0
    if getattr(args, "hole_max", None) is not None:
        s.hole_max = args.hole_max
        s.hole_auto = True
    if getattr(args, "holes", None) is not None:
        s.min_holes = args.holes
        s.max_holes = max(s.max_holes, args.holes)
    if getattr(args, "variant", None):
        s.variants = args.variant
    if getattr(args, "clear_fixes", False):
        s.asm_detach, s.asm_join = [], []
    if getattr(args, "detach", None) is not None:
        s.asm_detach = args.detach
    if getattr(args, "join", None):
        s.asm_join = args.join
    if getattr(args, "printer", None):
        s.printer = args.printer
        s.build_volume = PRINTERS[args.printer]
    if getattr(args, "tilt", None):
        lo, hi = (float(x) for x in args.tilt.split("-"))
        s.tilt_min, s.tilt_max = lo, hi
    if getattr(args, "layer_time", None) is None:
        # kullanıcının ölçtüğü gerçek plate sürelerinden ayarlanmış katman süresi (timecal); açıkça verilmediyse
        from .timecal import calibrated

        cal = calibrated(s.plate_overhead_min)
        if cal:
            s.layer_time = round(cal[0], 3)
            if getattr(args, "plate_overhead_min", None) is None:
                s.plate_overhead_min = round(cal[1], 1)
    return s


def _history_file() -> Path:
    return Path.home() / ".slaprep_history.json"


def _record_history(kit: Path, summary: dict):
    f = _history_file()
    try:
        h = json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
    except Exception:
        h = []
    h = [x for x in h if x.get("kit") != str(kit)]
    h.insert(0, {"kit": str(kit), **summary})
    f.write_text(json.dumps(h[:20], indent=2, ensure_ascii=False), encoding="utf-8")


def cmd_analyze(args):
    from .pipeline import Run
    from .report import print_summary, write_html

    kit = _kit(args.kit)
    s = settings_from_args(kit, args)
    run = Run(kit, s, log=lambda m: console.print(f"[dim]{m}[/dim]"))
    with console.status("STL'ler yükleniyor (ilk seferde önbellek oluşturulur)…"):
        run.load()
    console.print(f"{len(run.parts)} parça yüklendi ({run.timings['yükleme']} s).")
    run.assemble()
    console.print("[dim]Gizli yüzey haritası çıkarılıyor…[/dim]")
    run.surface_maps()
    run.write_assembly_outputs()
    run.save_json()
    rp = write_html(run, out_dir(kit) / "analysis.html", "(analiz)")   # prep'in report.html'ini ezmesin
    print_summary(run, console)
    console.print(f"Rapor: [link=file:///{rp}]{rp}[/link]")
    _record_history(kit, {"komut": "analyze", "parça": len(run.parts),
                          "yükseklik_mm": round(run.height_before, 1), "montaj": run.asm.mode})
    if not args.no_open:
        _open(rp)


def cmd_prep(args):
    from .pipeline import Run
    from .report import print_summary, write_html

    kit = _kit(args.kit)
    s = settings_from_args(kit, args)
    run = Run(kit, s, log=lambda m: console.print(f"[dim]{m}[/dim]"))
    with console.status("STL'ler yükleniyor…"):
        run.load()
    console.print(f"{len(run.parts)} parça yüklendi.")
    run.assemble()
    run.prepare()
    save_settings(kit, s)
    rp = write_html(run, out_dir(kit) / "report.html")
    print_summary(run, console)
    console.print(f"Rapor: {rp}")
    console.print(f"Plate dosyaları: {out_dir(kit) / 'plates'}")
    _record_history(kit, {"komut": "prep", "parça": len(run.parts), "ölçek": round(run.scale, 4),
                          "yükseklik_mm": round(run.height_after, 1), "montaj": run.asm.mode,
                          "plate": len({r.plate for r in run.results.values() if r.plate})})
    if not args.no_open:
        _open(rp)


def cmd_list(args):
    f = _history_file()
    if not f.exists():
        console.print("Henüz çalıştırılmış kit yok.")
        return
    from rich.table import Table

    t = Table(title="Son kitler")
    h = json.loads(f.read_text(encoding="utf-8"))
    cols = ["kit", "komut", "parça", "montaj", "yükseklik_mm", "ölçek", "plate"]
    for c in cols:
        t.add_column(c)
    for x in h:
        t.add_row(*[str(x.get(c, "–")) for c in cols])
    console.print(t)


def cmd_scan(args):
    import time

    from rich.progress import Progress
    from rich.table import Table

    from .scan import find_kits, scan_root

    root = _kit(args.root)
    t0 = time.time()
    n = sum(1 for d in find_kits(root) for f in d.iterdir() if f.suffix.lower() == ".stl")
    with Progress(console=console, transient=True) as pr:
        task = pr.add_task("STL bbox'ları okunuyor…", total=n)
        kits = scan_root(root, progress=lambda _: pr.advance(task))
    t = Table(title=f"Kit taraması — {root} ({time.time() - t0:.0f} s)")
    for c in ("Kit", "STL", "MB", "Üçgen (M)", "Boyut X×Y×Z (mm)", "Durum", "Detay"):
        t.add_column(c, overflow="fold")
    order = {"kısmen kaymış": 0, "tam montajlı": 1, "dağınık": 2, "tek parça": 3, "okunamadı": 4}
    for k in sorted(kits, key=lambda k: (order.get(k.status, 9), str(k.root).lower())):
        rel = str(k.root.relative_to(root)) if k.root != root else "."
        ext = "×".join(f"{x:.0f}" for x in k.extent) if k.extent is not None else "–"
        st = k.status + (" [yellow](destekli?)[/yellow]" if k.presupported else "")
        t.add_row(rel, str(len(k.files)), f"{k.size_mb:.0f}", f"{sum(f.triangles for f in k.files)/1e6:.1f}",
                  ext, st, k.detail)
    console.print(t)
    counts = {}
    for k in kits:
        counts[k.status] = counts.get(k.status, 0) + 1
    console.print("Özet: " + ", ".join(f"{v} {s}" for s, v in counts.items()))
    if args.json:
        out = [dict(kit=str(k.root), status=k.status, detail=k.detail, presupported=k.presupported,
                    zeroed_axes=k.zeroed_axes, connected_frac=round(k.connected_frac, 2),
                    extent=None if k.extent is None else np.round(k.extent, 1).tolist(),
                    files=[dict(name=f.path.name, triangles=f.triangles, ascii=f.ascii, error=f.error,
                                bounds=None if f.bounds is None else np.round(f.bounds, 2).tolist())
                           for f in k.files])
               for k in kits]
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
        console.print(f"JSON: {args.json}")


def _open(path: Path):
    import os

    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except Exception:
        pass


def main(argv=None):
    from .io import LoadError

    args = build_parser().parse_args(argv)
    try:
        {"analyze": cmd_analyze, "prep": cmd_prep, "list": cmd_list, "scan": cmd_scan}[args.cmd](args)
    except (LoadError, MemoryError) as e:
        msg = str(e) if isinstance(e, LoadError) else "Bellek yetmedi."
        console.print(f"[red]HATA:[/red] {msg}")
        console.print("İpucu: açık programları (CHITUBOX, tarayıcı…) kapatıp tekrar deneyin. Diskte yer sorunu değil; "
                      "işlem RAM + Windows sanal belleğini (sayfa dosyası) aştı.")
        sys.exit(3)


if __name__ == "__main__":
    main()
