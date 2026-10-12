"""Montaj çözücü testleri.

- Sentetik: pim/yuvalı kutular, Z sıfırlanmış → kaymalar geri bulunmalı (hızlı).
- Rango: Repaired kitinin çözülen Z kaymaları, montajlı Original kitiyle karşılaştırılır (yavaş, kit gerekir).
"""
import os
from pathlib import Path

import numpy as np
import pytest
import trimesh

from slaprep.assembly import detect_up_axis, detect_zeroed_axes, solve_assembly
from slaprep.geom import boxes_overlap
from slaprep.io import Part


def _box(lo, hi):
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    m = trimesh.creation.box(extents=hi - lo)
    m.apply_translation((lo + hi) / 2)
    return m


def _part(name, mesh):
    return Part(name=name, path=Path(name + ".stl"), lite=mesh, n_faces=len(mesh.faces), watertight=mesh.is_watertight,
                bodies=1, volume=mesh.volume, area=mesh.area, bounds=mesh.bounds.copy())


def _subdivide(m, max_edge=2.0):
    v, f = trimesh.remesh.subdivide_to_size(m.vertices, m.faces, max_edge=max_edge)
    return trimesh.Trimesh(v, f, process=True)


@pytest.fixture(scope="module")
def synthetic_kit():
    return make_synthetic_kit()


def make_synthetic_kit():
    """Y yukarı: kaide (yuvalı) → gövde (alt pim, üst yuva) → kafa (alt pim). Gerçek Z kaymaları 0, 7, 12."""
    import manifold3d as mf

    def M(m):
        return mf.Manifold(mf.Mesh(vert_properties=np.asarray(m.vertices, np.float32),
                                   tri_verts=np.asarray(m.faces, np.uint32)))

    def T(man):
        mm = man.to_mesh()
        return trimesh.Trimesh(mm.vert_properties[:, :3], mm.tri_verts, process=True)

    base = T(M(_box([0, 0, 0], [60, 20, 60])) - M(_box([25, 14, 25], [35, 20.5, 35])))
    body = T(M(_box([10, 20, 7], [50, 60, 47])) + M(_box([25.2, 14.4, 25.2], [34.8, 20.01, 34.8]))
             - M(_box([24, 54, 22], [32, 60.5, 30])))
    head = T(M(_box([15, 60, 12], [45, 85, 42])) + M(_box([24.2, 54.4, 22.2], [31.8, 60.01, 29.8])))
    truth = {"base": 0.0, "body": 7.0, "head": 12.0}
    parts = []
    for n, m in (("base", base), ("body", body), ("head", head)):
        m = _subdivide(m)
        m.apply_translation([0, 0, -m.bounds[0][2]])      # Z'yi parça başına sıfırla
        parts.append(_part(n, m))
    return parts, truth


def test_detect_axes(synthetic_kit):
    parts, _ = synthetic_kit
    assert detect_zeroed_axes(parts) == [2]
    assert detect_up_axis(parts, [2]) == (1, 1)


def test_solve_synthetic(synthetic_kit):
    parts, truth = synthetic_kit
    asm, _, _ = solve_assembly(parts)
    assert asm.mode == "kısmi"
    # mutlak Z konumları (min z) gerçeğe eşit olmalı: kaide min z = 0 hem gerçekte hem çözümde
    for p in parts:
        z = p.bounds[0][2] + asm.offsets[p.name][2]
        assert abs(z - truth[p.name]) < 0.25, (p.name, z, truth[p.name])
    assert asm.max_penetration < 0.5
    assert abs(asm.height({p.name: p for p in parts}) - 85.0) < 0.01


def test_centered_parts_are_unknown_and_all_kept():
    """Her parça kendi başına orijine ortalanmış (Radahn 1/9 gibi): montaj yok, hiçbir parça alternatif sayılıp
    dışlanmamalı; v1/v2 adları sadece 'olası alternatif' olarak bildirilir (doğrulanmamış, hepsi basılır)."""
    parts = []
    for i, (name, group, ext) in enumerate([("Torso", "A", [40, 30, 50]), ("Legs", "A", [36, 30, 45]),
                                            ("Head_v1", "A", [20, 22, 25]), ("Head_v2", "B", [21, 22, 24]),
                                            ("Hair", "B", [38, 28, 48])]):
        m = _subdivide(_box([-ext[0] / 2, -ext[1] / 2, 0], [ext[0] / 2, ext[1] / 2, ext[2]]), 4.0)
        p = _part(name, m)
        p.group = group
        parts.append(p)
    asm, _, _ = solve_assembly(parts)
    assert asm.mode in ("otomatik", "bilinmiyor")
    # şüphelenilen alternatifler dışında hiçbir parça dışlanmaz
    dropped = {p.name for p in parts} - set(asm.selected)
    assert dropped <= {"Head_v1", "Head_v2"} and len(dropped) <= 1
    assert any(set(g.members) == {"Head_v1", "Head_v2"} for g in asm.variant_groups)


def test_scale_reference_pillar_is_not_printed():
    """Stüdyonun ölçek çubuğu (Lucas Perez HEIGHT_PILLAR: düz kutu, figüre değmiyor) basılmaz ve boya katılmaz;
    figüre değmeyen ama organik (prizma olmayan) küçük parça (sigara gibi) referans sayılmaz."""
    base = _subdivide(_box([0, 0, 0], [60, 10, 60]))
    body = _subdivide(_box([10, 10, 10], [50, 60, 50]))
    head = _subdivide(_box([20, 60, 20], [40, 80, 40]))
    pillar = _box([-30, 10, 0], [-25, 90, 5])                      # 12 üçgen, 80 mm, kimseye değmiyor
    cig = trimesh.creation.icosphere(subdivisions=2, radius=2.0)
    cig.apply_translation([30, 75, 45])                           # kafanın önünde, değmiyor
    parts = [_part(n, m) for n, m in (("base", base), ("body", body), ("head", head), ("HEIGHT_PILLAR", pillar),
                                       ("cig", cig))]
    asm, _, _ = solve_assembly(parts, up_override="y")
    assert asm.mode == "tam"
    assert asm.reference_parts == ["HEIGHT_PILLAR"]
    assert "HEIGHT_PILLAR" not in asm.selected and "cig" in asm.selected
    assert abs(asm.height({p.name: p for p in parts}) - 80.0) < 0.01     # çubuğun 90 mm'lik tepesi sayılmaz


def test_variant_chosen_by_fitting_part():
    """İki gövde sürümü aynı yerde (alternatif): kanatlı sürümde kanat pimi için yuva var. Kanat yuvaya oturuyor,
    düz gövdeye gömülüyor → kanatlı sürüm seçilmeli (Arlecchino: Torso / TorsoForWings + WingBase)."""
    import manifold3d as mf

    def M(m):
        return mf.Manifold(mf.Mesh(vert_properties=np.asarray(m.vertices, np.float32),
                                   tri_verts=np.asarray(m.faces, np.uint32)))

    def T(man):
        mm = man.to_mesh()
        return trimesh.Trimesh(mm.vert_properties[:, :3], mm.tri_verts, process=True)

    base = _box([0, 0, 0], [60, 10, 60])
    plain = _box([10, 10, 10], [50, 60, 50])
    slotted = T(M(plain) - M(_box([24, 30, 44], [36, 42, 50.5])))       # arkada kanat pimi yuvası
    wing = T(M(_box([10, 25, 50], [50, 47, 58])) + M(_box([24.3, 30.3, 44.3], [35.7, 41.7, 50.01])))
    parts = [_part(n, _subdivide(m, 2.0)) for n, m in (("base", base), ("Torso", plain),
                                                        ("TorsoForWings", slotted), ("Wing", wing))]
    asm, _, _ = solve_assembly(parts, up_override="y")
    grp = [g for g in asm.variant_groups if set(g.members) == {"Torso", "TorsoForWings"}]
    assert grp and grp[0].selected == ["TorsoForWings"], asm.variant_groups
    assert "Wing" in asm.selected and "Torso" not in asm.selected


def test_up_axis_from_stability():
    """Tabanın düz yüzü yokken (kıvrımlı ayaklı kanepe: Astarion) ya da her yüzü düz bir kutu yandaki yüksek bir
    kapakla (açık sandık: Frieren) "en alttaki düz yüzey" kuralı yanlış ekseni seçiyordu. Montaj devrilmeden
    durabildiği yönde durur: yukarı +Y olmalı."""
    def ball(c, r):
        m = trimesh.creation.icosphere(subdivisions=4, radius=r)
        m.apply_translation(c)
        return m
    # kanepe: 4 küresel ayak (düz taban yok), üstünde yuvarlak oturak ve arka; en uzun eksen X (eski son çare)
    legs = trimesh.util.concatenate([ball([x, 3, z], 3) for x in (-40, 40) for z in (-20, 20)])
    seat = ball([0, 0, 0], 1.0)
    seat.apply_scale([48, 9, 26])
    seat.apply_translation([0, 14, 0])
    back = ball([0, 0, 0], 1.0)
    back.apply_scale([46, 22, 14])
    back.apply_translation([0, 38, -14])
    parts = [_part(n, m) for n, m in (("legs", legs), ("seat", seat), ("back", back))]
    k, s = detect_up_axis(parts, [])
    assert (k, s) == (1, 1), (k, s)


def test_separate_frame_folders_solved_apart():
    """Alt klasörler kendi koordinatlarında (Maomao dioraması): kaide klasörünün parçaları kendi içinde oturuyor,
    figür klasörü orijinde onların içinden geçiyor. Klasörler ayrı montajlanmalı (aralarında "üst üste = alternatif"
    kararı yok, figür parçası basılacaklardan çıkmıyor), yan yana konmalı ve kullanıcı uyarılmalı."""
    base = [_part(n, _subdivide(m)) for n, m in (("Base1", _box([0, 0, 3], [60, 10, 63])),
                                                  ("Base2", _box([60, 0, 3], [120, 10, 63])),
                                                  ("Fence", _box([0, 10, 3], [10, 35, 63])))]
    fig = [_part(n, _subdivide(m)) for n, m in (("Legs", _box([2, 0, 5], [12, 40, 25])),      # çitin içinden geçiyor
                                                 ("Body", _box([2, 40, 5], [12, 70, 25])))]
    for p in base:
        p.group = "Base"
    for p in fig:
        p.group = "Figure"
    asm, _, _ = solve_assembly(base + fig, up_override="y")
    assert "Legs" in asm.selected and "Fence" in asm.selected, asm.variant_groups
    assert any("ayrı koordinatlarda" in w for w in asm.warnings), asm.warnings
    P = {p.name: p for p in base + fig}
    assert all(asm.placed[n] for n in P)
    # figür kümesi kaidenin yanına kondu: artık kaide parçalarına gömülmüyor; boy en uzun kümeden (figür 70 mm)
    legs = P["Legs"].bounds + asm.offsets["Legs"]
    fence = P["Fence"].bounds + asm.offsets["Fence"]
    assert not boxes_overlap(legs, fence, range(3))
    assert abs(asm.height(P) - 70.0) < 0.01


def test_separate_frame_figure_seated_by_user_join():
    """Ayrı koordinatlı klasörler: kullanıcı figürün ayağını kaide parçasına bağlayınca (--join Base1:Legs) figür
    kümesi bütün olarak kaidenin üstüne oturur (yan yana konmaz), çite gömülmez, boy kaide + figür olur."""
    base = [_part(n, _subdivide(m)) for n, m in (("Base1", _box([0, 0, 3], [60, 10, 63])),
                                                  ("Base2", _box([60, 0, 3], [120, 10, 63])),
                                                  ("Fence", _box([0, 10, 3], [10, 35, 63])))]
    fig = [_part(n, _subdivide(m)) for n, m in (("Legs", _box([2, 0, 5], [12, 40, 25])),
                                                 ("Body", _box([2, 40, 5], [12, 70, 25])))]
    for p in base:
        p.group = "Base"
    for p in fig:
        p.group = "Figure"
    asm, _, _ = solve_assembly(base + fig, up_override="y", join=[("Base1", "Legs")])
    P = {p.name: p for p in base + fig}
    assert any("bağlantınızla birleştirildi" in w for w in asm.warnings), asm.warnings
    legs = P["Legs"].bounds + asm.offsets["Legs"]
    assert abs(legs[0][1] - 10.0) < 1.3, legs                       # kaidenin üst yüzüne değiyor
    assert np.allclose(asm.offsets["Body"] - asm.offsets["Legs"], 0.0)   # küme rijit taşındı
    assert not boxes_overlap(legs, P["Fence"].bounds + asm.offsets["Fence"], range(3), margin=-0.5)
    assert abs(asm.height(P) - 80.0) < 1.5
    assert asm.links["Legs"]["kind"] == "kullanıcı" and asm.links["Legs"]["to"] == "Base1"
    # "Başka konum dene": farklı bir yer, yine kaidenin üstünde
    asm2, _, _ = solve_assembly(base + fig, up_override="y", join=[("Base1", "Legs", 1)])
    legs2 = P["Legs"].bounds + asm2.offsets["Legs"]
    assert abs(legs2[0][1] - 10.0) < 1.3 and np.linalg.norm(asm2.offsets["Legs"] - asm.offsets["Legs"]) > 4.0


def test_version_sets_by_name():
    """Aynı figürün sürümleri adla ayrışıyor (Atsu: Atsu_Split_* montajlı, Atsu_Merge_Body ve Atsu_BUST orijinde):
    en çok parçalı takım seçilir, diğerleri basılmaz; --variant ile başka takım seçilebilir."""
    split = [_part(n, _subdivide(m)) for n, m in (("Atsu_Split_Base", _box([0, 0, 0], [60, 10, 60])),
                                                   ("Atsu_Split_Body", _box([10, 10, 10], [50, 60, 50])),
                                                   ("Atsu_Split_Head", _box([20, 60, 20], [40, 80, 40])))]
    merged = _part("Atsu_Merge_Body", _subdivide(_box([-25, 0, -25], [25, 70, 25])))
    bust = [_part(n, _subdivide(m)) for n, m in (("Atsu_BUST", _box([-15, 0, -15], [15, 30, 15])),
                                                  ("Atsu_BUST_Stand", _box([-10, -8, -10], [10, 0, 10])))]
    parts = split + [merged] + bust
    asm, _, _ = solve_assembly(parts, up_override="y")
    assert sorted(asm.selected) == ["Atsu_Split_Base", "Atsu_Split_Body", "Atsu_Split_Head"], asm.selected
    assert asm.mode == "tam" and any("sürümü var" in w for w in asm.warnings)
    asm2, _, _ = solve_assembly(parts, up_override="y", variant_choices=["*BUST*"])
    assert sorted(asm2.selected) == ["Atsu_BUST", "Atsu_BUST_Stand"], asm2.selected


def test_named_reference_mannequin_is_set_aside():
    """Ölçek referansı bazen düz çubuk değil insan mankenidir (Jin Sakai '250mmreference', T-pozunda, figürün yanında
    yatıyor): adı referans ve figüre değmiyorsa montaja, yukarı eksenine ve boya katılmaz, basılmaz."""
    base = _subdivide(_box([0, 0, 0], [60, 10, 60]))
    body = _subdivide(_box([10, 10, 10], [50, 60, 50]))
    head = _subdivide(_box([20, 60, 20], [40, 80, 40]))
    # yatan "manken": X boyunca 250 mm, düz prizma değil (ayrıntılı yüzey)
    man = trimesh.creation.capsule(height=230.0, radius=10.0, count=[24, 24])
    man.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0]))
    man.apply_translation([200, 10, 30])
    parts = [_part(n, m) for n, m in (("base", base), ("body", body), ("head", head), ("250mmreference", man))]
    asm, _, _ = solve_assembly(parts)
    assert asm.reference_parts == ["250mmreference"], asm.warnings
    assert asm.up_axis == 1 and asm.up_sign == 1
    assert abs(asm.height({p.name: p for p in parts}) - 80.0) < 0.01


def test_assembly_clearance_gap_counts_as_contact():
    """Stüdyo parçalar arasına montaj boşluğu bırakmış (ZEZ Hulkbuster: 0.4–1.2 mm): tam montajda bunlar temas
    sayılmalı, 'hiçbir parçaya temas etmiyor' denmemeli; 3 mm uzaktaki parça ise yine uyarılmalı."""
    base = _subdivide(_box([0, 0, 0], [60, 10, 60]))
    body = _subdivide(_box([10, 10.7, 10], [50, 60, 50]))           # kaideyle arasında 0.7 mm boşluk
    head = _subdivide(_box([20, 61.0, 20], [40, 80, 40]))           # gövdeyle arasında 1.0 mm
    hat = _subdivide(_box([22, 83.0, 22], [38, 90, 38]))            # kafadan 3 mm uzakta: gerçekten değmiyor
    parts = [_part(n, m) for n, m in (("base", base), ("body", body), ("head", head), ("hat", hat))]
    asm, _, _ = solve_assembly(parts, up_override="y")
    assert asm.mode == "tam"
    w = " ".join(asm.warnings)
    assert "hat" in w and "body" not in w and "head" not in w, asm.warnings


def test_base_at_origin_is_printed_when_figure_sits_on_it():
    """Kaide dosyada orijinde başlıyor (stüdyolarda çok yaygın: XY'de ortalı, tabanı 0) ve üstündeki gövde de
    XY'de ortalı, tabanı orijine yakın. İkisi de "orijinde unutulmuş" adayı görünür; ama birbirlerine temiz
    oturuyorlar: hiçbiri montaj dışı sayılmamalı ve kaide basılmalı.

    Eski davranış: kaidenin tek temiz teması gövdeyleydi, gövde de aday olduğu için kanıt sayılmıyor, kaide
    "kitin birleşik sürümü" diye basılmıyordu (figür kaidesiz basılırdı) — sessiz ve pahalı bir hata.
    """
    base = _subdivide(_box([-30, -20, 0], [30, 20, 10]))             # orijinde, XY ortalı
    body = _subdivide(_box([-15, -11, 10], [15, 11, 60]))            # kaidenin üstünde, XY ortalı
    head = _subdivide(_box([-12, -12, 60], [12, 12, 84]))
    arm_l = _subdivide(_box([-26, -6, 40], [-15, 6, 51]))
    arm_r = _subdivide(_box([15, -6, 40], [26, 6, 51]))
    parts = [_part(n, m) for n, m in (("Base", base), ("Torso", body), ("Head", head),
                                      ("Arm_L", arm_l), ("Arm_R", arm_r))]
    asm, _, _ = solve_assembly(parts)
    assert asm.mode == "tam"
    assert "Base" in asm.selected, f"kaide montaj dışı bırakıldı: {asm.warnings}"
    assert asm.placed["Base"]
    byname = {p.name: p for p in parts}
    assert abs(asm.height(byname) - 84.0) < 1.0           # kaide dahil boy
    assert not any("birleşik / alternatif" in w for w in asm.warnings), asm.warnings


def _ad_plate(x0, x1, y0, y1, z0, z1, letters=8):
    """Dağıtıcı reklam plakası gibi: harf başına bir düşük üçgenli kutu, düz ve geniş."""
    xs = np.linspace(x0, x1, letters + 1)
    m = trimesh.util.concatenate([_box([xs[i] + 2, y0, z0], [xs[i + 1] - 2, y1, z1]) for i in range(letters)])
    p = _part("Torso", m)                      # gerçek örnekte reklam dosyasının adı 'Torso.STL' idi
    p.bodies = letters
    return p


def test_ad_plate_is_ignored():
    """Reklam/filigran plakası (figürden geniş, düz, seyrek üçgen, kimseye değmiyor) montaja, yukarı eksenine ve
    boya katılmamalı, basılmamalı; figürün kendi düz ama değen parçası (kaide plakası) ayrılmamalı."""
    base = _subdivide(_box([0, 0, 0], [60, 10, 60]))
    body = _subdivide(_box([10, 10, 10], [50, 60, 50]))
    head = _subdivide(_box([20, 60, 20], [40, 80, 40]))
    plinth = _box([-5, -3, -5], [65, 0, 65])                    # az üçgenli düz kaide plakası ama kaideye değiyor
    parts = [_part(n, m) for n, m in (("base", base), ("body", body), ("head", head), ("plinth", plinth))]
    parts.append(_ad_plate(-300, 300, 0, 40, -120, -105))
    second = _ad_plate(-380, 380, -20, 60, -118, -104, letters=12)    # ikinci reklam plakası ilkiyle üst üste
    second.name = "Set"
    parts.append(second)
    # üçüncü plaka sahnenin içinden geçiyor ve gövdenin yüzüne değiyor (Maomao dioraması): yine de reklam
    cut = _ad_plate(-300, 300, 30, 40, 9, 11)
    cut.name = "pack"
    parts.append(cut)
    asm, _, _ = solve_assembly(parts)
    assert asm.junk_parts == ["Set", "Torso", "pack"], asm.warnings
    assert asm.mode == "tam" and asm.up_axis == 1
    assert "Torso" not in asm.selected and "plinth" in asm.selected
    assert abs(asm.height({p.name: p for p in parts}) - 83.0) < 0.01


def test_separate_group_is_reported():
    """Ana montaja değmeyen ama kendi içinde birleşik grup (Chimchar: ikinci dilde harfli ikinci kaide) bildirilmeli;
    kök parça figürün bulunduğu gruptan seçilmeli (ikinci kaide daha büyük olsa bile)."""
    base = _subdivide(_box([0, 0, 0], [60, 10, 60]))
    body = _subdivide(_box([10, 10, 10], [50, 60, 50]))
    head = _subdivide(_box([20, 60, 20], [40, 80, 40]))
    base2 = _subdivide(_box([200, 0, 0], [300, 12, 100]), 4.0)     # yan tarafta, gövdeden büyük ikinci kaide
    letter = _subdivide(_box([220, 12, 20], [230, 20, 30]))       # üstünde harf
    parts = [_part(n, m) for n, m in (("base", base), ("body", body), ("head", head), ("base2", base2),
                                       ("letter", letter))]
    asm, _, _ = solve_assembly(parts, up_override="y")
    assert asm.mode == "tam"
    assert any("ayrı grup" in w and "base2" in w and "letter" in w for w in asm.warnings), asm.warnings
    assert asm.root == "body"
    # figür kaideye gömülü oturuyor (temas çifti kabul edilmez) ama kaidenin içinde: ayrı grup DEĞİL (Frieren)
    sunk = _subdivide(_box([5, -5, 5], [55, 12, 55]))             # kaide gövdeye 2 mm gömülü (yukarıda: y 10'a kadar)
    rock = _subdivide(_box([55, -5, 5], [70, 5, 55]))              # kaideye yandan değen taş: kaide grubu 2 parça
    parts2 = [_part(n, m) for n, m in (("plinth", sunk), ("rock", rock), ("body", body), ("head", head))]
    asm2, _, _ = solve_assembly(parts2, up_override="y")
    assert not any("ayrı grup" in w for w in asm2.warnings), asm2.warnings


# Yerel kit yolları: SLAPREP_RANGO / SLAPREP_RANGO_ORIG ortam değişkenleriyle verilir.
# Verilmezse (ya da klasör yoksa) bu test atlanır.
RANGO = Path(os.environ.get("SLAPREP_RANGO", r"E:\3d\STL Modeller\Zenith_Rango_Repaired\Rango_STL_Repaired"))
RANGO_ORIG = Path(
    os.environ.get("SLAPREP_RANGO_ORIG", r"E:\3d\STL Modeller\Zenith_Rango_STL_Original\Rango_STL_Original")
)


@pytest.mark.rango
@pytest.mark.skipif(not (RANGO.exists() and RANGO_ORIG.exists()), reason="Rango kitleri yok")
def test_rango_matches_original():
    from slaprep.io import load_kit
    from slaprep.scan import stl_bounds

    parts = load_kit(RANGO)
    asm, _, _ = solve_assembly(parts)
    assert asm.mode == "kısmi" and asm.zeroed_axes == [2] and asm.up_axis == 1
    assert {"Head_V1", "Head_V2"} in [set(g.members) for g in asm.variant_groups]
    byname = {p.name: p for p in parts}
    assert abs(asm.height(byname) - 300.2) < 2.0
    # Original'daki gerçek Z konumu ile karşılaştır (ortak öteleme farkı hariç)
    errs = {}
    for p in parts:
        if not asm.placed[p.name]:
            continue
        truth = stl_bounds(RANGO_ORIG / p.path.name).bounds[0][2]
        errs[p.name] = p.bounds[0][2] + asm.offsets[p.name][2] - truth
    gauge = np.median(list(errs.values()))
    bad = {n: round(e - gauge, 2) for n, e in errs.items() if abs(e - gauge) > 0.5}
    assert not bad, f"0.5 mm'den büyük Z hatası: {bad}"
    assert asm.max_penetration < 0.75
