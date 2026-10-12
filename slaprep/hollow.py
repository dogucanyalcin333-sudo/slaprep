"""SDF tabanlı hollow + kavite ayrıştırma.

Dış yüzey asla sadeleştirilmez. İç kabuk: işaretli mesafe alanının −duvar seviye yüzeyi (marching cubes).
Kabuk bağımsız kavitelere ayrılır; her kavitenin kendi delikleri olmalıdır (holes.py).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import manifold3d as mf
import numpy as np
import trimesh
from skimage.measure import marching_cubes

from .geom import DistanceField


@dataclass
class Cavity:
    mesh: trimesh.Trimesh          # dışa dönük normallerle kapalı kavite yüzeyi (kavite "katısı")
    volume: float                  # mm³


@dataclass
class HollowResult:
    hollowed: bool
    reason: str
    cavities: list[Cavity] = field(default_factory=list)
    dropped_ml: float = 0.0        # atılan küçük kavitelerin toplamı (dolu kaldı)
    seconds: float = 0.0
    resolution: float = 0.0


def _mk_manifold(m: trimesh.Trimesh) -> mf.Manifold:
    mesh = mf.Mesh(vert_properties=np.ascontiguousarray(m.vertices, dtype=np.float32),
                   tri_verts=np.ascontiguousarray(m.faces, dtype=np.uint32))
    man = mf.Manifold(mesh)
    if man.status() != mf.Error.NoError:
        mesh.merge()
        man = mf.Manifold(mesh)
    return man


def to_manifold(m: trimesh.Trimesh, repair: bool = False) -> mf.Manifold:
    """repair=True: geçersizse önce yüz yönlerini düzelt, olmazsa pymeshfix ile onar (yavaş, büyük parçada dakikalar)."""
    man = _mk_manifold(m)
    if man.status() == mf.Error.NoError or not repair:
        if man.status() != mf.Error.NoError:
            raise ValueError(f"manifold değil: {man.status()}")
        return man
    r = m.copy()
    trimesh.repair.fix_winding(r)
    man = _mk_manifold(r)
    if man.status() == mf.Error.NoError:
        return man
    from .io import PMF_TIMEOUT, _faithful, pymeshfix_repair

    try:
        r = pymeshfix_repair(m, timeout=PMF_TIMEOUT)
    except TimeoutError as e:
        raise ValueError(f"onarım: {e}") from None
    if r is not None:
        ok, why = _faithful(m, r)
        if not ok:          # pymeshfix şekli bozuyorsa (Hulkbuster torso) bozuk parça çıkmasın: çağıran dolu bırakır
            raise ValueError(f"onarım şekli bozuyordu ({why})")
        man = _mk_manifold(r)
        if man.status() == mf.Error.NoError:
            return man
    raise ValueError(f"manifold değil, onarılamadı: {man.status()}")


def from_manifold(man: mf.Manifold) -> trimesh.Trimesh:
    mm = man.to_mesh()
    return trimesh.Trimesh(np.asarray(mm.vert_properties)[:, :3], np.asarray(mm.tri_verts), process=False)


def _dense(field_: DistanceField, lo, n, res, slab_points=6_000_000):
    xs = [lo[k] + np.arange(n[k]) * res for k in range(3)]
    vol = np.empty(n, dtype=np.float32)
    per = max(1, slab_points // (n[1] * n[2]))
    yy, zz = np.meshgrid(xs[1], xs[2], indexing="ij")
    yz = np.stack([yy.ravel(), zz.ravel()], 1).astype(np.float32)
    for i0 in range(0, n[0], per):
        i1 = min(n[0], i0 + per)
        xx = np.repeat(xs[0][i0:i1].astype(np.float32), len(yz))
        vol[i0:i1] = field_.signed(np.column_stack([xx, np.tile(yz, (i1 - i0, 1))])).reshape(i1 - i0, n[1], n[2])
    return vol


def sdf_grid(field_: DistanceField, bounds: np.ndarray, res: float, level: float | None = None, pad: int = 3,
             coarse: int = 4, slab_points: int = 6_000_000):
    """Izgara üzerinde işaretli mesafe (float32).

    level verilirse dar bant hızlandırması: önce `coarse` kat seyrek ızgara hesaplanır; mesafe alanı 1-Lipschitz
    olduğundan seviyeden uzak ince noktaların hangi tarafta kaldığı kesindir, sadece bant içi tam hesaplanır."""
    lo = bounds[0] - pad * res
    n = np.ceil((bounds[1] - bounds[0]) / res).astype(int) + 2 * pad + 1
    if level is None or coarse <= 1:
        return _dense(field_, lo, n, res, slab_points), lo
    rc = res * coarse
    nc = (n - 1) // coarse + 2
    C = _dense(field_, lo, nc, rc, slab_points)
    band = rc * 0.87 + res
    ci = [np.minimum(np.round(np.arange(n[k]) / coarse).astype(int), nc[k] - 1) for k in range(3)]
    vol = np.empty(n, dtype=np.float32)
    per = max(1, slab_points // (n[1] * n[2]))
    ys, zs = lo[1] + np.arange(n[1]) * res, lo[2] + np.arange(n[2]) * res
    for i0 in range(0, n[0], per):
        i1 = min(n[0], i0 + per)
        est = C[np.ix_(ci[0][i0:i1], ci[1], ci[2])]
        need = np.abs(est - level) < band
        if need.any():
            ii, jj, kk = np.nonzero(need)
            pts = np.column_stack([lo[0] + (ii + i0) * res, ys[jj], zs[kk]]).astype(np.float32)
            est[need] = field_.signed(pts)
        vol[i0:i1] = est
    return vol, lo


def inner_shell(mesh: trimesh.Trimesh, wall: float, res: float, field_: DistanceField | None = None):
    """−duvar seviye yüzeyi. Dönüş: (trimesh, normaller kavite dışına dönük) veya None."""
    f = field_ or DistanceField(mesh)
    vol, lo = sdf_grid(f, mesh.bounds, res, level=-wall)
    if vol.min() > -wall:
        return None
    v, fc, _, _ = marching_cubes(vol, level=-wall, spacing=(res, res, res), gradient_direction="ascent",
                                 allow_degenerate=False)
    v += lo
    shell = trimesh.Trimesh(v, fc, process=True)
    if shell.volume < 0:          # kavite "katısı" dışa dönük normallerle olmalı
        shell.invert()
    return shell


def hollow_part(mesh: trimesh.Trimesh, wall: float, *, min_cavity_ml: float, min_ml: float, min_frac: float,
                max_cavities: int, force: bool = False, res: float | None = None,
                field_: DistanceField | None = None) -> HollowResult:
    t0 = time.time()
    res = res or min(0.3, wall / 5)
    vol_part = abs(mesh.volume)
    if not force and vol_part < min_ml * 1000:          # kavite eşiğe hiç ulaşamaz (harf, saç teli gibi)
        return HollowResult(False, f"dolu: parça {vol_part / 1000:.1f} ml (< {min_ml} ml)", seconds=time.time() - t0,
                            resolution=res)
    f = field_ or DistanceField(mesh)
    shell = inner_shell(mesh, wall, res, f)
    if shell is None or len(shell.faces) == 0:
        return HollowResult(False, f"duvar {wall} mm için parça çok ince", seconds=time.time() - t0, resolution=res)
    # Marching cubes dejenere üçgenleri atarken iç kabukta minik delikler bırakabiliyor (Atsu Split_Pants: 0.30 mm
    # ızgarada 12 açık kenar; 0.27 / 0.33 mm'de kusursuz). İç yüzey görünmez: önce delikleri kapat, olmazsa ızgarayı
    # biraz kaydırıp yeniden hesapla; ancak hiçbiri olmazsa parça dolu kalır.
    man = None
    for attempt in range(3):
        try:
            man = to_manifold(shell)
            break
        except ValueError:
            if attempt == 0:
                trimesh.repair.fill_holes(shell)
                continue
            res *= 0.9
            shell = inner_shell(mesh, wall, res, f)
            if shell is None or len(shell.faces) == 0:
                break
    if man is None:
        return HollowResult(False, "dolu: iç kabuk geçersiz çıktı", seconds=time.time() - t0, resolution=res)
    comps = sorted(man.decompose(), key=lambda m: -m.volume())
    keep, dropped = [], 0.0
    for c in comps:
        v = c.volume()
        if v < min_cavity_ml * 1000:
            dropped += v
        else:
            keep.append(c)
    total = sum(c.volume() for c in keep)
    if len(keep) > max_cavities:
        # pratik değilse: en büyükler dışında kalanlar toplamın küçük bir kısmıysa onları dolu bırak
        extra = keep[max_cavities:]
        if sum(c.volume() for c in extra) < 0.15 * total:
            dropped += sum(c.volume() for c in extra)
            keep = keep[:max_cavities]
            total = sum(c.volume() for c in keep)
        elif not force:
            return HollowResult(False, f"{len(keep)} ayrı kavite çıktı (> {max_cavities}), her birine delik pratik değil",
                                dropped_ml=dropped / 1000, seconds=time.time() - t0, resolution=res)
    if not force and (total < min_ml * 1000 or total < min_frac * vol_part):
        why = (f"kavite {total/1000:.1f} ml (< {min_ml} ml)" if total < min_ml * 1000
               else f"kavite parçanın %{100*total/vol_part:.0f}'i (< %{min_frac*100:.0f})")
        return HollowResult(False, f"dolu: {why}", dropped_ml=dropped / 1000, seconds=time.time() - t0, resolution=res)
    if not keep:
        return HollowResult(False, "kavite yok", seconds=time.time() - t0, resolution=res)
    cavs = []
    for c in keep:
        cs = c.simplify(res * 0.25)         # iç yüzey görünmez; manifold korunarak sadeleştir
        cavs.append(Cavity(from_manifold(cs), cs.volume()))
    return HollowResult(True, f"{len(cavs)} kavite", cavs, dropped / 1000, time.time() - t0, res)


def measure_wall(outer: trimesh.Trimesh, cavities: list[Cavity], wall: float, n: int = 4000, seed: int = 0):
    """Rastgele dış yüzey noktalarından iç kabuğa en kısa mesafe. Kavitenin olmadığı ince bölgeler hariç."""
    if not cavities:
        return None
    pts, _ = trimesh.sample.sample_surface(outer, n, seed=seed)
    inner = trimesh.util.concatenate([c.mesh for c in cavities])
    d = DistanceField(inner).unsigned(pts)
    d = d[d < wall * 1.5 + 1.0]
    if len(d) < 20:
        return None
    return float(np.mean(d)), float(np.percentile(d, 1)), float(np.percentile(d, 99))
