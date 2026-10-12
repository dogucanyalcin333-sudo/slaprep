import numpy as np
import pytest
import trimesh

from slaprep.orient import MAX_PEEL_GROWTH, interior_samples, max_layer_area, orient_part, select_alternatives

BUILD = (218.0, 123.0, 260.0)


def _row(deg, score, height, peel, unvented=0, rect=(40.0, 40.0)):
    t = np.radians(deg)
    return dict(d=np.array([np.sin(t), 0.0, -np.cos(t)]), score=score, height=height, peel=peel, unvented=unvented,
                fits=True, rect=(*rect, 0.0))


def test_layer_area_grid_aligned():
    """Izgaraya hizalı yönde iki katman tek bölmeye düşmemeli (kayan nokta yuvarlaması kesiti 2 kat gösteriyordu)."""
    box = trimesh.creation.box(extents=(20.0, 10.0, 6.0))
    ip, g = interior_samples(box)
    assert abs(max_layer_area(ip[:, 2], g) - 200.0) < 0.15 * 200.0
    assert max_layer_area(np.zeros(0), g) == 0.0


def test_time_alternative_keeps_peel_safe():
    """Süre için önerilen daha alçak yön, katman kesitini MAX_PEEL_GROWTH katından fazla büyütmemeli (Rango pelerini:
    41 mm'lik yön kesiti 4.7 → 10.5 cm²'ye çıkarıyordu); kabul edilebilir alçak yön yine önerilir."""
    best = _row(0, 1.0, 73.0, 470.0)
    flat = _row(60, 0.65, 41.0, 1050.0)
    ok = _row(30, 0.87, 59.0, 660.0)
    sel = select_alternatives([best, ok, flat])
    ids = [id(r) for r in sel]
    assert sel[0] is best and id(ok) in ids and id(flat) not in ids
    assert all(r["peel"] <= MAX_PEEL_GROWTH * best["peel"] for r in sel)


def test_alternative_never_adds_unvented_cups():
    best = _row(0, 1.0, 80.0, 400.0, unvented=1)
    worse = _row(40, 0.9, 50.0, 420.0, unvented=3, rect=(20.0, 20.0))
    assert all(r is not worse for r in select_alternatives([best, worse]))


def test_front_direction_convention():
    """Ön yön: Z yukarı → −Y, Y yukarı → +Z; dosya baş aşağıysa (yukarı −Y: Atsu, Griffith) ön de ters
    ('önden' görünüm eskiden sırtı gösteriyordu); kullanıcının verdiği yön önceliklidir."""
    from slaprep.render import default_views, parse_axis

    assert np.allclose(default_views(2, 1)["front"][1], [0, -1, 0])
    assert np.allclose(default_views(1, 1)["front"][1], [0, 0, 1])
    assert np.allclose(default_views(1, -1)["front"][1], [0, 0, -1])
    assert np.allclose(default_views(1, 1, parse_axis("-x"))["front"][1], [-1, 0, 0])
    assert np.allclose(default_views(1, 1, parse_axis("y"))["front"][1], [0, 0, 1])   # yukarıya paralel: yok sayılır
    assert parse_axis("w") is None and parse_axis(None) is None


def test_visibility_weights():
    from slaprep.joints import visibility_weights

    n = np.array([[0, -1.0, 0], [0, 1.0, 0], [1.0, 0, 0], [0, 0, -1.0], [0, 0.7071, -0.7071], [0, -1.0, 0]])
    hidden = np.array([False] * 5 + [True])
    w = visibility_weights(n, hidden, np.array([0, 0, 1.0]), np.array([0, -1.0, 0]))
    assert w[0] == pytest.approx(1.0) and w[1] == pytest.approx(0.2) and w[2] == pytest.approx(0.6)
    assert w[3] < 0.25 and w[4] < w[1] and w[5] == 0.0          # alt, sırt-alt, birleşme yüzeyi


def test_supports_go_to_back_not_front():
    """Ayakta ince levha (alt yüzü birleşme yeri): eğik basılınca destek ya önüne ya sırtına gelir. Görünürlük
    ağırlığıyla sırtı plate'e bakan yön seçilmeli (eski puanlama ikisini eşit sayıyordu)."""
    from slaprep.joints import visibility_weights

    slab = trimesh.creation.box(extents=(60.0, 6.0, 80.0))
    up, front = np.array([0, 0, 1.0]), np.array([0, -1.0, 0])
    hidden = slab.face_normals[:, 2] < -0.9
    vis = visibility_weights(slab.face_normals, hidden, up, front)
    ori = orient_part(slab, hidden, np.zeros(len(slab.faces), bool), up, BUILD, 5.0, 2.0, vis=vis, front=front)
    front_in_print = ori.R[:3, :3] @ front
    assert front_in_print[2] > -0.55, front_in_print               # ön yüz destek isteyecek kadar aşağı bakmıyor
    assert ori.terms["sup_vis"] < 0.2 and ori.terms["sup_low"] + ori.terms["sup_hidden"] > 0.8
    assert "destek: gizli" in ori.describe()


def test_orient_part_alternatives_respect_rules():
    plate = trimesh.creation.box(extents=(80.0, 50.0, 6.0))
    hidden = plate.face_normals[:, 0] > 0.9                     # uç yüz birleşme yüzeyi
    ori = orient_part(plate, hidden, np.zeros(len(plate.faces), bool), np.array([0, 0, 1.0]), BUILD, 5.0, 2.0)
    top = ori.alternatives[0]
    assert top is ori
    for a in ori.alternatives[1:]:
        assert a.terms["peel"] <= MAX_PEEL_GROWTH * top.terms["peel"] + 1e-6
        assert a.terms["unvented"] <= top.terms["unvented"]
