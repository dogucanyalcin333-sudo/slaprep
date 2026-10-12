"""Yükleme: bellek bütçeli paralel çalıştırma ve güvenli önbellek yazımı."""
import numpy as np
import trimesh

from slaprep import io as sio


def _kit(tmp_path, n=4):
    kit = tmp_path / "kit"
    kit.mkdir()
    for i in range(n):
        m = trimesh.creation.box(extents=[10 + i, 10, 10])
        m.apply_translation([30 * i, 0, 0])
        m.export(kit / f"p{i}.stl")
    return kit


def test_load_with_tiny_memory_budget(tmp_path, monkeypatch):
    """Boş bellek hiçbir işe yetmese bile (bütçe ~0) işler tek tek çalışıp bitmeli."""
    monkeypatch.setattr(sio, "_free_memory", lambda: 1.0)
    parts = sio.load_kit(_kit(tmp_path))
    assert sorted(p.name for p in parts) == ["p0", "p1", "p2", "p3"]
    assert all(abs(p.volume - p.lite.volume) < 1e-6 for p in parts)
    cache = tmp_path / "kit" / "_slaprep" / "cache"
    assert not list(cache.glob("*.tmp.npz"))                     # geçici dosya kalmadı


def test_exclude_matches_nested_folder(tmp_path):
    """--exclude alt-alt klasörü de tanır (Chimchar: BASE\\OUISTICRAM ikinci dildeki kaide seti)."""
    kit = tmp_path / "kit"
    for rel in ("BASE/CHIMCHAR/c.stl", "BASE/OUISTICRAM/o1.stl", "BASE/OUISTICRAM/o2.stl", "BODY/b.stl"):
        f = kit / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        trimesh.creation.box(extents=[5, 5, 5]).export(f)
    parts = sio.load_kit(kit, exclude=["ouisticram"])
    assert sorted(p.name for p in parts) == ["b", "c"]


def test_repair_fixes_inconsistent_winding():
    """Su geçirmez ama bazı yüzleri ters (Godfrey lion_mane gibi): boolean'a uygun değil → onarım düzeltmeli."""
    m = trimesh.creation.icosphere(subdivisions=3, radius=10.0)
    f = m.faces.copy()
    f[:40] = f[:40, ::-1]                                        # 40 üçgeni ters çevir
    bad = trimesh.Trimesh(m.vertices, f, process=False)
    assert bad.is_watertight and not bad.is_winding_consistent and not sio.manifold_ok(bad)
    fixed, method = sio._try_repair(bad)
    assert fixed is not None and sio.manifold_ok(fixed), method
    assert abs(abs(fixed.volume) - m.volume) / m.volume < 0.01


def test_fidelity_detects_lost_geometry():
    """Onarım sadakati: parçanın bir kısmını silen 'onarım' (pymeshfix, Hulkbuster torso) kaybolan yüzey olarak
    görünmeli; birebir aynı mesh'te kayıp/ek sıfır olmalı."""
    a = trimesh.creation.box(extents=[40, 10, 10])
    lost, added = sio.repair_fidelity(a, a)
    assert lost < 0.001 and added < 0.001
    half = trimesh.creation.box(extents=[20, 10, 10])
    half.apply_translation([-10, 0, 0])                          # kutunun yarısı "silinmiş"
    lost, added = sio.repair_fidelity(a, half)
    assert lost > 0.3


def test_large_opening_is_not_capped():
    """Büyük açıklığı olan kabuk (ağzı açık kase): kapatmak yüzeyin büyük bir kısmı kadar uydurma kapak ekler —
    onarım reddedilir, parça olduğu gibi (dolu) basılır. Küçük delik ise kayıpsız doldurulur."""
    s = trimesh.creation.icosphere(subdivisions=4, radius=10.0)
    bowl = s.submesh([np.nonzero(s.triangles_center[:, 2] < 3.0)[0]], append=True)    # üstü açık
    fixed, method = sio._try_repair(bowl)
    assert fixed is None, method
    small = s.submesh([np.nonzero(np.linalg.norm(s.triangles_center - [0, 0, 10], axis=1) > 1.0)[0]], append=True)
    fixed, method = sio._try_repair(small)
    assert fixed is not None and fixed.is_watertight, method
    assert sio.repair_fidelity(small, fixed)[0] < 0.01


def test_repair_accepts_pinched_edge():
    """İki yüzeyin tek kenarda değdiği sculpt (Yhorm Weapon: 26 tane 4 yüzlü kenar): trimesh 'su geçirmez değil' der
    ama boolean motoru için kapalıdır; küçük delik doldurulup onarım kabul edilmeli (eskiden pymeshfix'e kalıp 10 dk
    sınırına takılıyor, parça dolu basılıyordu)."""
    a = trimesh.creation.box(extents=[10, 10, 10])
    b = a.copy()
    b.apply_translation([10, 10, 0])                              # a ile tek bir kenarı paylaşıyor
    a = trimesh.Trimesh(*trimesh.remesh.subdivide_to_size(a.vertices, a.faces, 1.0))
    b = trimesh.Trimesh(*trimesh.remesh.subdivide_to_size(b.vertices, b.faces, 1.0))
    m = trimesh.util.concatenate([a, b])
    m.merge_vertices()
    m = trimesh.Trimesh(m.vertices, m.faces[2:], process=True)    # küçük bir delik
    assert not m.is_watertight
    fixed, method = sio._try_repair(m)
    assert fixed is not None and sio.manifold_ok(fixed), method
    assert sio.repair_fidelity(m, fixed)[0] < 0.01


def test_micro_repair_removes_fin_and_fills_tiny_holes():
    """Yhorm omuz: 878 bin yüzde sadece ≤ 1 mm'lik delikler ve 3 yüzlü kenar "kanatları" vardı; pymeshfix şekli
    bozuyor (%2.3 kayıp), delik doldurma kanat yüzünden takılıyordu → parça dolu basılıyordu. Mikro onarım kanadı
    silip delikleri kayıpsız kapatmalı."""
    s = trimesh.creation.icosphere(subdivisions=4, radius=10.0)
    a, b, c = s.faces[100]
    # kanat (a, b, e): iki mevcut kenarı 3 yüzlü yapar, (b, e) sarkan açık kenar — Yhorm'daki imza
    d = [x for x in next(f for f in s.faces if a in f and b in f and c not in f) if x not in (a, b)][0]
    e = next(x for x in s.vertex_neighbors[a] if x not in (b, c, d))
    hole = np.argsort(np.linalg.norm(s.triangles_center - s.triangles_center[3000], axis=1))[:4]  # 6 köşeli delik
    keep = np.ones(len(s.faces), bool)
    keep[hole] = False
    m = trimesh.Trimesh(s.vertices, np.vstack([s.faces[keep], [[a, b, e]]]), process=False)
    _, cnt = np.unique(np.sort(m.edges, axis=1), axis=0, return_counts=True)
    assert (cnt == 3).sum() == 2 and not sio.manifold_ok(m)
    fixed, method = sio._try_repair(m)
    assert fixed is not None and sio.closed(fixed) and "mikro" in method, method
    assert sio.repair_fidelity(m, fixed)[0] < 0.01


def test_volume_estimate_open_mesh():
    """Onarılamayan (açık) parçanın hacmi dışbükey zarftan değil işaretli hacimden: iki ayrı küre açık kaldıysa
    zarf aradaki boşluğu da sayar (Griffith kafası 8.9 ↔ 37.7 ml)."""
    a = trimesh.creation.icosphere(subdivisions=3, radius=5.0)
    b = a.copy()
    b.apply_translation([30, 0, 0])
    m = trimesh.util.concatenate([a, b])
    m = m.submesh([np.arange(len(m.faces) - 3)], append=True)     # 3 üçgen eksik: su geçirmez değil
    assert not m.is_watertight
    v = sio.volume_estimate(m)
    assert abs(v - 2 * a.volume) / (2 * a.volume) < 0.05 and v < 0.5 * m.convex_hull.volume


def test_call_with_timeout_kills_slow_job():
    """pymeshfix C kodu Python'dan kesilemez: büyük parçada ayrı süreçte çalışır, süre dolunca süreç öldürülür
    (Griffith merged_base 40 dakikada bitmemişti)."""
    import time

    import pytest

    t0 = time.time()
    with pytest.raises(TimeoutError):
        sio._call_with_timeout(time.sleep, (30,), 2.0)
    assert time.time() - t0 < 20
    assert sio._call_with_timeout(abs, (-3,), 30.0) == 3


def test_to_manifold_repairs_or_raises_valueerror():
    """Hazırlık bu ValueError'u yakalayıp parçayı dolu bırakır; onarım açıkken ters yüzlü mesh kurtarılır."""
    import pytest

    from slaprep.hollow import to_manifold

    m = trimesh.creation.icosphere(subdivisions=2, radius=5.0)
    f = m.faces.copy()
    f[:10] = f[:10, ::-1]
    flipped = trimesh.Trimesh(m.vertices, f, process=False)
    with pytest.raises(ValueError):
        to_manifold(flipped)                                      # onarımsız: hata
    assert to_manifold(flipped, repair=True).volume() > 0         # onarımla: geçerli


def test_binary_stl_with_wrong_triangle_count(tmp_path):
    """Magics 'COLOR=… MATERIAL=…' başlıklı, sayı alanı yanlış ikili STL (ZEZ Hulkbuster Base_2): trimesh boş okur;
    dosya boyutuna göre okunmalı (CHITUBOX gibi)."""
    from slaprep.scan import stl_bounds

    m = trimesh.creation.icosphere(subdivisions=2, radius=10.0)
    tri = m.vertices[m.faces].astype(np.float32)
    dt = np.dtype([("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec = np.zeros(len(tri), dt)
    rec["n"] = m.face_normals
    rec["v"] = tri
    head = b"COLOR=\x7f\x7f\x7f MATERIAL=\x7f\x7f\x7f".ljust(80, b" ")
    f = tmp_path / "Base_2.stl"
    with open(f, "wb") as fh:
        fh.write(head + np.uint32(len(tri) - 20).tobytes() + rec.tobytes())   # sayı alanı 20 eksik
    mesh, note = sio.load_mesh_robust(f)
    assert len(mesh.faces) == len(m.faces) and mesh.is_watertight
    assert "uyuşmuyor" in note
    assert stl_bounds(f).triangles == len(m.faces)


def test_savez_atomic_replaces(tmp_path):
    f = tmp_path / "a.npz"
    sio._savez_atomic(f, x=np.arange(3))
    sio._savez_atomic(f, x=np.arange(5))
    assert np.load(f)["x"].tolist() == [0, 1, 2, 3, 4]
    assert [p.name for p in tmp_path.iterdir()] == ["a.npz"]
