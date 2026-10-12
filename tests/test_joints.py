"""Gizli yüzey haritası testleri (sentetik pim/yuvalı kit)."""
import numpy as np
import pytest

from slaprep.assembly import solve_assembly
from slaprep.joints import compute_safe, contact_map
from test_assembly import make_synthetic_kit


@pytest.fixture(scope="module")
def maps():
    parts, _ = make_synthetic_kit()
    asm, fields, _ = solve_assembly(parts)
    P = {p.name: p for p in parts}
    m = contact_map(asm.selected, P, fields, asm.offsets, asm.up_vector, asm.base_parts, 0.4)
    for n in m:
        compute_safe(m[n], P[n].lite, 2.5)
    return P, asm, m


def _faces(P, asm, name, pred):
    mesh = P[name].lite
    c = mesh.triangles_center + asm.offsets[name]
    return pred(c, mesh.face_normals)


def test_cut_faces_hidden(maps):
    P, asm, m = maps
    # kaide üst yüzü (y=20, gövdenin altında kalan kısım) gizli, gövde dışında kalan kısmı görünür
    top = _faces(P, asm, "base", lambda c, n: (n[:, 1] > 0.99) & (abs(c[:, 1] - 20) < 0.01))
    under_body = top & _faces(P, asm, "base", lambda c, n: (c[:, 0] > 11) & (c[:, 0] < 49) & (c[:, 2] > 8) & (c[:, 2] < 46))
    outside = top & _faces(P, asm, "base", lambda c, n: (c[:, 0] < 9) | (c[:, 0] > 51))
    assert m["base"].hidden[under_body].mean() > 0.95
    assert m["base"].hidden[outside].mean() < 0.05


def test_socket_floor_hidden_and_candidate(maps):
    P, asm, m = maps
    floor = _faces(P, asm, "base", lambda c, n: (n[:, 1] > 0.99) & (abs(c[:, 1] - 14) < 0.01))
    assert floor.any()
    assert m["base"].hidden[floor].mean() > 0.95      # pim ucuyla arasında 0.4 mm boşluk var
    assert m["base"].pin[floor].mean() < 0.05


def test_pins_detected(maps):
    P, asm, m = maps
    pin_side = _faces(P, asm, "body", lambda c, n: (c[:, 1] < 19.5) & (abs(n[:, 1]) < 0.1))
    assert pin_side.any()
    assert m["body"].pin[pin_side].mean() > 0.9
    pin_tip = _faces(P, asm, "head", lambda c, n: (n[:, 1] < -0.99) & (abs(c[:, 1] - 54.4) < 0.01))
    assert m["head"].pin[pin_tip].mean() > 0.9


def test_visible_not_hidden(maps):
    P, asm, m = maps
    head_top = _faces(P, asm, "head", lambda c, n: n[:, 1] > 0.99)
    assert m["head"].hidden[head_top].mean() == 0
    sides = _faces(P, asm, "body", lambda c, n: (abs(n[:, 0]) > 0.99) & (c[:, 1] > 25) & (c[:, 1] < 50))
    assert m["body"].hidden[sides].mean() == 0


def test_holes_only_on_mating_surfaces(maps):
    """Varsayılan: delik sadece birleşme yüzeyine. Kaide altı gizli ama dış yüzey → aday değil."""
    P, asm, m = maps
    b = m["base"]
    bottom = _faces(P, asm, "base", lambda c, n: n[:, 1] < -0.99)
    b.allowed = b.mating.copy()
    assert b.hidden[bottom].all()
    assert not b.candidate[bottom].any()
    top_under_body = _faces(P, asm, "base", lambda c, n: (n[:, 1] > 0.99) & (abs(c[:, 1] - 20) < 0.01)
                            & (c[:, 0] > 11) & (c[:, 0] < 49) & (c[:, 2] > 8) & (c[:, 2] < 46))
    assert b.candidate[top_under_body].mean() > 0.9
    compute_safe(b, P["base"].lite, 2.5)
    assert b.safe.any() and not (b.safe & bottom).any()
    b.allowed = None


def test_base_bottom_and_safe_zone(maps):
    P, asm, m = maps
    bottom = _faces(P, asm, "base", lambda c, n: n[:, 1] < -0.99)
    assert m["base"].hidden[bottom].all()
    # güvenli bölge gizli bölgenin içinde ve kenarlardan uzak
    s = m["base"].safe
    assert s.any() and not (s & ~m["base"].hidden).any()


def test_dominant_plane_with_degenerate_faces():
    """Sıfır alanlı üçgenler çoğunluktaysa (OUISTCRAM Chimchar) ağırlıklı örnekleme çökmemeli."""
    from slaprep.joints import _dominant_plane

    rng = np.random.default_rng(1)
    normals = np.tile([0.0, 0.0, 1.0], (400, 1)) + rng.normal(0, 0.01, (400, 3))
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    centers = rng.normal(0, 5, (400, 3))
    areas = np.zeros(400)
    areas[:40] = 1.0                                  # sadece 40 üçgenin alanı var (< 300 örnek)
    n, d, frac = _dominant_plane(normals, centers, areas)
    assert n[2] > 0.99 and frac > 0.9
    assert _dominant_plane(normals, centers, np.zeros(400)) is None
