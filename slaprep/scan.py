"""Hafif kit taraması: STL'leri mesh olarak yüklemeden bounding box çıkarır ve montaj durumunu sınıflandırır.

Binary STL'ler numpy.memmap ile okunur (sadece köşe koordinatlarının min/max'ı), ASCII STL'ler regex ile.
Hiçbir dosyaya yazılmaz.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .config import OUT_DIR
from .i18n import t

# kit.status değerleri: veri anahtarıdır (cli sıralaması, scan JSON çıktısı) — çevrilmez, ekranda
# status_text() ile gösterilir.
READ_FAIL, SINGLE, SCATTERED, SHIFTED, ASSEMBLED = \
    "okunamadı", "tek parça", "dağınık", "kısmen kaymış", "tam montajlı"


def status_text(status: str) -> str:
    """Durum anahtarının ekranda görünen karşılığı."""
    return {READ_FAIL: t("okunamadı"), SINGLE: t("tek parça"), SCATTERED: t("dağınık"),
            SHIFTED: t("kısmen kaymış"), ASSEMBLED: t("tam montajlı")}.get(status, status)

_REC = np.dtype([("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("a", "<u2")])
_SUPPORT_HINT = re.compile(r"(?<![a-z])(?<!no[ _\-])(?<!no)(?<!un)(pre[ _\-]?sup\w*|supported|supports?|sup)(?![a-z])",
                           re.IGNORECASE)
_VERTEX = re.compile(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)")


@dataclass
class StlInfo:
    path: Path
    triangles: int
    bounds: np.ndarray | None      # (2, 3); okunamadıysa None
    ascii: bool = False
    error: str = ""


@dataclass
class KitScan:
    root: Path                     # STL'leri doğrudan içeren klasör
    files: list[StlInfo]
    status: str = ""               # tek parça | tam montajlı | kısmen kaymış | dağınık | okunamadı
    detail: str = ""
    zeroed_axes: list[str] = field(default_factory=list)
    extent: np.ndarray | None = None
    connected_frac: float = 0.0
    presupported: bool = False
    junk: list[str] = field(default_factory=list)   # reklam/filigran plakası sanılan dosyalar (sınıflandırmaya katılmaz)

    @property
    def size_mb(self) -> float:
        return sum(f.path.stat().st_size for f in self.files) / 2**20


def stl_bounds(path: Path, chunk: int = 2_000_000) -> StlInfo:
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            head = fh.read(84)
        if len(head) < 84:
            return StlInfo(path, 0, None, error=t("dosya çok kısa"))
        n = int(np.frombuffer(head[80:84], "<u4")[0])
        if (84 + 50 * n != size and (size - 84) % 50 == 0 and size > 84
                and not head.lstrip().lower().startswith(b"solid")):
            n = (size - 84) // 50     # başlıktaki sayı yanlış (ör. Magics 'COLOR=' başlığı): dosya boyutuna göre
        if 84 + 50 * n == size and n > 0:
            mm = np.memmap(path, dtype=_REC, mode="r", offset=84, shape=(n,))
            lo = np.full(3, np.inf)
            hi = np.full(3, -np.inf)
            bad = 0
            for i in range(0, n, chunk):
                v = mm["v"][i:i + chunk].reshape(-1, 3)
                fin = np.isfinite(v).all(1)
                if not fin.all():
                    bad += int((~fin).sum())
                    v = v[fin]
                if len(v):
                    lo = np.minimum(lo, v.min(0))
                    hi = np.maximum(hi, v.max(0))
            del mm
            err = t("{n} geçersiz (NaN/inf) köşe").format(n=bad) if bad else ""
            return StlInfo(path, n, np.array([lo, hi], dtype=float) if np.isfinite(lo).all() else None, error=err)
        # ASCII
        data = path.read_bytes()
        if not data.lstrip().lower().startswith(b"solid"):
            return StlInfo(path, n, None, error=t("tanınmayan STL biçimi"))
        v = np.array(_VERTEX.findall(data), dtype=float)
        if not len(v):
            return StlInfo(path, 0, None, ascii=True, error=t("köşe bulunamadı"))
        return StlInfo(path, len(v) // 3, np.array([v.min(0), v.max(0)]), ascii=True)
    except Exception as e:  # bozuk dosya taramayı durdurmasın
        return StlInfo(path, 0, None, error=str(e))


def _components(boxes: np.ndarray, axes: list[int], margin: float) -> list[list[int]]:
    n = len(boxes)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            a, b = boxes[i], boxes[j]
            if np.all(a[0][axes] - margin <= b[1][axes]) and np.all(b[0][axes] - margin <= a[1][axes]):
                parent[find(i)] = find(j)
    comps: dict[int, list[int]] = {}
    for i in range(n):
        comps.setdefault(find(i), []).append(i)
    return sorted(comps.values(), key=len, reverse=True)


def junk_like(files: list[StlInfo]) -> set[Path]:
    """Sadece kutu + üçgen sayısıyla reklam/filigran plakası tahmini (assembly.detect_junk_parts'ın hafif hâli):
    düz (en kısa kenar ≤ en uzunun %10'u), diğer dosyaların medyanının ≤ %10'u kadar üçgen ve diğerlerinin toplam
    kutusundan ≥ 1.3 kat geniş ya da hiç kalınlığı yok."""
    if len(files) < 3:
        return set()
    ext = {f.path: np.sort(f.bounds[1] - f.bounds[0]) for f in files}
    thin = {p for p, e in ext.items() if e[0] <= 0.1 * e[2]}
    rest = [f for f in files if f.path not in thin] or files
    B = np.array([f.bounds for f in rest])
    rest_ext = float((B[:, 1].max(0) - B[:, 0].min(0)).max())
    med = float(np.median([f.triangles for f in rest]))
    out = {f.path for f in files if f.path in thin
           and ((f.triangles <= 0.1 * med and ext[f.path][2] >= 1.3 * rest_ext)   # figürden geniş seyrek plaka
                or ext[f.path][0] < 0.05)}                                         # kalınlığı yok: basılamaz
    return out if len(out) <= len(files) - 2 else set()


def classify(kit: KitScan) -> KitScan:
    ok = [f for f in kit.files if f.bounds is not None]
    names = " ".join([kit.root.name] + [p for p in kit.root.parts[-3:]] + [f.path.stem for f in kit.files])
    kit.presupported = bool(_SUPPORT_HINT.search(names))
    if not ok:
        kit.status, kit.detail = READ_FAIL, "; ".join({f.error for f in kit.files})
        return kit
    junk = junk_like(ok)
    if junk:
        kit.junk = sorted(p.name for p in junk)
        ok = [f for f in ok if f.path not in junk]
    _classify(kit, ok)
    if kit.junk:
        kit.detail = (kit.detail + "; " if kit.detail else "") \
            + t("reklam dosyası sayılmadı: {files}").format(files=", ".join(kit.junk))
    return kit


def _classify(kit: KitScan, ok: list[StlInfo]) -> KitScan:
    B = np.array([f.bounds for f in ok])
    lo, hi = B[:, 0].min(0), B[:, 1].max(0)
    kit.extent = hi - lo
    if len(ok) == 1:
        kit.status = SINGLE
        return kit
    ext = B[:, 1] - B[:, 0]
    scale = float(np.median(ext.max(1)))
    tol = max(0.05, 1e-4 * scale)
    zeroed = [k for k in range(3) if np.all(np.abs(B[:, 0, k]) < tol)]
    kit.zeroed_axes = ["xyz"[k] for k in zeroed]
    centers = (B[:, 0] + B[:, 1]) / 2
    free = [k for k in range(3) if k not in zeroed] or [0, 1, 2]
    # Her parça kendi başına orijine ortalanmış mı (sıfırlanmış eksen hariç; ör. XY merkez 0, taban Z=0)
    centered = np.all(np.abs(centers[:, free]) < 0.02 * ext.max(1)[:, None] + 0.5, axis=1).mean() > 0.8
    margin = max(1.0, 0.01 * scale)
    if len(zeroed) >= 2 or centered:
        kit.status = SCATTERED
        kit.detail = t("her parça orijinde (ortalanmış)") if centered \
            else t("{axes} eksenlerinde sıfırlanmış").format(axes=",".join(kit.zeroed_axes))
        return kit
    if len(zeroed) == 1:
        keep = [k for k in range(3) if k != zeroed[0]]
        comps = _components(B, keep, margin)
        kit.connected_frac = len(comps[0]) / len(ok)
        # Plate'e dizilmiş kitlerde 2B izdüşümler ayrık olur; Rango tipinde iç içe geçer.
        if kit.connected_frac >= 0.75:
            kit.status = SHIFTED
            kit.detail = t("{axis} ekseni parça başına sıfırlanmış, diğer eksenler korunmuş").format(
                axis=kit.zeroed_axes[0].upper())
        else:
            kit.status = SCATTERED
            kit.detail = t("{axis}=0 ama parçalar ayrık yerleşmiş (plate düzeni?)").format(
                axis=kit.zeroed_axes[0].upper())
        return kit
    comps = _components(B, [0, 1, 2], margin)
    kit.connected_frac = len(comps[0]) / len(ok)
    if kit.connected_frac >= 0.75:
        kit.status = ASSEMBLED
        kit.detail = t("bbox'lar tek kümede (%{pct})").format(pct=f"{kit.connected_frac*100:.0f}")
    else:
        kit.status = SCATTERED
        kit.detail = t("en büyük bağlı küme %{pct}, {n} küme").format(
            pct=f"{kit.connected_frac*100:.0f}", n=len(comps))
    return kit


def find_kits(root: Path) -> list[Path]:
    """STL'leri doğrudan içeren her klasör bir kit birimidir."""
    dirs = set()
    for p in Path(root).rglob("*"):
        if p.is_file() and p.suffix.lower() == ".stl" and OUT_DIR not in p.parts:
            dirs.add(p.parent)
    return sorted(dirs, key=lambda d: str(d).lower())


def scan_root(root: Path, workers: int = 8, progress=None) -> list[KitScan]:
    kits = find_kits(root)
    files = {d: sorted(f for f in d.iterdir() if f.is_file() and f.suffix.lower() == ".stl") for d in kits}
    allf = [f for d in kits for f in files[d]]
    infos = {}
    with ThreadPoolExecutor(workers) as ex:
        for info in ex.map(stl_bounds, allf):
            infos[info.path] = info
            if progress:
                progress(info)
    return [classify(KitScan(d, [infos[f] for f in files[d]])) for d in kits]
