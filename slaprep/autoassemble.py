"""Otomatik montaj: montaj bilgisi olmayan kitlerde (her parça orijine ortalanmış) parçaları ek yüzeylerinden
eşleştirerek birleştirir.

Varsayım: parçalar sadece ötelenmiş, döndürülmemiş (stüdyo export'larında "orijine ortala" yaygın).

Yöntem
1. Ek yüzeyi adayları: her parçada büyük düz bölgeler (stüdyolar parçaları düz kesimlerle böler).
2. Eşleştirme: aynı kesimden çıkan iki yüzün normalleri terstir ve kesim çizgisi (siluet) aynıdır. İki yüz
   A'nın düzlemine izdüşürülüp 2B'de FFT korelasyonuyla en iyi örtüştükleri kaymaya getirilir → B'nin A'ya
   göre 3B konumu (düzlem içi kayma + düzlemler çakışacak normal kayma). Pim/yuva şekli konumu sabitler.
3. 3B doğrulama + iyileştirme: aday konumda temas alanı − 3 × iç içe geçme alanı; eksen eksen küçük taramayla
   iyileştirilir.
4. Global çözüm: güvenilir eşleşmelerden en büyük yayılan ağaç (kök: en büyük parça), sonra aykırı değer reddiyle
   ağırlıklı en küçük kareler (3B).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx
import numpy as np
import trimesh
from scipy import ndimage
from scipy.signal import fftconvolve

from .geom import DistanceField
from .joints import planar_regions

RES = 0.3            # mm, yüz maskesi çözünürlüğü
NORMAL_DOT = -0.985  # zıt normal şartı (~10°)
CONTACT_TOL = 0.3
PEN_TOL = 0.6
STRICT_FACTOR = 5.0  # 1. aşamada gereken temas çarpanı (60 → 300 mm²)


@dataclass
class Facet:
    part: str
    faces: np.ndarray
    n: np.ndarray        # dış normal (alan ağırlıklı)
    d: float             # düzlem: x·n = d
    area: float
    pts: np.ndarray      # yüz üzerinde yoğun örnek noktalar (parça koordinatı)


@dataclass
class Match:
    a: str
    b: str
    t: np.ndarray        # B'ye uygulanacak öteleme (A çerçevesinde): x_b + t
    iou: float
    contact: float
    pen: float
    score: float
    used: bool = False
    residual: float = float("nan")


@dataclass
class AutoResult:
    offsets: dict[str, np.ndarray]           # her parçaya uygulanacak öteleme (köke göre)
    placed: dict[str, bool]
    matches: list[Match] = field(default_factory=list)
    root: str | None = None
    groups: list[dict] = field(default_factory=list)   # ana montaja eklenemeyen alt montajlar (üye → konum)
    prefer: set = field(default_factory=set)           # bir eklemenin sadece onunla mümkün olduğu alternatifler
    requires: dict = field(default_factory=dict)       # parça → birlikte olması gereken alternatif(ler)
    links: dict = field(default_factory=dict)          # parça → {to, contact, kind}: neye, nasıl bağlandı
    hint_failed: list = field(default_factory=list)    # uygulanamayan kullanıcı bağlantıları [(çapa, parça)]
    alt_root: str | None = None                        # ana montajdan büyük kalan alt montajın tohumu (kökle dene)


def facets_of(name: str, mesh: trimesh.Trimesh, min_area: float = 20.0, max_n: int = 12) -> list[Facet]:
    out = []
    for f in planar_regions(mesh, min_area=min_area):
        a = mesh.area_faces[f]
        n = (mesh.face_normals[f] * a[:, None]).sum(0)
        n /= np.linalg.norm(n)
        sub = mesh.submesh([f], append=True)
        k = int(np.clip(sub.area / (RES * RES * 0.3), 300, 40000))
        pts, _ = trimesh.sample.sample_surface(sub, k, seed=0)
        pts = np.vstack([pts, sub.vertices])
        out.append(Facet(name, f, n, float(np.median(pts @ n)), float(a.sum()), pts))
    out.sort(key=lambda x: -x.area)
    return out[:max_n]


def _basis(n):
    a = np.array([1.0, 0, 0]) if abs(n[0]) < 0.9 else np.array([0, 1.0, 0])
    u = np.cross(n, a)
    u /= np.linalg.norm(u)
    return u, np.cross(n, u)


def _mask(p2, lo, shape):
    ij = np.floor((p2 - lo) / RES).astype(int)
    ok = np.all((ij >= 0) & (ij < shape), axis=1)
    m = np.zeros(shape, bool)
    m[ij[ok, 0], ij[ok, 1]] = True
    m = ndimage.binary_closing(m, iterations=2)
    return ndimage.binary_fill_holes(m)


def facet_translation(fa: Facet, fb: Facet) -> tuple[np.ndarray, float] | None:
    """B yüzünü A yüzüne oturtan öteleme ve siluet IoU'su."""
    if fa.n @ fb.n > NORMAL_DOT:
        return None
    if not 0.4 < fa.area / fb.area < 2.5:
        return None
    u, v = _basis(fa.n)
    pa = np.column_stack([fa.pts @ u, fa.pts @ v])
    pb = np.column_stack([fb.pts @ u, fb.pts @ v])
    loa, lob = pa.min(0) - RES, pb.min(0) - RES
    ma = _mask(pa, loa, np.ceil((pa.max(0) - loa) / RES).astype(int) + 2)
    mb = _mask(pb, lob, np.ceil((pb.max(0) - lob) / RES).astype(int) + 2)
    corr = fftconvolve(ma.astype(np.float32), mb[::-1, ::-1].astype(np.float32), mode="full")
    k = np.unravel_index(np.argmax(corr), corr.shape)
    ov = float(corr[k])
    iou = ov / (ma.sum() + mb.sum() - ov)
    s = np.array(k) - (np.array(mb.shape) - 1)            # B hücresi i ↔ A hücresi i + s
    t_uv = loa - lob + s * RES
    t_n = fa.d - float(np.median(fb.pts @ fa.n))
    return u * t_uv[0] + v * t_uv[1] + fa.n * t_n, iou


def _score(fa_field: DistanceField, pts: np.ndarray, w: float, t: np.ndarray):
    p = pts + t
    m = np.all((p >= fa_field.bounds[0] - 1) & (p <= fa_field.bounds[1] + 1), axis=1)
    if not m.any():
        return 0.0, 0.0
    d = fa_field.signed(p[m])
    return float((np.abs(d) < CONTACT_TOL).sum() * w), float((d < -PEN_TOL).sum() * w)


def refine(fa_field, pts, w, t, steps=(1.0, 0.4, 0.15), it=2):
    """Eksen eksen küçük tarama: skor = temas − 3·iç içe."""
    best_c, best_p = _score(fa_field, pts, w, t)
    best = best_c - 3 * best_p
    for st in steps:
        for _ in range(it):
            improved = False
            for ax in range(3):
                for sgn in (-1, 1):
                    tt = t.copy()
                    tt[ax] += sgn * st
                    c, p = _score(fa_field, pts, w, tt)
                    if c - 3 * p > best:
                        best, best_c, best_p, t, improved = c - 3 * p, c, p, tt, True
            if not improved:
                break
    return t, best_c, best_p


def pair_match(a, b, fac, fields, samples, rng, min_iou=0.35, top=4) -> Match | None:
    cands = []
    for fa in fac[a]:
        for fb in fac[b]:
            r = facet_translation(fa, fb)
            if r is not None and r[1] >= min_iou:
                cands.append((r[1], r[0]))
    if not cands:
        return None
    cands.sort(key=lambda x: -x[0])
    pts, _, w = samples[b]
    sub = pts if len(pts) <= 6000 else pts[rng.choice(len(pts), 6000, replace=False)]
    ws = w * len(pts) / len(sub)
    best = None
    for iou, t in cands[:top]:
        t2, c, p = refine(fields[a], sub, ws, t)
        sc = c - 3 * p
        if best is None or sc > best.score:
            best = Match(a, b, t2, iou, c, p, sc)
    if best is None or best.contact < 20.0 or best.pen > 0.15 * best.contact + 2.0:
        return None
    return best


def pair_candidates(a, b, fac, min_iou=0.35, top=6) -> list[tuple[float, np.ndarray]]:
    """B'nin A'ya göre aday ötelemeleri (siluet IoU'suna göre sıralı, birbirine çok yakın olanlar tekilleştirilmiş)."""
    cands = []
    for fa in fac[a]:
        for fb in fac[b]:
            r = facet_translation(fa, fb)
            if r is not None and r[1] >= min_iou:
                cands.append((r[1], r[0]))
    cands.sort(key=lambda x: -x[0])
    out = []
    for iou, t in cands:
        if all(np.linalg.norm(t - u) > 2.0 for _, u in out):
            out.append((iou, t))
        if len(out) >= top:
            break
    return out


def _global_parts(b, T, pos, parts, fields, pts, w, ignore=()):
    """B'yi T konumunda (kök çerçevesi) kutusu değen her yerleşmiş parçaya karşı puanla: {parça: (temas, iç içe)}."""
    bb = parts[b].lite.bounds + T
    out = {}
    for p, P in pos.items():
        if p in ignore:
            continue
        pb = parts[p].lite.bounds + P
        if np.any(bb[0] - 1 > pb[1]) or np.any(pb[0] - 1 > bb[1]):
            continue
        out[p] = _score(fields[p], pts, w, T - P)
    return out


def _global_score(b, T, pos, parts, fields, pts, w, ignore=()):
    """B'yi T konumunda (kök çerçevesi) tüm yerleşmiş parçalara karşı puanla: toplam temas, toplam iç içe."""
    per = _global_parts(b, T, pos, parts, fields, pts, w, ignore)
    return (sum(c for c, _ in per.values()), sum(p for _, p in per.values()),
            sum(c > 10 for c, _ in per.values()))


def _refine_global(b, T, pos, parts, fields, pts, w, steps=(1.0, 0.4, 0.15), ignore=()):
    c, p, k = _global_score(b, T, pos, parts, fields, pts, w, ignore)
    best = c - 3 * p
    for st in steps:
        for _ in range(2):
            improved = False
            for ax in range(3):
                for sgn in (-1, 1):
                    TT = T.copy()
                    TT[ax] += sgn * st
                    c2, p2, k2 = _global_score(b, TT, pos, parts, fields, pts, w, ignore)
                    if c2 - 3 * p2 > best:
                        best, c, p, k, T, improved = c2 - 3 * p2, c2, p2, k2, TT, True
            if not improved:
                break
    return T, c, p, k


def bundle_adjust(pos, root, parts, fields, sub, sweeps=2, steps=(0.4, 0.15), alt=None, only=None, frozen=()):
    """Zincir boyunca biriken kaymayı gider: her parçayı (kök sabit) diğer tüm yerleşmişlere karşı yeniden ayarla.
    only: sadece bu parçalar ayarlanır (yeni eklenen parça ve komşuları; büyük kitlerde tam tur pahalı).
    frozen: konumu dosyadan bilinen, hiç oynatılmayan parçalar."""
    alt = alt or {}
    for _ in range(sweeps):
        for n in list(pos):
            if n == root or n in frozen or (only is not None and n not in only):
                continue
            others = {k: v for k, v in pos.items() if k != n}
            T, c, p, k = _refine_global(n, pos[n], others, parts, fields, *sub[n], steps=steps,
                                        ignore=alt.get(n, ()))
            pos[n] = T
    return pos


def _signed_box(pos, parts, lo, res, n):
    """pos parçalarının işaretli mesafesi, ızgaranın sadece onların kutusuna (+2 hücre) düşen kısmında.
    Dönüş: (kutu dilimi, mesafe) ya da kutu ızgaranın dışındaysa None."""
    import open3d as o3d

    scene = o3d.t.geometry.RaycastingScene()
    for p, P in pos.items():
        m = parts[p].lite
        tm = o3d.t.geometry.TriangleMesh()
        tm.vertex.positions = o3d.core.Tensor((m.vertices + P).astype(np.float32))
        tm.triangle.indices = o3d.core.Tensor(m.faces.astype(np.int32))
        scene.add_triangles(tm)
    plo = np.min([parts[p].lite.bounds[0] + P for p, P in pos.items()], axis=0) - 2 * res
    phi = np.max([parts[p].lite.bounds[1] + P for p, P in pos.items()], axis=0) + 2 * res
    i0 = np.clip(np.floor((plo - lo) / res).astype(int), 0, n - 1)
    i1 = np.clip(np.ceil((phi - lo) / res).astype(int) + 1, 1, n)
    if np.any(i1 <= i0):
        return None
    axes = [lo[k] + np.arange(i0[k], i1[k]) * res for k in range(3)]
    g = np.stack(np.meshgrid(*axes, indexing="ij"), -1).reshape(-1, 3).astype(np.float32)
    d = np.empty(len(g), np.float32)
    for i in range(0, len(g), 4_000_000):
        d[i:i + 4_000_000] = scene.compute_signed_distance(o3d.core.Tensor(g[i:i + 4_000_000])).numpy()
    return tuple(slice(i0[k], i1[k]) for k in range(3)), d.reshape(i1 - i0)


def assembly_grid(pos, parts, pad, res=1.0, max_vox=30_000_000, avoid=None):
    """Yerleşmiş parçaların ızgarası: yüzeye yakın (|d|<res) ve derin iç (d<-res) hücreler.
    avoid: temas ödülü getirmeyen ama içine girilmemesi gereken parçalar (kullanıcı bağlantısında çapanın komşuları):
    sadece "derin" hücrelerine eklenir."""
    lo = np.min([parts[p].lite.bounds[0] + P for p, P in pos.items()], axis=0) - pad - 2
    hi = np.max([parts[p].lite.bounds[1] + P for p, P in pos.items()], axis=0) + pad + 2
    vol = float(np.prod(hi - lo))
    if vol / res ** 3 > max_vox:
        res = (vol / max_vox) ** (1 / 3) + 1e-6
    n = np.ceil((hi - lo) / res).astype(int) + 1
    near = np.zeros(n, np.float32)
    deep = np.zeros(n, np.float32)
    # mesafe sadece montaj kutusunun (+2 hücre) içinde hesaplanır; dolgu bölgesi zaten boş (yakın/derin değil)
    box, d = _signed_box(pos, parts, lo, res, n)
    near[box] = np.abs(d) < res
    # "iç" eşiği hücreden sığ: ince parçalar (saç telleri, ~2 hücre kalınlık) kaba ızgarada da iç hücre üretsin,
    # yoksa onlara gömülen yanlış konumlar cezasız kalır. B'nin içi zaten 1 hücre aşındırılmış: gerçek temasta
    # iki iç bölge kesişmez.
    deep[box] = d < -0.3 * res
    if avoid:
        r = _signed_box(avoid, parts, lo, res, n)
        if r is not None:
            deep[r[0]] = np.maximum(deep[r[0]], r[1] < -0.3 * res)
    return dict(lo=lo, res=res, near=near, deep=deep)


def fft_dock(members, grid, parts, fields, sub, peaks=6, min_dir=None):
    """3B FFT kenetlenme: B'nin (tek parça adı ya da rijit grup {üye: grup içi konum}) tüm ötelemelerini tek
    seferde puanla. skor(T) = Σ B_yüzey(x−T)·A_yakın(x) − 3·Σ B_iç(x−T)·A_derin(x). Dönüş: en iyi ötelemeler.
    min_dir: (yön, değer) — sadece T·yön ≥ değer ötelemeleri (kümeyi kaidenin üstüne oturtma)."""
    if isinstance(members, str):
        members = {members: np.zeros(3)}
    lo, res, near, deep = grid["lo"], grid["res"], grid["near"], grid["deep"]
    # B'nin kendi ızgarası (aynı çözünürlük)
    blo = np.min([parts[m].lite.bounds[0] + P for m, P in members.items()], axis=0) - res
    bhi = np.max([parts[m].lite.bounds[1] + P for m, P in members.items()], axis=0)
    nb = np.ceil((bhi - blo) / res).astype(int) + 2
    shell = np.zeros(nb, np.float32)
    bax = [blo[k] + (np.arange(nb[k]) + 0.5) * res for k in range(3)]
    bg = np.stack(np.meshgrid(*bax, indexing="ij"), -1).reshape(-1, 3)
    inside = np.zeros(len(bg), bool)
    for m, P in members.items():
        ij = np.floor((sub[m][0] + P - blo) / res).astype(int)
        shell[ij[:, 0], ij[:, 1], ij[:, 2]] = 1.0
        mb = parts[m].lite.bounds + P
        sel = np.all((bg >= mb[0]) & (bg <= mb[1]), axis=1)
        inside[sel] |= fields[m].occupancy(bg[sel] - P)
    inside = ndimage.binary_erosion(inside.reshape(nb), iterations=1).astype(np.float32)
    if any(shell.shape[k] > near.shape[k] for k in range(3)):
        return []
    s = fftconvolve(near, shell[::-1, ::-1, ::-1], mode="valid") - 3.0 * fftconvolve(deep, inside[::-1, ::-1, ::-1], mode="valid")
    if min_dir is not None:              # izin verilen yarı uzay: T·yön ≥ değer (T = lo + k·res − blo)
        u, v = np.asarray(min_dir[0], float), float(min_dir[1])
        tu = sum((lo[k] - blo[k] + np.arange(s.shape[k]) * res).reshape([-1 if i == k else 1 for i in range(3)])
                 * u[k] for k in range(3))
        s = np.where(tu >= v, s, -np.inf)
    out = []
    sv = s.ravel()
    top = np.argpartition(sv, -4000)[-4000:] if sv.size > 4000 else np.arange(sv.size)
    flat = top[np.argsort(sv[top])[::-1]]
    for f in flat:
        if not np.isfinite(sv[f]):
            break
        k = np.array(np.unravel_index(f, s.shape))
        T = lo + k * res - blo
        if all(np.linalg.norm(T - u) > 4.0 for u in out):
            out.append(T)
        if len(out) >= peaks:
            break
    return out


def auto_assemble(parts: dict, fields: dict, samples: dict, names: list[str], log=lambda m: None,
                  min_contact: float = 60.0, max_facets: int = 40,
                  alternatives: dict[str, set] | None = None, up: np.ndarray | None = None,
                  exclude=None, hints=None, root: str | None = None,
                  fixed: dict[str, np.ndarray] | None = None, hint_min=None) -> AutoResult:
    """Artımlı montaj: kökten başlayıp her adımda, yerleşmiş parçalara karşı en güçlü oturan parçayı ekle.
    Aday konumlar ek yüzeyi eşleşmelerinden gelir; her aday TÜM yerleşmiş parçalara karşı puanlanır (yanlış yer
    başka bir parçaya gömüleceği için elenir). alternatives: olası alternatif çiftler (aynı yere oturabilirler,
    birbirlerine karşı iç içe geçme sayılmaz).
    Kullanıcı düzeltmeleri: exclude — montaja alınmayacak parçalar (yanlış yere oturuyordu); hints — [(çapa, parça)]:
    parça otomatik yerleştirilmez, çapası yerleşince sadece çapasıyla teması aranarak ona bağlanır.
    fixed: konumu dosyadan bilinen parçalar {ad: öteleme}: ana montaj bunlarla başlar ve hiç oynatılmaz; sadece
    diğerleri (montajlı kitte orijinde unutulmuş parçalar) onlara karşı yerleştirilir.
    hint_min: (yön, değer) — kullanıcı bağlantısıyla yerleşen parçanın ötelemesi T için T·yön ≥ değer (ayrı
    koordinatlı kümeyi kaideye oturtma: küme kaidenin üstünde durmalı, yan duvarına yaslanmamalı)."""
    exclude = set(exclude or ())
    fixed = {n: np.asarray(P, float) for n, P in (fixed or {}).items() if n in names and n not in exclude}
    frozen = set(fixed)
    names = [n for n in names if n not in exclude]
    # ipucu: (çapa, parça) ya da (çapa, parça, sıra) — sıra > 0: kullanıcı en iyi konumu beğenmedi, n. adayı istiyor
    rank = {h[1]: int(h[2]) for h in (hints or []) if len(h) > 2}
    hints = [(h[0], h[1]) for h in (hints or []) if h[0] in names and h[1] in names and h[0] != h[1]]
    movers = {b for _, b in hints}
    auto = [n for n in names if n not in movers]          # otomatik yerleştirilecekler
    # kopya: montaj sırasında "şu parça şu alternatifle bir arada olamaz" bilgisi eklenir, çağıranınki değişmesin
    alt = {k: set(v) for k, v in (alternatives or {}).items()}
    # alternatif kümeleri (Head_v1/Head_v2 gibi): bir aday kümenin sadece bir üyesiyle uyumlu olabilir (Radahn:
    # Miquella + saçı Head_v2'ye oturuyor, Head_v1'in yelesine gömülüyor)
    altsets, seen = [], set()
    for a0 in alt:
        if a0 in seen:
            continue
        comp, stack = set(), [a0]
        while stack:
            x = stack.pop()
            if x not in comp:
                comp.add(x)
                stack.extend(alt.get(x, ()))
        seen |= comp
        altsets.append(comp)
    prefer: set[str] = set()                     # bir eklemenin sadece onunla mümkün olduğu alternatifler
    requires: dict[str, set] = {}                # parça → birlikte olması gereken alternatif(ler)
    rng = np.random.default_rng(0)
    fac = {n: facets_of(n, parts[n].lite, max_n=max_facets) for n in names}
    log(f"Otomatik montaj: {sum(len(v) for v in fac.values())} ek yüzeyi adayı, {len(names)} parça.")
    sub = {}
    for n in names:
        pts, _, w = samples[n]
        s = pts if len(pts) <= 4000 else pts[rng.choice(len(pts), 4000, replace=False)]
        sub[n] = (s, w * len(pts) / len(s))
    cand = {}                                    # (a, b) → [(iou, t_ab)]  b'nin a'ya göre ötelemesi
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if a in frozen and b in frozen:
                continue                          # ikisinin de konumu biliniyor
            if fac[a] and fac[b]:
                c_ab = pair_candidates(a, b, fac)
                if c_ab:
                    cand[(a, b)] = c_ab
                    cand[(b, a)] = [(iou, -t) for iou, t in c_ab]
    forced_root = root in auto
    if fixed:
        root = max(fixed, key=lambda n: parts[n].volume)
    else:
        root = root if forced_root else max(auto, key=lambda n: parts[n].volume)
    matches: list[Match] = []
    # zemin kuralı kökün kaide olduğunu varsayar; kök elle (figür gövdesi) verildiyse uygulanmaz. Sabit montajda
    # zemin, montajın en alt noktasıdır.
    if fixed and up is not None:
        floor = min(float((parts[n].lite.vertices @ up).min() + P @ up) for n, P in fixed.items())
    else:
        floor = float((parts[root].lite.vertices @ up).min()) if up is not None and not forced_root else None
    strict = [True]          # 1. aşama: sadece güçlü ekler; 2. aşama: zayıf ekler de
    user_placed: set[str] = set()
    hint_tried: set = set()
    hint_cand: dict[str, str] = {}               # kullanıcı bağlantısında kullanılan aday: "2/4"

    def need(area):
        """Kabul için gereken temas: büyük parça küçük bir temasla oturmuş sayılmaz. Ek temas alanı kesit
        büyüklüğüyle ölçeklenir (yüzey alanıyla değil); bu yüzden oran küçük tutulur, yanlış yerleri iç içe geçme
        ve zemin kuralı eler. Önce güçlü ekler (≥ 5×) kurulur: açgözlü büyüme zayıf bir teması (Radahn:
        Miquella gövdesi kaide parçasına 78 mm²) asıl güçlü ekinden (saça 897 mm²) önce kapmasın."""
        return max(min_contact * (STRICT_FACTOR if strict[0] else 1.0), 0.004 * area)

    def group_score(members, T, base, detail=False):
        """Rijit grup (üye → grup içi konum) T kadar ötelenince `base` montajına karşı toplam temas/iç içe.
        Bir alternatif kümesinin birden çok üyesi `base`deyse sadece en iyi oturanı sayılır, diğerleri yok sayılır.
        detail=True: (c, p, k, {seçilen: [yok sayılanlar]}) — sadece seçimin belirgin fark yarattığı kümeler."""
        per: dict[str, list] = {}
        for m, P in members.items():
            for q, (c, p) in _global_parts(m, P + T, base, parts, fields, *sub[m], alt.get(m, ())).items():
                acc = per.setdefault(q, [0.0, 0.0])
                acc[0] += c
                acc[1] += p
        dropped, choice = set(), {}
        for s in altsets:
            present = [q for q in s if q in base]
            if len(present) < 2:
                continue
            sc = {q: (per[q][0] - 3 * per[q][1]) if q in per else 0.0 for q in present}
            best = max(present, key=lambda q: sc[q])
            others = [q for q in present if q != best]
            dropped |= set(others)
            # "sadece onunla mümkün": diğer alternatife belirgin gömülüyor (neredeyse aynı iki gövde arasındaki
            # küçük temas farkı tercih sayılmaz)
            pb = per.get(best, [0.0, 0.0])
            clash = [q for q in others if q in per and per[q][1] > max(20.0, 0.1 * pb[0]) and pb[1] < per[q][1] / 3]
            if clash:
                choice[best] = clash
        c_t = sum(v[0] for q, v in per.items() if q not in dropped)
        p_t = sum(v[1] for q, v in per.items() if q not in dropped)
        k_t = sum(v[0] > 10 for q, v in per.items() if q not in dropped)
        return (c_t, p_t, k_t, choice) if detail else (c_t, p_t, k_t)

    # kökün yatay izi (yukarı eksenine dik iki eksende): zemin kuralı sadece bu izin altına uygulanır
    if up is not None:
        hz = [k for k in range(3) if abs(up[k]) < 0.5]
        if fixed:
            rb = np.array([np.min([parts[n].lite.bounds[0] + P for n, P in fixed.items()], axis=0),
                           np.max([parts[n].lite.bounds[1] + P for n, P in fixed.items()], axis=0)])
        else:
            rb = parts[root].lite.bounds
        foot_lo, foot_hi = rb[0][hz], rb[1][hz]

    def group_floor_ok(members, T, use_floor):
        """Zemin kuralı: hiçbir parça kökün (kaide) taban yüzünün altına, onun yatay izi içinde inemez.
        Kaidenin yanındaki, biraz daha alçakta duran bir parça serbesttir."""
        if floor is None or not use_floor:
            return True
        for m, P in members.items():
            v = parts[m].lite.vertices[::7] + P + T
            below = (v @ up) < floor - 1.0
            if below.any():
                h = v[below][:, hz]
                inside = np.all((h > foot_lo + 2.0) & (h < foot_hi - 2.0), axis=1)
                if inside.any():
                    return False
        return True

    def refine_group(members, T, base, steps=(1.0, 0.4, 0.15)):
        c, p, k = group_score(members, T, base)
        best = c - 3 * p
        for st in steps:
            for _ in range(2):
                improved = False
                for ax in range(3):
                    for sgn in (-1, 1):
                        TT = T.copy()
                        TT[ax] += sgn * st
                        c2, p2, k2 = group_score(members, TT, base)
                        if c2 - 3 * p2 > best:
                            best, c, p, k, T, improved = c2 - 3 * p2, c2, p2, k2, TT, True
                if not improved:
                    break
        return T, c, p, k

    def quick(members, base, use_floor):
        """Ek yüzeyi adaylarından en iyi (iyileştirilmemiş) öteleme: (skor, T, a, b, iou) veya None."""
        tried, best = [], None
        for a in base:
            for b in members:
                for iou, t in cand.get((a, b), []):
                    T = base[a] + t - members[b]
                    if any(np.linalg.norm(T - u) < 1.5 for u in tried) or not group_floor_ok(members, T, use_floor):
                        continue
                    tried.append(T)
                    c, p, k = group_score(members, T, base)
                    if c >= min_contact * 0.5 and (best is None or c - 3 * p > best[0]):
                        best = (c - 3 * p, T, a, b, iou)
        return best

    def confirm(members, base, use_floor, q):
        """Hızlı adayı iyileştir ve kabul şartlarına bak. Dönüş: (T, c, p, k) veya None."""
        _, T0, a, b, iou = q
        T, c, p, k = refine_group(members, T0, base)
        area = max(parts[m].area for m in members)     # grup: en büyük üyenin ölçeği
        if c < need(area) or p > 0.1 * c + 2.0 or not group_floor_ok(members, T, use_floor):
            return None
        return T, c, p, k

    def note_choice(members, T, base):
        """Ekleme bir alternatifin sadece bir üyesiyle mümkünse: o üyeyi tercih et, üyeleri ona bağla ve
        diğerlerini bu üyeler için yok say (sonraki ince ayarda yelesine gömülmüş saç itilmesin)."""
        choice = group_score(members, T, base, detail=True)[3]
        for best, others in choice.items():
            prefer.add(best)
            for m in members:
                requires.setdefault(m, set()).add(best)
                alt.setdefault(m, set()).update(others)
            for o in others:
                alt.setdefault(o, set()).update(members)
            log(f"    ({best} ile uyumlu; {', '.join(others)} ile çakışıyor → {best} sürümü)")

    def record(members, base, q, res, label):
        _, _, a, b, iou = q
        T, c, p, k = res
        matches.append(Match(a, b, T + members[b] - base[a], iou, c, p, c - 3 * p, True, 0.0))
        log(f"  + {label} ← {a} (temas {c:.0f} mm², {k} parçaya değiyor)")
        note_choice(members, T, base)

    def attach(members, base, use_floor, label):
        """`members` grubunu `base` montajına ek yüzeyi adaylarıyla ekle; başarılıysa grup ötelemesi."""
        q = quick(members, base, use_floor)
        if q is None:
            return None
        res = confirm(members, base, use_floor, q)
        if res is None:
            return None
        record(members, base, q, res, label)
        return res[0]

    def grow(seed, pool, use_floor, start=None):
        """Tohumdan (veya `start` montajından) başlayıp havuzdaki parçaları tek tek ekleyerek (alt) montaj kur.
        Her adımda hızlı puanı en yüksek 3 aday iyileştirilir, en iyisi eklenir; olmazsa bu tur reddedilenler bir
        sonraki ekleme sonrası yeniden denenir."""
        pos = dict(start) if start else {seed: np.zeros(3)}
        failed: set[str] = set()
        while True:
            qs = []
            for b in pool:
                if b in pos or b in failed:
                    continue
                q = quick({b: np.zeros(3)}, pos, use_floor)
                if q is not None:
                    qs.append((q[0], b, q))
            qs.sort(key=lambda x: -x[0])
            best = None
            for _, b, q in qs[:3]:
                res = confirm({b: np.zeros(3)}, pos, use_floor, q)
                if res is None:
                    failed.add(b)
                    continue
                if best is None or res[1] - 3 * res[2] > best[0]:
                    best = (res[1] - 3 * res[2], b, q, res)
            if best is None:
                if not qs:
                    return pos
                continue                          # ilk 3 başarısız: başarısızlar dışarıda tekrar dene
            _, b, q, res = best
            record({b: np.zeros(3)}, pos, q, res, b if seed == root else f"{b} (alt montaj: {seed})")
            pos[b] = res[0]
            bb = parts[b].lite.bounds + pos[b]
            near = {m for m, P in pos.items()
                    if not (np.any(parts[m].lite.bounds[0] + P - 2 > bb[1]) or np.any(bb[0] - 2 > parts[m].lite.bounds[1] + P))}
            pos = bundle_adjust(pos, seed, parts, fields, sub, sweeps=1, alt=alt, only=near, frozen=frozen)
            failed.clear()                        # yeni parça yeni temas sağlar: hepsi yeniden denenebilir

    def build_comps(rest):
        """Kalan parçalardan alt montajlar (ör. ayrı bir figür). İç ekler her zaman güçlü olmalı: ilk kurulan
        alt montaj zayıf bir temasla başka bir grubun parçasını kapmasın (Radahn: Base_Part_6 → Miquella)."""
        saved, strict[0] = strict[0], True
        comps = []
        while rest:
            seed = max(rest, key=lambda n: parts[n].volume)
            comp = grow(seed, rest, use_floor=False)
            comps.append(comp)
            rest = [n for n in rest if n not in comp]
        strict[0] = saved
        if not saved:
            # zayıf aşama: tek kalan küçük parçalar (Miquella'nın kolları) önce kendi gruplarına zayıf temasla
            # eklenebilsin; yoksa tek seçenek ana montaja kenetlenmek olur ve yanlış yere oturabilirler
            for comp in sorted((c for c in comps if len(c) > 1), key=lambda c: -sum(parts[m].volume for m in c)):
                pool = [m for c in comps if len(c) == 1 for m in c]
                if not pool:
                    break
                seed = max(comp, key=lambda m: parts[m].volume)
                grown = grow(seed, pool, use_floor=False, start=comp)
                if len(grown) > len(comp):
                    comp.update(grown)
                    comps = [c for c in comps if not (len(c) == 1 and next(iter(c)) in grown)]
        return comps

    def label_of(comp):
        if len(comp) <= 3:
            return "+".join(sorted(comp))
        return f"{max(comp, key=lambda m: parts[m].volume)} grubu ({len(comp)} parça)"

    def merge_comps(comps):
        """Alt montajları kendi aralarında birleştir (ör. iki kaide yarısı); biri birleşirse True."""
        order = sorted(comps, key=lambda c: -sum(parts[m].volume for m in c))
        for i, big in enumerate(order):
            for small in order[i + 1:]:
                if len(big) == 1 and len(small) == 1:
                    continue                      # tek parçaları zaten alt montaj kurulurken denedik
                T = attach(small, big, False, f"{label_of(small)} → {label_of(big)}")
                if T is not None:
                    big.update({m: P + T for m, P in small.items()})
                    comps[:] = [x for x in comps if x is not small]
                    return True
        return False

    def dock(main, comps, use_floor=True):
        """3B FFT kenetlenme: ek yüzeyi eşleşmesi olmayan (organik temaslı: kuma gömülü taş, kaideye oturan
        kaya) parça ya da alt montajları `main`e (ana montaj ya da bir alt montaj) oturt.
        Dönüş: (skor, grup, T, c, p, k) veya None."""
        key_main = frozenset(main)
        cands = [c for c in sorted(comps, key=lambda c: -sum(parts[m].volume for m in c))
                 if c is not main and (frozenset(c), key_main, strict[0]) not in dock_failed][:6]
        if not cands:
            return None
        ext = lambda c: float(np.max(np.max([parts[m].lite.bounds[1] + P for m, P in c.items()], axis=0)   # noqa: E731
                                     - np.min([parts[m].lite.bounds[0] + P for m, P in c.items()], axis=0)))
        best = None
        grid, grid_pad = None, None
        # ızgara grubun boyutuna göre (en büyük gruba göre tek ızgara küçük parçalar için çok kaba kalıyor)
        for pad, comp in sorted(((float(np.ceil(ext(c) / 20.0) * 20.0), c) for c in cands), key=lambda x: x[0]):
            if pad != grid_pad:
                grid, grid_pad = assembly_grid(main, parts, pad, res=1.5), pad
            dock_failed.add((frozenset(comp), key_main, strict[0]))
            # ek yüzeyi kanıtı yok: grup için toplam yüzeyle ölçeklenen daha yüksek temas iste (büyük bir grup,
            # yüzeyi en çok saran yanlış bir yere de "oturabilir")
            area = max(parts[m].area for m in comp)
            req = need(area) if len(comp) == 1 else max(need(area), 0.01 * sum(parts[m].area for m in comp))
            for T0 in fft_dock(comp, grid, parts, fields, sub, peaks=4):
                T, c, p, k = refine_group(comp, T0, main)
                if c >= req and p <= 0.1 * c + 2.0 and group_floor_ok(comp, T, use_floor):
                    if best is None or c - 3 * p > best[0]:
                        best = (c - 3 * p, comp, T, c, p, k)
        return best

    dock_failed: set = set()     # (grup, ana montaj üyeleri, aşama): aynı durumda kenetlenmeyi tekrar deneme

    def hint_pose(b, a, base):
        """Kullanıcı bağlantısı: b, a'ya bağlanır. Aday konumlar sadece a'dan gelir (ek yüzeyi eşleşmeleri + a'ya
        karşı kenetlenme) ve a'ya karşı iyileştirilir; sonra tüm montaja karşı gömülme kontrol edilir.
        Dönüş: kabul edilebilir adaylar, iyiden kötüye [(skor, T, a ile temas, toplam temas, iç içe, değdiği)]."""
        anchor = {a: base[a]}
        cands = [base[a] + t for _, t in cand.get((a, b), [])]
        pad = float(np.max(parts[b].lite.extents))
        others = {n: P for n, P in base.items() if n != a}
        # kenetlenme çapaya temas arar ama komşulara gömülen yerleri baştan cezalandırır: yoksa en iyi tepeler çapanın
        # komşusuna giren yerlerde toplanıp hepsi sonradan eleniyordu (ayrı koordinatlı kaide: figürün ayağı kaide
        # parçasının yan duvarına, yandaki kaide parçasının içine "oturuyordu")
        reach = (parts[a].lite.bounds[0] + base[a] - pad, parts[a].lite.bounds[1] + base[a] + pad)
        avoid = {n: P for n, P in others.items() if np.all(parts[n].lite.bounds[0] + P <= reach[1])
                 and np.all(parts[n].lite.bounds[1] + P >= reach[0])}
        grid = assembly_grid(anchor, parts, pad, res=1.0, avoid=avoid)
        cands += fft_dock(b, grid, parts, fields, sub, peaks=6, min_dir=hint_min)
        # "başka konum" kullanıcı için anlamlı olmalı: aynı yüzeyde birkaç mm kaymış aday aynı yer sayılır
        distinct = max(4.0, 0.3 * float(np.max(parts[b].lite.extents)))
        out = []
        for T0 in cands:
            T, ca, pa, _ = _refine_global(b, T0, anchor, parts, fields, *sub[b])
            co, po, k = group_score({b: np.zeros(3)}, T, others) if others else (0.0, 0.0, 0)
            # kullanıcı bu ekin varlığını söyledi: çapaya pim/yuva toleransı kadar gömülme olağan (küçük parçada
            # oransal olarak büyük: Casca'nın eli bileğe 82 mm² temas / 10 mm² gömülme); diğer parçalara katı kural
            if ca < 10.0 or pa > 0.25 * ca + 5.0 or po > 0.1 * co + 2.0:
                continue
            if hint_min is not None and float(T @ np.asarray(hint_min[0], float)) < hint_min[1] - 0.5:
                continue
            out.append((ca - pa - 3 * po, T, ca, ca + co, pa + po, k + 1))
        uniq = []
        for o in sorted(out, key=lambda x: -x[0]):           # en iyiden başlayarak tekilleştir
            if all(np.linalg.norm(o[1] - u[1]) > distinct for u in uniq):
                uniq.append(o)
        return uniq[:4]

    def joint_assign(movers, opts):
        """Aynı montaja bağlanan kullanıcı bağlantılarını birlikte çöz: her parça için en iyi birkaç aday arasından
        birbirine gömülmeyen, en çok parçayı yerleştiren ve toplam puanı en yüksek atama (Casca: iki el de iki
        bileğe 'oturabiliyor'; sırayla yerleştirince ilk el diğerinin bileğini kapıyordu)."""
        from itertools import product

        memo: dict = {}

        def clash(m1, i1, m2, i2):
            k = (m1, i1, m2, i2)
            if k not in memo:
                T1, T2 = opts[m1][i1][1], opts[m2][i2][1]
                _, p12 = _score(fields[m1], sub[m2][0], sub[m2][1], T2 - T1)
                _, p21 = _score(fields[m2], sub[m1][0], sub[m1][1], T1 - T2)
                memo[k] = max(p12, p21) > 2.0
            return memo[k]
        if np.prod([len(opts[m]) + 1 for m in movers]) > 4000:     # çok parça: sırayla (her biri en iyisini alır)
            chosen, out = [], []
            for m in movers:
                for i in range(len(opts[m])):
                    if not any(clash(m2, i2, m, i) for m2, i2 in chosen):
                        chosen.append((m, i))
                        out.append((m, opts[m][i]))
                        break
            return out
        best, best_key = None, None
        for combo in product(*[list(range(len(opts[m]))) + [None] for m in movers]):
            idx = [(m, i) for m, i in zip(movers, combo) if i is not None]
            if any(clash(m1, i1, m2, i2) for k, (m1, i1) in enumerate(idx) for m2, i2 in idx[k + 1:]):
                continue
            chosen = [(m, opts[m][i]) for m, i in idx]
            key = (len(chosen), sum(o[0] for _, o in chosen))
            if best_key is None or key > best_key:
                best, best_key = chosen, key
        return best or []

    def place_hints(main, comps):
        """Çapası yerleşmiş (ana montajda ya da bir alt montajda) kullanıcı bağlantılarını uygula; aynı montaja
        gidenler birlikte çözülür. Ana montaja parça eklendiyse True."""
        changed = False
        for target in [main] + list(comps):
            pending = []
            for a, b in hints:
                if b in main or any(b in c for c in comps) or a not in target:
                    continue
                key = (a, b, frozenset(target))
                if key not in hint_tried:
                    hint_tried.add(key)
                    pending.append((a, b))
            if not pending:
                continue
            full = {b: hint_pose(b, a, target) for a, b in pending}
            opts = dict(full)
            for b, o in full.items():
                if rank.get(b) and o:                     # kullanıcının istediği aday (döngüsel)
                    opts[b] = [o[rank[b] % len(o)]]
            anchor_of = {b: a for a, b in pending}
            for b, chosen in joint_assign([b for _, b in pending], opts):
                _, T, ca, c, p, k = chosen
                a = anchor_of[b]
                target[b] = T
                user_placed.add(b)
                hint_cand[b] = f"{next(i for i, o in enumerate(full[b]) if o is chosen) + 1}/{len(full[b])}"
                matches.append(Match(a, b, T - target[a], 0.0, ca, p, ca - 3 * p, True, 0.0))
                log(f"  + {b} ← {a} (kullanıcı bağlantısı, temas {ca:.0f} mm²)")
                changed |= target is main
        return changed

    # 1) Ana montaj (kök: en büyük parça, zemin kuralıyla). Kullanıcının bağladığı parçalar otomatik yerleşmez.
    main = grow(root, auto, use_floor=True, start=fixed or None)
    comps: list[dict] = []
    docked, rebuild = 0, True
    for _ in range(2 * len(names)):
        # 2) Kalanlardan alt montajlar. Ana montaj her büyüdüğünde yeniden kurulur: bir alt montaja zayıf
        #    temasla yanlış yapışmış parça (Radahn: Base_Part_6 → Miquella grubu) önce ana montaja girebilsin.
        if rebuild:
            comps = build_comps([n for n in auto if n not in main])
            rebuild = False
        # 3) Kullanıcı bağlantıları, sonra alt montajları rijit gövde olarak ana montaja ekle (büyükten küçüğe)
        progress = place_hints(main, comps)
        for comp in sorted((c for c in comps if len(c) > 1 and not progress),
                           key=lambda c: -sum(parts[m].volume for m in c)):
            T = attach(comp, main, True, label_of(comp))
            if T is not None:
                main.update({m: P + T for m, P in comp.items()})
                progress = True
                break
        if not progress and merge_comps(comps):
            continue                              # 3b) iki alt montaj birleşti: ana montaja eklemeyi yeniden dene
        if not progress:
            # 4) Hâlâ yeri olmayan parça/alt montajlar: 3B FFT kenetlenme (kuma gömülü taş, kol yenindeki el,
            #    kaideye organik yüzeyle oturan kaya)
            best, target = (dock(main, comps), main) if docked < 12 else (None, None)
            if not strict[0] and docked < 12:
                # zayıf aşama: tek parça ana montaja değil bir alt montaja daha iyi oturuyor olabilir (Radahn:
                # Miquella'nın kolları — ekleri 20 mm²'den küçük, sadece kenetlenmeyle bulunuyor)
                singles = [c for c in comps if len(c) == 1]
                for tgt in (c for c in comps if len(c) > 1):
                    b2 = dock(tgt, singles, use_floor=False) if singles else None
                    if b2 is not None and (best is None or b2[0] > best[0]):
                        best, target = b2, tgt
            if best is not None:
                _, comp, T, c, p, k = best
                docked += 1
                b = max(comp, key=lambda m: parts[m].volume)
                matches.append(Match("(kenetlenme)", b, T + comp[b], 0.0, c, p, c - 3 * p, True, 0.0))
                where = "" if target is main else f" (alt montaj: {max(target, key=lambda m: parts[m].volume)})"
                log(f"  + {label_of(comp)}{where} ← kenetlenme (temas {c:.0f} mm², {k} parçaya değiyor)")
                note_choice(comp, T, target)
                target.update({m: P + T for m, P in comp.items()})
                if target is not main:
                    comps = [x for x in comps if x is not comp]
                    continue                      # alt montaj büyüdü: ana montaja eklemeyi yeniden dene
                progress = True
        switched = False
        if not progress:
            if not strict[0]:
                break
            strict[0], switched = False, True     # güçlü ekler bitti: zayıf eklere izin ver, baştan dene
            log("  (zayıf temaslı ekler deneniyor)")
        else:
            main = bundle_adjust(main, root, parts, fields, sub, sweeps=1, alt=alt, frozen=frozen)
        # yeni parçalar yeni ek yüzeyleri açar: ana montajı kalan parçalarla büyütmeye devam et. Zayıf aşamada
        # güçlü eklerle kurulmuş bir alt montajdan tek parça koparılmaz (Radahn: Miquella gövdesi kafası/saçıyla
        # 800+ mm² bağlıyken kılıca 78 mm² ile kapılıyordu); alt montajlar sadece bütün olarak eklenir.
        grouped = set() if strict[0] else {m for c in comps if len(c) > 1 for m in c}
        before = set(main)
        main = grow(root, [n for n in auto if n not in main and n not in grouped], use_floor=True, start=main)
        if strict[0] or switched:
            # güçlü aşamada ana montaja geçen parça bir alt montajdan kopmuş olabilir: alt montajları yeniden kur
            rebuild = progress or switched or set(main) != before
        else:
            # zayıf aşamada alt montajlar bölünmez: ana montaja girenleri listeden düşmek yeter
            comps = [c for c in comps if not set(c) & set(main)]
    # Kök (en büyük parça) figürün asıl gövdesinde olmayabilir: birleştirilemeyen daha büyük bir alt montaj kaldıysa
    # (1/6 Casca: kök kaide yarısı + ona zayıf bağlanan bacak ↔ gövde + 6 parça) çağıran o grubun tohumunu kök
    # yaparak yeniden dener (dict sırası: alt montajın ilk anahtarı tohumudur).
    def vol(c):
        return sum(parts[m].volume for m in c)
    big = max((c for c in comps if len(c) > 1 and not set(c) & set(main)), key=vol, default=None)
    alt_root = next(iter(big)) if big is not None and vol(big) > vol(main) else None
    main = bundle_adjust(main, root, parts, fields, sub, sweeps=2, alt=alt, frozen=frozen)
    left_groups = [sorted(c) for c in comps if len(c) > 1 and not set(c) <= set(main)]
    if left_groups:
        log("  Eklenemeyen alt montajlar: " + "; ".join(", ".join(g) for g in left_groups))
    placed = {n: n in main for n in parts}
    offsets = {n: main.get(n, np.zeros(3)) for n in parts}
    groups = [dict(c) for c in comps if len(c) > 1 and not set(c) & set(main)]
    # parça başına bağlantı: son kayıt geçerli (alt montajlar yeniden kurulunca eşleşmeler tekrar kaydedilir)
    links = {root: dict(to=None, contact=0, kind="kök")}
    for m in matches:
        if m.b in main and m.b != root:
            kind = ("kullanıcı" if m.b in user_placed else "kenetlenme" if m.a == "(kenetlenme)" else "ek yüzeyi")
            links[m.b] = dict(to=None if m.a == "(kenetlenme)" else m.a, contact=round(float(m.contact)), kind=kind)
            if m.b in hint_cand:
                links[m.b]["cand"] = hint_cand[m.b]
    failed = [(a, b) for a, b in hints if b not in main]
    for a, b in failed:
        log(f"  ! {b} → {a}: kullanıcı bağlantısı uygulanamadı ("
            + ("çapa yerleşmedi" if a not in main else "temaslı ve gömülmeyen bir konum bulunamadı") + ")")
    return AutoResult(offsets, placed, matches, root, groups, prefer & set(main),
                      {m: r for m, r in requires.items() if m in main}, links, failed, alt_root)


def auto_assemble_pairs(parts: dict, fields: dict, samples: dict, names: list[str], log=lambda m: None,
                        groups: dict | None = None) -> AutoResult:
    rng = np.random.default_rng(0)
    fac = {n: facets_of(n, parts[n].lite) for n in names}
    log(f"Otomatik montaj: {sum(len(v) for v in fac.values())} ek yüzeyi adayı, {len(names)} parça.")
    matches = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if not fac[a] or not fac[b]:
                continue
            A, B = (a, b) if parts[a].area >= parts[b].area else (b, a)
            m = pair_match(A, B, fac, fields, samples, rng)
            if m is not None:
                matches.append(m)
    log(f"Otomatik montaj: {len(matches)} güvenilir ek eşleşmesi.")
    G = nx.Graph()
    G.add_nodes_from(names)
    for m in matches:
        if G.has_edge(m.a, m.b) and G[m.a][m.b]["m"].score >= m.score:
            continue
        G.add_edge(m.a, m.b, weight=m.score, m=m)
    root = max(names, key=lambda n: parts[n].volume)
    comp = nx.node_connected_component(G, root)
    T = nx.maximum_spanning_tree(G.subgraph(comp))
    pos = {root: np.zeros(3)}
    for u, v in nx.bfs_edges(T, root):
        m = T[u][v]["m"]
        pos[v] = pos[u] + m.t if m.a == u else pos[u] - m.t
    # 3B ağırlıklı en küçük kareler + aykırı değer reddi
    edges = [d["m"] for _, _, d in G.subgraph(comp).edges(data=True)]
    idx = {n: i for i, n in enumerate(sorted(comp - {root}))}
    for _ in range(3):
        inl = [m for m in edges if np.linalg.norm(pos[m.b] - pos[m.a] - m.t) < 1.0]
        if not idx or not inl:
            break
        A = np.zeros((len(inl), len(idx)))
        Y = np.zeros((len(inl), 3))
        w = np.sqrt([max(m.score, 1.0) for m in inl])
        for r, m in enumerate(inl):
            if m.b in idx:
                A[r, idx[m.b]] += 1
            if m.a in idx:
                A[r, idx[m.a]] -= 1
            Y[r] = m.t
        sol = np.linalg.lstsq(A * w[:, None], Y * w[:, None], rcond=None)[0]
        pos = {root: np.zeros(3), **{n: sol[i] for n, i in idx.items()}}
    for m in edges:
        m.residual = float(np.linalg.norm(pos[m.b] - pos[m.a] - m.t))
        m.used = m.residual < 1.0
    placed = {n: n in pos for n in names}
    offsets = {n: pos.get(n, np.zeros(3)) for n in names}
    return AutoResult(offsets, placed, matches, root)
