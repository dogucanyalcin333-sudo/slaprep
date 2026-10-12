"""Gizli yüzey haritası: her parçanın yüzeyini gizli / görünür / pim olarak etiketler.

Birincil yöntem (montaj biliniyorsa): temas tespiti. Bir yüzey noktası, montajda başka bir parçanın içinde ya da
yüzeyine `contact_tol` mesafedeyse, veya dış normali boyunca 1 mm içinde başka bir parçaya çarpıyorsa gizlidir
(ikinci kural pim ucu ile yuva tabanı arasındaki ~0.4 mm boşluğu yakalar). Kaidenin alt yüzü de gizlidir.

Pimler: her temas için baskın kesim düzlemi bulunur; düzlemden karşı parçaya doğru çıkıntı yapan gizli yüzeyler
pimdir ve delik adayı değildir. Yuva tabanı düzlemin gerisinde kalır, adaydır.

Yedek yöntem (montaj bilinmiyorsa): büyük düzlemsel yüzeyler + en alttaki taban yüzü.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import open3d as o3d
import trimesh
from scipy.spatial import cKDTree

from .geom import DistanceField, boxes_overlap

COVER_RAY = 1.0        # mm: normal yönünde bu mesafede başka parça varsa yüzey örtülü
PIN_HEIGHT = 0.5       # mm: kesim düzleminden bu kadar dışarı çıkan gizli yüzey = pim
BOTTOM_TOL = 1.0       # mm
PIN_ENCLOSE = 2.0      # mm: pimin karşı yüzünden çıkan ışın bu mesafede yuva duvarına çarpmalı (kaçık oturma payı)


@dataclass
class SurfaceMap:
    """lite mesh yüzleri üzerinde etiketler."""
    name: str
    hidden: np.ndarray                 # bool (F,)
    pin: np.ndarray                    # bool (F,)
    bottom: np.ndarray                 # bool (F,): kaide tabanı
    covered_by: np.ndarray             # int (F,): örten parçanın indeksi, yoksa -1
    neighbors: list[str] = field(default_factory=list)
    method: str = "temas"              # temas | düzlemsel (yedek)
    safe: np.ndarray | None = None     # bool (F,): delik merkezi olabilecek yüzler
    safe_radius: float = 0.0
    wall_facing: np.ndarray | None = None   # bool (F,): duvar modunda duvara bakan (yumuşatılmış normal)
    mating: np.ndarray | None = None   # bool (F,): birleşme yüzeyi (komşu parçayla temas; yapıştırınca kapanır)
    allowed: np.ndarray | None = None  # bool (F,): delik açılmasına izin verilen gizli yüzler
    visibility: np.ndarray | None = None   # float (F,): destek izinin ne kadar göze batacağı (visibility_weights)

    @property
    def candidate(self) -> np.ndarray:
        """Delik adayı: izin verilen yüzler (varsayılan: sadece birleşme yüzeyleri), pimler hariç.
        Kaide altı ve duvar arkası gizlidir ama dış yüzeydir; delik orada açık kalır, kapatılması gerekir."""
        base = self.allowed if self.allowed is not None else self.hidden
        return base & ~self.pin

    def hidden_area(self, mesh: trimesh.Trimesh) -> float:
        return float(mesh.area_faces[self.hidden].sum())


def _cast(field: DistanceField, origins: np.ndarray, dirs: np.ndarray) -> np.ndarray:
    rays = np.hstack([origins, dirs]).astype(np.float32)
    return field.scene.cast_rays(o3d.core.Tensor(rays))["t_hit"].numpy()


def _dominant_plane(normals, centers, areas):
    """Alan ağırlıklı baskın normal kümesi ve düzlem. Dönüş: (n, d, kapsanan alan oranı) veya None."""
    nz = int(np.count_nonzero(areas > 0))      # sıfır alanlı (dejenere) üçgenler seçilemez (OUISTCRAM Chimchar)
    if len(normals) < 10 or nz < 10:
        return None
    rng = np.random.default_rng(0)
    cand = normals[rng.choice(len(normals), min(300, nz), replace=False, p=areas / areas.sum())]
    sup = (normals @ cand.T > 0.98) * areas[:, None]
    j = int(np.argmax(sup.sum(0)))
    m = normals @ cand[j] > 0.98
    n = (normals[m] * areas[m, None]).sum(0)
    n /= np.linalg.norm(n)
    d = float(np.median(centers[m] @ n))
    return n, d, float(areas[m].sum() / areas.sum())


def _prism_components(A: trimesh.Trimesh, faces: np.ndarray, n: np.ndarray) -> np.ndarray:
    """Kesim düzleminden çıkıntı yapan yüzlerden pim (prizma) olanları seç.

    Pim: yan duvarları düzleme dik (|n·np| < 0.35), tepesi düzleme paralel (n·np > 0.9); eğimli kesim devamları
    ve kubbe/kase yüzeyleri ara açılı normallere sahiptir, pim sayılmaz."""
    if len(faces) == 0:
        return faces
    sub = np.zeros(len(A.faces), bool)
    sub[faces] = True
    adj = A.face_adjacency
    adj = adj[sub[adj[:, 0]] & sub[adj[:, 1]]]
    import networkx as nx

    G = nx.Graph()
    G.add_nodes_from(faces.tolist())
    G.add_edges_from(adj.tolist())
    out = []
    for comp in nx.connected_components(G):
        f = np.fromiter(comp, int)
        a = A.area_faces[f]
        dots = A.face_normals[f] @ n
        side = a[np.abs(dots) < 0.35].sum()
        top = a[dots > 0.9].sum()
        tot = a.sum()
        if tot > 0 and (side + top) / tot > 0.75 and side > 0.2 * tot:
            out.append(f)
    return np.concatenate(out) if out else np.zeros(0, int)


def _enclosed_pins(A: trimesh.Trimesh, fa: DistanceField, fb: DistanceField, shift: np.ndarray,
                   faces: np.ndarray) -> np.ndarray:
    """Karşı parçaya gömülü çıkıntılar (pim). Yan duvar: içeri atılan ışın parçadan kısa mesafede çıkar ve hemen
    karşı parçaya çarpar. Pimin tepe yüzü, yan duvarların dışbükey zarfı içinde kalan gizli yüzlerdir."""
    if len(faces) < 4:
        return np.zeros(0, int)
    c = A.triangles_center[faces]
    n = A.face_normals[faces]
    t_exit = _cast(fa, c - n * 0.01, -n)
    ok = np.isfinite(t_exit) & (t_exit < 60.0)     # pim/tapa genişliği (gövdeyle birleşen pimler uzun olabilir)
    if not ok.any():
        return np.zeros(0, int)
    e = c[ok] - n[ok] * (t_exit[ok][:, None] + 0.02)
    t_b = _cast(fb, e + shift, -n[ok])
    enc = np.zeros(len(faces), bool)
    enc[np.nonzero(ok)[0][t_b < PIN_ENCLOSE]] = True
    if enc.sum() < 4:
        return np.zeros(0, int)
    side = faces[enc]
    # Pim tepesi: yüzeyin 1 mm altından yanlara atılan ışınlar kısa mesafede (pim yarı genişliği) parçadan çıkar
    # ve hemen karşı parçaya (yuva duvarı) çarpar. Kesim yüzeyinde yan ışınlar gövdede uzağa gider; yuva tabanında
    # parçanın kendi malzemesinde kalır.
    rest = faces[~enc]
    tops = np.zeros(0, int)
    if len(rest):
        K = 8
        cr, nr = A.triangles_center[rest], A.face_normals[rest]
        a = np.where(np.abs(nr[:, :1]) < 0.9, np.array([[1.0, 0, 0]]), np.array([[0, 1.0, 0]]))
        u = np.cross(nr, a)
        with np.errstate(invalid="ignore", divide="ignore"):
            u = np.nan_to_num(u / np.linalg.norm(u, axis=1, keepdims=True))   # dejenere üçgenler
        v = np.cross(nr, u)
        ang = np.linspace(0, 2 * np.pi, K, endpoint=False)
        dirs = (np.cos(ang)[None, :, None] * u[:, None] + np.sin(ang)[None, :, None] * v[:, None]).reshape(-1, 3)
        o = np.repeat(cr - nr * 1.0, K, axis=0)
        te = _cast(fa, o, dirs)
        hit = np.zeros(len(o), bool)
        okx = np.isfinite(te) & (te < 35.0)
        if okx.any():
            e = o[okx] + dirs[okx] * (te[okx][:, None] + 0.02)
            hit[np.nonzero(okx)[0]] = _cast(fb, e + shift, dirs[okx]) < PIN_ENCLOSE
        tops = rest[hit.reshape(-1, K).sum(1) >= K - 1]
    return np.unique(np.concatenate([side, tops]))


def contact_map(names: list[str], parts: dict, fields: dict[str, DistanceField], offsets: dict[str, np.ndarray],
                up: np.ndarray, base_parts: list[str], tol: float, floor: bool = True) -> dict[str, SurfaceMap]:
    """floor=False: model zemine oturmuyor (duvara asılı), en alttaki yüzler gizli sayılmaz."""
    maps = {}
    idx = {n: i for i, n in enumerate(names)}
    # montajın en alt seviyesi (taban yüzü tespiti için)
    lo = min(((parts[n].lite.vertices + offsets[n]) @ up).min() for n in names)
    for a in names:
        A = parts[a].lite
        c = A.triangles_center
        nrm = A.face_normals
        F = len(c)
        hidden = np.zeros(F, bool)
        pin = np.zeros(F, bool)
        covered = np.full(F, -1)
        neigh = []
        for b in names:
            if b == a:
                continue
            ba = A.bounds + offsets[a]
            bb = parts[b].lite.bounds + offsets[b]
            if not boxes_overlap(ba, bb, range(3), margin=COVER_RAY + 0.5):
                continue
            q = c + offsets[a] - offsets[b]               # B'nin koordinatlarına
            sel = np.all((q >= fields[b].bounds[0] - COVER_RAY - 0.5) & (q <= fields[b].bounds[1] + COVER_RAY + 0.5), 1)
            if not sel.any():
                continue
            ii = np.nonzero(sel)[0]
            d = fields[b].signed(q[ii])
            near = d < tol
            far = ~near
            if far.any():
                t = _cast(fields[b], q[ii[far]] + nrm[ii[far]] * 0.01, nrm[ii[far]])
                hit = np.zeros(len(ii), bool)
                hit[far] = t < COVER_RAY
                near |= hit
            hb = ii[near]
            if len(hb) == 0:
                continue
            neigh.append(b)
            hidden[hb] = True
            covered[hb] = idx[b]
            # pim tespiti: bu temasın kesim düzleminden B'ye doğru çıkıntı yapan yüzler
            pl = _dominant_plane(nrm[hb], c[hb], A.area_faces[hb])
            if pl is not None:
                n, dpl, frac = pl
                if frac > 0.3 and A.area_faces[hb].sum() * frac > 15.0:
                    h = c[hb] @ n - dpl
                    pin[_prism_components(A, hb[h > PIN_HEIGHT], n)] = True
            pin[_enclosed_pins(A, fields[a], fields[b], offsets[a] - offsets[b], hb)] = True
        # kaide tabanı: en altta, aşağı bakan yüzler. Kaide parçalarında içbükey/kabartmalı dipler (şişe dibi)
        # de dahil: aşağı bakan ve altında kendi gövdesi olmayan (sadece alttan görülebilen) alt bölge.
        cu = (c + offsets[a]) @ up
        bottom = (nrm @ up < -0.95) & (cu < lo + BOTTOM_TOL) & floor
        if floor and a in base_parts:
            plo, phi = cu.min(), cu.max()
            zone = (nrm @ up < -0.5) & (cu < plo + max(3.0, 0.3 * (phi - plo))) & (plo < lo + BOTTOM_TOL)
            if zone.any():
                zi = np.nonzero(zone)[0]
                t = _cast(fields[a], c[zi] - up * 0.01, np.tile(-up, (len(zi), 1)))
                bottom[zi[~np.isfinite(t)]] = True
        mating = hidden.copy()          # komşu parçayla temas eden yüzler (taban eklenmeden önce)
        hidden |= bottom
        maps[a] = SurfaceMap(a, hidden, pin & ~bottom, bottom, covered, neigh, mating=mating)
    return maps


def planar_regions(A: trimesh.Trimesh, min_area: float, max_angle_deg: float = 3.0, flat_tol: float = 0.15):
    """Komşu yüzleri normal açısı küçükse birleştirerek düz bölgeleri bul (sadeleştirilmiş mesh'te trimesh'in
    katı `facets`'i düzlemleri parçalar). Bölge alanı eşiği geçmeli ve fit düzlemine sapması küçük olmalı."""
    import networkx as nx

    adj = A.face_adjacency
    ok = A.face_adjacency_angles < np.radians(max_angle_deg)
    G = nx.Graph()
    G.add_edges_from(adj[ok].tolist())
    out = []
    for comp in nx.connected_components(G):
        f = np.fromiter(comp, int)
        area = A.area_faces[f].sum()
        if area < min_area:
            continue
        n = (A.face_normals[f] * A.area_faces[f, None]).sum(0)
        n /= np.linalg.norm(n)
        c = A.triangles_center[f]
        dev = c @ n
        if np.percentile(np.abs(dev - np.median(dev)), 95) <= flat_tol:
            out.append(f)
    return out


def planar_fallback_map(names: list[str], parts: dict, up: np.ndarray | None) -> dict[str, SurfaceMap]:
    """Montaj bilinmiyorsa: büyük düzlemsel yüzeyler (organik figürde geniş düz alan ≈ kesim yüzeyi) + taban."""
    maps = {}
    for a in names:
        A = parts[a].lite
        F = len(A.faces)
        hidden = np.zeros(F, bool)
        for fc in planar_regions(A, min_area=max(15.0, 0.002 * A.area)):
            hidden[fc] = True
        mating = hidden.copy()          # büyük düz yüzeyler ≈ kesim (birleşme) yüzeyi tahmini
        bottom = np.zeros(F, bool)
        if up is not None:
            cu = A.triangles_center @ up
            bottom = (A.face_normals @ up < -0.95) & (cu < cu.min() + BOTTOM_TOL)
            hidden |= bottom
        maps[a] = SurfaceMap(a, hidden, np.zeros(F, bool), bottom, np.full(F, -1), [], method="düzlemsel",
                             mating=mating)
    return maps


VIS_FRONT = 0.4        # ön yüz 0.6 + 0.4 = 1.0, yan 0.6, sırt 0.2
VIS_UNDER = 0.65       # alttan bakan yüz: tam aşağı bakan 0.35 katı


def visibility_weights(normals: np.ndarray, hidden: np.ndarray, up: np.ndarray, front: np.ndarray) -> np.ndarray:
    """Yüz başına 'destek izi ne kadar göze batar' ağırlığı (0 … 1), montajlı duruşta figüre bakan biri için:
    birleşme yüzeyi ve kaide altı (gizli) 0; sırt 0.2, yanlar 0.6, ön 1.0; alttan bakan yüzler (pelerin eteğinin altı,
    kol altı) ayrıca azaltılır. Yönlendirme destek gelecek (aşağı bakan) yüzeylerin bu ağırlıklı alanını küçültür:
    destekler önce birleşme yerlerine, sonra alt ve arka kısımlara gelir."""
    nf = normals @ np.asarray(front, float)
    nu = normals @ np.asarray(up, float)
    w = (1.0 - VIS_FRONT) + VIS_FRONT * nf
    w = np.where(nu < 0, w * (1.0 + VIS_UNDER * nu), w)
    w = np.clip(w, 0.0, 1.0)
    w[hidden] = 0.0
    return w


def compute_safe(m: SurfaceMap, mesh: trimesh.Trimesh, radius: float):
    """Gizli bölgeyi kenarından `radius` kadar daralt: delik merkezi buraya düşerse delik kenarı da gizli kalır.
    Öklid mesafesi jeodezik mesafeden küçük olduğu için tutucu (güvenli) bir yaklaşımdır."""
    c = mesh.triangles_center
    cand = m.candidate
    bad = ~cand
    safe = np.zeros(len(c), bool)
    if cand.any():
        if bad.any():
            d, _ = cKDTree(c[bad]).query(c[cand], k=1, distance_upper_bound=radius + 1e-6)
            safe[np.nonzero(cand)[0]] = ~np.isfinite(d) | (d > radius)
        else:
            safe[cand] = True
    m.safe = safe
    m.safe_radius = radius
    return safe


WALL_FACING = 0.7      # cos(45°): duvara bu açıdan daha dik bakan yüzler duvar modunda gizli
FEATURE_DIST = 0.3     # mm: seçili varyantın alternatifinden bu kadar uzak yüzleri = özel detay (anahtar yuvası)


def variant_features(run, names) -> dict[str, np.ndarray]:
    """Seçili varyantın neredeyse aynı alternatifine göre İÇERİ OYULMUŞ yüzleri (Dragon_8_Key'in anahtar yuvası).
    Sadece girintiler: alternatifin hacminin içinde kalan yüzler. Head_V1/V2 gibi farklı tasarımlar arasındaki
    genel farklar (V2'nin boyun kesimi) bu kurala girmez. Bu bölgeler delik adayı olmaz."""
    asm = run.asm
    out = {}
    for g in asm.variant_groups:
        for a, b in (g.conflicts or []):
            for sel, alt in ((a, b), (b, a)):
                if sel not in names or sel in out or alt in asm.selected or not asm.placed.get(alt):
                    continue
                if g.overlap < 0.9:
                    continue
                A = run.parts[sel].lite
                q = A.triangles_center + asm.offsets[sel] - asm.offsets[alt]
                d = run.fields[alt].signed(q)
                feat = d < -FEATURE_DIST                  # alternatifin içine oyulmuş (girinti)
                if feat.any() and feat.mean() < 0.15:     # küçük, yerel fark: gerçek bir detay
                    out[sel] = feat
    return out


def smoothed_normals(mesh: trimesh.Trimesh, radius: float = 3.0) -> np.ndarray:
    """Yüz normallerinin `radius` çevresinde alan ağırlıklı ortalaması. Pürüzlü yüzeylerde (pul, tüy) görünürlük
    yüzeyin genel yönüyle belirlenir; yüz yüz test benekli sonuç verir."""
    from scipy import sparse

    c = mesh.triangles_center
    tree = cKDTree(c)
    M = tree.sparse_distance_matrix(tree, radius, output_type="coo_matrix")
    M = sparse.coo_matrix((np.ones_like(M.data), (M.row, M.col)), shape=M.shape).tocsr()
    M = M + sparse.identity(len(c), format="csr")
    n = M @ (mesh.face_normals * mesh.area_faces[:, None])
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)


def wall_direction(run, names, feats) -> tuple[np.ndarray, str]:
    """Duvar yönü (montaj koordinatında, modelden duvara doğru birim vektör)."""
    s = run.settings
    asm = run.asm
    if s.wall_side:
        k = "xyz".index(s.wall_side.lower().lstrip("+-"))
        v = np.zeros(3)
        v[k] = -1.0 if s.wall_side.startswith("-") else 1.0
        return v, "elle (--wall-side)"
    up = asm.up_axis
    lo = np.min([run.parts[n].bounds[0] + asm.offsets[n] for n in names], axis=0)
    hi = np.max([run.parts[n].bounds[1] + asm.offsets[n] for n in names], axis=0)
    others = [k for k in range(3) if k != up]
    depth = min(others, key=lambda k: hi[k] - lo[k])          # duvara dik eksen: en ince yatay eksen
    # 1) varyant detayları (anahtar yuvası arkadadır): detay yüzlerinin ortalama normali
    tot = 0.0
    for n, f in feats.items():
        A = run.parts[n].lite
        tot += float((A.face_normals[f, depth] * A.area_faces[f]).sum())
    if abs(tot) > 1.0:
        v = np.zeros(3)
        v[depth] = np.sign(tot)
        return v, "anahtar yuvası yönünden"
    # 2) düz arka yüz: o yöne bakan düz alan
    area = {}
    for sgn in (1.0, -1.0):
        a = 0.0
        for n in names:
            A = run.parts[n].lite
            a += float(A.area_faces[A.face_normals[:, depth] * sgn > 0.95].sum())
        area[sgn] = a
    v = np.zeros(3)
    v[depth] = max(area, key=area.get)
    return v, "en düz yüzden tahmin"


def build_maps(run) -> dict[str, SurfaceMap]:
    asm = run.asm
    s = run.settings
    names = [n for n in asm.selected if asm.placed.get(n)]
    if asm.mode != "bilinmiyor" and names:
        floor = not s.wall_mount
        maps = contact_map(names, run.parts, run.fields, asm.offsets, asm.up_vector, asm.base_parts,
                           s.contact_tol, floor)
        # seçilmeyen varyantlar da basılacaksa kendi komşularıyla etiketlenir
        conflicts = {frozenset(c) for g in asm.variant_groups for c in (g.conflicts or [])}
        for v in (n for n in run.parts if n not in asm.selected and asm.placed.get(n)):
            others = [n for n in names if frozenset((n, v)) not in conflicts]
            maps[v] = contact_map(others + [v], run.parts, run.fields, asm.offsets, asm.up_vector,
                                  asm.base_parts, s.contact_tol, floor)[v]
        feats = variant_features(run, names) if s.wall_mount else {}
        for n, f in feats.items():
            maps[n].pin |= f            # askı yuvası gibi detaylara delik açılmaz
        if s.wall_mount:
            back, how = wall_direction(run, names, feats)
            run.wall_back = (back, how)
            for n, m in maps.items():
                L = run.parts[n].lite
                # yumuşatılmış normal pürüze (tüy, pul) tolerans sağlar; yüzün kendi normali de duvar tarafına
                # dönük olmalı ki keskin arka kenardaki yan yüzler gizli sayılmasın (delik kenardan taşmasın)
                facing = (smoothed_normals(L) @ back > WALL_FACING) & (L.face_normals @ back > 0.3)
                m.wall_facing = facing
                m.hidden |= facing
                m.method = "temas+duvar"
    else:
        maps = {}
    # ana montaja eklenemeyen ama kendi içinde birleşen gruplar: grup içi konumlarla gerçek temas haritası
    for g in getattr(asm, "groups", None) or []:
        members = [n for n in g if n not in maps]
        if len(members) >= 2:
            maps.update(contact_map(members, run.parts, run.fields, g, asm.up_vector, [], s.contact_tol, False))
    rest = [n for n in run.parts if n not in maps]
    if rest:
        up = asm.up_vector if asm else None
        maps.update(planar_fallback_map(rest, run.parts, up))
    for n, m in maps.items():
        # Delik yalnızca birleşme yüzeylerine: montajda komşu parça ağzı kapatır, baskıdan sonra tıkamak gerekmez.
        # Kaide altı / duvar arkası dış yüzeydir; sadece kullanıcı açıkça izin verirse.
        allowed = m.mating.copy() if m.mating is not None else m.hidden.copy()
        if s.hole_on_bottom:
            allowed |= m.bottom
        if s.hole_on_wall and m.wall_facing is not None:
            allowed |= m.wall_facing
        m.allowed = allowed
        compute_safe(m, run.parts[n].lite, s.hole / 2 + s.hole_margin)
    # destek yeri tercihi için görünürlük (montaj bilinmiyorsa da yukarı/ön kuralı geçerli: parça kendi duruşunda)
    up = asm.up_vector if asm else np.array([0.0, 0.0, 1.0])
    front = run.front_vector
    for n, m in maps.items():
        m.visibility = visibility_weights(run.parts[n].lite.face_normals, m.hidden, up, front)
    return maps
