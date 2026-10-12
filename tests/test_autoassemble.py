"""Otomatik montajın mekanizma testleri (yapay parçalar, kit gerekmez, hızlı).

Gerçek kitlerdeki davranış tools/regress.py ile ölçülür; burada her mekanizma tek başına sınanır:
- yerleşmeyen parçalar boya katılmaz,
- alternatif-farkında ekleme (Radahn: Miquella Head_v2'ye oturur, Head_v1'e gömülür),
- tercih edilen alternatifin ve aynı klasördeki eşinin seçilmesi,
- grup (alt montaj) kenetlenmesi,
- sadeleştirmenin sapma kontrolü.
"""
from pathlib import Path

import numpy as np
import trimesh

from slaprep.assembly import Assembly, _apply_preference
from slaprep.autoassemble import assembly_grid, auto_assemble, fft_dock
from slaprep.geom import DistanceField, sample_surface
from slaprep.io import Part, simplify_checked
from slaprep.variants import VariantGroup


def _box(lo, hi):
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    m = trimesh.creation.box(extents=hi - lo)
    m.apply_translation((lo + hi) / 2)
    return m


def _part(name, mesh, folder=""):
    return Part(name=name, path=Path(folder) / (name + ".stl"), lite=mesh, n_faces=len(mesh.faces),
                watertight=mesh.is_watertight, bodies=1, volume=float(mesh.volume), area=float(mesh.area),
                bounds=mesh.bounds.copy(), group=folder)


def _mf():
    import manifold3d as mf

    def M(m):
        return mf.Manifold(mf.Mesh(vert_properties=np.asarray(m.vertices, np.float32),
                                   tri_verts=np.asarray(m.faces, np.uint32)))

    def T(man):
        mm = man.to_mesh()
        return trimesh.Trimesh(mm.vert_properties[:, :3], mm.tri_verts, process=True)
    return M, T


def _centered(meshes: dict):
    """Her parçayı kendi kutusunun merkezine taşı (montaj bilgisi olmayan kit gibi). Dönüş: parçalar, kaymalar."""
    parts, shift = {}, {}
    for n, m in meshes.items():
        m = m.copy()
        s = -(m.bounds[0] + m.bounds[1]) / 2
        m.apply_translation(s)
        parts[n], shift[n] = _part(n, m), s
    return parts, shift


def _prep(parts):
    names = list(parts)
    fields = {n: DistanceField(parts[n].lite) for n in names}
    samples = {n: sample_surface(parts[n].lite) for n in names}
    return names, fields, samples


def test_height_ignores_unplaced_parts():
    a, b, c = _box([0, 0, 0], [10, 10, 10]), _box([0, 10, 0], [10, 30, 10]), _box([0, 0, 0], [10, 80, 10])
    parts = {"a": _part("a", a), "b": _part("b", b), "c": _part("c", c)}
    asm = Assembly("otomatik", 1, 1, [], {n: np.zeros(3) for n in parts}, {"a": True, "b": True, "c": False},
                   ["a", "b", "c"], [])
    assert abs(asm.height(parts) - 30.0) < 1e-6          # c'nin yeri bilinmiyor: 80 mm sayılmamalı


def _cyl(r, y0, y1):
    m = trimesh.creation.cylinder(radius=r, height=y1 - y0, sections=48)
    m.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
    m.apply_translation([0, (y0 + y1) / 2, 0])
    return m


def test_alternative_aware_attach():
    """Gövde üstüne iki alternatif kafa (pimle yuvaya oturan silindirler): büyük HeadA (y 40–70) ve küçük, üstü
    pimli HeadB (y 40–50). Rider HeadB'nin üst pimine oturur ve HeadA'nın hacmine gömülüdür → HeadB ile eklenmeli,
    HeadB tercih edilmeli. (Radahn: Miquella Head_v2'ye oturuyor, Head_v1'in yelesine gömülüyor.)"""
    parts, shift = _centered(_stack())
    names, fields, samples = _prep(parts)
    res = auto_assemble(parts, fields, samples, names, up=np.array([0, 1.0, 0]),
                        alternatives={"HeadA": {"HeadB"}, "HeadB": {"HeadA"}})
    assert all(res.placed.values()), res.placed
    r = res.root
    assert r == "Body"
    for n in names:
        truth = -(shift[n] - shift[r])
        assert np.linalg.norm(res.offsets[n] - truth) < 1.0, (n, res.offsets[n], truth)
    assert "HeadB" in res.prefer
    assert res.requires.get("Rider") == {"HeadB"}


def _stack():
    M, T = _mf()
    pin_low = _box([-3.8, 35.4, -3.8], [3.8, 40.01, 3.8])
    return {"Body": T(M(_cyl(15, 0, 40)) - M(_box([-4, 35.0, -4], [4, 40.1, 4]))),
            "HeadA": T(M(_cyl(15, 40, 70)) + M(pin_low)),
            "HeadB": T(M(_cyl(15, 40, 50)) + M(pin_low) + M(_box([-4, 49.99, -4], [4, 55, 4]))),
            "Rider": T(M(_cyl(15, 50, 60)) - M(_box([-4.2, 49.9, -4.2], [4.2, 55.3, 4.2])))}


def test_scattered_export_falls_back_to_auto_assembly():
    """Parçalar ne montajlı ne ortalanmış, dağınık konumlarda (1/6 Casca CA3D: Torso ile Leg_L orijinde üst üste):
    üst üste duran iki parça 'alternatif' sayılıp biri basılacaklardan çıkarılmamalı; otomatik montaj denenmeli."""
    from slaprep.assembly import solve_assembly

    s = _stack()
    base = _cyl(22, -8, 0)
    meshes = {"Body": s["Body"], "HeadA": s["HeadA"], "Base": base}
    moves = {"Body": [3.0, 2.0, -4.0], "HeadA": [5.0, -37.0, 3.0], "Base": [100.0, 7.0, -20.0]}
    parts = []
    for n, m in meshes.items():
        m = m.copy()
        m.apply_translation(moves[n])               # HeadA gövdenin içine düşüyor (dosyada üst üste)
        parts.append(_part(n, m))
    asm, _, _ = solve_assembly(parts)
    assert sorted(asm.selected) == ["Base", "Body", "HeadA"], asm.variant_groups
    assert asm.mode == "otomatik", asm.warnings
    assert any("montajlı değil" in w for w in asm.warnings)
    assert all(asm.placed[n] for n in meshes)
    # kafa pimiyle gövdenin yuvasına (gerçek yeri); düz diskin hangi düz yüze oturacağı bu yapay örnekte belirsiz
    rel = (asm.offsets["HeadA"] + moves["HeadA"]) - (asm.offsets["Body"] + moves["Body"])
    assert np.linalg.norm(rel) < 1.0, rel


def test_part_left_at_origin_is_placed():
    """Montajlı kitte bir parça dosyada orijine bırakılmış (Maomao dioraması Jinshi\\Bun, Atsu Sword_Belt):
    üst üste bindiği parçanın alternatifi sanılmamalı; montaj yerinde kalırken parça pimiyle yuvasına oturtulmalı."""
    from slaprep.assembly import solve_assembly

    M, T = _mf()
    base = _box([0, 0, 0], [60, 10, 60])
    body = _box([10, 10, 10], [50, 60, 50])
    head = T(M(_box([20, 60, 20], [40, 80, 40])) - M(_box([27, 74, 27], [33, 80.1, 33])))
    hat = T(M(_box([16, 80, 16], [44, 88, 44])) + M(_box([27.3, 74.4, 27.3], [32.7, 80.01, 32.7])))
    truth = -hat.bounds[0].copy()
    hat.apply_translation(truth)                               # dosyada köşesi orijinde
    parts = [_part(n, m) for n, m in (("Base", base), ("Body", body), ("Head", head), ("Hat", hat))]
    asm, _, _ = solve_assembly(parts, up_override="y")
    assert asm.mode == "tam" and "Hat" in asm.selected, asm.warnings
    assert asm.placed["Hat"] and asm.links["Hat"]["kind"] == "orijinden", asm.warnings
    assert np.linalg.norm(asm.offsets["Hat"] + truth) < 1.0, asm.offsets["Hat"]
    assert all(np.allclose(asm.offsets[n], 0) for n in ("Base", "Body", "Head"))


def test_user_join_and_detach():
    """Kullanıcı düzeltmeleri: 'Rider → HeadA' bağlantısı Rider'ı (otomatikte HeadB'ye giderdi) HeadA'nın üstüne
    koyar; 'HeadB ayır' HeadB'yi montaja almaz."""
    parts, shift = _centered(_stack())
    names, fields, samples = _prep(parts)
    alt = {"HeadA": {"HeadB"}, "HeadB": {"HeadA"}}
    res = auto_assemble(parts, fields, samples, names, up=np.array([0, 1.0, 0]), alternatives=alt,
                        hints=[("HeadA", "Rider")])
    assert res.placed["Rider"]
    truth = -(shift["Rider"] - shift["Body"]) + np.array([0, 20.0, 0])     # HeadA'nın tepesi: y 70–80
    assert np.linalg.norm(res.offsets["Rider"] - truth) < 1.0, (res.offsets["Rider"], truth)
    assert res.links["Rider"]["kind"] == "kullanıcı" and res.links["Rider"]["to"] == "HeadA"
    assert not res.hint_failed

    # "başka konum dene": 2. aday istenir; birden çok aday varsa farklı bir yer, yoksa aynı yer ve "1/1"
    n_cand = int(res.links["Rider"]["cand"].split("/")[1])
    res2 = auto_assemble(parts, fields, samples, names, up=np.array([0, 1.0, 0]), alternatives=alt,
                         hints=[("HeadA", "Rider", 1)])
    assert res2.placed["Rider"] and res2.links["Rider"]["to"] == "HeadA"
    moved = np.linalg.norm(res2.offsets["Rider"] - res.offsets["Rider"]) > 2.0
    assert moved == (n_cand > 1)
    assert res2.links["Rider"]["cand"] == (f"2/{n_cand}" if n_cand > 1 else "1/1")

    res = auto_assemble(parts, fields, samples, names, up=np.array([0, 1.0, 0]), alternatives=alt,
                        exclude=["HeadB"])
    assert not res.placed["HeadB"] and res.placed["HeadA"]


def test_preference_selects_variant_and_folder_mate():
    def g(members, sel):
        return VariantGroup(members, [sel], "geometri", 0.98, [tuple(members)], True)

    parts = {n: _part(n, _box([0, 0, 0], [10, 10, 10]), folder)
             for n, folder in (("Head_v1", "Radaam"), ("Radaam_Torso", "Radaam"),
                               ("Head_v2", "Miquella/v2"), ("Torso_v2", "Miquella/v2"))}
    heads, torsos = g(["Head_v1", "Head_v2"], "Head_v1"), g(["Radaam_Torso", "Torso_v2"], "Radaam_Torso")
    _apply_preference([heads, torsos], {"Head_v2"}, None, parts)
    assert heads.selected == ["Head_v2"]
    assert torsos.selected == ["Torso_v2"]          # Head_v2 ile aynı klasör

    # kullanıcı seçimi her zaman önce gelir
    heads, torsos = g(["Head_v1", "Head_v2"], "Head_v1"), g(["Radaam_Torso", "Torso_v2"], "Radaam_Torso")
    _apply_preference([heads, torsos], {"Head_v2"}, ["Head_v1"], parts)
    assert heads.selected == ["Head_v1"]
    assert torsos.selected == ["Radaam_Torso"]


def test_group_docking_finds_organic_seat():
    """Kayaya organik oturan (düz eki olmayan) iki parçalık rijit grup: kenetlenme grubun yerini bulmalı."""
    M, T = _mf()
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=12.0)
    sphere.apply_translation([0, 24.0, 0])
    block = T(M(_box([-30, 0, -30], [30, 20, 30])) - M(sphere))
    stick = _box([-3, 36.0, -3], [3, 46, 3])
    parts = {"Block": _part("Block", block), "Ball": _part("Ball", sphere), "Stick": _part("Stick", stick)}
    names, fields, samples = _prep(parts)
    rng = np.random.default_rng(0)
    sub = {}
    for n in names:
        pts, _, w = samples[n]
        s = pts if len(pts) <= 4000 else pts[rng.choice(len(pts), 4000, replace=False)]
        sub[n] = (s, w * len(pts) / len(s))
    moved = np.array([40.0, 30.0, -20.0])
    group = {"Ball": moved.copy(), "Stick": moved.copy()}     # grup gerçek yerinden kaydırılmış
    grid = assembly_grid({"Block": np.zeros(3)}, parts, 60.0, res=1.5)
    cands = fft_dock(group, grid, parts, fields, sub, peaks=4)
    assert cands
    assert min(np.linalg.norm(T0 + moved) for T0 in cands[:2]) < 3.0, cands


def test_checklist_threshold_matches_assembler():
    import inspect

    from slaprep import asmcheck, autoassemble

    min_contact = inspect.signature(autoassemble.auto_assemble).parameters["min_contact"].default
    assert asmcheck.STRONG_CONTACT == min_contact * autoassemble.STRICT_FACTOR


def test_checklist_orders_problems_first():
    from slaprep.asmcheck import checklist

    a = {"mode": "otomatik", "selected": ["A", "B", "C", "D", "E"],
         "links": {"A": {"kind": "kök", "contact": 0}, "B": {"to": "A", "contact": 900, "kind": "ek yüzeyi"},
                   "C": {"to": "A", "contact": 120, "kind": "ek yüzeyi"}, "E": {"kind": "ayrıldı"}}}
    parts = {n: {"placed": n in "ABC"} for n in "ABCDEF"}
    rows = checklist(parts, a)
    order = [r[0] for r in rows]
    assert order[0] == "D"                                   # yerleşmedi
    assert order.index("E") < order.index("C") < order.index("A")
    tags = {r[0]: r[2] for r in rows}
    assert tags["B"] == "ok" and tags["C"] == "check" and tags["F"] == "off"


def test_simplify_checked_reports_deviation():
    m = trimesh.creation.icosphere(subdivisions=6, radius=20.0)
    lite, dev = simplify_checked(m, 3000)
    assert len(lite.faces) <= 3000 * 1.05
    assert dev < 0.3
    assert abs(lite.volume - m.volume) / m.volume < 0.02
