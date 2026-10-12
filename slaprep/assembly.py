"""Montaj çözücü: koordinat durumu tespiti, eksik kaymaların çözümü, doğrulama.

Yöntem (kısmen kaymış kitler, ör. Rango): her parça sıfırlanmış eksende bilinmeyen bir kayma alır.
Her parça çifti için bu eksende 1B tarama yapılır; skor = temas alanı (yüzeyler arası < 0.3 mm)
− 3 × iç içe geçme alanı (> 0.6 mm). Düz kesim yüzeyleri, pim yan yüzleri ve organik temaslar
tek bir ölçütte birleşir. Güvenilir çiftler bir graf oluşturur; kaymalar en büyük yayılan ağaçla
başlatılıp aykırı değer reddiyle ağırlıklı en küçük karelerle çözülür.
"""
from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass, field

import networkx as nx
import numpy as np

from .geom import DistanceField, boxes_overlap, sample_surface
from .io import Part
from .variants import VariantGroup, detect_variants

AXES = "xyz"
ASSEMBLY_VERSION = 28   # algoritma değişince artır: montaj önbelleğini geçersiz kılar
CONTACT_TOL = 0.3      # mm: bu mesafedeki yüzey noktaları "temas"
PEN_TOL = 0.6          # mm: bundan derin noktalar "iç içe geçme" (pim toleransı ~0.4 + sadeleştirme payı)
GAP_TOL = 1.25         # mm: tam montajlı kitte parçalar arası montaj boşluğu (ZEZ Hulkbuster: 0.4–1.2 mm) temas sayılır
MIN_CONTACT = 20.0     # mm²: güvenilir eşleşme için en az temas alanı
MAX_AMBIGUITY = 0.6    # ikinci en iyi tepe / en iyi tepe
INLIER_RES = 1.0       # mm


@dataclass
class PairFit:
    a: str
    b: str
    delta: float           # t_b - t_a (sıfırlanmış eksende)
    contact: float         # mm²
    pen: float             # mm²
    ambiguity: float
    accepted: bool
    residual: float = math.nan
    used: bool = False


@dataclass
class Assembly:
    mode: str                          # "tam" | "kısmi" | "otomatik" | "bilinmiyor"
    up_axis: int
    up_sign: int
    zeroed_axes: list[int]
    offsets: dict[str, np.ndarray]     # her parçaya uygulanacak öteleme (montaj konumu)
    placed: dict[str, bool]            # konumu güvenilir mi
    selected: list[str]                # montaja giren parçalar (seçili varyantlar)
    variant_groups: list[VariantGroup]
    fits: list[PairFit] = field(default_factory=list)
    root: str | None = None
    penetration: list[tuple[str, str, float, float]] = field(default_factory=list)  # (a, b, p99 mm, alan>0.5 mm²)
    base_parts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # otomatik montajda ana montaja eklenemeyen ama kendi içinde birleşen gruplar: {üye: grup içi konum}
    groups: list[dict] = field(default_factory=list)
    # parça başına bağlantı: {to: komşu, contact: mm², kind: ek yüzeyi|kenetlenme|kullanıcı|kayma taraması|dosyadan|
    # kök|ayrıldı|kullanıcı ayırdı} — uygulamada güven/kontrol listesi için
    links: dict[str, dict] = field(default_factory=dict)
    fixes: dict = field(default_factory=dict)          # uygulanan kullanıcı düzeltmeleri {detach, join}
    reference_parts: list[str] = field(default_factory=list)   # ölçek/boy referansı (basılmaz, boya katılmaz)
    junk_parts: list[str] = field(default_factory=list)        # reklam/filigran dosyaları (montaja hiç katılmaz)

    @property
    def up_vector(self) -> np.ndarray:
        v = np.zeros(3)
        v[self.up_axis] = self.up_sign
        return v

    def up_range(self, parts: dict[str, Part], names=None) -> tuple[float, float]:
        names = names or self.selected
        # konumu bilinmeyen parçalar (otomatik montajda yeri bulunamayan) boya katılmaz
        names = [n for n in names if self.placed.get(n, True)] or names
        k, s = self.up_axis, self.up_sign
        lo, hi = [], []
        for n in names:
            b = parts[n].bounds + self.offsets[n]
            vals = b[:, k] * s
            lo.append(vals.min())
            hi.append(vals.max())
        return float(min(lo)), float(max(hi))

    def height(self, parts: dict[str, Part], names=None) -> float:
        lo, hi = self.up_range(parts, names)
        return hi - lo

    def figure_height(self, parts: dict[str, Part]) -> float:
        fig = [n for n in self.selected if n not in self.base_parts]
        return self.height(parts, fig) if fig else self.height(parts)

    @property
    def max_penetration(self) -> float:
        return max((p[2] for p in self.penetration), default=0.0)


# ---------------------------------------------------------------- tespit

def detect_zeroed_axes(parts: list[Part], tol: float = 0.05, frac: float = 0.75) -> list[int]:
    """Parçaların büyük çoğunluğunda (≥ %75) min'i ~0 olan eksenler: export sırasında parça parça sıfırlanmış.
    Bazı kitlerde birlikte export edilen birkaç parça 0'da olmaz (chiro: 18 ejderha parçasının 4'ü); bunların
    kayması da diğerleri gibi çözülür."""
    if len(parts) < 3:
        return []
    mins = np.array([p.bounds[0] for p in parts])
    return [k for k in range(3) if np.mean(np.abs(mins[:, k]) < tol) >= frac]


def detect_centered(parts: list[Part], zeroed: list[int], frac: float = 0.8) -> bool:
    """Parçaların çoğu kendi başına orijine ortalanmış mı (sıfırlanmamış eksenlerde merkez ≈ 0)?
    Bu durumda dosyalarda montaj bilgisi yoktur (her parça ayrı export edilmiş, hepsi üst üste durur)."""
    if len(parts) < 3:
        return False
    free = [k for k in range(3) if k not in zeroed] or [0, 1, 2]
    hits = 0
    for p in parts:
        c = (p.bounds[0] + p.bounds[1]) / 2
        size = float(np.max(p.bounds[1] - p.bounds[0]))
        if np.all(np.abs(c[free]) < 0.02 * size + 0.5):
            hits += 1
    return hits >= frac * len(parts)


def flat_bottom_scores(parts: list[Part], zeroed: list[int]) -> dict[tuple[int, int], float]:
    """Her aday yukarı yönü (eksen, işaret) için: montajın en alt ucunda, aşağı bakan düz yüzey alanı (mm²).
    Kaidelerin tabanı neredeyse her zaman geniş ve düzdür; figürün tepesi değildir."""
    scores = {}
    for k in range(3):
        if k in zeroed:
            continue
        for s in (1, -1):
            bottom = min((p.bounds[:, k] * s).min() for p in parts)
            area = 0.0
            for p in parts:
                if (p.bounds[:, k] * s).min() > bottom + 1.0:
                    continue
                m = p.lite
                down = m.face_normals[:, k] * s < -0.98
                near = (m.triangles_center[:, k] * s) < bottom + 1.0
                area += float(m.area_faces[down & near].sum())
            scores[(k, s)] = area
    return scores


def tipping_angles(parts: list[Part], zeroed: list[int], per_part: int = 20000) -> dict[tuple[int, int], float]:
    """Her aday yukarı yönü için montaj o yönde dururken devrilme açısı (derece): en alttaki temas noktalarının
    dışbükey çokgeninde ağırlık merkezinin kenara uzaklığı / ağırlık merkezinin yüksekliği. Negatif: ağırlık merkezi
    tabanın dışında (devrilir). Heykel tabanı üzerinde dengede durur; kıvrımlı ayaklı kanepe (Astarion) ya da her
    yüzü düz sandık (Frieren) gibi "düz taban" kuralının ayırt edemediği kitlerde belirleyicidir."""
    from scipy.spatial import ConvexHull

    rng = np.random.default_rng(0)
    pts, cw, cs = [], [], []
    for p in parts:
        v = np.asarray(p.lite.vertices)
        pts.append(v if len(v) <= per_part else v[rng.choice(len(v), per_part, replace=False)])
        cw.append(max(float(p.volume), 1e-6))
        cs.append(np.asarray(p.lite.centroid))
    pts = np.concatenate(pts)
    com = (np.asarray(cs) * np.asarray(cw)[:, None]).sum(0) / sum(cw)
    out = {}
    for k in range(3):
        if k in zeroed:
            continue
        o = [i for i in range(3) if i != k]
        for s in (1, -1):
            h = pts[:, k] * s
            bottom = float(h.min())
            tol = max(1.0, 0.01 * float(h.max() - bottom))
            sup = pts[h < bottom + tol][:, o]
            try:
                hull = ConvexHull(sup)
            except Exception:                 # tek nokta / çizgi: dengede duramaz
                out[(k, s)] = -90.0
                continue
            margin = float(-np.max(hull.equations[:, :2] @ com[o] + hull.equations[:, 2]))
            out[(k, s)] = float(np.degrees(np.arctan2(margin, max(com[k] * s - bottom, 1e-3))))
    return out


def detect_up_axis(parts: list[Part], zeroed: list[int], override: str | None = None) -> tuple[int, int]:
    """Yukarı yönü: en alt ucunda en geniş düz taban yüzeyi olan yön — o yönde montaj dengede duruyorsa. Düz taban
    belirgin değilse ya da o yönde devriliyorsa en dengeli yön (devrilme açısı en büyük). Hiçbiri yoksa +Z (STL'lerde
    yaygın), o da yoksa korunan eksenlerden en uzunu."""
    if override:
        k = AXES.index(override.lower().lstrip("+-"))
        return k, (-1 if override.startswith("-") else 1)
    scores = flat_bottom_scores(parts, zeroed)
    total = sum(p.area for p in parts)
    tilt = tipping_angles(parts, zeroed) if len(parts) > 1 and not zeroed else {}
    if scores:
        (k, s), best = max(scores.items(), key=lambda kv: (kv[1], kv[0] == (2, 1)))
        # düz taban belirgin ve o yönde ağırlık merkezi tabanın içinde: eski kural (Malenia, Sanji, Rango…)
        if (best > 0.002 * total and best > 1.5 * max([v for kv, v in scores.items() if kv != (k, s)] + [0])
                and tilt.get((k, s), 90.0) >= 0.0):
            return k, s
        if tilt:
            (k2, s2), a = max(tilt.items(), key=lambda kv: kv[1])
            if a >= 2.0:
                return k2, s2
        if (2, 1) in scores and scores[(2, 1)] >= 0.8 * best:
            return 2, 1
    lo = np.min([p.bounds[0] for p in parts], axis=0)
    hi = np.max([p.bounds[1] for p in parts], axis=0)
    ext = hi - lo
    cands = [k for k in range(3) if k not in zeroed] or [2]
    k = max(cands, key=lambda i: (ext[i] * (1.0 + 0.05 * (i == 2)), i))
    big = max(parts, key=lambda p: p.volume)
    c = (big.bounds[0][k] + big.bounds[1][k]) / 2
    sign = 1 if c <= (lo[k] + hi[k]) / 2 else -1
    return k, sign


# ---------------------------------------------------------------- çift eşleştirme

def _scores(fa: DistanceField, pts: np.ndarray, k: int, ts: np.ndarray):
    q = np.repeat(pts[None], len(ts), axis=0)
    q[:, :, k] += ts[:, None]
    d = fa.signed(q.reshape(-1, 3)).reshape(len(ts), len(pts))
    return (np.abs(d) < CONTACT_TOL).sum(1), (d < -PEN_TOL).sum(1)


def fit_pair(a: str, b: str, fa: DistanceField, sb, fb_bounds: np.ndarray, k: int,
             rng: np.random.Generator) -> PairFit | None:
    """B'yi k ekseninde kaydırarak A ile en iyi oturduğu konumu bul."""
    pts, _, w = sb
    fixed = [i for i in range(3) if i != k]
    lo, hi = fa.bounds[0][fixed] - 1.0, fa.bounds[1][fixed] + 1.0
    p = pts[np.all((pts[:, fixed] >= lo) & (pts[:, fixed] <= hi), axis=1)]
    if len(p) < 30:
        return None
    coarse = p if len(p) <= 3000 else p[rng.choice(len(p), 3000, replace=False)]
    wc = w * len(p) / len(coarse)
    ts = np.arange(fa.bounds[0][k] - fb_bounds[1][k] - 1.0, fa.bounds[1][k] - fb_bounds[0][k] + 1.5, 0.5)
    c, pe = _scores(fa, coarse, k, ts)
    val = (c - 3.0 * pe) * wc
    if val.max() <= 0:
        return None
    # yerel tepeler
    order = np.argsort(-val)
    peaks = []
    for i in order:
        if val[i] <= 0 or len(peaks) >= 3:
            break
        if all(abs(ts[i] - ts[j]) >= 2.0 for j in peaks):
            peaks.append(i)
    fine = p if len(p) <= 25000 else p[rng.choice(len(p), 25000, replace=False)]
    wf = w * len(p) / len(fine)
    best = None
    for i in peaks:
        t2 = np.arange(ts[i] - 0.75, ts[i] + 0.76, 0.05)
        c2, pe2 = _scores(fa, fine, k, t2)
        v2 = (c2 - 3.0 * pe2) * wf
        j = int(np.argmax(v2))
        if best is None or v2[j] > best[0]:
            best = (v2[j], t2[j], c2[j] * wf, pe2[j] * wf)
    v, t, contact, pen = best
    # Boşluklu oturma (pim/yuva, iç içe kabuklar): her iki yandan iç içe geçmeyle sınırlanan dar bir pencere
    # varsa parça o pencere içinde "oynar"; en iyi tahmin pencerenin ortasıdır, temas tepelerinden biri değil.
    mid = p if len(p) <= 8000 else p[rng.choice(len(p), 8000, replace=False)]
    wm = w * len(p) / len(mid)
    tw = np.arange(t - 3.0, t + 3.001, 0.05)
    _, pw = _scores(fa, mid, k, tw)
    free = pw * wm <= max(2.0, 0.01 * contact)
    i0 = int(np.argmin(np.abs(tw - t)))
    if free[i0]:
        lo_i, hi_i = i0, i0
        while lo_i > 0 and free[lo_i - 1]:
            lo_i -= 1
        while hi_i < len(tw) - 1 and free[hi_i + 1]:
            hi_i += 1
        if lo_i > 0 and hi_i < len(tw) - 1:          # iki yandan sınırlı
            t = float((tw[lo_i] + tw[hi_i]) / 2)
            c3, pe3 = _scores(fa, fine, k, np.array([t]))
            contact, pen = float(c3[0] * wf), float(pe3[0] * wf)
    # Rakip konum: 3 mm'den uzak VE kendisi de iç içe geçmesiz olmalı. Düz kesimlerde temas her kaymada
    # sürer; yanlış konumları eleyen pimlerin yarattığı iç içe geçmedir.
    far = (np.abs(ts - t) > 3.0) & (pe * wc <= max(2.0, 0.02 * contact))
    second = float(val[far].max()) if far.any() else 0.0
    amb = max(second, 0.0) / v if v > 0 else 1.0
    ok = contact >= MIN_CONTACT and pen <= 0.15 * contact + 2.0 and amb < MAX_AMBIGUITY
    return PairFit(a, b, float(t), float(contact), float(pen), float(amb), bool(ok))


def contact_at(fa: DistanceField, sb, offset_b: np.ndarray, gap: float = 0.0) -> tuple[float, float]:
    """(temas, iç içe geçme) mm². gap > 0: yüzeyler arasında gap mm'ye kadar boşluk da temas sayılır."""
    pts, _, w = sb
    p = pts + offset_b
    mg = 1.0 + gap
    m = np.all((p >= fa.bounds[0] - mg) & (p <= fa.bounds[1] + mg), axis=1)
    if not m.any():
        return 0.0, 0.0
    d = fa.signed(p[m])
    near = (np.abs(d) < CONTACT_TOL) | ((d > 0) & (d < gap))
    return float(near.sum() * w), float((d < -PEN_TOL).sum() * w)


# ---------------------------------------------------------------- graf çözümü

def solve_offsets(names: list[str], fits: list[PairFit], root: str) -> tuple[dict[str, float], set[str]]:
    """Kabul edilen çiftlerden kaymaları çöz. Dönüş: (kaymalar, kök bileşenindeki parçalar)."""
    G = nx.Graph()
    G.add_nodes_from(names)
    for f in fits:
        if f.accepted and f.a in G and f.b in G:
            if G.has_edge(f.a, f.b) and G[f.a][f.b]["fit"].contact >= f.contact:
                continue
            G.add_edge(f.a, f.b, weight=f.contact, fit=f)
    comp = nx.node_connected_component(G, root)
    T = nx.maximum_spanning_tree(G.subgraph(comp))
    t = {root: 0.0}
    for u, v in nx.bfs_edges(T, root):
        f = T[u][v]["fit"]
        t[v] = t[u] + (f.delta if f.a == u else -f.delta)
    edges = [d["fit"] for _, _, d in G.subgraph(comp).edges(data=True)]
    idx = {n: i for i, n in enumerate(sorted(comp - {root}))}
    for _ in range(3):
        inl = [f for f in edges if abs(t[f.b] - t[f.a] - f.delta) < INLIER_RES]
        if not idx or not inl:
            break
        A = np.zeros((len(inl), len(idx)))
        y = np.zeros(len(inl))
        w = np.sqrt([f.contact for f in inl])
        for r, f in enumerate(inl):
            if f.b in idx:
                A[r, idx[f.b]] += 1
            if f.a in idx:
                A[r, idx[f.a]] -= 1
            y[r] = f.delta
        sol = np.linalg.lstsq(A * w[:, None], y * w, rcond=None)[0]
        new = {root: 0.0, **{n: float(sol[i]) for n, i in idx.items()}}
        # LSQ eksik belirlenirse ağaç çözümünü koru
        if any(not np.isfinite(v) for v in new.values()):
            break
        t = new
    for f in edges:
        f.residual = t[f.b] - t[f.a] - f.delta
        f.used = abs(f.residual) < INLIER_RES
    return t, comp


def penetration_check(names, fields, samples, offsets, parts):
    """Montajda parçalar arası iç içe geçme: (a, b, p99 derinlik mm, 0.5 mm'den derin alan mm²)."""
    out = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            ba = parts[a].bounds + offsets[a]
            bb = parts[b].bounds + offsets[b]
            if not boxes_overlap(ba, bb, range(3), margin=0.5):
                continue
            depths, area = [], 0.0
            for x, y in ((a, b), (b, a)):
                pts, _, w = samples[x]
                p = pts + offsets[x] - offsets[y]
                m = np.all((p >= fields[y].bounds[0]) & (p <= fields[y].bounds[1]), axis=1)
                if not m.any():
                    continue
                d = -fields[y].signed(p[m])
                d = np.sort(d[d > 0])[::-1]
                # derinlik = en az 1 mm² alanın ulaştığı derinlik (tek tük kıymık noktaları sayılmaz)
                kmin = int(np.ceil(1.0 / w))
                if len(d) >= kmin:
                    depths.append(float(d[kmin - 1]))
                    area += float((d > 0.5).sum() * w)
            if depths:
                out.append((a, b, float(max(depths)), area))
    return out


def recheck_full(pen: list, parts: dict, offsets: dict, log=lambda m: None, limit: int = 12) -> list:
    """Sadeleştirilmiş kopyada eşiği aşan çiftleri tam çözünürlükte yeniden ölç.
    İnce parçalar (kumaş, pelerin) sadeleştirmede milimetrelerce bozulabilir; yanlış alarm vermesin."""
    flagged = [i for i, (a, b, d, area) in enumerate(pen) if d > 0.5 and area > 2.0][:limit]
    if not flagged:
        return pen
    log(f"{len(flagged)} çift tam çözünürlükte yeniden ölçülüyor…")
    import trimesh as _tm

    full, flds = {}, {}

    def get(n):
        if n not in full:
            m = parts[n].load_full()
            full[n] = m
            flds[n] = DistanceField(m)
        return full[n], flds[n]

    out = list(pen)
    for i in flagged:
        a, b, _, _ = pen[i]
        if not (parts[a].path.exists() and parts[b].path.exists()):
            continue
        depths, area = [], 0.0
        for x, y in ((a, b), (b, a)):
            mx, _ = get(x)
            _, fy = get(y)
            pts, _ = _tm.sample.sample_surface(mx, int(min(300_000, mx.area / 0.05)), seed=3)
            w = mx.area / len(pts)
            p = pts + offsets[x] - offsets[y]
            m = np.all((p >= fy.bounds[0]) & (p <= fy.bounds[1]), axis=1)
            if not m.any():
                continue
            d = np.sort(-fy.signed(p[m]))[::-1]
            d = d[d > 0]
            k = int(np.ceil(1.0 / w))
            if len(d) >= k:
                depths.append(float(d[k - 1]))
                area += float((d > 0.5).sum() * w)
        out[i] = (a, b, max(depths) if depths else 0.0, area)
    return out


def detect_base(parts: dict[str, Part], asm: Assembly) -> list[str]:
    """Kaide: en alttaki en büyük parça + ona yakın büyüklükte, onun üst seviyesinin altında başlayanlar."""
    k, s = asm.up_axis, asm.up_sign
    sel = [parts[n] for n in asm.selected if asm.placed.get(n, False)] or [parts[n] for n in asm.selected]

    def rng(p):
        v = (p.bounds + asm.offsets[p.name])[:, k] * s
        return v.min(), v.max()

    lo_all = min(rng(p)[0] for p in sel)
    height = max(rng(p)[1] for p in sel) - lo_all
    bottom = [p for p in sel if rng(p)[0] - lo_all < 0.05 * height]
    if not bottom:
        return []
    b0 = max(bottom, key=lambda p: p.volume)
    lo0, top0 = rng(b0)
    h0 = top0 - lo0
    # Kaideye katılan parça: büyük, kaidenin içinde başlıyor ve kaide yüksekliğinin yarısından fazla yukarı taşmıyor
    # (Rango: Sand evet; Malenia: Legs kaideye gömülü başlıyor ama çok yukarı uzanıyor → hayır)
    base = [b0.name] + [p.name for p in sel
                        if p is not b0 and p.volume >= 0.3 * b0.volume and rng(p)[0] < top0
                        and rng(p)[1] <= top0 + 0.5 * h0]
    return base


def _apply_preference(groups: list[VariantGroup], prefer: set[str], user_choices: list[str] | None,
                      parts: dict[str, Part]) -> list[VariantGroup]:
    """Otomatik montajın tercihlerini varyant seçimine uygula (kullanıcı o grupta seçim yapmadıysa):
    1) bir eklemenin sadece onunla mümkün olduğu alternatif seçilir (Miquella grubu → Head_v2),
    2) tercihi olmayan grupta, tercih edilen bir parçayla aynı klasördeki üye seçilir (Head_v2 ile aynı
       klasördeki Torso_v2: aynı sürümün parçaları)."""
    from .variants import _hull_measure, choose_set

    user = {c.lower() for c in (user_choices or [])}
    free = [g for g in groups if g.confirmed and not any(m.lower() in user for m in g.members)]
    if not prefer or not free:
        return groups
    measure = _hull_measure(parts)
    chosen_dirs = set()

    def reselect(g, picks):
        conflict = {frozenset(c) for c in (g.conflicts or [])}
        g.selected = choose_set(g.members, conflict, measure, {p.lower() for p in picks})

    for g in free:
        picks = [m for m in g.members if m in prefer]
        if picks:
            reselect(g, picks)
            chosen_dirs |= {parts[m].path.parent for m in g.selected}
    for g in free:
        if any(m in prefer for m in g.members):
            continue
        picks = [m for m in g.members if parts[m].path.parent in chosen_dirs]
        if picks and len(picks) < len(g.members):
            reselect(g, picks)
    return groups


def _fit_preferences(groups: list[VariantGroup], names: list[str], parts: dict[str, Part], fields: dict,
                     samples: dict) -> tuple[set[str], dict[str, tuple[list[str], list[str]]]]:
    """Tam montajda alternatif seçimi için kanıt: grubun dışındaki bir parça bir üyeye temiz oturup diğerine derin
    gömülüyorsa (Arlecchino: WingBase TorsoForWings'e oturuyor, Torso'ya 7 mm gömülüyor) o parça oturduğu üyeyi
    gerektirir. Dönüş: (tercih edilenler, {parça: (oturduğu üyeler, gömüldüğü üyeler)})."""
    in_groups = {m for g in groups for m in g.members}
    prefer: set[str] = set()
    needs: dict[str, tuple[list[str], list[str]]] = {}
    for g in groups:
        if len(g.members) < 2:
            continue
        for p in names:
            if p in in_groups:
                continue
            ok, bad = [], []
            for m in g.members:
                if not boxes_overlap(parts[p].bounds, parts[m].bounds, range(3), margin=GAP_TOL):
                    continue
                c, pe = contact_at(fields[m], samples[p], np.zeros(3), gap=GAP_TOL)
                if c >= MIN_CONTACT and pe <= 0.15 * c + 2.0:
                    ok.append(m)
                elif pe > max(20.0, 0.15 * c + 2.0):        # temas kuralını iç içe geçme yüzünden geçemiyor
                    bad.append(m)
            if ok and bad:
                prefer |= set(ok)
                needs[p] = (sorted(ok), sorted(bad))
    return prefer, needs


_REF_NAME = re.compile(r"height|scale|size|ruler|gauge|measure|reference|\bref\b|_ref\b|pillar|olcek|ölçek",
                       re.IGNORECASE)


def detect_reference_parts(parts: dict[str, Part], asm: Assembly) -> list[str]:
    """Stüdyonun ölçek/boy referans parçaları (ör. Lucas Perez 'HEIGHT_PILLAR': 300 mm'lik düz kutu = 1.80 m × 1/6).
    Figürün parçası değildir: basılmaz, boya katılmaz. Ölçüt: basit prizma (az üçgen, kutusunu dolduruyor) ve
    ya montajda hiçbir parçaya değmiyor + uzun ince/adı referans gibi, ya da (montaj bilinmiyorsa) adı referans
    gibi + uzun ince."""
    out = []
    for n in asm.selected:
        p = parts[n]
        ext = np.sort(np.asarray(p.bounds[1]) - np.asarray(p.bounds[0]))
        if p.n_faces > 200 or not p.watertight or ext[0] <= 0:
            continue
        if p.volume / float(np.prod(ext)) < 0.7:          # kutu/silindir gibi dolu bir prizma değil
            continue
        named = bool(_REF_NAME.search(n))
        elongated = ext[2] >= 4.0 * ext[1]
        isolated = (asm.mode in ("tam", "kısmi") and asm.placed.get(n) and n not in (asm.links or {})
                    and len(asm.selected) > 2)
        if (isolated and (elongated or named)) or (named and elongated):
            out.append(n)
    return out


def _touches_any(pn: Part, others: list[Part], fields: dict, samples: dict) -> bool:
    """Dosyadaki konumunda diğer parçalardan birine (montaj boşluğu dahil) iç içe geçmeden değiyor mu?"""
    n = pn.name
    for o in others:
        if o.name == n or not boxes_overlap(pn.bounds, o.bounds, range(3), margin=GAP_TOL):
            continue
        for a, b in ((o.name, n), (n, o.name)):
            c, pe = contact_at(fields[a], samples[b], np.zeros(3), gap=GAP_TOL)
            if c >= MIN_CONTACT and pe <= 0.15 * c + 2:
                return True
    return False


def detect_junk_parts(parts_list: list[Part], fields: dict, samples: dict) -> dict[str, str]:
    """Dağıtıcıların kit klasörüne kattığı reklam/filigran STL'leri: {parça: neden}.

    Bunlar düz yazı plakalarıdır (ör. 595 × 83 × 17 mm, 5 bin üçgen, harf başına bir gövde) ve farklı kitlerde aynı
    geometriyle ama farklı adla (lab, pack, Miniatures, hatta 'Torso.STL') çıkar; bu yüzden ada değil geometriye
    bakılır. Figürün parçası değillerdir: montaja, yukarı ekseni/boy tespitine katılmaz, basılmazlar. Ölçüt: hiçbir
    parçaya değmiyor VE (kalınlığı yok — basılamaz — ya da düz plaka: en kısa kenar ≤ en uzunun %10'u, üçgen
    yoğunluğu diğer parçaların medyanının ≤ %2'si, ve ya diğer parçaların toplam kutusundan ≥ 1.3 kat geniş ya da
    ≥ 5 ayrı gövde)."""
    if len(parts_list) < 3:
        return {}
    ext = {p.name: np.sort(np.asarray(p.bounds[1]) - np.asarray(p.bounds[0])) for p in parts_list}
    thin = {n for n, e in ext.items() if e[0] <= 0.1 * e[2]}
    rest = [p for p in parts_list if p.name not in thin] or parts_list
    rest_ext = float(np.max(np.max([p.bounds[1] for p in rest], 0) - np.min([p.bounds[0] for p in rest], 0)))
    dens = float(np.median([p.n_faces / max(p.area, 1e-6) for p in rest]))
    cand, sure = {}, set()
    for p in parts_list:
        e = ext[p.name]
        if p.name not in thin or e[2] <= 0:
            continue
        if e[0] < 0.05:
            cand[p.name] = f"kalınlığı yok ({e[2]:.0f} × {e[1]:.0f} mm düz yüzey, basılamaz)"
            sure.add(p.name)
            continue
        wide = e[2] >= 1.3 * rest_ext
        sparse = p.n_faces / max(p.area, 1e-6) <= 0.02 * dens
        if sparse and (wide or p.bodies >= 5):
            cand[p.name] = (f"{e[2]:.0f} × {e[1]:.0f} × {e[0]:.0f} mm düz plaka, {p.bodies} gövde, seyrek üçgen"
                            + (", figürün tamamından geniş" if wide else ""))
            if wide:
                sure.add(p.name)
    # Kalınlıksız ya da figürün tamamından geniş seyrek plaka kitin parçası olamaz: temas kontrolü gerekmez (Maomao
    # dioramasında plakalar sahnenin içinden geçip tesadüfen birkaç parçaya "değiyordu"). Diğer adaylar ancak hiçbir
    # parçaya değmiyorsa (birbirine değmeleri sayılmaz: reklam dosyaları çoğu zaman üst üste, hepsi orijinde).
    others = [p for p in parts_list if p.name not in cand]
    out = {p.name: cand[p.name] for p in parts_list
           if p.name in cand and (p.name in sure or not _touches_any(p, others, fields, samples))}
    return out if len(out) <= len(parts_list) - 2 else {}


_ALT_NAME = re.compile(r"(?:^|[ _\-.])(?:alternative|alternate|alt)(?=$|[ _\-.\d])", re.IGNORECASE)

# Stüdyoların sürüm adlarında kullandığı sözcükler: aynı figürün bölünmüş / tek parça / büst sürümleri
_VERSION_WORDS = {"split": "split", "splitted": "split", "merge": "merged", "merged": "merged", "bust": "bust",
                  "full": "full", "onepart": "onepart", "multipart": "multipart"}


def _version_word(p: Part) -> tuple[str, str] | None:
    """Parça adındaki sürüm sözcüğü: (anahtar, adda geçtiği hâli) — ör. Atsu_BUST_Stand → ("bust", "BUST")."""
    for tok in re.split(r"[ _\-.]+", p.name):
        key = _VERSION_WORDS.get(tok.lower())
        if key:
            return key, tok
    return None


def detect_version_sets(parts_list: list[Part], choices: list[str] | None
                        ) -> tuple[VariantGroup | None, set[str], str | None]:
    """Ad sözcüğüyle ayrışan sürüm takımları (Atsu: Atsu_Split_* 31 parça, Atsu_Merge_Body, Atsu_BUST +
    Atsu_BUST_Stand). Takımlar dosyada farklı yerlerde durabilir (bölünmüş heykel montajlı, büst ve tek parça gövde
    orijinde): üst üste binme ölçüsü onları ayıramaz, ama takımların hepsi aynı figür. En az iki farklı sürüm
    sözcüğü varsa takımlar birbirinin alternatifidir; kullanıcının seçtiği (--variant), yoksa en çok parçalı takım
    seçilir. Sözcüksüz parçalar (ortak) her takımla basılır. Dönüş: (grup, dışlananlar, uyarı)."""
    from fnmatch import fnmatch

    sets: dict[str, list[str]] = {}
    shown: dict[str, str] = {}
    for p in parts_list:
        vw = _version_word(p)
        if vw:
            sets.setdefault(vw[0], []).append(p.name)
            shown.setdefault(vw[0], vw[1])
    if len(sets) < 2:
        return None, set(), None
    pats = [c.lower() for c in (choices or [])]
    picked = [k for k, ms in sets.items()
              if any(k == c or shown[k].lower() == c or any(fnmatch(m.lower(), c) for m in ms) for c in pats)]
    chosen = picked[0] if picked else max(sets, key=lambda k: (len(sets[k]), k == "split"))
    members = sorted((m for ms in sets.values() for m in ms), key=str.lower)
    keep = sorted(sets[chosen], key=str.lower)
    dropped = set(members) - set(keep)
    conflicts = [tuple(sorted((a, b))) for ka, A in sets.items() for kb, B in sets.items() if ka < kb
                 for a in A for b in B]
    group = VariantGroup(members, keep, "ad", -1.0, conflicts, True)
    desc = ", ".join(f"{shown[k]} ({len(ms)} parça)" for k, ms in sorted(sets.items(), key=lambda kv: -len(kv[1])))
    other = next(k for k in sets if k != chosen)
    why = "seçtiğiniz" if picked else "en çok parçalı"
    warn = (f"Kitte aynı figürün birden çok sürümü var: {desc}. {why.capitalize()} '{shown[chosen]}' sürümü "
            f"hazırlanıyor, diğerleri basılmayacak. Başka sürüm için ör. --variant \"*{shown[other]}*\".")
    return group, dropped, warn


def _at_origin(p: Part) -> bool:
    """Parça dosyada orijine bırakılmış gibi mi: iki eksende merkezi ~0 ya da kutusunun köşesi tam orijinde
    (Maomao dioraması Jinshi\\Bun: [0,0,0]–[31,30,28]; Atsu Sword_Belt: x/y ortalanmış, z 5'ten başlıyor)."""
    b = np.asarray(p.bounds)
    c = (b[0] + b[1]) / 2
    size = float(np.max(b[1] - b[0]))
    centered = int(np.sum(np.abs(c) < 0.02 * size + 0.5))
    # orijin noktası kutuya yakın olmalı: ortalanmış bir figürün simetri ekseninde ama yukarıda duran parça
    # (Sanji: ağızdaki sigara) orijinde değildir
    m = max(10.0, 0.1 * size)
    near = bool(np.all((b[0] - m <= 0) & (b[1] + m >= 0)))
    return near and (centered >= 2 or bool(np.all(np.abs(b[0]) < 0.05)))


def misplaced_parts(parts_list: list[Part], fields: dict, samples: dict, strong: float = 300.0) -> list[str]:
    """Montajlı kitte montaj konumunda olmayan, orijinde unutulmuş parçalar. Orijinde duran parça yerinde değildir:
    orijinde olmayan hiçbir parçaya temiz değmiyorsa (Maomao dioraması Jinshi\\Bun) ya da bir parçanın içine derin
    gömülüyor ve montaja güçlü bir ekle (≥ strong mm²) bağlı değilse (Atsu: Torso, Stand_Ground_Part_2, Sword_Belt
    orijinde birbirinin ve şapkanın içine yığılmış; şapkaya tesadüfen de değiyorlar). Bunlar üst üste bindikleri
    parçaların "alternatifi" sayılmaz, yerleri otomatik montajla aranır."""
    if len(parts_list) < 4:
        return []
    cand = [p for p in parts_list if _at_origin(p)]
    if not cand:
        return []
    cand_names = {p.name for p in cand}
    clean: dict[str, dict[str, float]] = {}     # aday → {komşu: en büyük temiz temas}
    flags: dict[str, tuple[bool, bool]] = {}    # aday → (deep, heap)
    for p in cand:
        deep = heap = False
        clean[p.name] = {}
        for o in parts_list:
            if o is p or not boxes_overlap(p.bounds, o.bounds, range(3), margin=GAP_TOL):
                continue
            for a, b in ((o, p), (p, o)):
                c, pe = contact_at(fields[a.name], samples[b.name], np.zeros(3), gap=GAP_TOL)
                if pe > max(20.0, 0.15 * c + 2.0):
                    deep = True
                    heap |= o in cand        # orijinde başka bir parçayla iç içe: dosyada orijine yığılmışlar
                elif c >= MIN_CONTACT:
                    clean[p.name][o.name] = max(clean[p.name].get(o.name, 0.0), c)
                # orijinde birebir üst üste iki kopya (Maomao: Base_No_Fence ↔ Base_W_Fence_No_Cuts)
                heap |= o in cand and c >= 0.5 * min(a.area, b.area)
        flags[p.name] = (deep, heap)

    # Yerinde olduğu bilinen parçalar: orijinde durmayanlar. Bunlara temiz oturan aday da yerindedir ve kendisi
    # dayanak olur: kaidesi orijinde başlayan bir figürde gövde de (XY'de ortalı, tabanı z≈10) aday görünür;
    # kaide yalnızca ona değdiği için eskiden ikisi birbirinin kanıtını geçersiz kılıyor, kaide basılmıyordu.
    inplace = {p.name for p in parts_list if p.name not in cand_names}
    area = {p.name: p.area for p in parts_list}
    rescued = True
    while rescued:
        rescued = False
        for p in cand:
            if p.name in inplace:
                continue
            best = max((c for o, c in clean[p.name].items() if o in inplace), default=0.0)
            deep, heap = flags[p.name]
            # Yığındaki parça montaja ancak tesadüfen değer (Atsu Stand_Ground_Part_2 şapkanın kenarına); orijinde
            # başlayan gerçek bir kaide ise figürle yüzeyinin belirgin bir kısmı kadar temas eder.
            if best > 0.0 and not (deep and best < strong) and not (heap and best < max(strong, 0.1 * area[p.name])):
                inplace.add(p.name)
                rescued = True
    lost = [p.name for p in cand if p.name not in inplace]
    return lost if len(lost) <= 0.4 * len(parts_list) else []   # çoğu ortadaysa kit zaten montajsız


def place_onto(fixed_names: list[str], movers: list[str], parts: dict[str, Part], fields: dict, samples: dict,
               up: np.ndarray, log=lambda m: None):
    """Konumu dosyadan bilinen montaja (yerinde, sabit) diğer parçaları otomatik montajla yerleştir.
    Dönüş: AutoResult (movers için offsets/placed/links)."""
    from .autoassemble import auto_assemble

    sub = {n: parts[n] for n in list(fixed_names) + list(movers)}
    return auto_assemble(sub, fields, samples, list(sub), log, up=up,
                         fixed={n: np.zeros(3) for n in fixed_names})


def frame_clusters(parts_list: list[Part], fields: dict, samples: dict) -> list[list[Part]] | None:
    """Alt klasörler farklı koordinatlarda mı export edilmiş: öyleyse aynı koordinattaki klasör kümeleri.
    İşaretler: klasörlerin sıfırlanmış eksenleri farklı (Ichigo: figürün Z'si parça başına sıfırlanmış, kaidenin
    değil) ya da iki klasör arasında iç içe geçme temastan çok büyük (Maomao dioraması: Jinshi ↔ Maomao 2.6 bin mm²
    temas, 37 bin mm² iç içe; aynı koordinattaki klasörlerde parçalar birbirine oturur, gömülmez). Kök klasörde
    parça varsa ya da tek klasör varsa None."""
    folders: dict[str, list[Part]] = {}
    for p in parts_list:
        folders.setdefault(p.group, []).append(p)
    if len(folders) < 2 or "" in folders:
        return None
    sig = {g: tuple(detect_zeroed_axes(ps)) for g, ps in folders.items() if len(ps) >= 3}
    names = sorted(folders)
    same = {g: {g} for g in names}
    if len(set(sig.values())) > 1:
        link = set()                    # export biçimleri farklı: her klasör ayrı
    else:
        if any(sig.values()):
            return None                 # hepsi parça başına sıfırlanmış (kısmi): konumlar henüz çözülmedi
        contact: dict[tuple, float] = {}
        pen: dict[tuple, float] = {}
        for i, a in enumerate(parts_list):
            for b in parts_list[i + 1:]:
                if a.group == b.group or not boxes_overlap(a.bounds, b.bounds, range(3), margin=GAP_TOL):
                    continue
                key = tuple(sorted((a.group, b.group)))
                c, pe = contact_at(fields[a.name], samples[b.name], np.zeros(3), gap=GAP_TOL)
                pen[key] = pen.get(key, 0.0) + pe
                if c >= MIN_CONTACT and pe <= 0.15 * c + 2.0:
                    contact[key] = contact.get(key, 0.0) + c
        link = {k for k, c in contact.items() if c >= MIN_CONTACT and pen.get(k, 0.0) <= max(500.0, 2.0 * c)}
    for a, b in link:                   # aynı koordinattaki klasörleri birleştir
        merged = same[a] | same[b]
        for g in merged:
            same[g] = merged
    clusters = []
    for g in names:
        if not any(g in c for c in clusters):
            clusters.append(sorted(same[g]))
    if len(clusters) < 2:
        return None
    return [[p for g in cl for p in folders[g]] for cl in clusters]


def _seat_clusters(asms: list[Assembly], clusters: list[list[Part]], parts: dict[str, Part], fields: dict,
                   samples: dict, join: list, log=lambda m: None) -> tuple[dict, list[str], dict]:
    """Kullanıcının kümeler arası bağlantıları (çapa bir kümede, parça başka kümede; ör. Ichigo: kaide parçası ←
    figürün ayağı): parçanın kümesi rijit gövde olarak çapanın kümesine oturtulur. Konum, çapa kümesindeki yakın
    parçalara karşı temas/iç içe geçmeyle aranır (kullanıcı bağlantısıyla aynı arama; 'başka konum' sırası geçerli).
    Dönüş: ({küme: (çapa kümesi, öteleme)}, uyarılar, {parça: bağlantı})."""
    from .autoassemble import auto_assemble

    where = {p.name: i for i, cl in enumerate(clusters) for p in cl}
    seats: dict[int, tuple[int, np.ndarray]] = {}
    warns, links = [], {}

    def chain_root(i):
        seen = set()
        while i in seats and i not in seen:
            seen.add(i)
            i = seats[i][0]
        return i

    for j in join:
        a, b = j[0], j[1]
        ia, ib = where.get(a), where.get(b)
        if ia is None or ib is None or ia == ib:
            continue
        if ib in seats or chain_root(ia) == ib:
            warns.append(f"{b} → {a}: {b} kümesi zaten başka bir kümeye bağlı, bu bağlantı yok sayıldı.")
            continue
        A, B = asms[ia], asms[ib]
        if not A.placed.get(a) or not B.placed.get(b):
            warns.append(f"{b} → {a}: {'çapa' if not A.placed.get(a) else 'parça'} kendi kümesinde yerleşmemiş, "
                         "kümeler birleştirilemedi.")
            continue
        # çapa + onun çevresindeki parçalar sabit (parça onlara gömülmesin); uzak parçalar aramayı yavaşlatmasın
        reach = float(np.max(parts[b].bounds[1] - parts[b].bounds[0]))
        lo_a, hi_a = parts[a].bounds[0] + A.offsets[a] - reach, parts[a].bounds[1] + A.offsets[a] + reach
        fixed = {n: np.asarray(A.offsets[n], float) for n in A.selected if A.placed.get(n) and n != b and (
            n == a or boxes_overlap((lo_a, hi_a), parts[n].bounds + A.offsets[n], range(3)))}
        sub = {n: parts[n] for n in [*fixed, b]}
        # küme çapanın üstünde durmalı (figür kaideye basar, yanına değil): kümenin en alt noktası çapanın
        # yüksekliğinin ortasından aşağı inmez (ayak pimleri kaide yuvasına gömülebilir)
        up = A.up_vector
        a_lo, a_hi = sorted((float(parts[a].bounds[0] @ up), float(parts[a].bounds[1] @ up)))
        a_mid = (a_lo + a_hi) / 2 + float(np.asarray(A.offsets[a]) @ up)
        low_rel = min(min(float(parts[n].bounds[0] @ up), float(parts[n].bounds[1] @ up)) + float(
            (np.asarray(B.offsets[n]) - np.asarray(B.offsets[b])) @ up) for n in B.selected if B.placed.get(n))
        log(f"Küme bağlantısı: {b} ({len(clusters[ib])} parçalık küme) → {a} …")
        res = auto_assemble(sub, fields, samples, list(sub), log, up=up, fixed=fixed, hints=[tuple(j)],
                            hint_min=(up, a_mid - low_rel))
        if not res.placed.get(b):
            warns.append(f"{b} → {a}: temaslı ve gömülmeyen bir konum bulunamadı; kümeler yan yana bırakıldı. "
                         "Başka bir parça çiftiyle deneyin (ör. figürün ayağı ↔ kaidenin üst parçası).")
            continue
        seats[ib] = (ia, np.asarray(res.offsets[b], float) - np.asarray(B.offsets[b], float))
        links[b] = dict(res.links.get(b) or dict(to=a, contact=0), kind="kullanıcı")
    return seats, warns, links


def _merge_clusters(asms: list[Assembly], clusters: list[list[Part]], parts: dict[str, Part],
                    seats: dict | None = None) -> Assembly:
    """Ayrı koordinatlarda çözülen klasör kümelerini tek montajda birleştir. Kullanıcının bağladığı küme çapa
    kümesine oturur (seats: {küme: (çapa kümesi, öteleme)}); bağlanmayanlardan en büyüğü yerinde kalır, diğerleri
    yanına dizilir (tabanları hizalı): kümelerin birbirine göre yeri dosyalardan bilinmez."""
    seats = seats or {}
    vol = [sum(parts[n].volume for n in a.selected) for a in asms]
    order = sorted(range(len(asms)), key=lambda i: -vol[i])
    roots = [i for i in order if i not in seats]
    main = asms[roots[0]]
    k, s = main.up_axis, main.up_sign
    side = [i for i in range(3) if i != k][0]

    def box(a, T=np.zeros(3)):
        ns = [n for n in a.selected if a.placed.get(n)] or list(a.selected) or list(a.offsets)
        return (np.min([parts[n].bounds[0] + a.offsets[n] + T for n in ns], axis=0),
                np.max([parts[n].bounds[1] + a.offsets[n] + T for n in ns], axis=0))

    lo_m, hi_m = box(main)
    cursor = hi_m[side] + 20.0
    T_of: dict[int, np.ndarray] = {}
    for i in roots:
        T = np.zeros(3)
        if i != roots[0]:
            lo, hi = box(asms[i])
            T[side] = cursor - lo[side]
            T[k] = (lo_m[k] - lo[k]) if s > 0 else (hi_m[k] - hi[k])
            cursor += hi[side] - lo[side] + 20.0
        T_of[i] = T
    while len(T_of) < len(asms):             # oturtulan kümeler çapa kümelerini izler (zincir olabilir)
        for i, (ia, rel) in seats.items():
            if i not in T_of and ia in T_of:
                T_of[i] = T_of[ia] + rel
    out = Assembly(main.mode, k, s, main.zeroed_axes, {}, {}, [], [], root=main.root, fixes=main.fixes)
    labels = []
    for i in order:
        a, T = asms[i], T_of[i]
        folders = sorted({p.group for p in clusters[i]})
        label = ", ".join(folders)
        labels.append(f"{label} ({a.mode}{'' if a.up_axis == k and a.up_sign == s else ', yukarı ekseni farklı'})")
        for n, off in a.offsets.items():
            out.offsets[n] = np.asarray(off, float) + T
        out.placed.update(a.placed)
        out.selected += a.selected
        out.variant_groups += a.variant_groups
        out.fits += a.fits
        out.penetration += a.penetration
        out.links.update(a.links)
        out.groups += [{m: np.asarray(P, float) + T for m, P in g.items()} for g in a.groups]
        out.warnings += [f"[{label}] {w}" for w in a.warnings]
    if len(roots) == 1:
        out.warnings.insert(0, "Alt klasörler ayrı koordinatlarda export edilmiş; her biri ayrı montajlandı ve sizin "
                               "bağlantınızla birleştirildi: " + "; ".join(labels) + ". Önizlemede kontrol edin; yanlış "
                               "yerdeyse Montaj kontrolü → 'Başka konum dene'.")
    else:
        out.warnings.insert(0, "Alt klasörler ayrı koordinatlarda export edilmiş; her biri ayrı montajlandı ve yan yana "
                               "kondu: " + "; ".join(labels) + ". Kümelerin birbirine göre yeri (figürün kaidede "
                               "nerede durduğu) dosyalardan bilinmiyor; boy en uzun kümeden ölçüldü. Figürü kaideye "
                               "oturtmak için: Montaj kontrolü'nde figürün kaideye değen parçasını seçip çapa olarak "
                               "kaide parçasını seçin, 'Bağla' (CLI: --join KAİDE:PARÇA), sonra montajı yeniden çözün.")
    return out


def _links_from_fits(fits: list[PairFit], placed: dict, kind: str, root: str | None) -> dict[str, dict]:
    """Kısmi/tam montajda parça başına en güçlü kabul edilmiş temas: {parça: {to, contact, kind}}."""
    out = {}
    for f in sorted((f for f in fits if f.accepted), key=lambda f: -f.contact):
        for n, o in ((f.a, f.b), (f.b, f.a)):
            if placed.get(n) and n not in out:
                out[n] = dict(to=o, contact=round(float(f.contact)), kind=kind)
    if root and placed.get(root):
        out[root] = dict(to=None, contact=0, kind="kök")
    return out


# ---------------------------------------------------------------- ana giriş

def solve_assembly(parts_list: list[Part], up_override: str | None = None,
                   variant_choices: list[str] | None = None, base_override: list[str] | None = None,
                   log=lambda msg: None, detach: list[str] | None = None,
                   join: list | None = None) -> tuple[Assembly, dict[str, DistanceField], dict]:
    """detach / join: kullanıcının montaj düzeltmeleri (yanlış yere oturan parçalar; [çapa, parça] bağlantıları)."""
    parts = {p.name: p for p in parts_list}
    names = [p.name for p in parts_list]
    detach = [n for n in (detach or []) if n in parts]
    # bağlantı: (çapa, parça) ya da (çapa, parça, aday sırası)
    join = [tuple(j) for j in (join or []) if j[0] in parts and j[1] in parts and j[0] != j[1]]
    links: dict[str, dict] = {}
    fields = {n: DistanceField(parts[n].lite) for n in names}
    samples = {n: sample_surface(parts[n].lite) for n in names}
    rng = np.random.default_rng(0)
    # Reklam/filigran plakaları (figürden geniş, montajı ve yukarı ekseni bozar) çözümden önce ayrılır
    junk = detect_junk_parts(parts_list, fields, samples)
    # Adı ölçek referansı olan ve hiçbir parçaya değmeyen parça (Jin Sakai '250mmreference': T-pozunda 250 mm insan
    # mankeni) de çözümden önce ayrılır: figürün yanında durup yukarı eksenini ve boyu saptırıyordu
    early_refs = [p.name for p in parts_list if p.name not in junk and _REF_NAME.search(p.name) and len(parts_list) > 2
                  and not _touches_any(p, [q for q in parts_list if q.name != p.name and q.name not in junk],
                                       fields, samples)]
    aside = set(junk) | set(early_refs)
    if aside:
        log("Montaj dışı tutulan: " + ", ".join(sorted(aside)))
        parts_list = [p for p in parts_list if p.name not in aside]
        names = [p.name for p in parts_list]
        detach = [n for n in detach if n not in aside]
        join = [j for j in join if j[0] not in aside and j[1] not in aside]

    def _solve_one(parts_list, detach, join):
        """Tek koordinat sistemindeki parçaların montajı (mod tespiti, alternatifler, çözüm, doğrulama)."""
        names = [p.name for p in parts_list]
        detach = [n for n in detach if n in names]
        join = [j for j in join if j[0] in names and j[1] in names]
        links: dict[str, dict] = {}
        zeroed = detect_zeroed_axes(parts_list)
        up_k, up_s = detect_up_axis(parts_list, zeroed, up_override)
        warnings: list[str] = []

        centered = detect_centered(parts_list, zeroed)
        if centered:
            mode_guess, sweep = "otomatik", None
            warnings.append("Parçalar tek tek orijine ortalanmış: dosyalarda montaj bilgisi yok (her parça ayrı "
                            "export edilmiş). Ek yüzeylerinden otomatik montaj denendi.")
        elif len(zeroed) == 1:
            mode_guess, sweep = "kısmi", zeroed
        elif not zeroed:
            mode_guess, sweep = "tam", []
        else:
            mode_guess, sweep = "bilinmiyor", None

        # montajlı kitte orijinde unutulmuş parçalar alternatif aramasına girmez (yerleri ayrıca aranır)
        misplaced = misplaced_parts(parts_list, fields, samples) if mode_guess == "tam" else []
        tam_warn: list[str] = []          # tam montaj varsayımına dayalı uyarılar (montaj tutmazsa atılır)
        log("Alternatif parçalar aranıyor…")
        groups = detect_variants([p for p in parts_list if p.name not in misplaced], fields, sweep, variant_choices)
        if mode_guess == "tam":
            # Adı "Alternative/Alt" olan ve montajdaki parçalara derin gömülen parça o parçaların yerine geçen bir
            # seçenektir (Maomao dioraması: Alternative_Base, Base1..6 bloklarına 10.5 mm gömülü): basılmaz
            user = {c.lower() for c in (variant_choices or [])}
            grouped = {m for g in groups for m in g.members}
            for p in parts_list:
                if (p.name in misplaced or p.name in grouped or p.name.lower() in user
                        or not _ALT_NAME.search(p.name)):
                    continue
                hits = []
                for o in parts_list:
                    if o is p or o.name in misplaced or not boxes_overlap(p.bounds, o.bounds, range(3)):
                        continue
                    c, pe = contact_at(fields[o.name], samples[p.name], np.zeros(3))
                    if pe > max(20.0, 0.15 * c + 2.0):
                        hits.append(o.name)
                if hits:
                    groups.append(VariantGroup(sorted([p.name] + hits), sorted(hits), "ad+geometri", -1.0,
                                               [tuple(sorted((p.name, h))) for h in hits], True))
                    tam_warn.append(f"{p.name}: adı alternatif ve {', '.join(sorted(hits))} içine gömülüyor — o "
                                    f"parçaların yerine geçen bir seçenek; basılmayacak (istenirse --variant {p.name}).")
        if mode_guess == "tam" and groups:
            prefer, needs = _fit_preferences(groups, [n for n in names if n not in misplaced], parts, fields,
                                             samples)
            if prefer:
                groups = _apply_preference(groups, prefer, variant_choices, parts)
                for p, (ok, bad) in sorted(needs.items()):
                    if not set(ok) & {m for g in groups for m in g.selected}:
                        continue
                    tam_warn.append(f"{p}: sadece {', '.join(ok)} ile oturuyor ({', '.join(bad)} içine gömülüyor) — "
                                    f"{', '.join(ok)} seçildi. Diğer sürüm isteniyorsa --variant ile seçip {p} parçasını "
                                    "--exclude ile çıkarın.")
        unselected = {m for g in groups for m in g.members if m not in g.selected}
        selected = [n for n in names if n not in unselected]
        variant_pairs = {frozenset(c) for g in groups for c in (g.conflicts or [])}

        offsets = {n: np.zeros(3) for n in names}
        placed = {n: False for n in names}
        sub_groups: list[dict] = []
        weak_placed: set[str] = set()      # otomatik montajda zayıf temasla yerleşenler (doğrulamada ilk elenecekler)
        fits: list[PairFit] = []
        root = max(selected, key=lambda n: parts[n].volume) if selected else None

        if mode_guess == "kısmi":
            k = zeroed[0]
            fixed = [i for i in range(3) if i != k]
            cand = [(a, b) for i, a in enumerate(names) for b in names[i + 1:]
                    if frozenset((a, b)) not in variant_pairs
                    and boxes_overlap(parts[a].bounds, parts[b].bounds, fixed, margin=1.0)]
            log(f"{len(cand)} aday çift için {AXES[k].upper()} kayması taranıyor…")
            for a, b in cand:
                # küçük yüzeyli parçayı örnekle, büyüğün mesafe alanına karşı tara
                A, B = (a, b) if parts[a].area >= parts[b].area else (b, a)
                f = fit_pair(A, B, fields[A], samples[B], parts[B].bounds, k, rng)
                if f is not None:
                    fits.append(f)
            t_sel, comp = solve_offsets(selected, [f for f in fits if f.a in selected and f.b in selected], root)
            for n, v in t_sel.items():
                offsets[n][k] = v
                placed[n] = True
            # seçilmeyen varyantlar: montaja yerleşmiş parçalara göre konumla
            for n in unselected:
                best = None
                for f in fits:
                    if not f.accepted or n not in (f.a, f.b):
                        continue
                    other = f.b if f.a == n else f.a
                    if other in unselected or not placed.get(other):
                        continue
                    if best is None or f.contact > best.contact:
                        best = f
                if best:
                    other = best.b if best.a == n else best.a
                    d = best.delta if best.b == n else -best.delta
                    offsets[n][k] = offsets[other][k] + d
                    placed[n] = True
            # normalize: montajın sıfırlanmış eksendeki minimumu 0 olsun
            zmin = min((parts[n].bounds[0][k] + offsets[n][k]) for n in names if placed[n])
            for n in names:
                if placed[n]:
                    offsets[n][k] -= zmin
            missing = [n for n in selected if not placed[n]]
            if missing:
                warnings.append("Konumu belirsiz (eşleşme bulunamadı): " + ", ".join(missing))
            mode = "kısmi" if len(missing) <= len(selected) * 0.25 else "bilinmiyor"
            links = _links_from_fits(fits, placed, "kayma taraması", root)
        elif mode_guess == "tam":
            good, near = set(), set()
            for i, a in enumerate(names):
                for b in names[i + 1:]:
                    if frozenset((a, b)) in variant_pairs or a in misplaced or b in misplaced:
                        continue
                    if not boxes_overlap(parts[a].bounds, parts[b].bounds, range(3), margin=GAP_TOL):
                        continue
                    c, pe = contact_at(fields[a], samples[b], np.zeros(3))
                    if c < MIN_CONTACT:          # stüdyo montaj boşluğu bırakmış olabilir: yüzeyler birbirine bakıyor
                        c = contact_at(fields[a], samples[b], np.zeros(3), gap=GAP_TOL)[0]
                    if c >= MIN_CONTACT and pe <= 0.15 * c + 2:
                        good |= {a, b}
                        fits.append(PairFit(a, b, 0.0, c, pe, 0.0, True, 0.0, True))
                    elif c >= 5.0 and pe >= 2.0:
                        near |= {a, b}           # deliğe saplanmış (kaidedeki mızrak): konumu montajda, uyarı gerekmez
            for n in names:
                placed[n] = n in good
            mode = "tam" if len(good & set(selected)) >= 0.6 * len(selected) else "bilinmiyor"
            if mode == "tam":
                warnings += tam_warn
                missing = [n for n in selected if n not in good and n not in near and n not in misplaced]
                if missing:
                    warnings.append("Hiçbir parçaya temas etmiyor: " + ", ".join(missing))
                # Birbirine değen ama ana montaja (figür) hiç değmeyen parça grupları: ikinci bir kaide seti gibi
                # (OUISTCRAM Chimchar: iki dilde isim harfli iki kaide yan yana). Sessizce basılmasın, bildirilsin.
                G = nx.Graph()
                G.add_nodes_from(n for n in selected if n in good)
                G.add_edges_from((f.a, f.b) for f in fits if f.a in G and f.b in G)
                comps = sorted(nx.connected_components(G), key=lambda c: -sum(parts[n].volume for n in c))

                def box(c):
                    return np.array([np.min([parts[n].bounds[0] for n in c], 0), np.max([parts[n].bounds[1] for n in c], 0)])
                # figür kaideye gömülü oturuyorsa (Frieren: gövde sandığa 4.8 mm) temas çifti kabul edilmez ama grup
                # ayrı değildir: sadece ana grubun kutusuna hiç girmeyen (yanda duran) gruplar bildirilir
                separate = [c for c in comps[1:] if len(c) >= 2 and comps
                            and not boxes_overlap(box(c), box(comps[0]), range(3), margin=GAP_TOL)]
                if comps:
                    root = max(comps[0], key=lambda n: parts[n].volume)
                main_dirs = {parts[n].path.parent for n in comps[0]} if comps else set()
                for c in separate:
                    if len(c) >= 2:
                        # kendi klasöründeki üyeler klasör adıyla, figürle aynı klasördekiler adıyla çıkarılır
                        # (Chimchar: BASE\OUISTICRAM harfleri + BASE klasöründeki "BASE OUISTICRAM")
                        toks = sorted({parts[n].path.parent.name for n in c if parts[n].path.parent not in main_dirs})
                        toks += sorted(n for n in c if parts[n].path.parent in main_dirs)
                        how = f"--exclude \"{','.join(toks)}\"" if len(toks) <= 4 else "--exclude"
                        warnings.append(f"Ana montaja değmeyen ayrı grup ({len(c)} parça): {', '.join(sorted(c))} — "
                                        "yedek/alternatif set olabilir (ör. başka dilde isim harfli ikinci kaide). "
                                        f"Basılacak; istemiyorsanız {how} ile çıkarın.")
                for n in names:
                    placed[n] = True
                links = _links_from_fits(fits, placed, "dosyadan", root)
                movers = [m for m in misplaced if m in selected]
                # Orijindeki büyük parça (montajın ≥ %30'u kadar) yerine konacak bir parça değil, kitin birleşik /
                # alternatif sürümüdür (Maomao: Base_No_Fence 785 ml ↔ bölünmüş Base1..6): basılmaz, bildirilir
                main_vol = sum(parts[n].volume for n in comps[0]) if comps else 0.0
                user = {c.lower() for c in (variant_choices or [])}
                whole = [m for m in movers if parts[m].volume >= 0.3 * main_vol and m.lower() not in user]
                if whole:
                    movers = [m for m in movers if m not in whole]
                    selected = [n for n in selected if n not in whole]
                    groups.append(VariantGroup(sorted(whole), [], "geometri", -1.0, [], True))
                    for m in whole:
                        placed[m] = False
                    warnings.append("Dosyada orijinde duran büyük parçalar kitin birleşik / alternatif sürümü gibi "
                                    f"(montajın %{100 * max(parts[m].volume for m in whole) / max(main_vol, 1):.0f}'i "
                                    f"kadar): {', '.join(sorted(whole))}. Basılmayacak; isterseniz --variant ile seçin.")
                if movers and comps:
                    # dosyada orijinde unutulmuş parçalar: montaj yerinde sabit, onlar ek yüzeyi / kenetlenmeyle
                    # yerleştirilir (Atsu: Stand_Ground_Part_2, Sword_Belt_1/2; Maomao dioraması: Jinshi\Bun)
                    log(f"Orijinde duran {len(movers)} parçanın yeri aranıyor: {', '.join(movers)}")
                    upv = np.zeros(3)
                    upv[up_k] = up_s
                    res = place_onto(sorted(comps[0]), movers, parts, fields, samples, upv, log)
                    found = [m for m in movers if res.placed.get(m)]
                    for m in movers:
                        placed[m] = m in found
                        if m in found:
                            offsets[m] = np.asarray(res.offsets[m], float)
                            lk = res.links.get(m, {})
                            links[m] = dict(to=lk.get("to"), contact=lk.get("contact", 0), kind="orijinden")
                    if found:
                        warnings.append("Dosyada montaj konumunda olmayan (orijinde duran) parçalar ek yüzeylerinden "
                                        "yerine oturtuldu — önizlemede kontrol edin: " + ", ".join(found))
                    lost = [m for m in movers if m not in found]
                    if lost:
                        warnings.append("Dosyada montaj konumunda olmayan (orijinde duran) parçaların yeri "
                                        "bulunamadı (ayrı basılacak, boya katılmadı): " + ", ".join(lost))
            else:
                # Dosyalardaki konumlar montaj değil (parçalar dağınık / plate düzeninde export edilmiş, ortalanmamış:
                # 1/6 Casca CA3D). Konuma dayalı "aynı yeri kaplıyor = alternatif" kararları da geçersiz (Torso ile Leg_L
                # orijinde üst üste duruyordu, sol bacak basılmayacaktı): varyantlar sadece adla yeniden aranır ve
                # otomatik montaj denenir.
                log("Parçalar birbirine değmiyor: dosyalar montajlı değil, otomatik montaj deneniyor…")
                warnings.append("Dosyalardaki konumlar montajlı değil (parçaların çoğu birbirine değmiyor): ek "
                                "yüzeylerinden otomatik montaj denendi.")
                mode_guess = "otomatik"
                groups = detect_variants(parts_list, fields, None, variant_choices)
                unselected = {m for g in groups for m in g.members if m not in g.selected}
                selected = [n for n in names if n not in unselected]
                variant_pairs = {frozenset(c) for g in groups for c in (g.conflicts or [])}
                placed = {n: False for n in names}
                fits = []
                root = max(selected, key=lambda n: parts[n].volume) if selected else None
        if mode_guess == "otomatik":
            from .autoassemble import auto_assemble

            # ad/boyut benzerliğinden gelen olası alternatifler aynı yere oturabilir: birbirine karşı ceza yok
            alt: dict[str, set] = {}
            for g in groups:
                for a, b in (g.conflicts or []):
                    alt.setdefault(a, set()).add(b)
                    alt.setdefault(b, set()).add(a)
            upv = np.zeros(3)
            upv[up_k] = up_s
            res = auto_assemble(parts, fields, samples, names, log, alternatives=alt, up=upv, exclude=detach, hints=join)
            if res.alt_root:
                # kök parçanın grubu figürün büyük kısmını içermiyor: büyük alt montajın tohumunu kök yaparak yeniden dene
                log(f"Ana montaj küçük kaldı; {res.alt_root} kök alınarak yeniden deneniyor…")
                res2 = auto_assemble(parts, fields, samples, names, log, alternatives=alt, up=upv, exclude=detach,
                                     hints=join, root=res.alt_root)

                def placed_vol(r):
                    return sum(parts[n].volume for n in names if r.placed.get(n))
                if placed_vol(res2) > placed_vol(res):
                    res = res2
            links = dict(res.links)
            for a, b in res.hint_failed:
                warnings.append(f"Kullanıcı bağlantısı uygulanamadı: {b} → {a} ("
                                + ("çapa montaja yerleşmedi" if not res.placed.get(a)
                                   else f"{b} için {a} ile temaslı ve başka parçaya gömülmeyen bir konum bulunamadı") + ").")
            n_placed = sum(res.placed[n] for n in names)
            vol_frac = sum(parts[n].volume for n in names if res.placed[n]) / max(sum(parts[n].volume for n in names), 1)
            # Kabul: parçaların çoğu ya da (kökü/kaideyi içeren) ana montaj hacmin yarısından fazlası. Ayrı duran
            # bir alt montaj (Radahn: Miquella + saçları) ana figürün boyunu ölçmeye engel değil.
            if n_placed >= max(3, 0.6 * len(names)) or (n_placed >= 4 and vol_frac >= 0.5):
                mode, root = "otomatik", res.root
                offsets = {n: np.asarray(res.offsets[n], float).copy() for n in names}
                placed = dict(res.placed)
                sub_groups = [{m: np.asarray(P, float).copy() for m, P in g.items()} for g in res.groups]
                from .autoassemble import STRICT_FACTOR

                best_c: dict[str, float] = {}
                for m in res.matches:
                    best_c[m.b] = max(best_c.get(m.b, 0.0), m.contact)
                # kullanıcının bağladığı parçalar doğrulamada geri ayrılmaz (iç içe geçme uyarısı yine verilir)
                weak_placed = {n for n, c in best_c.items() if c < 60.0 * STRICT_FACTOR
                               and res.links.get(n, {}).get("kind") != "kullanıcı"}
                # alternatifleri gerçek konumlarında hacim örtüşmesiyle doğrula; yukarı eksenini montajdan bul
                shifted = []
                for n in names:
                    if placed[n]:
                        q = copy.copy(parts[n])
                        q.lite = parts[n].lite.copy()
                        q.lite.apply_translation(offsets[n])
                        q.bounds = parts[n].bounds + offsets[n]
                        shifted.append(q)
                sfields = {q.name: DistanceField(q.lite) for q in shifted}
                # sadece önceden şüphelenilen (ad/boyut benzeri) çiftler onaylanabilir: montaj hatası ilgisiz bir
                # parçayı "alternatif" yapıp basılacaklardan çıkaramasın
                # bir alternatife bağlı eklenen parçalar (Miquella → Head_v2) diğer alternatifle (Head_v1) aynı yeri
                # kaplar: bu çiftler de alternatif sayılır
                allowed = {a: set(v) for a, v in alt.items()}
                for m, req in res.requires.items():
                    for r in req:
                        for o in alt.get(r, ()):
                            allowed.setdefault(m, set()).add(o)
                            allowed.setdefault(o, set()).add(m)
                confirmed = [g for g in detect_variants(shifted, sfields, [], variant_choices)
                             if all(b in allowed.get(a, ()) for a, b in (g.conflicts or []))]
                confirmed = _apply_preference(confirmed, res.prefer, variant_choices, parts)
                in_conf = {m for g in confirmed for m in g.members}
                groups = confirmed + [g for g in groups if not (set(g.members) & in_conf)]
                unselected = {m for g in groups for m in g.members if m not in g.selected}
                selected = [n for n in names if n not in unselected]
                variant_pairs = {frozenset(c) for g in groups for c in (g.conflicts or [])}
                # kullanıcı diğer sürümü seçtiyse ona bağlı eklenmiş parçaların montajda yeri yok
                orphan = sorted(m for m, req in res.requires.items()
                                if placed.get(m) and m not in unselected and req & unselected)
                if orphan:
                    for m in orphan:
                        placed[m] = False
                    need_alt = sorted(set().union(*(res.requires[m] for m in orphan)))
                    warnings.append(f"{', '.join(orphan)}: sadece {', '.join(need_alt)} ile birlikte oturuyor; seçilen "
                                    "sürümde yeri yok (ayrı basılacak, istemiyorsanız --exclude ile çıkarın).")
                up_k, up_s = detect_up_axis(shifted, [], up_override)
                # yükseklik/normalize: montajın en alt noktası 0 olsun (yukarı ekseninde)
                missing = [n for n in names if not placed[n]]
                if missing:
                    grouped = [sorted(g) for g in res.groups]
                    singles = [n for n in missing if not any(n in g for g in grouped)]

                    def g_height(g):
                        return (max(parts[m].bounds[1][up_k] + P[up_k] for m, P in g.items())
                                - min(parts[m].bounds[0][up_k] + P[up_k] for m, P in g.items()))

                    txt = "; ".join([" + ".join(sorted(g)) + f" (kendi içinde birleşti, kendi boyu ~{g_height(g):.0f} mm)"
                                     for g in res.groups] + singles)
                    warnings.append(f"Otomatik montajda yeri bulunamadı (hacim payı %{100 * (1 - vol_frac):.0f}; ayrı "
                                    f"basılacak, boya katılmadı — figürün üstüne taşan bir parçaysa gerçek boy daha "
                                    f"fazladır): {txt}")
                log(f"Otomatik montaj: {n_placed}/{len(names)} parça yerleşti (hacim payı %{100 * vol_frac:.0f}).")
            else:
                mode = "bilinmiyor"
                warnings.append(f"Otomatik montaj yetersiz ({n_placed}/{len(names)} parça yerleşti); montaj bilinmiyor.")
        elif mode_guess not in ("kısmi", "tam"):
            mode = "bilinmiyor"

        # kullanıcı düzeltmeleri: otomatik montajda zaten uygulandı (ayrılanlar montaja alınmadı); diğer modlarda
        # ayrılan parçanın yeri bilinmiyor sayılır (boya katılmaz), bağlantılar uygulanamaz
        if detach and mode in ("tam", "kısmi"):
            for n in detach:
                placed[n] = False
        if join and mode != "otomatik":
            warnings.append("Montaj bağlantısı düzeltmeleri sadece otomatik montajda (parçalar orijinde) uygulanır; "
                            f"bu kitte montaj: {mode}.")
        if detach or join:
            txt = ([f"ayrılan: {', '.join(detach)}"] if detach else []) + \
                  ([f"bağlanan: {', '.join(f'{j[1]} → {j[0]}' + (f' (konum {j[2] + 1})' if len(j) > 2 and j[2] else '') for j in join)}"]
                   if join else [])
            warnings.append("Kullanıcı montaj düzeltmeleri uygulandı (" + "; ".join(txt) + ").")
        for n in detach:
            links[n] = dict(to=None, contact=0, kind="kullanıcı ayırdı")
        asm = Assembly(mode, up_k, up_s, zeroed, offsets, placed, selected, groups, fits, root, warnings=warnings,
                       groups=sub_groups, links=links, fixes=dict(detach=detach, join=[list(j) for j in join]))
        if mode == "bilinmiyor":
            for n in names:
                asm.placed[n] = False
            if any(g.reason != "ad" for g in asm.variant_groups):
                # konum güvenilmezken geometriye dayalı "alternatif" kararı bir parçayı basılacaklardan çıkarmasın
                asm.variant_groups = detect_variants(parts_list, fields, None, variant_choices)
                asm.selected = [n for n in names
                                if n not in {m for g in asm.variant_groups for m in g.members if m not in g.selected}]
        else:
            log("Montaj doğrulanıyor (iç içe geçme kontrolü)…")
            sel_placed = [n for n in selected if placed[n]]
            asm.penetration = penetration_check(sel_placed, fields, samples, offsets, parts)
            asm.penetration = recheck_full(asm.penetration, parts, offsets, log)
            bad =[(a, b, d) for a, b, d, area in asm.penetration if d > 0.5 and area > 2.0]
            # Yaygın derin iç içe geçme = montaj yanlış (ör. plate'e dizilmiş kit "kısmen kaymış" sanıldı).
            # Tek tük derin temas tasarımdan olabilir (Rango: poncho gövdeye gömülü).
            involved = {n for a, b, d, area in asm.penetration if d > 1.0 and area > 5.0 for n in (a, b)}
            drop = involved & weak_placed
            if asm.mode == "otomatik" and drop:
                # Zayıf temasla (kenetlenme/küçük ek) yerleşip başka parçaya gömülen parçalar: tüm montajı reddetmek
                # yerine sadece onların yeri bilinmiyor sayılır (Radahn: Miquella'nın 0.5 ml kolları Radahn'a)
                for n in drop:
                    asm.placed[n] = False
                    asm.links[n] = dict(asm.links.get(n, {}), kind="ayrıldı")
                asm.warnings.append("Zayıf temasla yerleşen ama başka parçalara gömülen parçaların yeri bilinmiyor "
                                    "sayıldı (ayrı basılacak): " + ", ".join(sorted(drop)))
                sel_placed = [n for n in sel_placed if n not in drop]
                asm.penetration = [x for x in asm.penetration if x[0] not in drop and x[1] not in drop]
                bad = [x for x in bad if x[0] not in drop and x[1] not in drop]
                involved -= drop
            # Sadece kendi çözdüğümüz (kısmi) montajı reddet; tam montajlı kitte konumlar dosyanın kendisinden gelir.
            if asm.mode in ("kısmi", "otomatik") and len(involved) > max(2, 0.3 * len(sel_placed)):
                asm.mode = "bilinmiyor"
                asm.warnings.append(f"Montaj doğrulanamadı: {len(involved)} parça birbirine 1 mm'den derin geçiyor "
                                    f"({', '.join(sorted(involved))}; parçalar plate'e dizilmiş olabilir). Montaj "
                                    "bilinmiyor moduna geçildi.")
                for n in names:
                    asm.placed[n] = False
                    asm.offsets[n] = np.zeros(3)
                asm.penetration = []
                asm.groups = []
                asm.links = {}
                bad = []
                # Geometriye dayalı varyant kararları da geçersiz: hiçbir parçayı dışlama, sadece ad benzerliğini bildir
                asm.variant_groups = detect_variants(parts_list, fields, None, variant_choices)
                asm.selected = [n for n in names
                                if n not in {m for g in asm.variant_groups for m in g.members if m not in g.selected}]
            for a, b, d in bad:
                asm.warnings.append(f"{a} ↔ {b}: {d:.2f} mm iç içe geçme (beklenen < 0.5 mm; tasarımdan olabilir, "
                                    "önizlemeyi kontrol edin).")
        if asm.mode == "bilinmiyor":
            asm.warnings.append("Montaj bilinmiyor: hedef yükseklik için modelin orijinal boyu gerekir (--orig-height, ürün "
                                "sayfasındaki değer) ya da --scale; delikler tahmini kesim yüzeylerine açılır.")
        for g in asm.variant_groups:
            if not g.confirmed and set(g.selected) == set(g.members):
                asm.warnings.append(f"Olası alternatif parçalar (sadece ad benzerliği, hepsi basılacak): "
                                    f"{' / '.join(g.members)}. İstemediğinizi hariç tutun (--exclude).")
        return asm

    # Sürüm takımları (ada göre; Atsu: bölünmüş / tek parça gövde / büst): seçilmeyen takım çözüme girmez
    vgroup, vdrop, vwarn = detect_version_sets(parts_list, variant_choices)
    if vdrop:
        log("Seçilmeyen sürüm: " + ", ".join(sorted(vdrop)))
        parts_list = [p for p in parts_list if p.name not in vdrop]
        names = [p.name for p in parts_list]
    # Alt klasörler farklı koordinatlarda export edilmişse (Maomao dioraması, Ichigo) her küme ayrı çözülür
    clusters = frame_clusters(parts_list, fields, samples)
    if clusters:
        log(f"Alt klasörler ayrı koordinatlarda: {len(clusters)} küme ayrı montajlanıyor…")
        asms = [_solve_one(cl, detach, join) for cl in clusters]
        seats, seat_warns, seat_links = _seat_clusters(asms, clusters, parts, fields, samples, join, log)
        asm = _merge_clusters(asms, clusters, parts, seats)
        asm.warnings[1:1] = seat_warns
        asm.links.update(seat_links)
        if seats or seat_warns:
            asm.fixes = dict(asm.fixes or {}, join=[list(j) for j in join])
    else:
        asm = _solve_one(parts_list, detach, join)
    if vgroup:
        asm.variant_groups.append(vgroup)
        asm.warnings.insert(0, vwarn)
        for n in vdrop:
            asm.offsets[n] = np.zeros(3)
            asm.placed[n] = False
    asm.reference_parts = early_refs + detect_reference_parts(parts, asm)
    if asm.reference_parts:
        asm.selected = [n for n in asm.selected if n not in asm.reference_parts]
        k = asm.up_axis
        for n in asm.reference_parts:
            if n in early_refs:
                asm.offsets[n] = np.zeros(3)
                asm.placed[n] = False
            h = float(parts[n].bounds[1][k] - parts[n].bounds[0][k])
            asm.links[n] = dict(to=None, contact=0, kind="referans")
            why = "adı referans, figüre değmiyor" if n in early_refs else "düz prizma, figüre değmiyor"
            asm.warnings.append(f"{n}: stüdyonun ölçek/boy referans parçası gibi ({why}; boyu "
                                f"{h:.1f} mm — genelde karakterin boyu × ölçek). Basılmayacak ve yüksekliğe "
                                "katılmadı (basmak için --print-reference).")
    asm.base_parts = base_override or (detect_base(parts, asm) if asm.selected and asm.mode != "bilinmiyor" else [])
    if junk:
        asm.junk_parts = sorted(junk)
        for n in junk:
            asm.offsets[n] = np.zeros(3)
            asm.placed[n] = False
            asm.links[n] = dict(to=None, contact=0, kind="reklam")
        asm.warnings.append("Reklam/filigran dosyası gibi görünen parçalar montaja katılmadı ve basılmayacak: "
                            + "; ".join(f"{n} — {junk[n]}" for n in sorted(junk))
                            + ". Yanlışsa --print-reference ile basılır.")
    return asm, fields, samples
