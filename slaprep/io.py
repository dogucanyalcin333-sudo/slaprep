"""STL okuma/yazma, doğrulama, onarım ve analiz için sadeleştirilmiş kopyalar."""
from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import trimesh

from .config import OUT_DIR, out_dir

LITE_FACES = 80_000     # analiz kopyası üçgen sayısı
CACHE_VERSION = 3
LITE_VERSION = 2         # 2: kenar daraltma çökerse voxel kümelemeli sadeleştirme


@dataclass
class Part:
    name: str
    path: Path
    lite: trimesh.Trimesh          # sadeleştirilmiş, analiz için (orijinal koordinatlarda)
    n_faces: int
    watertight: bool
    bodies: int
    volume: float                  # mm³
    area: float                    # mm²
    bounds: np.ndarray             # (2, 3)
    repaired: bool = False
    repair_failed: bool = False
    warnings: list[str] = field(default_factory=list)
    _repaired_path: Path | None = None
    group: str = ""                # kitin üst düzey alt klasörü ("" = kök)

    @property
    def volume_ml(self) -> float:
        return self.volume / 1000.0

    def load_full(self) -> trimesh.Trimesh:
        """Tam çözünürlüklü mesh (onarıldıysa onarılmış hali). Orijinal dosyaya dokunulmaz."""
        if self._repaired_path is not None and self._repaired_path.exists():
            d = np.load(self._repaired_path)
            return trimesh.Trimesh(d["v"], d["f"], process=False)
        return load_mesh_robust(self.path)[0]


def simplify(mesh: trimesh.Trimesh, target_faces: int, max_step: float = 0.5) -> trimesh.Trimesh:
    """fast-simplification ile AŞAMALI sadeleştir: her adımda üçgenlerin en fazla `max_step` kadarı atılır.
    Tek adımda büyük oranlı sadeleştirme yoğunluğu çok düzensiz mesh'lerde çöküyor (Radahn Legs: 2.2M → 80k
    tek adımda 3 mm sapma, 11 ml hacim kaybı; aşamalı: 0.13 mm). Thread-safe DEĞİL: süreç içinde seri çağır."""
    import fast_simplification

    if len(mesh.faces) <= target_faces:
        return mesh.copy()
    m = mesh
    while len(m.faces) > target_faces * 1.02:
        keep = max(1.0 - max_step, target_faces / len(m.faces))
        v, f = fast_simplification.simplify(
            np.ascontiguousarray(m.vertices, dtype=np.float64),
            np.ascontiguousarray(m.faces, dtype=np.int64),
            target_reduction=1.0 - keep,
        )
        nm = trimesh.Trimesh(np.asarray(v, dtype=np.float64), f, process=True)
        if len(nm.faces) >= len(m.faces) * 0.98:      # ilerleme yok
            break
        m = nm
    return m


def _cluster_simplify(mesh: trimesh.Trimesh, target_faces: int) -> trimesh.Trimesh:
    """Önce voxel kümeleme (open3d) ile yoğunluğu eşitle, sonra kenar daraltmayla hedefe in. Kenar daraltmanın
    çöktüğü mesh'lerde (Radahn Legs: 2.2M üçgen, iki yöntem de 4–5 mm sapıyor) 0.1 mm sapma verir; sonuç
    watertight olmayabilir (kümelemeden manifold olmayan kenarlar) ama yön/işaret sorguları için yeterli."""
    import open3d as o3d

    voxel = float(np.sqrt(2.0 * mesh.area / (5.0 * target_faces)))
    o = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(np.asarray(mesh.vertices, dtype=np.float64)),
                                  o3d.utility.Vector3iVector(np.asarray(mesh.faces, dtype=np.int32)))
    s = o.simplify_vertex_clustering(voxel, o3d.geometry.SimplificationContraction.Quadric)
    c = trimesh.Trimesh(np.asarray(s.vertices), np.asarray(s.triangles), process=True)
    return simplify(c, target_faces)


def simplify_checked(mesh: trimesh.Trimesh, target_faces: int, field=None) -> tuple[trimesh.Trimesh, float]:
    """Aşamalı sadeleştir ve tam mesh'e sapmayı ölç (p99, mm). Sapma büyükse önce voxel kümelemeli yolu, sonra
    daha küçük adımları dene; en az sapanı döndür."""
    from .geom import DistanceField

    f = field or DistanceField(mesh)
    tol = max(0.3, 0.003 * float(np.linalg.norm(mesh.extents)))
    best = None
    for method in ("0.5", "küme", "0.3", "0.15"):
        lite = _cluster_simplify(mesh, target_faces) if method == "küme" else simplify(mesh, target_faces,
                                                                                         max_step=float(method))
        pts, _ = trimesh.sample.sample_surface(lite, 20000, seed=0)
        dev = float(np.percentile(f.unsigned(pts), 99))
        if best is None or dev < best[1]:
            best = (lite, dev)
        if dev <= tol:
            break
    return best


def _drop_debris(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Hacimsiz/minik kırıntı gövdeleri at (resmi kitlerde sık: yüzlerce 0 ml'lik parça)."""
    bodies = mesh.split(only_watertight=False)
    if len(bodies) <= 1:
        return mesh
    keep = [b for b in bodies if b.area > 0.002 * mesh.area or (b.is_watertight and abs(b.volume) > 1.0)]
    return trimesh.util.concatenate(keep) if keep else mesh


def manifold_ok(m: trimesh.Trimesh) -> bool:
    """Boolean (manifold3d: hollow ve delik açma) için geçerli mi. Su geçirmez olmak yetmez: yüz yönleri tutarlı
    ve kenarlar manifold olmalı (Godfrey lion_mane: delik doldurma sonrası su geçirmez ama yönleri tutarsızdı,
    hazırlık boolean'da çöküyordu)."""
    try:
        import manifold3d as mf

        mesh = mf.Mesh(vert_properties=np.ascontiguousarray(m.vertices, dtype=np.float32),
                       tri_verts=np.ascontiguousarray(m.faces, dtype=np.uint32))
        if mf.Manifold(mesh).status() == mf.Error.NoError:
            return True
        mesh.merge()
        return mf.Manifold(mesh).status() == mf.Error.NoError
    except Exception:
        return False


PMF_TIMED_FACES = 300_000   # bundan büyük mesh'te pymeshfix ayrı süreçte, süre sınırıyla çalışır
PMF_TIMEOUT = 600.0         # s: Griffith merged_base (1.9 M üçgen) 40 dakikada bitmemişti


def _pmf_clean(v, f):
    import pymeshfix

    return pymeshfix.clean_from_arrays(v, f, remove_smallest_components=False)


def _call_with_timeout(fn, args, timeout: float):
    """fn'i ayrı süreçte çalıştır; süre dolarsa süreci öldür ve TimeoutError. (C eklentisi Python'dan kesilemez.)"""
    import multiprocessing as mp

    pool = mp.get_context("spawn").Pool(1)
    try:
        return pool.apply_async(fn, args).get(timeout=timeout)
    except mp.TimeoutError:
        raise TimeoutError(f"{timeout / 60:.0f} dakikada bitmedi") from None
    finally:
        pool.terminate()


def pymeshfix_repair(m: trimesh.Trimesh, timeout: float | None = None) -> trimesh.Trimesh | None:
    """pymeshfix ile kapsamlı onarım (deliği, ters yüzü, manifold olmayan kenarı düzeltir). Su geçirmez sonuç
    yoksa None. timeout: büyük mesh'te süre sınırı (aşılırsa TimeoutError)."""
    try:
        import pymeshfix  # noqa: F401
    except ImportError:
        return None
    # pymeshfix 0.18: float64/int32 C-sıralı dizi ister; küçük ama gerçek gövdeleri silmesin
    args = (np.ascontiguousarray(m.vertices, dtype=np.float64), np.ascontiguousarray(m.faces, dtype=np.int32))
    if timeout and len(m.faces) > PMF_TIMED_FACES:
        v, f = _call_with_timeout(_pmf_clean, args, timeout)
    else:
        v, f = _pmf_clean(*args)
    r = trimesh.Trimesh(v, f, process=True)
    if not closed(r):
        return None
    if r.volume < 0:
        r.invert()
    return r


def closed(m: trimesh.Trimesh) -> bool:
    """Kapalı katı mı: her kenarı 2 yüzlü (trimesh) ya da boolean motorunun kabul ettiği kapalı manifold. İkincisi
    iki yüzeyin tek bir kenarda değdiği sculpt dosyalarını da kapsar (o kenar 4 yüzlüdür; Yhorm Weapon: 26 kenar):
    trimesh 'su geçirmez değil' der ama hollow/boolean için geçerlidir."""
    return bool(m.is_watertight) or manifold_ok(m)


REPAIR_VERSION = 4       # 2: her onarım sadakat testinden geçer; çok gövdeli parçalar gövde gövde onarılıp birleştirilir
#                          3: kapalılık boolean motoruna göre (4 yüzlü kenarlı sculpt'lar artık onarılmış sayılıyor)
#                          4: mikro onarım (3 yüzlü kenar "kanatları" + ≤1 mm delikler; Yhorm omuz/torso)
VOLUME_VERSION = 1       # onarılamayan parçanın hacmi: işaretli hacim (eskiden dışbükey zarf)
FIDELITY_TOL = 0.5       # mm: bundan fazla sapan yüzey "kaybolan/eklenen" sayılır
MAX_LOST = 0.01          # orijinal yüzeyin en çok %1'i kaybolabilir (iç içe geçen gövdelerin içte kalan yüzleri hariç)
MAX_ADDED = 0.15         # onarımın eklediği yüzey en çok %15 (delik kapakları olağan)


def repair_fidelity(orig: trimesh.Trimesh, rep: trimesh.Trimesh, n: int = 60000) -> tuple[float, float]:
    """Onarım şekli koruyor mu: (kaybolan, eklenen) yüzey oranları.
    kaybolan: orijinal yüzey noktalarından onarılmış katının DIŞINDA kalanların oranı (birleşimle içeride kalan
    gövde yüzleri kayıp sayılmaz); eklenen: onarılmış yüzeyin orijinalde karşılığı olmayan kısmı (delik kapakları,
    uydurma yüzey). Hulkbuster torso + pymeshfix: kaybolan %29, eklenen %19 — iç içe geçen 127 gövdeyi siliyordu."""
    from .geom import DistanceField

    po, _ = trimesh.sample.sample_surface(orig, n, seed=0)
    pr, _ = trimesh.sample.sample_surface(rep, n, seed=1)
    lost = float(np.mean(DistanceField(rep).signed(po) > FIDELITY_TOL))
    added = float(np.mean(DistanceField(orig).unsigned(pr) > FIDELITY_TOL))
    return lost, added


def _faithful(orig, rep) -> tuple[bool, str]:
    lost, added = repair_fidelity(orig, rep)
    return lost <= MAX_LOST and added <= MAX_ADDED, f"kaybolan %{100 * lost:.1f}, eklenen %{100 * added:.1f}"


def _close_body(b: trimesh.Trimesh) -> trimesh.Trimesh | None:
    """Tek gövdeyi kapat ve yönünü düzelt (önce hafif yöntem, olmazsa sadece bu gövdeye pymeshfix — başka gövdelerle
    kesişimler onu yanıltamaz). Sadakat testinden geçmezse None."""
    m = b.copy()
    m.merge_vertices()
    trimesh.repair.fill_holes(m)
    trimesh.repair.fix_winding(m)
    if closed(m):
        if m.volume < 0:
            m.invert()
        if manifold_ok(m):
            return m
    try:
        r = pymeshfix_repair(m, timeout=PMF_TIMEOUT)
    except Exception:                   # zaman aşımı dahil
        return None
    if r is None or not manifold_ok(r) or not _faithful(b, r)[0]:
        return None
    return r


def pymeshfix_fill_only(m: trimesh.Trimesh) -> tuple[trimesh.Trimesh | None, float]:
    """Sadece delikleri doldur (pymeshfix'in kendini kesen bölgeleri silme adımı YOK — o adım Hulkbuster torsosunda
    yüzeyin %29'unu siliyordu). Dönüş: (su geçirmez mesh veya None, kendini kesen üçgen oranı)."""
    import pymeshfix

    tm = pymeshfix.PyTMesh()
    tm.set_quiet(True)
    tm.load_array(np.ascontiguousarray(m.vertices, dtype=np.float64), np.ascontiguousarray(m.faces, dtype=np.int32))
    tm.fill_small_boundaries(nbe=0, refine=True)
    if tm.n_boundaries:
        return None, 0.0
    v, f = tm.return_arrays()
    frac = len(tm.select_intersecting_triangles()) / max(len(f), 1)
    r = trimesh.Trimesh(v, f, process=True)
    # 4 yüzlü (iki yüzeyin değdiği) kenarlar PyTMesh'te ayrılır; trimesh köşeleri yeniden birleştirince "su
    # geçirmez değil" der ama boolean motoru için kapalıdır (Yhorm: delik doldurma kayıpsız, eskiden reddediliyordu)
    if not closed(r):
        return None, frac
    if r.volume < 0:
        r.invert()
    return r, frac


def micro_repair(m: trimesh.Trimesh, max_bad: float = 0.001, rounds: int = 3) -> trimesh.Trimesh | None:
    """Az sayıda minik kusurlu sculpt (Yhorm omuz: 878 bin yüzde 84 açık kenar — hepsi ≤ 1 mm'lik 25 döngü — ve
    3 yüzlü 2 kenar): tek sayıda (3, 5…) yüze sahip kenarlara değen "kanat" yüzleri silinir, açık kenarlar ve
    silmenin açtığı minik delikler PyTMesh ile doldurulur. 3 yüzlü kenar PyTMesh'in sınır takibini bozup delik
    doldurmayı engelliyordu. Silinecek yüz yüzeyin max_bad oranını aşarsa (gerçek kusur büyük) None."""
    import pymeshfix

    r = m.copy()
    r.merge_vertices()
    r.update_faces(r.nondegenerate_faces())
    r.update_faces(r.unique_faces())
    for _ in range(rounds):
        e = np.sort(r.edges, axis=1)
        _, inv, cnt = np.unique(e, axis=0, return_inverse=True, return_counts=True)
        odd = (cnt > 2) & (cnt % 2 == 1)
        bad = np.unique(r.edges_face[odd[inv.ravel()]])
        if not len(bad):
            break
        if r.area_faces[bad].sum() > max_bad * r.area:
            return None
        keep = np.ones(len(r.faces), bool)
        keep[bad] = False
        r.update_faces(keep)
        r.remove_unreferenced_vertices()
    tm = pymeshfix.PyTMesh()
    tm.set_quiet(True)
    tm.load_array(np.ascontiguousarray(r.vertices, np.float64), np.ascontiguousarray(r.faces, np.int32))
    tm.fill_small_boundaries(nbe=0, refine=True)
    if tm.n_boundaries:
        return None
    v, f = tm.return_arrays()
    out = trimesh.Trimesh(v, f, process=True)
    if not closed(out):
        return None
    if out.volume < 0:
        out.invert()
    return out


def _union_bodies(mesh: trimesh.Trimesh) -> tuple[trimesh.Trimesh | None, str]:
    """Çok gövdeli parça (zırh plakaları gövdeye geçmiş): her gövde ayrı kapatılır, sonra boolean birleşimle tek
    katı yapılır — iç içe geçen kısımlar silinmez, birleşir."""
    import manifold3d as mf

    bodies = sorted(mesh.split(only_watertight=False), key=lambda b: -b.area)
    total = mesh.area
    if len(bodies) < 2 or bodies[1].area < 0.01 * total:
        return None, "tek gövde"                               # geri kalanlar kırıntı: gövde birleştirme anlamsız
    mans, dropped = [], 0.0
    for b in bodies:
        if b.area < 0.002 * total and not (b.is_watertight and abs(b.volume) > 1.0):
            dropped += b.area                                  # kırıntı
            continue
        c = _close_body(b)
        if c is None:
            dropped += b.area
            continue
        mm = mf.Mesh(vert_properties=np.ascontiguousarray(c.vertices, dtype=np.float32),
                     tri_verts=np.ascontiguousarray(c.faces, dtype=np.uint32))
        man = mf.Manifold(mm)
        if man.status() != mf.Error.NoError:
            mm.merge()
            man = mf.Manifold(mm)
        mans.append(man)
    if not mans or dropped > 0.01 * total:                     # kapatılamayan gövdeler yüzeyin %1'inden fazla
        return None, f"kapatılamayan gövdeler yüzeyin %{100 * dropped / total:.1f}'i"
    u = mf.Manifold.batch_boolean(mans, mf.OpType.Add).to_mesh()
    r = trimesh.Trimesh(np.asarray(u.vert_properties)[:, :3], np.asarray(u.tri_verts), process=True)
    return (r, "") if len(r.faces) and closed(r) else (None, "birleşim kapalı değil")


def _try_repair(mesh: trimesh.Trimesh) -> tuple[trimesh.Trimesh | None, str]:
    """Su geçirmez / boolean'a uygun olmayan parçayı onar. Sırayla: hızlı onarım (kırıntı at + delik doldur + yön
    düzelt), çok gövdeliyse gövde gövde onarıp birleştirme, en son tüm mesh'e pymeshfix. Her aday sadakat testinden
    geçer (repair_fidelity): şekli bozan onarım kabul edilmez. Hiçbiri geçmezse (None, neden) — parça olduğu gibi,
    dolu basılır (CHITUBOX çok gövdeli/açık dosyaları zaten dilimler)."""
    reasons = []

    def accept(cand, label):
        if cand is None or not manifold_ok(cand):          # boolean motoru kapalı, yönlü manifold kabul etmeli
            return False
        ok, why = _faithful(mesh, cand)
        if not ok:
            reasons.append(f"{label}: şekli bozuyordu ({why})")
        return ok

    m = _drop_debris(mesh)
    m.merge_vertices()
    trimesh.repair.fill_holes(m)
    if not m.is_winding_consistent:
        trimesh.repair.fix_winding(m)
    if closed(m) and m.volume < 0:
        m.invert()
    if accept(m, "delik doldurma"):
        return m, "kırıntı temizliği + delik doldurma"
    try:
        mr = micro_repair(m)
    except Exception as e:
        mr = None
        reasons.append(f"mikro onarım hatası: {e}")
    if mr is not None and accept(mr, "mikro onarım"):
        return mr, "mikro onarım (kanat yüzler silindi, minik delikler dolduruldu)"
    try:
        u, why = _union_bodies(mesh)
    except Exception as e:
        u, why = None, f"hata: {e}"
    if u is not None and accept(u, "gövde birleştirme"):
        return u, "gövdeler tek tek kapatılıp birleştirildi"
    if u is None and why != "tek gövde":
        reasons.append(f"gövde birleştirme: {why}")
    # sadece delik doldurma (silme yok): kendini kesen bölge azsa kabul (çoksa hollow'un iç/dış ayrımı güvenilmez)
    try:
        fo, inter = pymeshfix_fill_only(m)
    except Exception as e:
        fo, inter = None, 0.0
        reasons.append(f"delik doldurma (pymeshfix) hatası: {e}")
    if fo is not None:
        if inter > 0.005:
            reasons.append(f"delik doldurma: kendini kesen üçgen %{100 * inter:.1f}")
        elif accept(fo, "delik doldurma (pymeshfix)"):
            return fo, "delik doldurma (pymeshfix, silmesiz)"
    elif not any(r_.startswith("delik doldurma (pymeshfix) hatası") for r_ in reasons):
        reasons.append("delik doldurma: bazı açıklıklar kapatılamadı")
    try:
        r = pymeshfix_repair(m, timeout=PMF_TIMEOUT)
    except TimeoutError as e:
        r = None
        reasons.append(f"pymeshfix: {e}")
    except Exception as e:
        r = None
        reasons.append(f"pymeshfix hatası: {e}")
    if r is not None and accept(r, "pymeshfix"):
        return r, "pymeshfix"
    return None, "; ".join(reasons) or "su geçirmez sonuç üretilemedi"


def _read_binary_stl(path) -> tuple[trimesh.Trimesh | None, str]:
    """İkili STL'yi başlıktaki üçgen sayısına değil dosya boyutuna göre oku (CHITUBOX gibi). Bazı programlar
    (ör. Magics 'COLOR=… MATERIAL=…' başlıklı) sayı alanını yanlış yazıyor; trimesh o zaman dosyayı boş okuyor
    (ZEZ Hulkbuster Base_2: başlık 937132, dosya 937592 üçgen)."""
    path = Path(path)
    size = path.stat().st_size
    if size < 84 + 50:
        return None, ""
    with open(path, "rb") as fh:
        head = fh.read(84)
    n_hdr = int(np.frombuffer(head[80:84], "<u4")[0])
    n_size = (size - 84) // 50
    n = n_size if (size - 84) % 50 == 0 else min(n_hdr, n_size)
    if n <= 0:
        return None, ""
    dt = np.dtype([("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("a", "<u2")])
    d = np.fromfile(path, dtype=dt, count=n, offset=84)
    v = d["v"].astype(np.float64)
    v = v[np.isfinite(v).all(axis=(1, 2))]
    if not len(v):
        return None, ""
    m = trimesh.Trimesh(v.reshape(-1, 3), np.arange(len(v) * 3).reshape(-1, 3), process=True)
    note = (f"STL başlığındaki üçgen sayısı ({n_hdr}) dosya boyutuyla uyuşmuyor ({n_size}); dosya boyutuna göre "
            "okundu (CHITUBOX gibi)." if n_hdr != n else "")
    return m, note


def load_mesh_robust(path) -> tuple[trimesh.Trimesh, str]:
    """STL'yi yükle; trimesh boş okursa (bozuk başlık) dosya boyutuna göre ikili okuma. Dönüş: (mesh, not)."""
    mesh = trimesh.load_mesh(path, process=True)
    if isinstance(mesh, trimesh.Scene):
        mesh = mesh.to_geometry() if mesh.geometry else trimesh.Trimesh()
    if len(mesh.faces):
        return mesh, ""
    raw, note = _read_binary_stl(path)
    if raw is None or not len(raw.faces):
        raise ValueError(f"{Path(path).name}: dosyada okunabilir üçgen yok (0 üçgen).")
    return raw, note or "trimesh dosyayı boş okudu; ikili STL olarak dosya boyutuna göre okundu."


def _savez_atomic(path, **arrays):
    """Önce geçici dosyaya yaz, sonra yerine taşı: süreç yazarken düşerse (bellek bitti) yarım dosya kalmasın."""
    path = str(path)
    tmp = path[:-4] + ".tmp.npz" if path.endswith(".npz") else path + ".tmp.npz"
    np.savez(tmp, **arrays)
    os.replace(tmp, path)


def volume_estimate(mesh: trimesh.Trimesh) -> float:
    """mm³. Su geçirmez değilse (onarılamayan parça) de işaretli hacim, açıklıklar küçükse doğruya yakındır;
    dışbükey zarf (eski yöntem) girintili parçalarda çok fazla verir (Griffith kafası: 8.9 ↔ 37.7 ml). İşaretli
    hacim anlamsızsa (≤ 0 ya da zarftan büyük: yönler tutarsız) zarfa düşülür."""
    if mesh.is_watertight:
        return float(abs(mesh.volume))
    hull = float(mesh.convex_hull.volume)
    v = float(abs(mesh.volume))
    return v if 0.0 < v <= hull else hull


def _load_worker(args):
    """Ayrı süreçte: yükle, doğrula, gerekirse onar, sadeleştir. Sonuçları önbelleğe yazar."""
    with np.errstate(divide="ignore", invalid="ignore"):     # açık/düz gövdelerde trimesh'in 0/0 kütle merkezi uyarısı
        return _load_part(args)


def _load_part(args):
    path, cache_file = args
    mesh, load_note = load_mesh_robust(path)
    info = dict(n_faces=len(mesh.faces), watertight=bool(mesh.is_watertight),
                repaired=False, repair_failed=False, repaired_path="", repair_method="", load_note=load_note)
    try:
        info["bodies"] = int(mesh.body_count)
    except Exception:
        info["bodies"] = -1
    # su geçirmez ama yönleri tutarsız / manifold olmayan dosya da onarılır (yoksa hollow/delik boolean'ı çöker)
    if not mesh.is_watertight or not mesh.is_winding_consistent or not manifold_ok(mesh):
        fixed, method = _try_repair(mesh)
        info["repair_method"] = method
        if fixed is not None:
            rp = str(cache_file).replace(".npz", ".repaired.npz")
            _savez_atomic(rp, v=fixed.vertices, f=fixed.faces)
            mesh = fixed
            info["repaired"] = True
            info["repaired_path"] = rp
            info["bodies"] = int(mesh.body_count)
        else:
            info["repair_failed"] = True
    info["volume"] = volume_estimate(mesh)
    info["volume_v"] = VOLUME_VERSION
    info["area"] = float(mesh.area)
    info["bounds"] = mesh.bounds.copy()
    lite, dev = simplify_checked(mesh, LITE_FACES) if len(mesh.faces) > LITE_FACES else (mesh.copy(), 0.0)
    info["lite_dev"] = dev
    info["lite_v"] = LITE_VERSION
    info["manifold_v"] = 1                   # onarım boolean uygunluğu kontrol edilerek yapıldı
    info["repair_v"] = REPAIR_VERSION        # onarım sadakat testiyle yapıldı
    _savez_atomic(cache_file, v=lite.vertices, f=lite.faces, version=CACHE_VERSION,
                  **{k: np.asarray(v) for k, v in info.items()})
    return cache_file


def list_stl(kit: Path) -> list[Path]:
    kit = Path(kit)
    files = [p for p in kit.rglob("*") if p.suffix.lower() == ".stl" and OUT_DIR not in p.parts]
    return sorted(files, key=lambda p: p.stem.lower())


def _cache_name(p: Path, name: str | None = None) -> str:
    st = p.stat()
    return f"{name or p.stem}.{st.st_size}.{int(st.st_mtime)}.v{CACHE_VERSION}.npz"


def part_names(kit: Path, files: list[Path]) -> dict[Path, tuple[str, str]]:
    """Dosya → (benzersiz parça adı, grup). Grup = kitin üst düzey alt klasörü. Aynı ad farklı klasörlerde
    varsa ada grup öneki eklenir."""
    out = {}
    stems: dict[str, int] = {}
    for f in files:
        stems[f.stem.lower()] = stems.get(f.stem.lower(), 0) + 1
    for f in files:
        rel = f.relative_to(kit).parts
        group = rel[0] if len(rel) > 1 else ""
        name = f.stem if stems[f.stem.lower()] == 1 or not group else f"{group}_{f.stem}"
        out[f] = (name, group)
    return out


def matches(name: str, group: str, patterns: list[str] | None, stem: str | None = None,
            folders: tuple[str, ...] = ()) -> bool:
    """Parça adı (veya dosya adı) bir desenle (büyük/küçük harf duyarsız, * ? destekli) ya da parçanın klasör
    adlarından biriyle eşleşiyor mu. Ör: "Base" klasörün tamamı, "*_Key", "Dragon_1". folders: kit içindeki tüm ara
    klasörler (Chimchar: BASE\\OUISTICRAM → "OUISTICRAM" o alt klasörü çıkarır)."""
    from fnmatch import fnmatch

    for p in patterns or []:
        q = p.strip().strip("/\\").lower()
        if not q:
            continue
        if fnmatch(name.lower(), q) or (stem and fnmatch(stem.lower(), q)) or (group and group.lower() == q):
            return True
        if any(f.lower() == q for f in folders):
            return True
    return False


def rel_folders(kit: Path, f: Path) -> tuple[str, ...]:
    """Dosyanın kit klasörüne göre ara klasör adları."""
    try:
        return Path(f).relative_to(kit).parts[:-1]
    except ValueError:
        return ()


def expand(patterns: list[str] | None, parts: list[Part]) -> list[str]:
    """Desenleri gerçek parça adlarına aç."""
    return [p.name for p in parts if matches(p.name, p.group, patterns, p.path.stem)]


def _lite_broken(cache_file: Path) -> bool:
    """Önbellekteki analiz kopyası bozuk mu: sapma ölçülmemiş eski kayıtta hacim %3'ten fazla sapıyor ya da
    üçgenlerin çoğu iğne gibi (kenar medyanı < 0.05 mm)."""
    try:
        d = np.load(cache_file)
        if "lite_dev" in d.files:
            # eski sadeleştiriciyle üretilmiş ve sapması tolerans üstü → yeni yöntemle yeniden üret
            ext = np.ptp(np.asarray(d["bounds"]), axis=0)
            tol = max(0.3, 0.003 * float(np.linalg.norm(ext)))
            return float(d["lite_dev"]) > tol and int(d["lite_v"]) < LITE_VERSION if "lite_v" in d.files \
                else float(d["lite_dev"]) > tol
        if not bool(d["watertight"]) and not bool(d["repaired"]):
            return False                     # hacim dışbükey zarftan; karşılaştırma anlamsız
        m = trimesh.Trimesh(d["v"], d["f"], process=False)
        dv = abs(abs(m.volume) - float(d["volume"])) / max(float(d["volume"]), 1.0)
        return dv > 0.03 or float(np.median(m.edges_unique_length)) < 0.05
    except Exception:
        return True


# Yükleme işçisinin tepe belleği ≈ STL dosya boyutu × bu katsayı (ölçüm: batman toso_clean 126 MB ikili STL,
# onarım + sapma kontrolüyle 4.8 GB). 8 büyük parça aynı anda yüklenince 15 GB RAM'li makinede bellek bitiyordu.
MEM_PER_STL_BYTE = 40.0
MIN_JOB_MEM = 0.5 * 2**30


def _free_memory() -> float:
    try:
        import psutil

        return float(psutil.virtual_memory().available)
    except Exception:
        return 4.0 * 2**30


def _children_private() -> float:
    """Bu sürecin alt süreçlerinin (yükleme işçileri) şu anki özel belleği."""
    try:
        import psutil

        tot = 0.0
        for c in psutil.Process().children(recursive=True):
            try:
                mi = c.memory_info()
                tot += getattr(mi, "private", mi.rss)
            except psutil.Error:
                pass
        return tot
    except Exception:
        return 0.0


class LoadError(RuntimeError):
    """Bir STL yüklenemedi (çoğunlukla bellek yetmedi); mesaj kullanıcıya gösterilir."""


def _run_load_jobs(jobs: list[tuple[str, str]], progress=None):
    """Yükleme işlerini boş belleğe göre paralel çalıştır: aynı anda çalışanların tahmini bellek toplamı boş
    belleğin %80'ini geçmez (her zaman en az bir iş çalışır). Büyükler önce. Bir işçi süreci düşerse (bellek
    bitti) kalanlar tek tek yeniden denenir; tek başına da yüklenemeyen dosya açık bir hatayla bildirilir."""
    from concurrent.futures import FIRST_COMPLETED, wait
    from concurrent.futures.process import BrokenProcessPool

    size = {j: Path(j[0]).stat().st_size for j in jobs}
    est = {j: max(MIN_JOB_MEM, size[j] * MEM_PER_STL_BYTE) for j in jobs}
    pending = sorted(jobs, key=lambda j: -size[j])

    def fits(j, used, budget):
        """Yeni iş başlasın mı: çalışan işçilerin tahmini tepelerine kadar daha büyüyebilecekleri pay ayrıldıktan
        sonra kalan boş bellek yeni işe yetmeli (çalışırken başka program belleği kaptıysa da beklenir)."""
        if used <= 0:
            return True
        growth = max(0.0, used - _children_private())
        return used + est[j] <= budget and est[j] + growth <= 0.85 * _free_memory()

    def run(max_workers: int, budget: float):
        # max_tasks_per_child=1: her işçi bir parça yükleyip kapanır, belleği tamamen geri verir (açık kalan işçiler
        # önceki büyük parçanın belleğini tutuyordu: batman 19 parça, tepe 21.6 GB)
        with ProcessPoolExecutor(max_workers, max_tasks_per_child=1) as ex:
            running: dict = {}
            used = 0.0
            while pending or running:
                while pending and len(running) < max_workers:
                    j = next((j for j in pending if fits(j, used, budget)), None)
                    if j is None:
                        if running:
                            break
                        j = pending[0]                       # tek başına bile sığmıyor: yine de tek başına dene
                    pending.remove(j)
                    running[ex.submit(_load_worker, j)] = j
                    used += est[j]
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for f in done:
                    j = running.pop(f)
                    used -= est[j]
                    try:
                        f.result()
                    except BrokenProcessPool:
                        pending[:0] = [j] + list(running.values())   # düşen havuzdakiler yeniden denenecek
                        raise
                    except MemoryError as e:
                        raise LoadError(f"Bellek yetmedi: {Path(j[0]).name} ({size[j] / 2**20:.0f} MB).") from e
                    except Exception as e:
                        raise LoadError(f"{Path(j[0]).name} yüklenemedi: {type(e).__name__}: {e}") from e
                    if progress:
                        progress()

    workers = max(1, min(len(jobs), (os.cpu_count() or 2) // 2, 8))
    try:
        run(workers, 0.9 * _free_memory())
    except BrokenProcessPool:
        # bir işçi bellek yetmediği için düştü: bitmemiş olanları tek tek (sırayla) dene
        pending[:] = sorted({j for j in pending if not Path(j[1]).exists()}, key=lambda j: -size[j])
        try:
            run(1, float("inf"))
        except BrokenProcessPool as e:
            j = pending[0] if pending else None
            name = f"{Path(j[0]).name} ({size[j] / 2**20:.0f} MB)" if j else "bir dosya"
            raise LoadError(f"Bellek yetmedi: {name} tek başına bile yüklenemedi. Açık programları kapatıp tekrar "
                            "deneyin; Windows'un sanal bellek (sayfa dosyası) sürücüsünde boş yer olduğundan emin "
                            "olun.") from e


def _repair_checked(cache_file: Path) -> bool:
    """Önbellekteki onarım güncel kurallarla mı yapıldı. Eski sürüm onarımları (yön tutarlılığı / şekil sadakati
    kontrol edilmeden; Hulkbuster torso pymeshfix ile %29'u kaybolmuştu) False → parça yeniden yüklenip onarılır.
    Onarım gerektirmeyen parçalara dokunulmaz."""
    try:
        with np.load(cache_file) as d:              # Windows: açık dosya silinemez, önce kapat
            if not bool(d["repaired"]) and not bool(d["repair_failed"]):
                return True
            # başarılı onarımlar 2. sürümden beri geçerli (sonraki sürümler sadece başarısızları kurtarıyor)
            need = REPAIR_VERSION if bool(d["repair_failed"]) else 2
            ok = "repair_v" in d.files and int(d["repair_v"]) >= need
            if bool(d["repair_failed"]) and not ("volume_v" in d.files and int(d["volume_v"]) >= VOLUME_VERSION):
                ok = False                          # açık parçanın hacmi eskiden dışbükey zarftan (fazla) ölçülüyordu
            rp = str(d["repaired_path"])
        if not ok and rp:
            Path(rp).unlink(missing_ok=True)
        return ok
    except Exception:
        return False


def load_kit(kit: Path, only: list[str] | None = None, progress=None, exclude: list[str] | None = None) -> list[Part]:
    """Klasördeki tüm STL'leri yükle. Ağır iş paralel süreçlerde yapılır, sonuç önbelleklenir.
    only/exclude: parça adı, * deseni veya alt klasör adı."""
    kit = Path(kit)
    files = list_stl(kit)
    names = part_names(kit, files)
    if only:
        files = [f for f in files if matches(names[f][0], names[f][1], only, f.stem, rel_folders(kit, f))]
    if exclude:
        files = [f for f in files if not matches(names[f][0], names[f][1], exclude, f.stem, rel_folders(kit, f))]
    cache = out_dir(kit) / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    current = {_cache_name(f, names[f][0]) for f in files}
    for old in cache.glob("*.npz"):
        base = old.name.replace(".repaired.npz", ".npz")
        if base not in current and not only and not exclude:
            old.unlink(missing_ok=True)
    jobs = []
    for f in files:
        c = cache / _cache_name(f, names[f][0])
        if c.exists() and _lite_broken(c):
            c.unlink()                       # eski sürümde bozuk üretilmiş analiz kopyası: sadece bunu yeniden üret
        if c.exists() and not _repair_checked(c):
            c.unlink()                       # eski sürümde boolean'a uygun olmayan onarım: yeniden onar
        if not c.exists():
            jobs.append((str(f), str(c)))
    if jobs:
        _run_load_jobs(jobs, progress)
    parts = []
    for f in files:
        d = np.load(cache / _cache_name(f, names[f][0]))
        lite = trimesh.Trimesh(d["v"], d["f"], process=False)
        rp = str(d["repaired_path"])
        p = Part(
            name=names[f][0], path=f, lite=lite, n_faces=int(d["n_faces"]),
            watertight=bool(d["watertight"]), bodies=int(d["bodies"]),
            volume=float(d["volume"]), area=float(d["area"]), bounds=d["bounds"],
            repaired=bool(d["repaired"]), repair_failed=bool(d["repair_failed"]),
            _repaired_path=Path(rp) if rp else None, group=names[f][1],
        )
        method = str(d["repair_method"])
        if "load_note" in d.files and str(d["load_note"]):
            p.warnings.append(str(d["load_note"]))
        if p.repaired:
            p.warnings.append(f"Watertight değildi, onarıldı ({method}).")
        if p.repair_failed:
            p.warnings.append("Su geçirmez değil; otomatik onarım şekli bozacağı için yapılmadı — parça orijinal haliyle "
                              f"ve dolu basılacak (hollow/delik yok; CHITUBOX dilimleyebilir). Ayrıntı: {method}")
        if p.bodies > 1:
            p.warnings.append(f"{p.bodies} ayrı gövde içeriyor.")
        parts.append(p)
    return parts


def unit_warning(parts: list[Part]) -> str | None:
    lo = np.min([p.bounds[0] for p in parts], axis=0)
    hi = np.max([p.bounds[1] for p in parts], axis=0)
    ext = float(np.max(hi - lo))
    if ext < 5:
        return f"Kit çok küçük ({ext:.2f} birim): dosyalar inch veya metre biriminde olabilir."
    if ext > 2000:
        return f"Kit çok büyük ({ext:.0f} birim): dosyalar mikron veya farklı birimde olabilir."
    return None


def save_stl(mesh: trimesh.Trimesh, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(path)
