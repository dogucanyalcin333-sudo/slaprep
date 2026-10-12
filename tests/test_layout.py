"""Plate yerleşimi: çakışma yok, aralık ≥ gap, plate sınırları içinde."""
import numpy as np
import trimesh

from slaprep.layout import TimeModel, _compact_heights, _pack_once, _Part, pack, placement_transform


def test_pack_single_part():
    """Tek parçalı grup (ayrı plate'e konan şişe, tek parçalı alt klasör) çökmeden dizilir."""
    lay = pack({"Bottle": trimesh.creation.box(extents=[30, 30, 60])}, (223, 126), 3.0)
    assert lay.n_plates == 1 and not lay.unplaceable


def _placed(items, lay):
    out = {}
    for n, p in lay.placements.items():
        m = items[n].copy()
        m.apply_transform(placement_transform(p))
        out[n] = m
    return out


def _min_gap(a: trimesh.Trimesh, b: trimesh.Trimesh) -> float:
    """İki dışbükey kutunun XY izdüşümleri arası en kısa mesafe (örnekleme ile)."""
    pa, _ = trimesh.sample.sample_surface(a, 4000, seed=0)
    pb, _ = trimesh.sample.sample_surface(b, 4000, seed=1)
    from scipy.spatial import cKDTree

    d, _ = cKDTree(pb[:, :2]).query(pa[:, :2])
    return float(d.min())


def test_pack_boxes_no_overlap():
    rng = np.random.default_rng(0)
    items = {}
    for i in range(14):
        e = rng.uniform([10, 8, 5], [60, 40, 30])
        m = trimesh.creation.box(extents=e)
        m.apply_transform(trimesh.transformations.rotation_matrix(rng.uniform(0, np.pi), [0, 0, 1]))
        m.apply_translation([0, 0, -m.bounds[0][2]])
        items[f"p{i}"] = m
    lay = pack(items, (223, 126), 3.0)
    assert not lay.unplaceable
    placed = _placed(items, lay)
    for n, m in placed.items():
        assert m.bounds[0][0] >= -111.5 + 2.4 and m.bounds[1][0] <= 111.5 - 2.4, n
        assert m.bounds[0][1] >= -63 + 2.4 and m.bounds[1][1] <= 63 - 2.4, n
    names = list(placed)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if lay.placements[a].plate != lay.placements[b].plate:
                continue
            assert _min_gap(placed[a], placed[b]) >= 2.4, (a, b)


def _rect(w, d, h):
    """Siluet noktaları + yükseklik (pack'in kabul ettiği önceden hesaplanmış biçim)."""
    xs, ys = np.meshgrid(np.linspace(-w / 2, w / 2, 60), np.linspace(-d / 2, d / 2, 60))
    return np.column_stack([xs.ravel(), ys.ravel()]).astype(np.float32), float(h)


def test_height_compaction_moves_tall_part_to_taller_plate():
    """İlk plate: geniş kısa parça + orta boy (100 mm); uzun (150 mm) parça ikinci plate'e düşer ve yanında yer kalır.
    Orta parça uzunun yanına taşınınca ilk plate'in süresi 100 → 10 mm'ye iner (toplam 250 → 160)."""
    angles = [0.0, 90.0]
    gr = 6 + 2
    parts = [_Part("S", _rect(90, 44, 10), angles, gr), _Part("M", _rect(40, 40, 100), angles, gr),
             _Part("T", _rect(90, 44, 150), angles, gr)]
    nx = ny = 200                                               # 100 × 100 mm plate, 0.5 mm hücre
    lay = _pack_once(parts, [0, 1, 2], nx, ny, 6, gr, "bl", None, angles)
    assert lay.placements["M"].plate == lay.placements["S"].plate != lay.placements["T"].plate
    before = lay.time_proxy
    lay = _compact_heights(lay, parts, angles, "bl", gr)
    assert lay.placements["M"].plate == lay.placements["T"].plate
    assert lay.time_proxy < before and abs(lay.time_proxy - 160) < 1e-6
    assert lay.moved == 1 and lay.n_plates == 2


def test_time_goal_groups_tall_parts():
    """3 uzun + 1 orta + 2 kısa parça: yeni yerleşim uzunları bir araya toplar, eski (alan sırası, sıkıştırmasız,
    önce plate) yerleşimden belirgin şekilde kısa sürer."""
    items = {"T1": _rect(40, 88, 150), "T2": _rect(40, 88, 140), "T3": _rect(40, 88, 130),
             "M1": _rect(44, 44, 80), "S1": _rect(44, 44, 12), "S2": _rect(44, 90, 8)}
    tm = TimeModel()
    old = pack(items, (100, 100), 3.0, yaw_step=90, orders=("area",), random_orders=0, compact=False, goal="plates")
    new = pack(items, (100, 100), 3.0, yaw_step=90, goal="time", time_model=tm)
    assert new.seconds(tm) < old.seconds(tm) - 3600              # en az 1 saat kısa
    assert new.placements["T1"].plate == new.placements["T2"].plate


def test_prefer_folders_only_when_free():
    """Alt klasörlere göre ayırmak sadece süreye/plate'e mal olmuyorsa (Radahn: ayırınca +1 plate, ~4 sa)."""
    from slaprep.layout import PlateLayout, prefer_folders

    def lay(*heights):
        return PlateLayout(n_plates=len(heights), heights={i + 1: h for i, h in enumerate(heights)})

    tm = TimeModel()
    one = lay(126, 122, 109, 77, 62)                                  # tek grup: 5 plate
    sep = [lay(126, 82, 58), lay(100, 125), lay(115)]                 # klasörlere göre: 6 plate
    assert not prefer_folders(sep, one, tm, "time")
    assert not prefer_folders(sep, one, tm, "plates")
    # ayırmak bedavaysa (aynı plate sayısı, aynı yükseklikler) düzen için ayrılır
    assert prefer_folders([lay(120), lay(80)], lay(120, 80), tm, "time")


def test_l_shapes_rotations():
    """Asimetrik parçalar: yaw dönüşleri maske ile mesh'i aynı şekilde döndürmeli."""
    items = {}
    for i in range(6):
        a = trimesh.creation.box(extents=[50, 10, 5])
        b = trimesh.creation.box(extents=[10, 30, 5])
        b.apply_translation([20, 20, 0])
        m = trimesh.util.concatenate([a, b])
        m.apply_translation([0, 0, -m.bounds[0][2]])
        items[f"L{i}"] = m
    lay = pack(items, (223, 126), 3.0)
    placed = _placed(items, lay)
    names = list(placed)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if lay.placements[a].plate == lay.placements[b].plate:
                assert _min_gap(placed[a], placed[b]) >= 2.4, (a, b, lay.placements[a].yaw, lay.placements[b].yaw)
