"""Drenaj/havalandırma deliği yerleştirme + boolean.

Delikler sadece güvenli gizli bölgeye açılır (montajdan sonra görünmeyen yerler). Görünür yüzeye asla otomatik
delik açılmaz (--allow-visible-holes hariç). Her kavitenin en az 2 deliği olur; biri baskı yönünde kavitenin en
alçak bölgesine (vakum/reçine hapsini önler), diğeri mümkün olduğunca uzağa.
"""
from __future__ import annotations

from dataclasses import dataclass

import manifold3d as mf
import numpy as np
import open3d as o3d
import trimesh
from scipy.spatial import cKDTree

from .geom import DistanceField


@dataclass
class HoleCandidates:
    pts: np.ndarray        # (N,3) yüzey noktası
    nrm: np.ndarray        # (N,3) dış normal
    cavity: np.ndarray     # (N,) kavite indeksi
    visible: np.ndarray    # (N,) bool: görünür yüzeyden (sadece --allow-visible-holes ile)
    inner: np.ndarray | None = None   # (N,3) deliğin kaviteye açıldığı nokta


# ---------------------------------------------------------------- çanak (havza) analizi
#
# Alttan baskılı (MSLA) yazıcıda parça plate'ten aşağı sarkar; slicer z'si plate'ten uzaklaşır. Kavitenin
# z'ye göre her yerel minimumu, o bölge açık bir delikle hava/reçine alana kadar FEP'e karşı bir vantuz
# (suction cup) oluşturur. Baskı bitince parça baş aşağı durur: kavitenin z'ye göre yerel maksimumları
# reçinenin hapsolduğu ceplerdir. Anlamlı çanaklar topolojik "persistence" ile bulunur: bir çanak, daha derin
# bir çanakla birleştiği eyer yüksekliği ile kendi dibi arasındaki fark kadar "derindir".

@dataclass
class Basin:
    vertex: int            # çanağın dibi (kavite köşesi)
    z: float               # dip yüksekliği (slicer z'si; maksimumlar için -z)
    depth: float           # persistence (mm); global çanak için inf
    owner: np.ndarray      # (V,) bool: dibin çevresindeki bölge (delik bu bölgede olmalı)


def _adjacency(mesh: trimesh.Trimesh):
    from scipy import sparse

    e = mesh.edges_unique
    n = len(mesh.vertices)
    A = sparse.coo_matrix((np.ones(len(e) * 2), (np.r_[e[:, 0], e[:, 1]], np.r_[e[:, 1], e[:, 0]])), shape=(n, n))
    A = A.tocsr()
    return A.indptr, A.indices


def basins(mesh: trimesh.Trimesh, z: np.ndarray, min_depth: float = 2.0, reach: float = 4.0, adj=None,
           xy: np.ndarray | None = None, min_area: float = 25.0) -> list[Basin]:
    """z alanının anlamlı yerel minimum çanakları (birleşme-bul ile persistence).
    xy verilirse, dibe yakın bölgenin yatay kesiti (bbox alanı) min_area'dan küçük çanaklar sayılmaz: vakum
    kuvveti kesit alanıyla orantılıdır, iğne ucu kadar cepler önemsizdir."""
    indptr, ind = adj if adj is not None else _adjacency(mesh)
    n = len(z)
    order = np.argsort(z, kind="stable")
    parent = np.full(n, -1, dtype=np.int64)
    birth_v: dict[int, int] = {}
    owner_at = np.full(n, -1, dtype=np.int64)
    depth: dict[int, float] = {}

    def find(x):
        r = x
        while parent[r] != r:
            r = parent[r]
        while parent[x] != r:
            parent[x], x = r, parent[x]
        return r

    for v in order:
        roots = set()
        for u in ind[indptr[v]:indptr[v + 1]]:
            if parent[u] >= 0:
                roots.add(find(u))
        if not roots:
            parent[v] = v
            birth_v[v] = v
            owner_at[v] = v
            continue
        old = min(roots, key=lambda r: z[birth_v[r]])
        for r in roots:
            if r != old:
                depth[r] = float(z[v] - z[birth_v[r]])
                parent[r] = old
        parent[v] = old
        owner_at[v] = old
    out = []
    for r, bv in birth_v.items():
        d = depth.get(r, np.inf)
        if d < min_depth:
            continue
        # bölge: bu çanağa (o hâliyle) atanmış ve dibe `reach` mm'den yakın yükseklikteki köşeler
        own = (owner_at == r) & (z <= z[bv] + min(reach, d if np.isfinite(d) else reach))
        if xy is not None and np.isfinite(d):
            pts = xy[(owner_at == r) & (z <= z[bv] + min(d, 2 * reach))]
            if len(pts) < 3 or np.prod(np.ptp(pts, axis=0)) < min_area:
                continue
        out.append(Basin(int(bv), float(z[bv]), d, own))
    out.sort(key=lambda b: b.z)
    return out


def cavity_analysis_mesh(cav, faces: int = 6000) -> trimesh.Trimesh:
    from .io import simplify

    m = getattr(cav, "_lite", None)
    if m is None:
        m = simplify(cav.mesh, faces) if len(cav.mesh.faces) > faces else cav.mesh
        cav._lite = m
        cav._adj = _adjacency(m)
    return m


def vent_plan(cav, R: np.ndarray, min_depth: float = 2.0, pockets: bool = True):
    """Baskı yönünde (R) kavitenin vantuz çanakları (yerel min) ve reçine cepleri (yerel max)."""
    m = cavity_analysis_mesh(cav)
    z = m.vertices @ R[2, :3]
    xy = np.column_stack([m.vertices @ R[0, :3], m.vertices @ R[1, :3]])
    cups = basins(m, z, min_depth, adj=cav._adj, xy=xy)
    pk = basins(m, -z, min_depth, adj=cav._adj, xy=xy) if pockets else []
    return m, cups, pk


def _basin_hits(c: HoleCandidates, idx: np.ndarray, m: trimesh.Trimesh, basin: Basin) -> np.ndarray:
    """idx adaylarından deliği bu çanağın bölgesine açılanlar."""
    if c.inner is None or not len(idx):
        return np.zeros(0, int)
    from scipy.spatial import cKDTree

    tree = getattr(m, "_kd", None)
    if tree is None:
        tree = cKDTree(m.vertices)
        m._kd = tree
    d, vi = tree.query(c.inner[idx])
    ok = basin.owner[vi] & (d < 3.0)
    return idx[ok]


def _rays(scene, o, d):
    r = np.hstack([o, d]).astype(np.float32)
    return scene.cast_rays(o3d.core.Tensor(r))["t_hit"].numpy()


def _circle(n: np.ndarray, r: float, k: int = 8) -> np.ndarray:
    """n'ye dik çember üzerinde k nokta ofseti."""
    a = np.array([1.0, 0, 0]) if abs(n[0]) < 0.9 else np.array([0, 1.0, 0])
    u = np.cross(n, a)
    u /= np.linalg.norm(u)
    v = np.cross(n, u)
    t = np.linspace(0, 2 * np.pi, k, endpoint=False)
    return np.outer(np.cos(t), u) * r + np.outer(np.sin(t), v) * r


def find_candidates(outer: trimesh.Trimesh, cavities, pts: np.ndarray, nrm: np.ndarray, wall: float,
                    diameter: float, visible: np.ndarray | None = None, max_n: int = 6000) -> HoleCandidates:
    """Aday noktalardan, arkasında (normalin tersinde, duvar + 0.5 mm içinde) bir kavite olanları seç.
    Delik çevresi de kontrol edilir: çember üzerindeki 8 ışın da dış yüzeyden çıkmadan kaviteye ulaşmalı."""
    if len(pts) > max_n:
        sel = np.random.default_rng(0).choice(len(pts), max_n, replace=False)
        pts, nrm = pts[sel], nrm[sel]
        visible = visible[sel] if visible is not None else None
    vis = visible if visible is not None else np.zeros(len(pts), bool)
    f_out = DistanceField(outer)
    # noktaları tam çözünürlüklü dış yüzeye oturt
    cav_fields = [DistanceField(c.mesh) for c in cavities]
    reach = wall + 0.6
    best_cav = np.full(len(pts), -1)
    best_t = np.full(len(pts), np.inf)
    o = pts + nrm * 0.3          # yüzeyin hemen dışından başla
    d = -nrm
    t_out = _rays(f_out.scene, o + d * 0.6, d)    # dış yüzeyi geçtikten sonra tekrar dışa çıkış
    for ci, fc in enumerate(cav_fields):
        t = _rays(fc.scene, o, d)
        ok = (t < reach + 0.3) & (t < best_t) & (t < t_out + 0.6)
        best_cav[ok] = ci
        best_t[ok] = t[ok]
    keep = best_cav >= 0
    inner = o + d * np.where(np.isfinite(best_t), best_t, 0.0)[:, None]
    # çember kontrolü (delik kenarı da kaviteye açılmalı, başka yerden dışarı çıkmamalı)
    r = diameter / 2
    K = 8
    for ci, fc in enumerate(cav_fields):
        idx = np.nonzero(keep & (best_cav == ci))[0]
        if not len(idx):
            continue
        off = np.concatenate([_circle(nrm[i], r, K) for i in idx])
        oo = np.repeat(o[idx], K, axis=0) + off
        dd = np.repeat(d[idx], K, axis=0)
        tc = _rays(fc.scene, oo, dd)
        to = _rays(f_out.scene, oo + dd * 0.6, dd)
        ts = _rays(f_out.scene, oo, dd)          # ağız kenarı yüzeyde mi (kenardan/kademeden taşmıyor mu)
        good = ((tc < reach + 0.8) & (tc < to + 0.6) & (ts < 0.3 + 1.0)).reshape(len(idx), K).all(1)
        keep[idx[~good]] = False
    return HoleCandidates(pts[keep], nrm[keep], best_cav[keep], vis[keep], inner[keep])


def hole_diameters(s) -> list[float]:
    """Denenecek delik çapları (büyükten küçüğe). --hole verildiyse sabit çap (+ yer yoksa hole_min yedeği)."""
    if not s.hole_auto:
        return sorted({float(s.hole), float(min(s.hole, s.hole_min))}, reverse=True)
    ds = {round(float(x), 2) for x in np.arange(s.hole_min, s.hole_max + 1e-6, 1.0)}
    ds |= {float(s.hole), float(s.hole_max)}
    return sorted((d for d in ds if s.hole_min <= d <= s.hole_max), reverse=True)


def preferred_diameter(vol_ml: float, s) -> float:
    """Kavite büyüdükçe daha geniş delik: < 10 ml → hole, < 40 ml → hole+1, üstü → hole+2 (hole_max ile sınırlı)."""
    if not s.hole_auto:
        return float(s.hole)
    d = s.hole if vol_ml < 10 else (s.hole + 1.0 if vol_ml < 40 else s.hole + 2.0)
    return float(np.clip(d, s.hole_min, s.hole_max))


def desired_count(cav, s) -> int:
    """Kavite başına hedef delik sayısı: hacim (her ml_per_hole ml için +1) ve uzunluk (her mm_per_hole mm için +1)."""
    vol_ml = cav.volume / 1000
    ext = float(np.max(cav.mesh.extents))
    n = max(2, int(np.ceil(vol_ml / s.ml_per_hole)) + 1, int(np.ceil(ext / s.mm_per_hole)) + 1)
    if s.min_holes:
        n = max(n, int(s.min_holes))
    return int(min(n, max(s.max_holes, s.min_holes or 0)))


def _pick(c: HoleCandidates, ci: int, zc: np.ndarray, d: float, want: int, vent=None, max_total: int = 12):
    """Bir kavite için delik indeksleri. Dönüş: (indeksler, {çanak/cep istatistikleri}).

    1) Her vantuz çanağının (baskı yönünde yerel minimum) dibine en alçak aday — FEP'e karşı vakumu önler.
    2) Reçine ceplerine (yerel maksimum; parça baş aşağı sarkarken reçine orada birikir) en yüksek aday.
    3) Hedef sayıya kadar en uzak noktalar (yüksek tarafa hafif tercih). Delikler arası ≥ 3 × çap."""
    stats = dict(cups=0, cups_vented=0, pockets=0, pockets_vented=0)
    m = np.nonzero(c.cavity == ci)[0]
    if len(m) == 0:
        return [], stats
    hid = m[~c.visible[m]]          # görünür adaylar sadece hiç gizli aday yoksa
    if len(hid):
        m = hid
    min_sep = 3.0 * d
    chosen: list[int] = []

    def far_enough(idx):
        if not chosen or not len(idx):
            return idx
        dd = np.min(np.linalg.norm(c.pts[idx][:, None] - c.pts[chosen][None], axis=2), axis=1)
        return idx[dd >= min_sep]

    if vent is not None:
        mesh, cups, pockets = vent
        stats["cups"], stats["pockets"] = len(cups), len(pockets)
        for b in cups:
            hits = far_enough(_basin_hits(c, m, mesh, b))
            if len(hits):
                chosen.append(int(hits[np.argmin(zc[hits])]))
                stats["cups_vented"] += 1
            elif any(_basin_hits(c, np.array(chosen, int), mesh, b).tolist()):
                stats["cups_vented"] += 1          # zaten seçilmiş bir delik bu çanağa açılıyor
        for b in pockets:
            if len(chosen) >= max(want, stats["cups_vented"]) + 1:
                break
            if len(_basin_hits(c, np.array(chosen, int), mesh, b)):
                stats["pockets_vented"] += 1
                continue
            hits = far_enough(_basin_hits(c, m, mesh, b))
            if len(hits):
                chosen.append(int(hits[np.argmax(zc[hits])]))
                stats["pockets_vented"] += 1
    if not chosen:
        chosen = [int(m[np.argmin(zc[m])])]
    P = c.pts[m]
    dist = np.min(np.linalg.norm(P[:, None] - c.pts[chosen][None], axis=2), axis=1)
    zfirst = zc[chosen[0]]
    while len(chosen) < min(want, max_total):
        score = dist + 0.3 * (zc[m] - zfirst)
        score[dist < min_sep] = -np.inf
        j = int(np.argmax(score))
        if not np.isfinite(score[j]):
            break
        chosen.append(int(m[j]))
        dist = np.minimum(dist, np.linalg.norm(P - c.pts[m[j]], axis=1))
    return chosen[:max_total], stats


def choose_holes(cand_by_d: dict[float, HoleCandidates], cavities, R: np.ndarray, s):
    """Yönlendirilmiş çerçevede (R: montaj→baskı, z yukarı) kavite başına delikleri seç.

    Her kavite için tercih edilen çapa kadar olan çaplar denenir; toplam açıklık alanını (adet × çap²) en çok
    büyüten çap seçilir (eşitlikte büyük çap). Dönüş: ([(nokta, normal, kavite, çap)], eksik kaviteler,
    [(kavite, adet, hedef, çap)])."""
    holes, missing, plan = [], [], []
    for ci, cav in enumerate(cavities):
        pref = preferred_diameter(cav.volume / 1000, s)
        want = desired_count(cav, s)
        vent = vent_plan(cav, R)
        best = None
        for d, c in cand_by_d.items():
            if d > pref + 1e-6 or not len(c.pts):
                continue
            zc = (c.pts @ R[:3, :3].T)[:, 2]
            sel, st = _pick(c, ci, zc, d, want, vent, max_total=max(s.max_holes, want) + 4)
            if len(sel) < 2:
                continue
            # önce vantuz çanaklarının hepsine delik, sonra reçine cepleri, sonra toplam açıklık alanı
            score = (st["cups_vented"] - st["cups"], st["pockets_vented"], min(len(sel), want) * d * d, d)
            if best is None or score > best[0]:
                best = (score, d, c, sel, st)
        if best is None:
            missing.append(ci)
            continue
        _, d, c, sel, st = best
        holes += [(c.pts[i], c.nrm[i], ci, d) for i in sel]
        plan.append((ci, len(sel), want, d, st))
    return holes, missing, plan


def hole_cylinder(p: np.ndarray, n: np.ndarray, diameter: float, wall: float, segments: int = 24) -> mf.Manifold:
    """Yüzey normalinde silindir: dışarıda 0.5 mm başlar, içeride duvar + 1 mm'ye kadar iner."""
    length = wall + 1.0 + 0.5
    cyl = mf.Manifold.cylinder(length, diameter / 2, diameter / 2, segments)   # z: 0..length
    z = -n / np.linalg.norm(n)
    a = np.array([1.0, 0, 0]) if abs(z[0]) < 0.9 else np.array([0, 1.0, 0])
    x = np.cross(a, z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    start = p + n * 0.5
    M = np.column_stack([x, y, z, start])        # 3x4
    return cyl.transform(M)


def mouth_points(p, n, diameter, k=12):
    return np.vstack([p[None], p + _circle(n, diameter / 2, k)])


def project_to_surface(mesh: trimesh.Trimesh, pts: np.ndarray):
    """Noktaları en yakın yüzey noktasına taşı, o yüzün normalini al."""
    f = DistanceField(mesh)
    ans = f.scene.compute_closest_points(o3d.core.Tensor(np.asarray(pts, np.float32)))
    q = ans["points"].numpy().astype(float)
    tri = ans["primitive_ids"].numpy()
    return q, mesh.face_normals[tri]


def candidate_points_from_map(lite: trimesh.Trimesh, safe: np.ndarray, spacing: float = 1.0):
    """Güvenli yüz merkezlerini yaklaşık `spacing` aralıkla seyrelt."""
    idx = np.nonzero(safe)[0]
    if len(idx) == 0:
        return np.zeros((0, 3)), np.zeros((0, 3))
    c = lite.triangles_center[idx]
    key = np.floor(c / spacing).astype(np.int64)
    _, first = np.unique(key, axis=0, return_index=True)
    idx = idx[first]
    return lite.triangles_center[idx], lite.face_normals[idx]


def separation_ok(holes, diameter) -> bool:
    if len(holes) < 2:
        return True
    P = np.array([h[0] for h in holes])
    d, _ = cKDTree(P).query(P, k=2)
    return bool(np.all(d[:, 1] >= diameter * 1.5))
