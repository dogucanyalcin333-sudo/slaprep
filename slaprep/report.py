"""report.html (tek dosya, gömülü görseller) ve konsol özeti."""
from __future__ import annotations

import base64
import html
from datetime import datetime
from pathlib import Path

import numpy as np
from rich.console import Console
from rich.table import Table

from .assembly import AXES

MODE_TXT = {"tam": "tam montajlı", "kısmi": "kısmen kaymış, çözüldü", "otomatik": "otomatik birleştirildi (ek yüzeylerinden)", "bilinmiyor": "bilinmiyor"}


def _img(p: Path, width: str = "100%") -> str:
    if not p or not Path(p).exists():
        return ""
    b = base64.b64encode(Path(p).read_bytes()).decode()
    return f'<img src="data:image/png;base64,{b}" style="max-width:{width}">'


def _ok(v) -> str:
    if v is None:
        return "–"
    return '<span class="ok">✔</span>' if v else '<span class="bad">✘</span>'


def grouping_text(g: dict | None) -> str:
    """Plate düzeni kararı (alt klasörlere göre / tek grup) için kullanıcıya açıklama; söylenecek bir şey yoksa ''."""
    from .fmt import fmt_duration

    if not g or not g.get("folders") or not g.get("single"):
        return ""
    f, s = g["folders"], g["single"]
    nums = (f"alt klasörlere göre {f['n_plates']} plate ~{fmt_duration(f['minutes'] * 60)}, tek grup "
            f"{s['n_plates']} plate ~{fmt_duration(s['minutes'] * 60)}")
    if g["chosen"] == "single" and g.get("auto"):
        return (f"Plate'ler süre için alt klasörlere ayrılmadı ({nums}). Klasör düzenini her koşulda isterseniz: "
                "Yazıcı sekmesi → 'Plate'leri alt klasörlere ayır: evet' (CLI: --plates-by-folder).")
    if g["chosen"] == "folders" and not g.get("auto") and (s["n_plates"] < f["n_plates"]
                                                          or s["minutes"] < f["minutes"] - 15):
        return (f"Plate'ler isteğiniz üzerine alt klasörlere göre ayrıldı ({nums}). En kısa süre için: Yazıcı "
                "sekmesi → 'Plate'leri alt klasörlere ayır: otomatik' (CLI: --plates-by-folder kaldırılır).")
    return ""


def summary_rows(run):
    asm = run.asm
    rows = [("Kit", str(run.kit)), ("Parça sayısı", str(len(run.parts)))]
    if asm:
        rows.append(("Montaj durumu", MODE_TXT[asm.mode]))
        rows.append(("Yukarı ekseni", ("+" if asm.up_sign > 0 else "−") + AXES[asm.up_axis].upper()))
        if asm.zeroed_axes:
            rows.append(("Sıfırlanmış eksen", ", ".join(AXES[k].upper() for k in asm.zeroed_axes)))
        for g in asm.variant_groups:
            if not getattr(g, "confirmed", True) and set(g.selected) == set(g.members):
                rows.append(("Olası alternatif", f"{' / '.join(g.members)} (ad benzerliği, doğrulanamadı — hepsi basılacak)"))
                continue
            rest = [m for m in g.members if m not in g.selected]
            rows.append(("Alternatif grubu", f"seçili: <b>{' + '.join(g.selected)}</b> / alternatif: {', '.join(rest)} "
                                            f"({g.reason}{'' if g.overlap < 0 else f', örtüşme %{g.overlap*100:.0f}'})"))
        if asm.mode != "bilinmiyor":
            h = f"{run.height_before:.1f} mm"
            if run.scale != 1.0:
                h += f" → <b>{run.height_after:.1f} mm</b>"
            rows.append(("Montajlı yükseklik", h))
            rows.append(("Kaide parçaları", ", ".join(asm.base_parts) or "–"))
            rows.append(("Figür yüksekliği (kaide hariç)", f"{run.figure_height_before:.1f} mm"
                         + (f" → {run.figure_height_before * run.scale:.1f} mm" if run.scale != 1 else "")))
            rows.append(("En büyük iç içe geçme", f"{asm.max_penetration:.2f} mm"))
            k = asm.up_axis
            for g in getattr(asm, "groups", None) or []:
                gh = (max(run.parts[m].bounds[1][k] + P[k] for m, P in g.items())
                      - min(run.parts[m].bounds[0][k] + P[k] for m, P in g.items()))
                rows.append(("Ayrı grup (montaja eklenemedi)",
                             f"{', '.join(sorted(g))} — kendi içinde birleşti, kendi boyu {gh:.1f} mm"
                             + (f" → {gh * run.scale:.1f} mm" if run.scale != 1 else "") + "; yüksekliğe katılmadı"))
        if getattr(run, "wall_back", None) is not None:
            v, how = run.wall_back
            k = int(np.argmax(np.abs(v)))
            use = ("duvara bakan yüzlere de delik açıldı" if run.settings.hole_on_wall
                   else "duvara bakan yüzler gizli sayıldı, delik açılmadı (--holes-on-wall-back ile açılabilir)")
            rows.append(("Duvar montajı", f"evet — duvar yönü {'+' if v[k] > 0 else '−'}{AXES[k].upper()} ({how}); {use}"))
    if asm and asm.mode == "bilinmiyor" and run.settings.orig_height:
        rows.append(("Yükseklik (verilen orijinal boydan)",
                     f"{run.settings.orig_height:g} mm → <b>{run.settings.orig_height * run.scale:.1f} mm</b>"))
    rows.append(("Ölçek", f"{run.scale:.4f}"))
    if getattr(run, "print_minutes", 0):
        from .layout import fmt_duration

        rows.append(("Tahmini baskı süresi", f"{fmt_duration(run.print_minutes * 60)} "
                                             f"({len(run.plate_checks)} plate, tahmin)"))
    if getattr(run, "prepared", False):
        from .resin import run_summary, summary_lines

        lines = summary_lines(run_summary(run))
        if lines:
            rows.append(("Reçine (tahmin)", "<br>".join(html.escape(x) for x in lines)))
    return rows


def write_html(run, path: Path, title_extra: str = "") -> Path:
    asm = run.asm
    s = run.settings
    css = """
    body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#222;background:#fafafa;max-width:1200px}
    h1{margin-bottom:4px} h2{border-bottom:2px solid #ddd;padding-bottom:4px;margin-top:32px}
    table{border-collapse:collapse;margin:8px 0;background:#fff} td,th{border:1px solid #ddd;padding:4px 8px;font-size:13px;vertical-align:top}
    th{background:#f0f0f0;text-align:left} .ok{color:#1a7f37;font-weight:bold} .bad{color:#c62828;font-weight:bold}
    .warn{background:#fff8e1;border-left:4px solid #f9a825;padding:6px 10px;margin:4px 0}
    .grid{display:flex;flex-wrap:wrap;gap:12px} .card{background:#fff;border:1px solid #ddd;padding:8px;width:360px}
    .card h4{margin:2px 0 6px} small{color:#666}
    """
    out = [f"<!doctype html><html lang='tr'><head><meta charset='utf-8'><title>SLAPrep — {html.escape(run.kit.name)}</title>"
           f"<style>{css}</style></head><body>"]
    out.append(f"<h1>SLAPrep raporu: {html.escape(run.kit.name)}</h1>"
               f"<small>{datetime.now():%Y-%m-%d %H:%M} {html.escape(title_extra)}</small>")

    out.append("<h2>Kit özeti</h2><table>")
    for k, v in summary_rows(run):
        out.append(f"<tr><th>{k}</th><td>{v}</td></tr>")
    out.append("</table>")

    if run.warnings or any(r.warnings for r in run.results.values()):
        out.append("<h2>Uyarılar</h2>")
        for w in run.warnings:
            out.append(f"<div class='warn'>{html.escape(w)}</div>")
        for n, r in run.results.items():
            if not r.printed:
                continue
            for w in r.warnings:
                out.append(f"<div class='warn'><b>{html.escape(n)}</b>: {html.escape(w)}</div>")

    # montaj kontrol listesi: kendi çözdüğümüz montajda (otomatik/kısmi) güveni düşük parçalar
    if asm and asm.mode in ("otomatik", "kısmi"):
        from .asmcheck import TAG_COLORS, assembly_dict, checklist

        rows = checklist({n: {"placed": asm.placed.get(n, False)} for n in run.parts}, assembly_dict(asm))
        todo = [r for r in rows if r[2] in ("bad", "check", "user")]
        n_ok = sum(1 for r in rows if r[2] == "ok")
        out.append(f"<h2>Montaj kontrolü</h2><p>{n_ok} parça güvenilir eklerle yerleşti"
                   + (f"; aşağıdaki {len(todo)} parçayı önizlemede kontrol edin. Yanlışsa uygulamanın "
                      "<b>Montaj kontrolü</b> sekmesinden ayırın ya da doğru komşusuna bağlayın "
                      "(CLI: <code>--detach</code>, <code>--join ÇAPA:PARÇA</code>)." if todo else ".") + "</p>")
        if todo:
            out.append("<table><tr><th>Parça</th><th>Durum</th><th>Bağlandığı</th><th>Temas (mm²)</th></tr>")
            for n, text, tag, to, c in todo:
                out.append(f"<tr><td>{html.escape(n)}</td><td style='color:{TAG_COLORS[tag]}'>{html.escape(text)}</td>"
                           f"<td>{html.escape(str(to))}</td><td>{c}</td></tr>")
            out.append("</table>")

    # parça tablosu
    out.append("<h2>Parçalar</h2><table><tr><th>Parça</th><th>Üçgen</th><th>Watertight</th>"
               "<th>Basılacak</th><th>Hollow</th><th>Kavite</th><th>Delik</th><th>Reçine (ml)</th>"
               "<th>Duvar ölçümü</th><th>Yönlendirme</th><th>Plate</th></tr>")
    for n, p in run.parts.items():
        r = run.results[n]
        vol = f"{r.vol_before:.1f}" if not r.hollowed else f"{r.vol_before:.1f} → <b>{r.vol_after:.1f}</b>"
        hol = ("evet" if r.hollowed else "dolu") + (f" <small>({html.escape(r.hollow_reason)})</small>" if r.hollow_reason else "")
        wall = f"{r.wall_measured[0]:.2f} ({r.wall_measured[1]:.2f}–{r.wall_measured[2]:.2f})" if r.wall_measured else "–"
        out.append(f"<tr><td>{html.escape(n)}</td><td>{p.n_faces:,}</td><td>{_ok(p.watertight)}"
                   f"{' (onarıldı)' if p.repaired else ''}</td><td>{_ok(r.printed)}</td><td>{hol}</td>"
                   f"<td>{r.cavities or '–'}</td><td>{r.holes_text()}</td><td>{vol}</td><td>{wall}</td>"
                   f"<td>{html.escape(r.orientation) or '–'}</td><td>{r.plate or '–'}</td></tr>")
    printed = [r for r in run.results.values() if r.printed]
    tb = sum(r.vol_before for r in printed)
    ta = sum(r.vol_after for r in printed)
    out.append(f"<tr><th colspan=7>Toplam (basılacak parçalar)</th><th>{tb:.1f} → <b>{ta:.1f}</b></th><th colspan=3></th></tr></table>")

    # kontroller
    checks = [(n, r.checks) for n, r in run.results.items() if r.checks]
    if checks:
        keys = sorted({k for _, c in checks for k in c})
        out.append("<h2>Otomatik kontroller</h2><table><tr><th>Parça</th>" + "".join(f"<th>{k}</th>" for k in keys) + "</tr>")
        for n, c in checks:
            out.append(f"<tr><td>{html.escape(n)}</td>" + "".join(f"<td>{_ok(c.get(k))}</td>" for k in keys) + "</tr>")
        out.append("</table>")

    if run.plate_checks:
        from .layout import fmt_duration

        gap = run.settings.gap
        goal = "en kısa toplam süre" if s.plate_goal != "plates" else "en az plate"
        out.append(f"<h2>Plate'ler</h2><p>Yerleşim hedefi: <b>{goal}</b>. SLA'da baskı süresi plate'teki en yüksek "
                   "parçayla belirlenir (katman sayısı); uzun parçalar aynı plate'e toplanır, kısa parçalar onların "
                   f"boşluğuna 'bedavaya' sığar. Süre tahmini: {s.layer_height:g} mm katman × {s.layer_time:g} sn + "
                   f"plate başına {s.plate_overhead_min:g} dk.</p>")
        from .resin import resin_cell, run_summary

        rs = run_summary(run)
        out.append("<table><tr><th>Plate</th><th>Parçalar</th><th>En yüksek (mm)</th><th>Tahmini süre</th>"
                   f"<th>Reçine (+%{rs['extra_pct']:g} destek)</th>"
                   f"<th>Sınırlar içinde</th><th>En kısa parça arası (≥ {gap:.0f} mm)</th><th>Dosya</th></tr>")
        for k, c in sorted(run.plate_checks.items()):
            names = ", ".join(n for n, r in run.results.items() if r.plate == k)
            g = c["min_gap"]
            gs = "–" if g is None else f"{_ok(g >= gap - 0.5)} {g:.1f} mm"
            mh = c.get("max_height")
            est = c.get("est_min")
            out.append(f"<tr><td>{html.escape(k)}</td><td>{html.escape(names)}</td>"
                       f"<td>{'–' if mh is None else f'{mh:.0f}'}</td>"
                       f"<td>{'–' if est is None else fmt_duration(est * 60)}</td>"
                       f"<td>{html.escape(resin_cell(rs['plates'].get(k), rs['currency']))}</td><td>{_ok(c['inside'])}</td>"
                       f"<td>{gs}</td><td>plates/{html.escape(k)}.stl</td></tr>")
        if getattr(run, "print_minutes", 0):
            out.append(f"<tr><th colspan='3'>Toplam</th><th>{fmt_duration(run.print_minutes * 60)}</th>"
                       f"<th>{html.escape(resin_cell(rs['total'], rs['currency']))}</th><td colspan='3'></td></tr>")
        out.append("</table>")
        msg = grouping_text(getattr(run, "grouping", None))
        if msg:
            out.append(f"<div class='warn'>{html.escape(msg)}</div>")

    # delik koordinatları
    holes = [(n, r.holes) for n, r in run.results.items() if r.holes]
    if holes:
        out.append("<h2>Delik koordinatları</h2><p><small>Çıktı (parts/ ve plate) koordinatlarında, mm. "
                   "CHITUBOX auto-support bir deliğin ağzına destek koyarsa o desteği silin.</small></p><table>"
                   "<tr><th>Parça</th><th>#</th><th>Kavite</th><th>Çap</th><th>Konum (X, Y, Z)</th></tr>")
        for n, hs in holes:
            for i, h in enumerate(hs, 1):
                x, y, z = h.get("pos_out", h["pos"])
                out.append(f"<tr><td>{html.escape(n)}</td><td>{i}</td><td>{h['cavity'] + 1}</td><td>{h['d']:.1f}</td>"
                           f"<td>{x:.1f}, {y:.1f}, {z:.1f}</td></tr>")
        out.append("</table>")

    # pair tablosu (montaj)
    if asm and asm.fits:
        out.append("<details><summary><b>Montaj eşleşmeleri</b> (çözücü detayları)</summary><table>"
                   "<tr><th>A</th><th>B</th><th>Kayma (mm)</th><th>Temas (mm²)</th><th>İç içe (mm²)</th>"
                   "<th>Belirsizlik</th><th>Kabul</th><th>Kullanıldı</th><th>Artık (mm)</th></tr>")
        for f in sorted(asm.fits, key=lambda f: -f.contact):
            res = "" if f.residual != f.residual else f"{f.residual:+.2f}"
            out.append(f"<tr><td>{f.a}</td><td>{f.b}</td><td>{f.delta:+.2f}</td><td>{f.contact:.0f}</td>"
                       f"<td>{f.pen:.1f}</td><td>{f.ambiguity:.2f}</td><td>{_ok(f.accepted)}</td>"
                       f"<td>{_ok(f.used)}</td><td>{res}</td></tr>")
        out.append("</table></details>")

    # görseller
    out.append("<h2>Görseller</h2><div class='grid'>")
    for key in ("front", "side"):
        if key in run.previews:
            out.append(f"<div class='card' style='width:520px'><h4>Montaj — {'önden' if key == 'front' else 'yandan'}</h4>"
                       f"{_img(run.previews[key])}</div>")
    if "height_ruler" in run.previews:
        out.append(f"<div class='card' style='width:420px'><h4>Boy (cetvelli, basılacak ölçek)</h4>"
                   f"{_img(run.previews['height_ruler'])}</div>")
    out.append("</div>")
    part_imgs = [(n, run.previews.get(f"part:{n}")) for n in run.parts if run.previews.get(f"part:{n}")]
    if part_imgs:
        out.append("<h3>Parçalar: birleşme yüzeyi / delik açılabilir (yeşil, koyu yeşil = güvenli merkez), gizli ama dış yüzey — delik yok (mavi), pim (turuncu), delikler (kırmızı)</h3><div class='grid'>")
        for n, p in part_imgs:
            out.append(f"<div class='card'><h4>{html.escape(n)}</h4>{_img(p)}</div>")
        out.append("</div>")
    plate_imgs = sorted((k, v) for k, v in run.previews.items() if k.startswith("plate:"))
    if plate_imgs:
        out.append("<h3>Plate'ler (üstten)</h3><div class='grid'>")
        for k, p in plate_imgs:
            out.append(f"<div class='card' style='width:560px'><h4>{k.split(':')[1]}</h4>{_img(p)}</div>")
        out.append("</div>")

    out.append("<h2>Hatırlatmalar</h2><ul>"
               "<li>Gizli (birleşme) yüzeyler plate'e bakacak şekilde yönlendirildi: destek izleri birleşme "
               "yüzeyinde kalır, montaj öncesi hafif zımpara gerekebilir.</li>"
               "<li>CHITUBOX auto-support delik ağzına destek koyarsa o desteği silin (koordinatlar yukarıda).</li>"
               "<li>Orijinal STL dosyalarına dokunulmadı; tüm çıktı bu klasörde.</li></ul>")
    hole_txt = (f"delik {s.hole:g}–{s.hole_max:g} mm (kavite boyutuna göre otomatik)" if s.hole_auto
                else f"delik {s.hole:g} mm (sabit)")
    out.append(f"<p><small>Ayarlar: duvar {s.wall} mm, {hole_txt}, yazıcı {s.printer} "
               f"{s.build_volume[0]:.0f}×{s.build_volume[1]:.0f}×{s.build_volume[2]:.0f} mm. "
               f"Süreler: {', '.join(f'{k} {v}s' for k, v in run.timings.items())}</small></p>")
    out.append("</body></html>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out), encoding="utf-8")
    return path


def print_summary(run, console: Console):
    t = Table(title=f"SLAPrep — {run.kit.name}", show_header=False)
    for k, v in summary_rows(run):
        t.add_row(k, v.replace("<b>", "[bold]").replace("</b>", "[/bold]"))
    console.print(t)
    pt = Table(title="Parçalar")
    for c in ("Parça", "Hacim (ml)", "Basılacak", "Hollow", "Kavite", "Delik", "Reçine sonrası", "Plate"):
        pt.add_column(c)
    for n, r in run.results.items():
        pt.add_row(n, f"{r.vol_before:.1f}", "✔" if r.printed else "–", "evet" if r.hollowed else "dolu",
                   str(r.cavities or "–"), r.holes_text(), f"{r.vol_after:.1f}", str(r.plate or "–"))
    console.print(pt)
    printed = [r for r in run.results.values() if r.printed]
    console.print(f"Toplam reçine (basılacak): [bold]{sum(r.vol_before for r in printed):.1f} ml → "
                  f"{sum(r.vol_after for r in printed):.1f} ml[/bold]")
    if getattr(run, "prepared", False):
        from .resin import run_summary, summary_lines

        for line in summary_lines(run_summary(run)):
            console.print(line)
    allw = list(run.warnings) + [f"{n}: {w}" for n, r in run.results.items() if r.printed for w in r.warnings]
    if allw:
        console.print("[bold yellow]Uyarılar:[/bold yellow]")
        for w in allw:
            console.print(f"  • {w}")
