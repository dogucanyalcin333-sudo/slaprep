"""Aşamaları sırayla çalıştıran orkestrasyon. CLI ve testler bunu kullanır."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import trimesh

from . import io as sio
from .assembly import AXES, Assembly, solve_assembly
from .config import SEPARATE_GROUP, Settings, out_dir
from .render import preview_mesh, render_assembly, render_height_view, render_turntable


@dataclass
class PartResult:
    name: str
    printed: bool = True                 # bu çalıştırmada hazırlanıp basılacak mı
    scale: float = 1.0
    hollowed: bool = False
    hollow_reason: str = ""
    cavities: int = 0
    holes: list = field(default_factory=list)       # [{"pos": [x,y,z], "d": mm, "cavity": i}]
    vol_before: float = 0.0              # ml (ölçekli, dolu)
    vol_after: float = 0.0               # ml
    wall_measured: tuple | None = None   # (ortalama, min, max)
    watertight_out: bool | None = None
    orientation: str = ""
    support: dict | None = None          # destek gelecek alanın dağılımı: {hidden, low (arka/alt), visible (ön/yan)}
    plate: str | None = None             # "plate_01" veya "Base/plate_01"
    hidden_frac: float = 0.0
    checks: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    R: np.ndarray | None = None          # montaj (ölçekli) → baskı yönü dönüşümü
    hole_plan: list = field(default_factory=list)   # [(kavite, açılan, hedef, çap)]

    def holes_text(self) -> str:
        if not self.holes:
            return "–"
        ds = sorted({h["d"] for h in self.holes}, reverse=True)
        want = sum(p[2] for p in self.hole_plan) if self.hole_plan else len(self.holes)
        cnt = f"{len(self.holes)}" if len(self.holes) >= want else f"{len(self.holes)}/{want}"
        return f"{cnt} × {'/'.join(f'{d:g}' for d in ds)} mm"


@dataclass
class Run:
    kit: Path
    settings: Settings
    log: callable = print
    parts: dict[str, sio.Part] = field(default_factory=dict)
    asm: Assembly | None = None
    fields: dict = field(default_factory=dict)
    samples: dict = field(default_factory=dict)
    results: dict[str, PartResult] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    previews: dict[str, Path] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)
    scale: float = 1.0
    height_before: float = 0.0
    height_after: float = 0.0
    figure_height_before: float = 0.0
    maps: dict = field(default_factory=dict)
    layout: object = None
    plate_checks: dict = field(default_factory=dict)
    layouts: dict = field(default_factory=dict)
    wall_back: tuple | None = None          # (duvar yönü vektörü, nasıl bulundu) — sadece --wall-mount
    print_minutes: float = 0.0              # tahmini toplam baskı süresi (dk; plate başına en yüksek parça)
    # birden çok alt klasör varsa: {chosen: folders|single, auto, folders: {n_plates, minutes}, single: {...}}
    grouping: dict | None = None

    @property
    def out(self) -> Path:
        return out_dir(self.kit)

    @property
    def front_vector(self) -> np.ndarray:
        """Figürün baktığı yön (montaj koordinatı): kullanıcının verdiği (--front-axis) ya da export kuralı. Destek
        yeri seçiminde arka = −ön, önizlemelerde 'önden' = bu yön."""
        from .render import default_views, parse_axis

        asm = self.asm
        k, sgn = (asm.up_axis, asm.up_sign) if asm is not None else (2, 1)
        return default_views(k, sgn, parse_axis(self.settings.front_axis))["front"][1]

    def _t(self, name, t0):
        self.timings[name] = round(time.time() - t0, 1)

    # ------------------------------------------------------------ aşamalar
    def load(self, progress=None):
        t0 = time.time()
        plist = sio.load_kit(self.kit, progress=progress, exclude=self.settings.exclude)
        if not plist:
            raise SystemExit(f"Klasörde STL bulunamadı: {self.kit}")
        self.parts = {p.name: p for p in plist}
        w = sio.unit_warning(plist)
        if w:
            self.warnings.append(w)
        from .scan import _SUPPORT_HINT

        if _SUPPORT_HINT.search(" ".join([*self.kit.parts[-3:], *self.parts])):
            self.warnings.append("Klasör/dosya adı ön-destekli (pre-supported) kit olduğunu gösteriyor: destekli "
                                 "modeller hollow/delik/yönlendirme için uygun değil, desteksiz sürümü kullanın.")
        for p in plist:
            self.results[p.name] = PartResult(p.name, vol_before=p.volume_ml, vol_after=p.volume_ml)
            for msg in p.warnings:
                self.results[p.name].warnings.append(msg)
        self._t("yükleme", t0)

    def assemble(self):
        t0 = time.time()
        s = self.settings
        plist = list(self.parts.values())
        variants = sio.expand(s.variants, plist)
        for v in s.variants:
            if not sio.expand([v], plist):
                self.warnings.append(f"--variant {v}: eşleşen parça yok.")
        base = sio.expand(s.base, plist)
        import hashlib
        import pickle

        from .assembly import ASSEMBLY_VERSION
        from .geom import DistanceField, sample_surface

        # montaj algoritmasının kaynak kodu değişince önbellek kendiliğinden geçersizleşsin
        src = hashlib.sha1(b"".join((Path(__file__).parent / f).read_bytes()
                                    for f in ("assembly.py", "variants.py", "autoassemble.py", "joints.py"))).hexdigest()
        detach = sio.expand(s.asm_detach, plist)
        join = [tuple(j) for j in (s.asm_join or []) if j[0] in self.parts and j[1] in self.parts]
        for j in s.asm_join or []:
            if j[0] not in self.parts or j[1] not in self.parts:
                self.warnings.append(f"Montaj bağlantısı {j[1]} → {j[0]}: parça bulunamadı (hariç tutulmuş olabilir).")
        key = hashlib.sha1(repr((ASSEMBLY_VERSION, src, sorted((p.name, p.n_faces, len(p.lite.faces), round(p.volume, 3),
                                                                p.bounds.round(4).tolist())
                                                          for p in self.parts.values()),
                                 s.up_axis, variants, base, sorted(detach), join)).encode()).hexdigest()
        # ayar/parça seti başına ayrı dosya: farklı ayarlarla (ör. regresyon, duvar sürümü) çalışmak diğerinin
        # önbelleğini ezmesin; en yeni birkaç tanesi tutulur
        cache = self.out / "cache" / f"assembly_{key[:16]}.pkl"
        legacy = self.out / "cache" / "assembly.pkl"          # eski tek dosyalı önbellek: eşleşirse taşı
        if not cache.exists() and legacy.exists():
            try:
                if pickle.loads(legacy.read_bytes())[0] == key:
                    legacy.replace(cache)
            except Exception:
                pass
        cached = None
        if cache.exists():
            try:
                k, cached = pickle.loads(cache.read_bytes())
                cached = cached if k == key else None
            except Exception:
                cached = None
        if cached is not None:
            self.log("Montaj önbellekten alındı.")
            self.asm = cached
            self.fields = {n: DistanceField(p.lite) for n, p in self.parts.items()}
            self.samples = {n: sample_surface(p.lite) for n, p in self.parts.items()}
        else:
            self.asm, self.fields, self.samples = solve_assembly(
                list(self.parts.values()), s.up_axis, variants, base, log=self.log, detach=detach, join=join)
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(".tmp")              # yarım dosya kalmasın (yazarken süreç düşerse)
            tmp.write_bytes(pickle.dumps((key, self.asm)))
            tmp.replace(cache)
            old = sorted(cache.parent.glob("assembly*.pkl"), key=lambda f: f.stat().st_mtime, reverse=True)
            for f in old[6:]:
                f.unlink(missing_ok=True)
        refs = set(getattr(self.asm, "reference_parts", None) or [])
        for w in self.asm.warnings:
            head = "Hiçbir parçaya temas etmiyor:"
            if refs and w.startswith(head):           # ölçek referansı zaten ayrıca bildiriliyor
                rest = [x.strip() for x in w[len(head):].split(",") if x.strip() not in refs]
                if not rest:
                    continue
                w = f"{head} {', '.join(rest)}"
            self.warnings.append(w)
        if self.asm.mode != "bilinmiyor":
            self.height_before = self.asm.height(self.parts)
            self.figure_height_before = self.asm.figure_height(self.parts)
        sel = set(self.asm.selected)
        refs = set(getattr(self.asm, "reference_parts", None) or []) | set(getattr(self.asm, "junk_parts", None) or [])
        for n, r in self.results.items():
            r.printed = (n in refs and s.print_reference) or (n not in refs and (n in sel or s.print_all_variants))
        if s.only:
            for n, r in self.results.items():
                p = self.parts[n]
                r.printed = r.printed and sio.matches(n, p.group, s.only, p.path.stem)
        self._t("montaj", t0)

    def write_assembly_outputs(self):
        """assembled.stl (analiz çözünürlüğünde) + önden/yandan önizleme."""
        t0 = time.time()
        asm = self.asm
        names = [n for n in asm.selected if asm.placed.get(n) or asm.mode == "tam"]
        if asm.mode == "bilinmiyor":
            names = list(asm.selected)
        meshes = []
        for n in names:
            m = self.parts[n].lite.copy()
            m.apply_translation(asm.offsets[n])
            if self.scale != 1.0:
                m.apply_scale(self.scale)  # orijine göre: montaj konumları da ölçeklenir
            meshes.append((n, m))
        if asm.mode != "bilinmiyor":
            sio.save_stl(trimesh.util.concatenate([m for _, m in meshes]), self.out / "assembled.stl")
        pv = [(n, preview_mesh(m, 10000)) for n, m in meshes]
        title = self.kit.name if asm.mode != "bilinmiyor" else f"{self.kit.name} (montaj bilinmiyor)"
        fr = self.front_vector
        self.previews.update(render_assembly(pv, asm.up_axis, asm.up_sign, self.out / "previews", title, front=fr))
        if asm.mode != "bilinmiyor":
            # stüdyo fotoğraflarıyla karşılaştırma için 8 açıdan gri görünüm (uygulamada Montaj kontrolü sekmesi)
            render_turntable(pv, asm.up_axis, asm.up_sign, self.out / "previews", front=fr)
            # boy görünümü (uygulamada cetvel hedef boya göre canlı çizilir; raporda cetvelli kopya)
            self.previews.update(render_height_view(pv, list(asm.base_parts), asm.up_axis, asm.up_sign,
                                                    self.out / "previews", self.scale, front=fr))
        self._t("montaj çıktıları", t0)

    def surface_maps(self, render=True):
        """Aşama 2: gizli yüzey haritası + parça önizlemeleri."""
        from .joints import build_maps
        from .render import label_colors, render_part

        t0 = time.time()
        self.maps = build_maps(self)
        planar = [n for n, m in self.maps.items() if m.method == "düzlemsel" and self.results[n].printed]
        if planar and (self.asm is None or self.asm.mode == "bilinmiyor"):
            self.warnings.append("Montaj bilinmiyor: delikler tahmini kesim yüzeylerine (büyük düz yüzeyler) açılacak.")
        elif planar:
            self.warnings.append("Konumu bilinmeyen parçalarda delikler tahmini kesim yüzeylerine (büyük düz yüzeyler) "
                                 "açılacak: " + ", ".join(planar))
        for n, m in self.maps.items():
            mesh = self.parts[n].lite
            r = self.results[n]
            r.hidden_frac = m.hidden_area(mesh) / mesh.area
            if render and r.printed:
                hid = m.hidden
                focus = (mesh.face_normals[hid] * mesh.area_faces[hid, None]).sum(0) if hid.any() else None
                up = self.asm.up_vector if self.asm else np.array([0, 0, 1.0])
                self.previews[f"part:{n}"] = render_part(
                    mesh, label_colors(m.hidden, m.safe, m.pin, m.allowed), self.out / "previews" / f"part_{n}.png",
                    f"{n} — gizli %{r.hidden_frac*100:.0f} ({m.method})", focus, up, self._hole_marks(n))
        self._t("gizli yüzeyler", t0)
        return self.maps

    # ------------------------------------------------------------ prep: aşama 3-5
    def prepare(self):
        from .hollow import measure_wall
        from .layout import fmt_duration, pack, prefer_folders
        from .scale import compute_scale

        t0 = time.time()
        s = self.settings
        self.prepared = True                 # rapor: hazır hacimler (reçine/maliyet) gösterilebilir
        self.scale, w = compute_scale(self)
        self.warnings.extend(w)
        self.height_after = self.height_before * self.scale
        if (self.asm is None or self.asm.mode == "bilinmiyor") and s.orig_height:
            self.height_after = s.orig_height * self.scale        # montaj yok: kullanıcının verdiği orijinal boy
        if s.presupported and s.steps == "hollow":
            # "sadece hollow" istendi ama hazır desteklenmiş kitte hollow yapılmıyor: yapılacak iş kalmaz
            self.warnings.append("Hazır desteklenmiş kitte hollow yapılmaz: parçalar olduğu gibi kopyalanacak. "
                                 "Plate'e dizmek için 'Yönlendir + plate'e diz' işlemini açın.")
        self.surface_maps(render=False)
        work = self.out / "cache" / "work"
        work.mkdir(parents=True, exist_ok=True)
        names = [n for n, r in self.results.items() if r.printed]
        names.sort(key=lambda n: -self.parts[n].volume)
        infos = {}
        for i, n in enumerate(names, 1):
            self.log(f"[{i}/{len(names)}] {n} hazırlanıyor…")
            tp = time.time()
            infos[n] = self._prepare_part(n, work)
            self.timings[f"parça:{n}"] = round(time.time() - tp, 1)
        if s.steps == "hollow":
            # Sadece hollow + delik: plate dizilmez. Delikler yine parçanın en iyi baskı yönüne göre seçilir
            # (vantuz çanakları), ama çıktı parçanın orijinal (montaj) duruşundadır; yönü kullanıcı verir.
            for n in names:
                self._finish_part(n, work, infos[n]["ori"], rotate=False)
            self._t("parça hazırlama", t0)
            self.layouts, self.plate_checks, self.print_minutes = {}, {}, 0.0
            self._export_parts_only(names, work)
            self.write_assembly_outputs()
            self.save_json("prep.json")
            return
        t2 = time.time()
        choice = self._choose_orientations(infos)
        self._t("yön seçimi", t2)
        oriented = {}
        sil = {}
        for n in names:
            sil[n] = self._finish_part(n, work, choice[n])
            o = infos[n]["lite"].copy()
            o.apply_transform(choice[n].R)
            oriented[n] = o
        self._t("parça hazırlama", t0)
        # plate yerleşimi (alt klasörlere göre gruplu veya tek grup)
        t1 = time.time()
        W, D, Z = s.build_volume
        tm = self.time_model
        kw = dict(goal=s.plate_goal, time_model=tm)
        # "ayrı plate" parçaları (ör. şeffaf reçine) diğerleriyle karışmaz: kendi plate grubuna dizilir
        alone = self._separate_names(names)
        rest = [n for n in names if n not in alone]
        folders = self._folder_groups(rest)
        sep = one = None
        if len(folders) > 1 and s.plates_by_folder is not False:
            sep = {g: pack({n: sil[n] for n in members}, (W, D), s.gap, s.plates, **kw)
                   for g, members in folders.items()}
        if rest and (s.plates_by_folder is not True or sep is None):
            one = pack({n: sil[n] for n in rest}, (W, D), s.gap, s.plates, **kw)
        if sep is not None and one is not None:
            # otomatik: klasörlere ayırmak sadece hedefe (süre / plate) mal olmuyorsa — dosya düzeni derli toplu kalır
            use_sep = prefer_folders(list(sep.values()), one, tm, s.plate_goal)
        else:
            use_sep = sep is not None
        self.layouts = sep if use_sep else ({"": one} if one is not None else {})
        if alone:
            self.layouts[SEPARATE_GROUP] = pack({n: sil[n] for n in alone}, (W, D), s.gap, None, **kw)
            self.log(f"Ayrı plate'e dizilen: {', '.join(alone)}")
        self.grouping = None
        if len(folders) > 1:
            def stat(lays):
                return None if lays is None else dict(
                    n_plates=sum(lay.n_plates for lay in lays), minutes=round(sum(lay.seconds(tm) for lay in lays) / 60, 1))
            self.grouping = dict(chosen="folders" if use_sep else "single",
                                 auto=s.plates_by_folder is None,
                                 folders=stat(None if sep is None else list(sep.values())),
                                 single=stat(None if one is None else [one]))
            if s.plates_by_folder is None:
                self.log(f"Plate düzeni (otomatik): {'alt klasörlere göre' if use_sep else 'tek grup'} — klasörlere göre "
                         f"{self.grouping['folders']['n_plates']} plate ~{fmt_duration(self.grouping['folders']['minutes'] * 60)}, "
                         f"tek grup {self.grouping['single']['n_plates']} plate "
                         f"~{fmt_duration(self.grouping['single']['minutes'] * 60)}.")
        for g, lay in self.layouts.items():
            for n in lay.unplaceable:
                self.results[n].warnings.append("Plate'e hiçbir yönde sığmıyor (build volume'den büyük).")
            if s.plates is not None and lay.unplaceable:
                self.warnings.append(f"--plates {s.plates}{f' ({g})' if g else ''}: parçalar {s.plates} plate'e "
                                     f"sığmıyor ({len(lay.unplaceable)} parça dışarıda kaldı).")
        too_big = [n for n in names if not choice[n].fits]
        if too_big:
            # parçanın en sıkı kutusuyla (yönlü sınır kutusu) sığacağı ölçek; eğim/destek payı için %5 daha küçük öner
            ext = {n: np.sort(infos[n]["lite"].bounding_box_oriented.primitive.extents) for n in too_big}
            b = np.sort([W - 2 * s.gap, D - 2 * s.gap, Z - s.support_height])
            k = 0.95 * min(float(np.min(b / np.maximum(ext[n], 1e-6))) for n in too_big)
            sizes = ", ".join(f"{n} ({'×'.join(f'{e:.0f}' for e in ext[n][::-1])} mm)" for n in too_big)
            h = getattr(self, "height_after", 0.0)
            self.warnings.append(
                f"Yazıcıya sığmayan parçalar (plate'e konmadı; parts/ altında ayrı dosya): {sizes}. Kiti en çok "
                f"~%{100 * k:.0f}'ine küçültün (--scale {self.scale * k:.2f}"
                + (f", ya da --height {h * k:.0f}" if h else "") + ") veya bu parçaları CHITUBOX'ta bölün.")
        self._export(oriented, work)
        self._t("yerleşim + export", t1)
        self.write_assembly_outputs()
        self.save_json("prep.json")

    @property
    def time_model(self):
        from .layout import TimeModel

        s = self.settings
        return TimeModel(s.layer_height, s.layer_time, s.plate_overhead_min * 60.0)

    def _folder_groups(self, names) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for n in names:
            out.setdefault(self.parts[n].group, []).append(n)
        return dict(sorted(out.items()))

    def _separate_names(self, names) -> list[str]:
        """Kullanıcının "ayrı plate" dediği parçalar (--separate; ör. şeffaf reçineyle basılacak şişe)."""
        s = self.settings
        return [n for n in names if sio.matches(n, self.parts[n].group, s.separate, self.parts[n].path.stem)]

    def plate_groups(self, names) -> dict[str, list[str]]:
        """Yön aramasında kullanılan plate grupları: --plates-by-folder ise alt klasörlere göre, --no-plates-by-folder
        ise tek grup; otomatikte yön araması başında hızlı karşılaştırmayla verilen karar (_auto_by_folder).
        "Ayrı plate" parçaları her zaman kendi grubundadır. Son yerleşimde iki düzen yeniden karşılaştırılır (prepare)."""
        by_folder = self.settings.plates_by_folder
        if by_folder is None:
            by_folder = getattr(self, "_auto_by_folder", False)
        sep = self._separate_names(names)
        rest = [n for n in names if n not in sep]
        out = (self._folder_groups(rest) if by_folder else {"": rest}) if rest else {}
        if sep:
            out[SEPARATE_GROUP] = sep
        return out

    def _part_off(self, n):
        """Parçanın montaj ötelemesi: ana montajdaysa montaj konumu, kendi içinde birleşmiş bir gruptaysa grup içi
        konumu, yoksa sıfır."""
        asm = self.asm
        if asm is None:
            return np.zeros(3)
        if asm.placed.get(n):
            return asm.offsets[n]
        return next((g[n] for g in getattr(asm, "groups", None) or [] if n in g), np.zeros(3))

    def _assembly_xf(self, n):
        """Parçanın orijinal koordinatından ölçekli montaj koordinatına."""
        off = self._part_off(n)
        T = np.eye(4) * self.scale
        T[3, 3] = 1.0
        T[:3, 3] = off * self.scale
        return T

    def _prepare_part(self, n, work):
        from .hollow import hollow_part, measure_wall, to_manifold, from_manifold
        from .holes import candidate_points_from_map, choose_holes, find_candidates, hole_cylinder, hole_diameters
        from .joints import compute_safe
        from .orient import orient_part

        s = self.settings
        p = self.parts[n]
        r = self.results[n]
        m = self.maps[n]
        X = self._assembly_xf(n)
        full = p.load_full()
        full.apply_transform(X)
        lite = p.lite.copy()
        lite.apply_transform(X)
        r.scale = self.scale
        r.vol_before = abs(full.volume) / 1000
        r.vol_after = r.vol_before
        force_solid = sio.matches(n, p.group, s.solid, p.path.stem)
        force_hollow = sio.matches(n, p.group, s.hollow, p.path.stem)
        cavities = []
        if s.presupported:
            # Hazır desteklenmiş kit: destekler modelin içinde. Hollow etmek destek köklerini açar,
            # yönlendirmek desteklerin yönünü bozar: parça olduğu gibi bırakılıp sadece plate'e dizilir.
            r.hollow_reason = "hazır desteklenmiş: dolu bırakıldı"
        elif s.steps == "plates":
            r.hollow_reason = "sadece plate dizimi: dolu"
        elif force_solid:
            r.hollow_reason = "dolu: sizin tercihiniz (--solid)"
        elif p.repair_failed:
            r.hollow_reason = "watertight değil, onarılamadı"
        else:
            h = hollow_part(full, s.wall, min_cavity_ml=s.min_cavity_ml, min_ml=s.hollow_min_ml,
                            min_frac=s.hollow_min_frac, max_cavities=s.max_cavities, force=force_hollow)
            r.hollow_reason = h.reason
            cavities = h.cavities if h.hollowed else []
            if force_hollow and not h.hollowed:
                r.warnings.append(f"--hollow istendi ama hollow edilemedi: {h.reason}")
        up = self.asm.up_vector if self.asm else np.array([0, 0, 1.0])
        cand = None
        cand_by_d = {}
        if cavities:
            # her çap için güvenli bölge ayrı: büyük delik, gizli bölge sınırından daha uzakta olmalı
            for d in hole_diameters(s):
                compute_safe(m, p.lite, (d / 2 + s.hole_margin) / self.scale)
                pts, nrm = candidate_points_from_map(lite, m.safe, spacing=max(0.8, d / 3))
                vis = np.zeros(len(pts), bool)
                if s.allow_visible_holes:
                    down = ~m.hidden & (lite.face_normals @ up < -0.5)
                    vp, vn = candidate_points_from_map(lite, down, spacing=max(0.8, d / 3))
                    pts, nrm = np.vstack([pts, vp]), np.vstack([nrm, vn])
                    vis = np.concatenate([vis, np.ones(len(vp), bool)])
                cand_by_d[d] = find_candidates(full, cavities, pts, nrm, s.wall, d, vis)
            cand = cand_by_d[min(cand_by_d)]       # yönlendirme skoru için en geniş aday kümesi
        if s.presupported:
            from .orient import keep_pose

            # destekli parçanın altlığı z=0'da: ayrıca kaldırılmaz (support_height eklenmez)
            ori = keep_pose(lite, s.build_volume, s.gap)
        else:
            ori = orient_part(lite, m.hidden, m.pin, up, s.build_volume, s.support_height, s.gap,
                              (s.tilt_min, s.tilt_max), cand, cavities, s.wall,
                              vis=getattr(m, "visibility", None), front=self.front_vector)
        # aşama 1 bitti: ağır veriyi diske yaz, yön global yerleşimde kesinleşecek
        import pickle

        with open(work / f"{n}.p1.pkl", "wb") as fh:
            pickle.dump(dict(v=full.vertices.astype(np.float32), f=full.faces.astype(np.int32),
                             cavities=cavities, cand_by_d=cand_by_d), fh, protocol=pickle.HIGHEST_PROTOCOL)
        return dict(lite=lite, ori=ori)

    def _finish_part(self, n, work, ori, rotate: bool = True):
        """Aşama 2: seçilen yöne göre delikler, boolean, çıktı. rotate=False: çıktı orijinal duruşta kalır."""
        import pickle

        from .hollow import measure_wall, to_manifold, from_manifold
        from .holes import choose_holes, hole_cylinder

        s = self.settings
        r = self.results[n]
        with open(work / f"{n}.p1.pkl", "rb") as fh:
            st = pickle.load(fh)
        (work / f"{n}.p1.pkl").unlink(missing_ok=True)
        full = trimesh.Trimesh(st["v"].astype(float), st["f"], process=False)
        cavities, cand_by_d = st["cavities"], st["cand_by_d"]
        r.orientation = ori.describe()
        t = ori.terms
        if "sup_vis" in t:
            r.support = dict(hidden=round(t["sup_hidden"], 3), low=round(t["sup_low"], 3),
                             visible=round(max(t["sup_vis"], 0.0), 3))
        if not ori.fits:
            r.warnings.append("Hiçbir yönde build volume'e sığmıyor.")
        holes = []
        if cavities:
            holes, missing, plan = choose_holes(cand_by_d, cavities, ori.R, s)
            r.hole_plan = plan
            small = sorted({d for *_, d in holes if d < s.hole})
            if small:
                r.warnings.append(f"Birleşme yüzeyi dar: bazı delikler {', '.join(f'{d:g}' for d in small)} mm'ye düşürüldü.")
            short = [(cnt, want) for _, cnt, want, _, _ in plan if cnt < want]
            if short:
                r.warnings.append("Birleşme yüzeylerinde hedeflenen kadar delik yeri yok: "
                                  + ", ".join(f"{c}/{w}" for c, w in short) + " delik açıldı.")
            cups = sum(st["cups"] for *_, st in plan)
            unvented = sum(st["cups"] - st["cups_vented"] for *_, st in plan)
            if unvented:
                r.warnings.append(f"Vantuz riski: {cups} çanaktan {unvented} tanesine birleşme yüzeyinden delik "
                                  "açılamadı (baskı sırasında FEP'e yapışma/çekme olabilir).")
            if missing:
                keep = [ci for ci in range(len(cavities)) if ci not in missing]
                lost = sum(cavities[ci].volume for ci in missing) / 1000
                if not keep:
                    r.hollow_reason = "dolu bırakıldı: birleşme yüzeyinde delik yeri yok"
                    r.warnings.append("Dolu bırakıldı: birleşme yüzeylerinde delik yeri yok (dış yüzeye delik "
                                      "istersen: --holes-on-bottom / --holes-on-wall-back / --allow-visible-holes).")
                    cavities, holes = [], []
                else:
                    r.warnings.append(f"{len(missing)} kavite ({lost:.1f} ml) için birleşme yüzeyinde delik yeri yok: "
                                      "o bölge dolu.")
                    remap = {old: new for new, old in enumerate(keep)}
                    cavities = [cavities[ci] for ci in keep]
                    holes = [(a, b, remap[c], d) for a, b, c, d in holes if c in remap]
                    r.hole_plan = [(remap[c], *rest) for c, *rest in r.hole_plan if c in remap]
        # boolean
        out = None
        if cavities:
            try:
                man = to_manifold(full, repair=True)
                subs = [to_manifold(c.mesh, repair=True) for c in cavities] + \
                       [hole_cylinder(a, b, d, s.wall) for a, b, _, d in holes]
                import manifold3d as mf

                man = man - mf.Manifold.batch_boolean(subs, mf.OpType.Add)
                out = from_manifold(man)
                r.hollowed = True
                r.cavities = len(cavities)
                r.wall_measured = measure_wall(full, cavities, s.wall)
            except ValueError as e:
                # tek bir parçanın geometrisi yüzünden bütün hazırlık çökmesin: bu parça dolu basılır
                self.log(f"  ! {n}: hollow/delik boolean'ı yapılamadı ({e}); dolu bırakılıyor.")
                r.warnings = [w for w in r.warnings if not w.startswith(
                    ("Vantuz", "Birleşme yüzeyi dar", "Birleşme yüzeylerinde hedeflenen", "Dolu bırakıldı"))]
                r.hole_plan = []
                r.warnings.append("Geometri hollow/delik işlemine uygun değil ve onarılamadı (manifold değil): dolu "
                                  "bırakıldı, delik açılmadı.")
                r.hollow_reason = "dolu bırakıldı: geometri boolean'a uygun değil"
                cavities, holes = [], []
        if out is None:
            out = full
            r.hollowed = False
        r.vol_after = abs(out.volume) / 1000
        # kapalı: her kenar 2 yüzlü (trimesh) ya da boolean motorunun kabul ettiği kapalı manifold (iki yüzeyin bir
        # kenarda değdiği sculpt dosyaları: çakışık köşelerle geçerli)
        r.watertight_out = bool(out.is_watertight) or sio.manifold_ok(out)
        r.holes = [dict(pos=a.tolist(), normal=b.tolist(), d=float(d), cavity=int(c)) for a, b, c, d in holes]
        self._check_holes(n, r, holes, cavities)
        if rotate:
            out.apply_transform(ori.R)
        np.savez(work / f"{n}.npz", v=out.vertices.astype(np.float32), f=out.faces.astype(np.int32))
        r.R = ori.R if rotate else np.eye(4)
        from .layout import full_silhouette

        return full_silhouette(out)         # son yerleşim tam çözünürlüklü silueti kullanır

    def _choose_orientations(self, infos: dict) -> dict:
        """Global yön seçimi: önce her parçanın en iyi yönü; plate sayısı alan alt sınırının üstündeyse büyük izli
        parçaları puanı yakın, daha küçük izli alternatif yönlerine çevirip yeniden yerleştir. Sadece plate
        (veya sığmayan parça) sayısı azalıyorsa kabul edilir — yön kalitesinden boşuna ödün verilmez."""
        from .layout import area_lower_bound, fmt_duration, pack, prefer_folders

        s = self.settings
        W, D, Z = s.build_volume
        choice = {n: info["ori"] for n, info in infos.items()}

        def olite(n, o):
            m = infos[n]["lite"].copy()
            m.apply_transform(o.R)
            return m

        tm = self.time_model
        fast = dict(yaw_step=90, strategies=("contact",), orders=("area", "height"), random_orders=0,
                    goal=s.plate_goal, time_model=tm)
        plates_key = lambda lay: (len(lay.unplaceable), lay.n_plates, round(lay.seconds(tm) / 60))  # noqa: E731
        # en kısa süre + plate başına karar eşiği: fazladan plate ancak belirgin süre kazancıyla
        time_key = lambda lay: (len(lay.unplaceable), round(tm.decision_minutes(lay)), lay.n_plates)  # noqa: E731
        final_key = plates_key if s.plate_goal == "plates" else time_key

        def search(members, alt_parts, start, start_items, start_lay, key, lb, by_height):
            """Açgözlü yön araması: her adımda ölçütü iyileştiren ilk alternatif yönü al."""
            ch, items, cur, steps = dict(start), dict(start_items), start_lay, []
            for _ in range(6):
                if key is plates_key and cur.n_plates <= lb and not cur.unplaceable:
                    break
                improved = False
                # büyük izliler (plate sayısını düşürür) ve uzun parçalar (alçaltmak plate'in süresini kısaltır)
                by_fp = sorted(alt_parts, key=lambda n: -np.prod(ch[n].footprint))[:8]
                by_h = sorted(alt_parts, key=lambda n: -ch[n].height)[:8] if by_height else []
                order = list(dict.fromkeys([x for pair in zip(by_h, by_fp) for x in pair] + by_fp + by_h))
                for n in order:
                    for o in infos[n]["ori"].alternatives[1:]:
                        if o is ch[n]:
                            continue
                        trial = dict(items)
                        trial[n] = olite(n, o)
                        lay = pack(trial, (W, D), s.gap, s.plates, **fast)
                        if key(lay) < key(cur):
                            steps.append((n, ch[n], o, lay))
                            ch[n], items, cur, improved = o, trial, lay, True
                            break
                    if improved:
                        break
                if not improved:
                    break
            return ch, cur, steps

        # otomatik plate düzeni: yön aramasını hangi düzende yapacağımıza hızlı bir karşılaştırmayla karar ver
        folders = self._folder_groups(list(infos))
        if s.plates_by_folder is None and len(folders) > 1:
            all_items = {n: olite(n, choice[n]) for n in infos}
            sep = [pack({n: all_items[n] for n in mem}, (W, D), s.gap, s.plates, **fast) for mem in folders.values()]
            one = pack(all_items, (W, D), s.gap, s.plates, **fast)
            self._auto_by_folder = prefer_folders(sep, one, tm, s.plate_goal)
        for g, members in self.plate_groups(list(infos)).items():
            alt_parts = [n for n in members if len(infos[n]["ori"].alternatives or []) > 1]
            if not alt_parts or len(members) < 2:
                continue
            items = {n: olite(n, choice[n]) for n in members}
            cur = pack(items, (W, D), s.gap, s.plates, **fast)
            lb = int(np.ceil(area_lower_bound(items, (W, D), s.gap) - 1e-6))
            self.log(f"Plate verimi{f' ({g})' if g else ''}: {cur.n_plates} plate, alan alt sınırı {lb}, "
                     f"tahmini süre {fmt_duration(cur.seconds(tm))}.")
            # açgözlü arama yola bağlı (Sanji: süreyi izleyen arama ilk adımda başka yola sapıp 3 plate'te kalıyordu):
            # hedef "en kısa süre" ise plate sayısını izleyen arama da yapılır, sonuçlar aynı ölçütle karşılaştırılır
            runs = [search(members, alt_parts, choice, items, cur, plates_key, lb, False)]
            if s.plate_goal != "plates":
                runs.append(search(members, alt_parts, choice, items, cur, time_key, lb, True))
            ch, lay, steps = min(runs, key=lambda r: final_key(r[1]))
            for n, o_old, o_new, lay_n in steps:
                lower = o_new.height < o_old.height - 1.0
                self.log(f"  {n}: {'daha alçak' if lower else 'daha küçük izli'} yöne çevrildi ({o_old.height:.0f} → "
                         f"{o_new.height:.0f} mm) → {lay_n.n_plates} plate, {fmt_duration(lay_n.seconds(tm))}")
            for n in members:
                if ch[n] is not choice[n]:
                    o0 = infos[n]["ori"]
                    lower = ch[n].height < o0.height - 1.0
                    peel = (f" (yükseklik {o0.height:.0f} → {ch[n].height:.0f} mm, en büyük katman kesiti "
                            f"{o0.terms['peel'] / 100:.1f} → {ch[n].terms['peel'] / 100:.1f} cm²)")
                    self.results[n].warnings.append(
                        ("Baskı süresi için puanı yakın, daha alçak bir yön seçildi" if lower else
                         "Plate verimi için puanı yakın, taban izi daha küçük bir yön seçildi") + peel + ".")
                    choice[n] = ch[n]
        return choice

    def _check_holes(self, n, r, holes, cavities):
        """Otomatik kontroller: kavite başına ≥ 2 delik, delik ağzı montajda örtülü, pim üzerinde değil."""
        from .holes import mouth_points
        from .joints import COVER_RAY

        if not cavities:
            r.checks["watertight"] = r.watertight_out
            return
        per = np.bincount([c for _, _, c, _ in holes], minlength=len(cavities))
        r.checks["kavite başına ≥2 delik"] = bool(np.all(per >= 2))
        if r.hole_plan:
            r.checks["vantuz çanakları delikli"] = all(st["cups_vented"] >= st["cups"] for *_, st in r.hole_plan)
        r.checks["watertight"] = r.watertight_out
        m = self.maps[n]
        lite = self.parts[n].lite
        from scipy.spatial import cKDTree

        tree = cKDTree(lite.triangles_center)
        asm = self.asm
        # ana montajda değil ama kendi içinde birleşmiş bir gruptaysa (otomatik montaj): grup içi konumlar
        frame = next((g for g in getattr(asm, "groups", None) or [] if n in g), None)
        off = self._part_off(n)
        if frame is not None and not asm.placed.get(n):
            offs, others = frame, [o for o in frame if o != n]
        else:
            offs = asm.offsets
            others = [o for o in asm.selected if o != n and asm.placed.get(o)]
        covered_all, pin_free = True, True
        import open3d as o3d

        from .joints import _cast

        scene = self.fields[n].scene
        for a, b, _, diameter in holes:
            q = mouth_points(a, b, diameter) / self.scale - off          # parçanın orijinal koordinatı
            # Çember noktalarını delik ekseni boyunca yüzeye oturt (matkabın keseceği yer). En yakın nokta
            # izdüşümü pürüzlü yüzeylerde (tüy, pul) yandaki sivriye kayabiliyor.
            bn = np.asarray(b, float) / np.linalg.norm(b)
            rays = np.hstack([q + bn * 3.0, np.tile(-bn, (len(q), 1))]).astype(np.float32)
            hit = scene.cast_rays(o3d.core.Tensor(rays))
            th = hit["t_hit"].numpy()
            pid = hit["primitive_ids"].numpy()
            okr = np.isfinite(th) & (th < 13.0)          # ağız bir kademe/oluk üstündeyse oluğun tabanı
            cp = scene.compute_closest_points(o3d.core.Tensor(q.astype(np.float32)))
            q2 = q.copy()                      # ışın isabet etmezse: ağız kenarı parçanın dışına taşıyor;
            q2[okr] = q[okr] + bn * 3.0 - bn * th[okr, None]   # o nokta eksen düzleminde kalır
            fidx = cp["primitive_ids"].numpy().astype(np.int64)
            fidx[okr] = pid[okr]
            qn = lite.face_normals[fidx]
            qn[~okr] = bn                      # taşan noktada örtülme delik ekseni yönünde test edilir
            q = q2
            fi = fidx
            if m.pin[fi[0]]:
                pin_free = False
            # Ağız ancak komşu parça tarafından kapatılıyorsa (birleşme yüzeyi) geçerli. Kaide altı / duvar arkası
            # sadece kullanıcı izin verdiyse sayılır (dış yüzey: baskıdan sonra tıkanması gerekir).
            ok = np.zeros(len(q), bool)
            if self.settings.hole_on_bottom:
                ok |= m.bottom[fi] & okr
            if self.settings.hole_on_wall and m.wall_facing is not None:
                ok |= m.wall_facing[fi] & okr
            if m.method.startswith("temas"):
                qa = q + off
                for o in others:
                    if ok.all():
                        break
                    qb = qa - offs[o]
                    d = self.fields[o].signed(qb)
                    ok |= d < self.settings.contact_tol + 0.05
                    rest = ~ok
                    if rest.any():
                        t = _cast(self.fields[o], qb[rest] + qn[rest] * 0.01, qn[rest])
                        hit = np.zeros(len(q), bool)
                        hit[np.nonzero(rest)[0]] = t < COVER_RAY + 0.5
                        ok |= hit
            else:
                ok |= m.candidate[fi] & okr
            covered_all &= bool(ok.all())
        r.checks["delikler birleşme yüzeyinde"] = covered_all
        r.checks["pim üzerinde delik yok"] = pin_free
        if not covered_all:
            r.warnings.append("Bir deliğin ağzı montajda komşu parçayla tamamen kapanmıyor olabilir "
                              "(önizlemeyi kontrol edin).")

    def _export_parts_only(self, names, work):
        """Sadece hollow + delik: parts/[grup/]<parça>.stl orijinal duruşta; plate dosyası yazılmaz (eskileri silinir)."""
        import shutil

        pdir, pldir = self.out / "parts", self.out / "plates"
        for d in (pdir, pldir):
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
        pdir.mkdir(parents=True, exist_ok=True)
        for f in (self.out / "previews").glob("*plate_*.png"):
            f.unlink()
        multi = len({self.parts[n].group for n in names}) > 1
        for n in names:
            d = np.load(work / f"{n}.npz")
            m = trimesh.Trimesh(d["v"].astype(float), d["f"], process=False)
            g = self.parts[n].group if multi else ""
            sio.save_stl(m, (pdir / g if g else pdir) / f"{n}.stl")
            self.results[n].plate = None

    def _export(self, oriented, work):
        """parts/[grup/]<parça>.stl ve plates/[grup/]plate_NN.stl yaz. Plate etiketi: "plate_01" veya "Base/plate_01"."""
        import shutil

        from .layout import placement_transform
        from .render import render_plate

        s = self.settings
        pdir = self.out / "parts"
        pldir = self.out / "plates"
        for d in (pdir, pldir):
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True, exist_ok=True)
        for f in (self.out / "previews").glob("*plate_*.png"):
            f.unlink()
        per_plate: dict[str, list] = {}
        for g, lay in self.layouts.items():
            for n, pl in lay.placements.items():
                d = np.load(work / f"{n}.npz")
                m = trimesh.Trimesh(d["v"].astype(float), d["f"], process=False)
                T = placement_transform(pl)
                m.apply_transform(T)
                sio.save_stl(m, (pdir / g if g else pdir) / f"{n}.stl")
                r = self.results[n]
                label = f"{g}/plate_{pl.plate:02d}" if g else f"plate_{pl.plate:02d}"
                r.plate = label
                full_T = T @ r.R
                for h in r.holes:
                    h["pos_out"] = (full_T[:3, :3] @ np.asarray(h["pos"]) + full_T[:3, 3]).round(2).tolist()
                ol = oriented[n].copy()
                ol.apply_transform(T)
                per_plate.setdefault(label, []).append((n, m, ol))
        # plate'e konamayan (yazıcıdan büyük) parçalar da kaybolmasın: elle bölünmek / küçültülmek üzere ayrı dosya
        placed_any = {n for lay in self.layouts.values() for n in lay.placements}
        for n in oriented:
            if n not in placed_any and (work / f"{n}.npz").exists():
                d = np.load(work / f"{n}.npz")
                m = trimesh.Trimesh(d["v"].astype(float), d["f"], process=False)
                m.apply_translation(-m.bounds[0])
                g = self.parts[n].group if len(self.layouts) > 1 else ""
                sio.save_stl(m, (pdir / g if g else pdir) / f"{n}.stl")
        W, D, Z = s.build_volume
        from .layout import fmt_duration

        tm = self.time_model
        self.plate_checks = {}
        for label, items in sorted(per_plate.items()):
            sio.save_stl(trimesh.util.concatenate([m for _, m, _ in items]), pldir / f"{label}.stl")
            self.plate_checks[label] = self._plate_check([m for _, m, _ in items], (W, D, Z))
            # baskı süresi plate'teki en yüksek parçayla belirlenir (destek yüksekliği dahil, tam çözünürlük)
            h = max(m.bounds[1][2] for _, m, _ in items)
            sec = tm.plate_seconds(h) + tm.plate_overhead
            self.plate_checks[label].update(max_height=round(float(h), 1), est_min=round(sec / 60.0, 1))
            png = self.out / "previews" / f"{label.replace('/', '__')}.png"
            self.previews[f"plate:{label}"] = render_plate(
                [(n, ol) for n, _, ol in items], (W, D), png,
                f"{label} — {len(items)} parça, en yüksek {h:.0f} mm, ~{fmt_duration(sec)}")
        self.print_minutes = round(sum(c["est_min"] for c in self.plate_checks.values()), 1)
        # parça önizlemeleri delikleriyle
        from .render import label_colors, render_part

        up = self.asm.up_vector
        for n in oriented:
            r = self.results[n]
            m = self.maps[n]
            mesh = self.parts[n].lite
            off = self._part_off(n)
            marks =[(np.asarray(h["pos"]) / self.scale - off, np.asarray(h["normal"]), h["d"] / self.scale)
                     for h in r.holes] or None
            hid = m.hidden
            focus = (mesh.face_normals[hid] * mesh.area_faces[hid, None]).sum(0) if hid.any() else None
            self.previews[f"part:{n}"] = render_part(
                mesh, label_colors(m.hidden, m.safe, m.pin, m.allowed), self.out / "previews" / f"part_{n}.png",
                f"{n} — {'hollow' if r.hollowed else 'dolu'}, {len(r.holes)} delik", focus, up, marks)

    def _plate_check(self, meshes, build):
        """Son plate'te: parçalar plate sınırları içinde mi, yükseklik sığıyor mu, parçalar arası en kısa XY mesafe."""
        from scipy import ndimage

        from .layout import CELL, silhouette

        W, D, Z = build
        nx, ny = int(np.ceil(W / CELL)) + 1, int(np.ceil(D / CELL)) + 1
        labels = np.zeros((nx, ny), np.int32)
        inside = True
        for i, m in enumerate(meshes, 1):
            inside &= bool(np.all(m.bounds[0][:2] >= [-W / 2 - 0.01, -D / 2 - 0.01])
                           and np.all(m.bounds[1][:2] <= [W / 2 + 0.01, D / 2 + 0.01]) and m.bounds[1][2] <= Z)
            mask, lo = silhouette(m)
            o = np.round((lo + [W / 2, D / 2]) / CELL).astype(int)
            a0, b0 = max(o[0], 0), max(o[1], 0)
            a1, b1 = min(o[0] + mask.shape[0], nx), min(o[1] + mask.shape[1], ny)
            sub = mask[a0 - o[0]:a1 - o[0], b0 - o[1]:b1 - o[1]]
            labels[a0:a1, b0:b1][sub] = i
        gap = np.inf
        for i in range(1, len(meshes) + 1):
            if len(meshes) < 2:
                break
            dist = ndimage.distance_transform_edt(labels != i) * CELL
            others = (labels > 0) & (labels != i)
            if others.any():
                gap = min(gap, float(dist[others].min()) - CELL)    # rasterizasyon payı
        return dict(inside=inside, min_gap=None if not np.isfinite(gap) else round(gap, 2))

    def _hole_marks(self, n):
        return [(np.asarray(h["pos"]), np.asarray(h["normal"]), h["d"]) for h in self.results[n].holes] or None

    def _result_dict(self, n) -> dict:
        r = self.results.get(n)
        if r is None:
            return {}
        return dict(printed=r.printed, hidden_frac=round(r.hidden_frac, 3), hollowed=r.hollowed,
                    hollow_reason=r.hollow_reason, cavities=r.cavities, holes=r.holes,
                    hole_plan=[dict(cavity=c, count=k, target=w, d=d, **st) for c, k, w, d, st in r.hole_plan],
                    vol_before_ml=round(r.vol_before, 2), vol_after_ml=round(r.vol_after, 2),
                    wall_measured=r.wall_measured, group=self.parts[n].group,
                    watertight_out=r.watertight_out, orientation=r.orientation, support=r.support, plate=r.plate,
                    checks=r.checks, warnings=r.warnings)

    def save_json(self, name="analysis.json"):
        asm = self.asm
        d = {
            "kit": str(self.kit),
            "parts": {
                n: dict(volume_ml=round(p.volume_ml, 2), faces=p.n_faces, watertight=p.watertight,
                        bodies=p.bodies, repaired=p.repaired, repair_failed=p.repair_failed,
                        bounds=np.round(p.bounds, 2).tolist(),
                        offset=np.round(asm.offsets[n], 3).tolist() if asm else None,
                        placed=asm.placed.get(n) if asm else None,
                        **self._result_dict(n))
                for n, p in self.parts.items()
            },
            "scale": self.scale,
            "height_after_mm": round(self.height_after, 2),
            "plates": dict(self.plate_checks),
            "print_time_min": self.print_minutes,
            "plate_goal": self.settings.plate_goal,
            "steps": self.settings.steps,
            "grouping": self.grouping,
        }
        if asm:
            d["assembly"] = dict(
                mode=asm.mode, up_axis=AXES[asm.up_axis], up_sign=asm.up_sign,
                zeroed_axes=[AXES[k] for k in asm.zeroed_axes], root=asm.root,
                selected=asm.selected, base_parts=asm.base_parts,
                reference_parts=list(getattr(asm, "reference_parts", None) or []),
                junk_parts=list(getattr(asm, "junk_parts", None) or []),
                height_mm=round(self.height_before, 2), figure_height_mm=round(self.figure_height_before, 2),
                variant_groups=[dict(members=g.members, selected=g.selected, reason=g.reason,
                                     overlap=round(g.overlap, 3)) for g in asm.variant_groups],
                unattached_groups=[sorted(g) for g in getattr(asm, "groups", None) or []],
                links=getattr(asm, "links", None) or {},
                fixes=getattr(asm, "fixes", None) or {},
                pairs=[dict(a=f.a, b=f.b, delta=round(f.delta, 3), contact_mm2=round(f.contact, 1),
                            pen_mm2=round(f.pen, 1), ambiguity=round(f.ambiguity, 2), accepted=f.accepted,
                            used=f.used, residual=None if np.isnan(f.residual) else round(f.residual, 3))
                       for f in sorted(asm.fits, key=lambda f: -f.contact)],
                max_penetration_mm=round(asm.max_penetration, 3),
                penetration=[dict(a=a, b=b, p99_mm=round(dd, 3), area_over_05_mm2=round(ar, 1))
                             for a, b, dd, ar in asm.penetration],
            )
        d["warnings"] = self.warnings
        d["timings_s"] = self.timings
        p = self.out / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
        return p
