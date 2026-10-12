"""Delik sayısı/çapı kuralları ve seçim."""
import numpy as np
import trimesh

from slaprep.config import Settings
from slaprep.hollow import Cavity
from slaprep.holes import HoleCandidates, choose_holes, desired_count, hole_diameters, preferred_diameter


def _cav(extents, vol_ml):
    m = trimesh.creation.box(extents=extents)
    return Cavity(m, vol_ml * 1000)


def test_count_grows_with_size():
    s = Settings()
    assert desired_count(_cav([20, 20, 20], 5), s) == 2
    assert desired_count(_cav([40, 40, 30], 50), s) == 4          # 50 ml → ceil(2.5)+1
    assert desired_count(_cav([120, 40, 40], 30), s) == 4          # uzun kavite: ceil(120/50)+1
    assert desired_count(_cav([130, 130, 40], 200), s) == 8        # üst sınır
    s.min_holes = 5
    assert desired_count(_cav([20, 20, 20], 5), s) == 5


def test_diameter_rules():
    s = Settings()
    assert hole_diameters(s) == [5.0, 4.0, 3.0, 2.0]
    assert preferred_diameter(5, s) == 3.0
    assert preferred_diameter(25, s) == 4.0
    assert preferred_diameter(150, s) == 5.0
    s.hole_auto, s.hole = False, 2.5
    assert hole_diameters(s) == [2.5, 2.0]
    assert preferred_diameter(150, s) == 2.5


def _grid_cands(spacing, n=12):
    xs = np.arange(n) * spacing
    P = np.array([[x, y, 0.0] for x in xs for y in xs])
    N = np.tile([0, 0, -1.0], (len(P), 1))
    return HoleCandidates(P, N, np.zeros(len(P), int), np.zeros(len(P), bool))


def test_choose_prefers_large_diameter_and_spreads():
    s = Settings()
    cav = _cav([100, 100, 40], 150)                                 # tercih 5 mm, hedef 8
    R = np.eye(4)
    cands = {d: _grid_cands(4.0) for d in hole_diameters(s)}
    holes, missing, plan = choose_holes(cands, [cav], R, s)
    assert not missing
    ds = {d for *_, d in holes}
    assert ds == {5.0}
    P = np.array([h[0] for h in holes])
    dd = np.linalg.norm(P[:, None] - P[None], axis=2) + np.eye(len(P)) * 1e9
    assert dd.min() >= 15.0 - 1e-9                                  # ≥ 3 × çap
    assert len(holes) >= 4


def _arch_cavity():
    """∩ biçimli kavite: iki bacak aşağı bakıyor → baskı yönünde (z yukarı) iki vantuz çanağı."""
    import manifold3d as mf

    from slaprep.hollow import from_manifold

    def box(lo, hi):
        return mf.Manifold.cube(np.subtract(hi, lo).tolist()).translate(list(lo))

    man = box([0, 0, 0], [10, 10, 40]) + box([30, 0, 0], [40, 10, 40]) + box([0, 0, 30], [40, 10, 40])
    m = from_manifold(man)
    m = trimesh.Trimesh(*trimesh.remesh.subdivide_to_size(m.vertices, m.faces, max_edge=2.0))
    return Cavity(m, abs(m.volume))


def test_basins_find_both_cups_of_arch():
    from slaprep.holes import vent_plan

    cav = _arch_cavity()
    _, cups, pockets = vent_plan(cav, np.eye(4))
    assert len(cups) == 2
    assert all(abs(b.z - 0.0) < 0.5 for b in cups)
    finite = [b.depth for b in cups if np.isfinite(b.depth)]
    assert len(finite) == 1 and abs(finite[0] - 30) < 1.0          # iki çanak z=30'da birleşiyor
    assert len(pockets) == 1                                       # tepe tek düz cep
    # ters çevrilince (∪) tek çanak, iki cep
    flip = np.diag([1.0, -1.0, -1.0, 1.0])
    cav2 = _arch_cavity()
    _, cups2, pockets2 = vent_plan(cav2, flip)
    assert len(cups2) == 1 and len(pockets2) == 2


def test_vented_pick_covers_each_cup():
    from slaprep.holes import _pick, vent_plan

    cav = _arch_cavity()
    vent = vent_plan(cav, np.eye(4))
    # adaylar: kavite yüzeyinin dışında, iç noktası kavite yüzeyi (dış duvardan gelen ışın)
    rng = np.random.default_rng(0)
    pts, fi = trimesh.sample.sample_surface(cav.mesh, 600, seed=1)
    nrm = cav.mesh.face_normals[fi]
    c = HoleCandidates(pts + nrm * 2.0, nrm, np.zeros(len(pts), int), np.zeros(len(pts), bool), pts)
    zc = c.pts[:, 2]
    sel, st = _pick(c, 0, zc, 3.0, 2, vent)
    assert st["cups"] == 2 and st["cups_vented"] == 2
    lows = sorted(c.inner[sel][:, 0][c.inner[sel][:, 2] < 3.0])
    assert len(lows) >= 2 and lows[0] < 10.5 and lows[-1] > 29.5        # iki bacağın dibinde birer delik


def test_falls_back_to_smaller_diameter_when_area_small():
    s = Settings()
    cav = _cav([60, 40, 30], 50)                                    # tercih 5 mm
    # 5 mm için sadece tek aday, 3 mm için geniş alan
    c5 = HoleCandidates(np.zeros((1, 3)), np.array([[0, 0, -1.0]]), np.zeros(1, int), np.zeros(1, bool))
    cands = {5.0: c5, 4.0: c5, 3.0: _grid_cands(3.0), 2.0: _grid_cands(3.0)}
    holes, missing, _ = choose_holes(cands, [cav], np.eye(4), s)
    assert not missing and {d for *_, d in holes} == {3.0} and len(holes) >= 2
