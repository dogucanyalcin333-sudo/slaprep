"""Deneme kiti üretir: bölünmüş, pim/yuvalı, montajlı küçük bir "figür" (telifsiz, kodla üretilir).

    python tools\\make_demo_kit.py                 # .\\demo-kit klasörüne yazar
    python tools\\make_demo_kit.py C:\\yol\\demo    # başka bir yere

Sonra::

    slaprep prep demo-kit --height 120

Kitin SLAPrep'in bütün yollarını tetiklemesi amaçlanmıştır:
  * parçalar montajlı duruşta (dosyadaki konumlar gerçek montaj) → "tam montajlı" kipi,
  * gövde/kafa/kaide arasında pim + yuva → birleşme (mating) yüzeyi ve gizli yüz bulma,
  * gövde ve kaide hollow eşiğinin üstünde (hacim), kollar altında → hem hollow hem "dolu" sonucu,
  * iki kol aynı komşuya bağlanır (sol/sağ) → varyant/eşleştirme mantığı,
  * `Head_v2` ikinci bir kafa: ad kalıbından alternatif parça olarak tanınmalı,
  * `HEIGHT_PILLAR`: ölçek referansı (basılmaması gerekir).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import trimesh

PIN_R = 2.0            # pim yarıçapı (mm)
PIN_H = 4.0            # pim boyu
CLEARANCE = 0.4        # pim-yuva boşluğu: SLAPrep bunu temas sayar (montaj boşluğu toleransı)


def _box(size, center=(0, 0, 0)) -> trimesh.Trimesh:
    m = trimesh.creation.box(extents=size)
    m.apply_translation(center)
    return m


def _cyl(r, h, center=(0, 0, 0)) -> trimesh.Trimesh:
    m = trimesh.creation.cylinder(radius=r, height=h, sections=48)
    m.apply_translation(center)
    return m


TORSO_HW = 15.0        # gövde yarı genişliği (omuz yüzeyi x = ±15)
SHOULDER_Z = 45.0


def _pin_z(part: trimesh.Trimesh, z_face: float, xy=(0.0, 0.0)) -> trimesh.Trimesh:
    """Yatay birleşme yüzeyine (z = z_face) yukarı doğru pim ekle."""
    return trimesh.boolean.union([part, _cyl(PIN_R, PIN_H, (xy[0], xy[1], z_face + PIN_H / 2 - 0.01))])


def _socket_z(part: trimesh.Trimesh, z_face: float, xy=(0.0, 0.0)) -> trimesh.Trimesh:
    """z = z_face yüzünden yukarı yuva aç. Delik yüzün biraz altından başlar: aşağıda ince bir
    kapak kalıp parçadan kopmasın (iki ayrı gövde çıkmasın)."""
    depth = PIN_H + 1.0
    hole = _cyl(PIN_R + CLEARANCE, depth, (xy[0], xy[1], z_face + depth / 2 - 1.0))
    return trimesh.boolean.difference([part, hole])


def _x_cylinder(r, length, x_inner, sx, z) -> trimesh.Trimesh:
    """X ekseni boyunca kol: iç ucu x = sx*x_inner'da (gövdenin omuz yüzeyi)."""
    m = _cyl(r, length)
    m.apply_transform(trimesh.transformations.rotation_matrix(np.radians(90), [0, 1, 0]))
    m.apply_translation((sx * (x_inner + length / 2), 0, z))
    return m


def build() -> dict[str, trimesh.Trimesh]:
    """Montajlı duruşta parçalar: kaide (z 0–10), gövde (10–60), kafa (60–~84), iki kol (omuzlarda).

    Ölçüler en küçük yaygın reçine yazıcısına (127 × 80 mm plate) ölçeksiz sığacak şekilde seçildi.
    """
    parts: dict[str, trimesh.Trimesh] = {}

    base = _box((60.0, 40.0, 10.0), (0, 0, 5))
    parts["Base"] = _pin_z(base, 10.0)

    torso = _box((2 * TORSO_HW, 22.0, 50.0), (0, 0, 35))
    torso = _socket_z(torso, 10.0)                       # kaidenin pimi
    torso = _pin_z(torso, 60.0)                          # kafanın yuvası
    for sx in (-1, 1):                                   # omuz pimleri: X boyunca
        pin = _cyl(PIN_R, PIN_H)
        pin.apply_transform(trimesh.transformations.rotation_matrix(np.radians(90), [0, 1, 0]))
        pin.apply_translation((sx * (TORSO_HW + PIN_H / 2 - 0.01), 0, SHOULDER_Z))
        torso = trimesh.boolean.union([torso, pin])
    parts["Torso"] = torso

    def head(radius: float) -> trimesh.Trimesh:
        """Küre, z = 60'ta düz kesilmiş (birleşme yüzeyi) + pim yuvası."""
        h = trimesh.creation.icosphere(subdivisions=3, radius=radius)
        h.apply_translation((0, 0, 60.0 + radius * 0.85))
        h = trimesh.boolean.difference([h, _box((4 * radius, 4 * radius, 40.0), (0, 0, 60.0 - 20.0))])
        return _socket_z(h, 60.0)

    parts["Head_v1"] = head(12.0)
    parts["Head_v2"] = head(13.5)          # aynı kafanın ikinci sürümü: ad kalıbından alternatif sayılmalı

    for name, sx in (("Arm_L", -1), ("Arm_R", 1)):
        arm = _x_cylinder(5.5, 28.0, TORSO_HW, sx, SHOULDER_Z)
        # omuz pimi için yuva: kolun iç ucundan içeri doğru
        hole = _cyl(PIN_R + CLEARANCE, PIN_H + 1.0)
        hole.apply_transform(trimesh.transformations.rotation_matrix(np.radians(90), [0, 1, 0]))
        hole.apply_translation((sx * (TORSO_HW + (PIN_H + 1.0) / 2 - 1.0), 0, SHOULDER_Z))
        parts[name] = trimesh.boolean.difference([arm, hole])

    # ölçek referansı: 100 mm'lik çubuk, figürün yanında — basılmaması gerekir
    parts["HEIGHT_PILLAR"] = _box((6.0, 6.0, 100.0), (45.0, 0, 50.0))
    return parts


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "demo-kit").resolve()
    out.mkdir(parents=True, exist_ok=True)
    parts = build()
    for name, mesh in parts.items():
        if not mesh.is_watertight:
            print(f"uyarı: {name} watertight değil (SLAPrep onarmayı denerse de bu bir deneme kiti)")
        mesh.export(out / f"{name}.stl")
        print(f"{name}.stl  {len(mesh.faces)} üçgen  {abs(mesh.volume)/1000:.1f} ml")
    print(f"\nDeneme kiti: {out}")
    print(f'Deneyin:  slaprep prep "{out}" --height 90')
    return 0


if __name__ == "__main__":
    sys.exit(main())
