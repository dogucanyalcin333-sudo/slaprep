"""Plate yerleşimi.

Parça siluetleri (XY izdüşümü) 0.5 mm ızgaraya rasterize edilir; çakışma araması FFT korelasyonuyla yapılır.
Her parça için birçok yatay açı denenir; konum seçimi "en çok temas" (parçayı mevcut parçalara/kenara yaslar,
boşlukları doldurur) ya da sol-alt (bottom-left) kuralıyla yapılır. Birden çok sıralama/strateji denenir ve
en iyi sonuç seçilir: önce en az plate, sonra en kısa toplam baskı süresi (plate başına en yüksek parça
toplamı), sonra en sıkı yerleşim. Parça arası ve kenar boşluğu `gap`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import trimesh
from scipy import ndimage
from scipy.signal import fftconvolve

from .fmt import fmt_duration  # noqa: F401  (pipeline/rapor buradan da alır)

CELL = 0.5   # mm


@dataclass
class Placement:
    name: str
    plate: int               # 1'den başlar
    yaw: float               # derece (Z ekseni etrafında, saat yönü tersine)
    offset: np.ndarray       # (x, y): döndürülmüş maskenin (0,0) hücresinin plate koordinatı (plate merkezli)
    mask_lo: np.ndarray = None      # döndürülmüş noktaların maske kökeni
    mask_shape: tuple = (0, 0)


@dataclass
class TimeModel:
    """SLA baskı süresi: plate'in süresi en yüksek parçasıyla belirlenir (katman sayısı); parça sayısı/alanı süreyi
    değiştirmez. Varsayılanlar Photon Mono M7 için tipik: 0.05 mm katman, ~6.5 sn/katman (pozlama + kaldırma)."""
    layer_height: float = 0.05        # mm
    layer_time: float = 6.5           # sn / katman
    plate_overhead: float = 15 * 60   # sn / plate: alt katmanlar, hazırlık, temizlik
    # Karar eşiği: bir plate fazla kullanmak ancak en az bu kadar süre kazandırıyorsa (fazladan plate = kullanıcının
    # bir kez daha başına gelmesi; Sanji: 3 plate 12 sa 46 dk ↔ 2 plate 12 sa 45 dk). Sadece seçimde kullanılır.
    plate_switch: float = 60 * 60     # sn

    def plate_seconds(self, height_mm: float) -> float:
        return height_mm / self.layer_height * self.layer_time

    def total_seconds(self, heights) -> float:
        hs = list(heights)
        return sum(self.plate_seconds(h) for h in hs) + len(hs) * self.plate_overhead

    def decision_minutes(self, lay) -> float:
        """'En kısa süre' hedefinin karşılaştırma ölçüsü: tahmini süre + plate başına karar eşiği (dk)."""
        return (lay.seconds(self) + lay.n_plates * self.plate_switch) / 60.0


@dataclass
class PlateLayout:
    placements: dict[str, Placement] = field(default_factory=dict)
    n_plates: int = 0
    unplaceable: list[str] = field(default_factory=list)
    heights: dict[int, float] = field(default_factory=dict)     # plate → en yüksek parça
    strategy: str = ""
    utilization: float = 0.0                                     # parça alanı / kullanılabilir plate alanı
    moved: int = 0                                               # yükseklik sıkıştırmasında taşınan parça

    @property
    def time_proxy(self) -> float:
        """Toplam baskı süresi göstergesi: plate başına en yüksek parça yüksekliklerinin toplamı (mm)."""
        return float(sum(self.heights.values()))

    def seconds(self, tm: TimeModel) -> float:
        return tm.total_seconds(self.heights.values())


def silhouette_points(mesh: trimesh.Trimesh, cell: float = CELL) -> np.ndarray:
    """Mesh'in XY izdüşümünü temsil eden noktalar (yüzey örnekleri + köşeler)."""
    n = int(np.clip(mesh.area / (cell * cell * 0.25), 2000, 300000))
    pts, _ = trimesh.sample.sample_surface(mesh, n, seed=0)
    return np.vstack([pts, mesh.vertices])[:, :2]


def rasterize(xy: np.ndarray, angle_deg: float = 0.0, cell: float = CELL):
    """Noktaları açı kadar döndürüp doldurulmuş maskeye çevir. Dönüş: (maske, köşe xy)."""
    if angle_deg:
        a = np.radians(angle_deg)
        c, s = np.cos(a), np.sin(a)
        xy = xy @ np.array([[c, s], [-s, c]])
    lo = xy.min(0) - cell
    ij = np.floor((xy - lo) / cell).astype(int)
    shape = ij.max(0) + 2
    m = np.zeros(shape, bool)
    m[ij[:, 0], ij[:, 1]] = True
    m = ndimage.binary_closing(m, iterations=2, border_value=0)
    m = ndimage.binary_fill_holes(m)
    return m, lo


def silhouette(mesh: trimesh.Trimesh, cell: float = CELL):
    """Geriye uyumluluk: açısız siluet maskesi."""
    return rasterize(silhouette_points(mesh, cell), 0.0, cell)


def _disk(r: int) -> np.ndarray:
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    return xx * xx + yy * yy <= r * r


class _Part:
    def __init__(self, name, mesh, angles, gr):
        """mesh: Trimesh ya da (XY noktaları, yükseklik) — tam çözünürlüklü siluet için önceden hesaplanmış."""
        self.name = name
        if isinstance(mesh, tuple):
            pts, self.height = mesh
        else:
            self.height = float(mesh.bounds[1][2])
            pts = silhouette_points(mesh)
        self.masks = {}
        for a in angles:
            m, lo = rasterize(pts, a)
            self.masks[a] = (m, lo)
        self.area = float(self.masks[angles[0]][0].sum()) * CELL * CELL
        m0 = self.masks[angles[0]][0]
        self.maxdim = max(m0.shape) * CELL
        self._halo = {}

    def halo(self, a, gr):
        """Boşluk payının (gr) hemen dışındaki 2 hücrelik halka; maske gr+2 kadar dolgulu koordinatta."""
        if a not in self._halo:
            h = gr + 2
            pm = np.pad(self.masks[a][0], h)
            self._halo[a] = ndimage.binary_dilation(pm, structure=_disk(h)) & \
                ~ndimage.binary_dilation(pm, structure=_disk(gr))
        return self._halo[a]


def _find_spot(P: _Part, occ: np.ndarray, angles, strategy: str, gr: int):
    """Dolu plate maskesinde P için en iyi yer: (açı, i0, j0, maske, köşe) ya da None."""
    nx, ny = occ.shape
    occf = occ.astype(np.float32)
    best = None
    occp = None
    for a in angles:
        mk, lo = P.masks[a]
        if mk.shape[0] > nx or mk.shape[1] > ny:
            continue
        coll = fftconvolve(occf, mk[::-1, ::-1].astype(np.float32), mode="valid")
        free = coll < 0.5
        if not free.any():
            continue
        if strategy == "contact":
            # Parçanın boşluk payının hemen dışındaki halkanın dolu hücrelerle örtüşmesi = temas.
            # Plate kenarı dolu sayılır (dolgu True): parçalar kenara ve birbirine yaslanır.
            h = gr + 2
            if occp is None:
                occp = np.pad(occ, h, constant_values=True).astype(np.float32)
            cont = fftconvolve(occp, P.halo(a, gr)[::-1, ::-1].astype(np.float32), mode="valid")
            ii, jj = np.indices(coll.shape)
            score = np.where(free, cont[:coll.shape[0], :coll.shape[1]], -np.inf) - 1e-3 * jj - 1e-5 * ii
            k = np.unravel_index(np.argmax(score), score.shape)
            key = (-float(score[k]),)
            i0, j0 = int(k[0]), int(k[1])
        else:
            fr = np.argwhere(free)
            j = np.lexsort((fr[:, 0], fr[:, 1]))[0]
            i0, j0 = (int(v) for v in fr[j])
            key = (j0, i0)
        if best is None or key < best[0]:
            best = (key, a, i0, j0, mk, lo)
    return None if best is None else best[1:]


def _footprint(mk: np.ndarray, i0: int, j0: int, gr: int, shape) -> np.ndarray:
    """Plate boyutunda: maske (i0, j0)'da, boşluk payı (gr) kadar büyütülmüş."""
    nx, ny = shape
    grown = ndimage.binary_dilation(np.pad(mk, gr), structure=_disk(gr))
    gi0, gj0 = i0 - gr, j0 - gr
    sub = np.zeros(shape, bool)
    a0_, b0_ = max(gi0, 0), max(gj0, 0)
    a1_, b1_ = min(gi0 + grown.shape[0], nx), min(gj0 + grown.shape[1], ny)
    sub[a0_:a1_, b0_:b1_] = grown[a0_ - gi0:a1_ - gi0, b0_ - gj0:b1_ - gj0]
    return sub


def _place(out: PlateLayout, P: _Part, pi: int, spot, shape):
    a, i0, j0, mk, lo = spot
    nx, ny = shape
    out.placements[P.name] = Placement(P.name, pi + 1, float(a),
                                       np.array([i0 * CELL - nx * CELL / 2, j0 * CELL - ny * CELL / 2]), lo, mk.shape)


def _plate_occ(out: PlateLayout, byname: dict, pi: int, usable: np.ndarray, gr: int) -> np.ndarray:
    """Plate'in doluluk maskesini yerleşimlerden yeniden kur (parça çıkarınca)."""
    nx, ny = usable.shape
    occ = ~usable.copy()
    for p in out.placements.values():
        if p.plate != pi + 1:
            continue
        i0 = int(round((p.offset[0] + nx * CELL / 2) / CELL))
        j0 = int(round((p.offset[1] + ny * CELL / 2) / CELL))
        occ |= _footprint(byname[p.name].masks[p.yaw][0], i0, j0, gr, (nx, ny))
    return occ


def _pack_once(parts: list[_Part], order: list[int], nx, ny, g, gr, strategy, max_plates, angles):
    usable = np.zeros((nx, ny), bool)
    usable[g:nx - g, g:ny - g] = True
    plates: list[np.ndarray] = []
    out = PlateLayout(strategy=strategy)
    for idx in order:
        P = parts[idx]
        placed = False
        for pi in range(len(plates) + 1):
            if max_plates is not None and pi >= max_plates:
                break
            if pi == len(plates):
                plates.append(~usable.copy())
            spot = _find_spot(P, plates[pi], angles, strategy, gr)
            if spot is None:
                continue
            a, i0, j0, mk, lo = spot
            plates[pi] = plates[pi] | _footprint(mk, i0, j0, gr, (nx, ny))
            _place(out, P, pi, spot, (nx, ny))
            out.heights[pi + 1] = max(out.heights.get(pi + 1, 0.0), P.height)
            placed = True
            break
        if not placed:
            out.unplaceable.append(P.name)
    _finalize(out, parts, usable)
    out._plates = plates            # sıkıştırma için doluluk maskeleri
    out._usable = usable
    return out


def _finalize(out: PlateLayout, parts: list[_Part], usable: np.ndarray):
    """Plate numaralarını boşluksuz yap, yükseklikleri ve doluluğu yeniden hesapla."""
    byname = {p.name: p for p in parts}
    used_plates = sorted({p.plate for p in out.placements.values()})
    renum = {old: new for new, old in enumerate(used_plates, 1)}
    for p in out.placements.values():
        p.plate = renum[p.plate]
    out.heights = {}
    for p in out.placements.values():
        out.heights[p.plate] = max(out.heights.get(p.plate, 0.0), byname[p.name].height)
    out.n_plates = len(used_plates)
    used = sum(byname[n].area for n in out.placements)
    out.utilization = used / max(out.n_plates, 1) / (usable.sum() * CELL * CELL)


def _compact_heights(out: PlateLayout, parts: list[_Part], angles, strategy, gr):
    """Yükseklik sıkıştırması: baskı süresi plate'in en yüksek parçasıyla belirlenir. Kısa bir plate'i 'uzatan'
    parça, daha yüksek bir plate'te yer varsa oraya taşınır (orada süreyi artırmaz); plate tamamen boşalırsa kalkar.
    Sadece süreyi kısaltan ya da plate sayısını azaltan taşımalar yapılır."""
    byname = {p.name: p for p in parts}
    usable = out._usable
    plates = out._plates
    shape = usable.shape
    changed = True
    while changed:
        changed = False
        hts = {pi: max((byname[p.name].height for p in out.placements.values() if p.plate == pi + 1), default=0.0)
               for pi in range(len(plates))}
        for pi in sorted(hts, key=lambda k: hts[k]):                  # önce kısa plate'ler
            members = sorted((p.name for p in out.placements.values() if p.plate == pi + 1),
                             key=lambda n: -byname[n].height)
            if not members:
                continue
            # 1) plate'i tamamen boşaltmayı dene (hepsi daha yüksek plate'lere sığarsa bir plate eksilir)
            # 2) olmazsa en yüksek parçasını taşımayı dene (plate'in süresi kısalır)
            for attempt in ("all", "top"):
                if attempt == "top" and (len(members) < 2 or
                                         byname[members[1]].height >= byname[members[0]].height - 0.5):
                    continue        # en yüksek parçayı taşımak plate'in süresini kısaltmıyor (eşit boyda başka var)
                trial_occ = {qi: plates[qi].copy() for qi in hts if qi != pi}
                moves = []
                for n in (members if attempt == "all" else members[:1]):
                    P = byname[n]
                    for qi in sorted(trial_occ, key=lambda k: -hts[k]):
                        if hts[qi] < P.height:
                            continue
                        spot = _find_spot(P, trial_occ[qi], angles, strategy, gr)
                        if spot is not None:
                            trial_occ[qi] |= _footprint(spot[3], spot[1], spot[2], gr, shape)
                            moves.append((P, qi, spot))
                            break
                    else:
                        break
                ok = len(moves) == (len(members) if attempt == "all" else 1)
                if ok:
                    for P, qi, spot in moves:
                        _place(out, P, qi, spot, shape)
                        out.moved += 1
                    for qi, occ in trial_occ.items():
                        plates[qi] = occ
                    plates[pi] = _plate_occ(out, byname, pi, usable, gr)
                    changed = True
                    break
            if changed:
                break
    _finalize(out, parts, usable)
    # boşalan plate'lerin maskeleri: numaralar yeniden dizildi, maskeleri yeniden kur
    out._plates = [_plate_occ(out, byname, k, usable, gr) for k in range(out.n_plates)]
    return out


def full_silhouette(mesh: trimesh.Trimesh, cell: float = CELL) -> tuple[np.ndarray, float]:
    """Tam çözünürlüklü mesh'ten siluet noktaları + yükseklik (sadeleştirilmiş kopyada ince uçlar kısalır)."""
    pts = silhouette_points(mesh, cell)
    return pts.astype(np.float32), float(mesh.bounds[1][2])


def pack(items: dict[str, trimesh.Trimesh], plate_xy: tuple[float, float], gap: float,
         max_plates: int | None = None, yaw_step: float = 30.0, strategies=("contact", "bl"),
         orders=("area", "height", "maxdim"), random_orders: int = 2, goal: str = "time",
         time_model: TimeModel | None = None, compact: bool = True) -> PlateLayout:
    """items: baskı yönünde, XY'de merkezlenmiş mesh'ler. En iyi yerleşimi döndürür.
    goal="time": en kısa tahmini toplam baskı süresi (uzun parçalar aynı plate'te toplanır; gerekirse bir plate
    fazla kullanılır); goal="plates": önce en az plate, sonra süre."""
    tm = time_model or TimeModel()
    W, D = plate_xy
    nx, ny = int(W / CELL), int(D / CELL)
    g = int(np.ceil(gap / CELL))
    gr = g + 2                                    # +2 hücre: iki maskenin rasterizasyon payı (≥ gap garanti)
    angles = [float(a) for a in np.arange(0.0, 360.0, yaw_step)]
    parts = [_Part(n, m, angles, gr) for n, m in items.items()]
    if not parts:
        return PlateLayout()
    keys = {
        "area": lambda i: -parts[i].area,
        "height": lambda i: (-round(parts[i].height / 5), -parts[i].area),
        "maxdim": lambda i: -parts[i].maxdim,
    }
    order_list = [(o, sorted(range(len(parts)), key=keys[o])) for o in orders]
    rng = np.random.default_rng(0)
    base = order_list[0][1]
    for r in range(random_orders if len(parts) > 1 else 0):     # tek parçada karıştırılacak sıra yok
        o = list(base)
        # küçük karıştırma: komşu öğeleri rastgele yer değiştir (büyükten küçüğe eğilimi korunur)
        for _ in range(len(o)):
            i = rng.integers(0, max(1, len(o) - 1))
            if rng.random() < 0.5:
                o[i], o[i + 1] = o[i + 1], o[i]
        order_list.append((f"karışık{r + 1}", o))
    best = None
    for strat in strategies:
        for oname, order in order_list:
            lay = _pack_once(parts, order, nx, ny, g, gr, strat, max_plates, angles)
            if compact:
                lay = _compact_heights(lay, parts, angles, strat, gr)
            lay.strategy = f"{strat}/{oname}"
            if goal == "plates":
                key = (len(lay.unplaceable), lay.n_plates, round(lay.seconds(tm) / 60.0), -lay.utilization)
            else:   # süre + plate başına karar eşiği: fazladan plate ancak belirgin süre kazandırıyorsa
                key = (len(lay.unplaceable), round(tm.decision_minutes(lay)), lay.n_plates, -lay.utilization)
            if best is None or key < best[0]:
                best = (key, lay)
    return best[1]


def prefer_folders(sep: list[PlateLayout], one: PlateLayout, tm: TimeModel, goal: str = "time",
                   tolerance_min: float = 15.0) -> bool:
    """Plate'leri alt klasörlere göre ayırmak mı (sep: klasör başına yerleşimler), tek grup mu (one)?
    Klasörlere ayırmak dosya düzenini derli toplu tutar; ama ancak hedefe (süre / plate) mal olmuyorsa seçilir
    (Radahn: klasörlere göre 6 plate 23 sa 22 dk ↔ tek grup 5 plate 19 sa 09 dk)."""
    n_sep = sum(lay.n_plates for lay in sep)
    if any(lay.unplaceable for lay in sep) != bool(one.unplaceable):
        return not any(lay.unplaceable for lay in sep)
    if goal == "plates":
        return n_sep <= one.n_plates
    sec_sep = sum(lay.seconds(tm) for lay in sep) + n_sep * tm.plate_switch
    sec_one = one.seconds(tm) + one.n_plates * tm.plate_switch
    return sec_sep <= sec_one + tolerance_min * 60


def area_lower_bound(items: dict[str, trimesh.Trimesh], plate_xy: tuple[float, float], gap: float) -> float:
    """Plate sayısı için alan alt sınırı: (siluet + yarım boşluk payı) alanları toplamı / kullanılabilir alan."""
    W, D = plate_xy
    g = int(np.ceil(gap / CELL))
    usable = (W / CELL - 2 * g) * (D / CELL - 2 * g)
    r = max(1, g // 2)
    tot = 0.0
    for m in items.values():
        mk, _ = silhouette(m)
        tot += ndimage.binary_dilation(np.pad(mk, r), structure=_disk(r)).sum()
    return tot / usable


def placement_transform(p: Placement) -> np.ndarray:
    """Merkezlenmiş baskı mesh'ini plate konumuna götüren 4x4: Z etrafında yaw + XY öteleme.

    rasterize(): xy' = R·xy (xy @ [[c, s], [-s, c]]) → hücre = (xy' - lo)/CELL. Maske köşesi plate'te `offset`
    (plate merkezli) noktasına konur: plate = offset + (R·xy - lo)."""
    a = np.radians(p.yaw)
    c, s = np.cos(a), np.sin(a)
    A = np.eye(4)
    A[:2, :2] = np.array([[c, -s], [s, c]])
    A[:2, 3] = p.offset - p.mask_lo
    return A
