"""Alternatif parça tespiti (Head_V1 / Head_V2 gibi)."""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from .geom import DistanceField
from .i18n import t
from .io import Part

# Sonda duran varyant ekleri: _V1, -v2, _A, _alt, _alt2, option1, version 2, nohat, with_hat ...
_OPT = r"(?:hat|helmet|cape|hood|weapon|sword|gun|base|hair|beard|mask)"
_SUFFIX = re.compile(
    r"(?:[ _\-.]+(?:v\d+|ver\d+|version[ _]?\d*|alt\d*|alternat\w*|option[ _]?\d*|opt\d+|var\d+"
    r"|variant[ _]?\d*|(?:no|with)[ _\-]?" + _OPT + r"|[a-d])"
    r"|(?<=[a-z])V\d+)$",
    re.IGNORECASE,
)
# Ortada geçen işaretler: Head_NoHat_V2 gibi
_TOKENS = re.compile(r"(?:^|[ _\-])(?:nohat|no_hat|withhat|with_hat|alt|option|version)(?=$|[ _\-])", re.IGNORECASE)

GRID_RES = 0.75   # mm, hacim örtüşmesi için voksel boyutu


@dataclass
class VariantGroup:
    members: list[str]        # sıralı
    selected: list[str]       # montaja giren, birbiriyle örtüşmeyen alt küme
    reason: str               # "ad" | "geometri" | "ad+geometri"
    overlap: float            # en büyük hacim örtüşme oranı (0-1), bilinmiyorsa -1
    conflicts: list = None    # birbirinin alternatifi olan (örtüşen) çiftler
    confirmed: bool = True    # geometriyle doğrulandı mı; sadece ad benzerliğiyse hiçbir parça dışlanmaz

    def describe(self) -> str:
        rest = [m for m in self.members if m not in self.selected]
        return " + ".join(self.selected) + " " + t("(alternatif: {others})").format(others=", ".join(rest))


def name_stem(name: str) -> str | None:
    """Ad varyant kalıbı içeriyorsa kök adı döndür."""
    s = name
    changed = False
    for _ in range(3):
        n = _SUFFIX.sub("", s)
        if n == s or not n:
            break
        s, changed = n, True
    t = _TOKENS.sub("", s)
    if t != s and t:
        s, changed = t, True
    return s.lower() if changed else None


_SIDE = re.compile(r"(?:^|(?<=[ _\-.]))(left|right|sol|sag|sağ|l|r|lft|rgt|izq|der)(?=$|[ _\-.\d])", re.IGNORECASE)


def _mirror_names(a: str, b: str) -> bool:
    """Adlar sadece sol/sağ ekiyle mi ayrışıyor (Foot_Left / Foot_Right, BRAZO_IZQ / BRAZO_DER)?
    Ayna simetrik parçaların boyutları aynıdır ama birbirinin alternatifi değildir."""
    sa, sb = _SIDE.sub("#", a.lower()), _SIDE.sub("#", b.lower())
    return sa == sb and "#" in sa


def _occupancy_grid(field: DistanceField, res: float):
    lo = np.floor(field.bounds[0] / res).astype(int)
    hi = np.ceil(field.bounds[1] / res).astype(int) + 1
    axes = [np.arange(lo[k], hi[k]) * res + res / 2 for k in range(3)]
    g = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1).reshape(-1, 3)
    occ = field.occupancy(g).reshape([len(a) for a in axes])
    return lo, occ


def volume_overlap(fa: DistanceField, fb: DistanceField, sweep_axes: list[int]) -> tuple[float, np.ndarray]:
    """İki parçanın hacimsel örtüşme oranı (küçük hacme göre).

    sweep_axes boş değilse (koordinatı sıfırlanmış eksen), B o eksende kaydırılarak en iyi örtüşme aranır.
    Dönüş: (oran, B için kayma vektörü).
    """
    res = GRID_RES
    la, A = _occupancy_grid(fa, res)
    lb, B = _occupancy_grid(fb, res)
    va, vb = A.sum(), B.sum()
    if va == 0 or vb == 0:
        return 0.0, np.zeros(3)
    # Ortak kafes: B'yi A'nın kafesine yerleştir (sabit eksenlerde aynı indeks = aynı konum)
    lo = np.minimum(la, lb)
    hi = np.maximum(la + A.shape, lb + B.shape)
    shape = hi - lo
    pad = np.zeros(3, dtype=int)
    for k in sweep_axes:
        pad[k] = shape[k]          # dairesel korelasyonun sarmasını önle
    GA = np.zeros(shape + pad, dtype=np.float32)
    GB = np.zeros(shape + pad, dtype=np.float32)
    oa, ob = la - lo, lb - lo
    GA[oa[0]:oa[0] + A.shape[0], oa[1]:oa[1] + A.shape[1], oa[2]:oa[2] + A.shape[2]] = A
    GB[ob[0]:ob[0] + B.shape[0], ob[1]:ob[1] + B.shape[1], ob[2]:ob[2] + B.shape[2]] = B
    if not sweep_axes:
        ov = float((GA * GB).sum())
        return ov / min(va, vb), np.zeros(3)
    ax = tuple(sweep_axes)
    FA = np.fft.rfftn(GA, axes=ax)
    FB = np.fft.rfftn(GB, axes=ax)
    corr = np.fft.irfftn(FA * np.conj(FB), s=[GA.shape[k] for k in ax], axes=ax)
    other = tuple(k for k in range(3) if k not in sweep_axes)
    corr = corr.sum(axis=other) if other else corr
    idx = np.unravel_index(np.argmax(corr), corr.shape)
    shift = np.zeros(3)
    for i, k in enumerate(sweep_axes):
        s = idx[i]
        if s > GA.shape[k] // 2:
            s -= GA.shape[k]
        shift[k] = s * res
    return float(corr[idx]) / min(va, vb), shift


def detect_variants(parts: list[Part], fields: dict[str, DistanceField], sweep_axes: list[int] | None,
                    choices: list[str] | None = None, min_overlap: float = 0.5) -> list[VariantGroup]:
    """Ad kalıbı ve/veya hacim örtüşmesi (>%50) ile alternatif grupları bul.

    sweep_axes None ise montaj bilinmiyor demektir: sadece ad kalıbına güvenilir.
    """
    names = [p.name for p in parts]
    byname = {p.name: p for p in parts}
    stems = {n: name_stem(n) for n in names}
    pairs: dict[tuple[str, str], tuple[str, float]] = {}

    def containment(a, b):
        """Küçük bbox'ın büyük bbox içinde kalan oranı (sabit eksenlerde)."""
        fixed = [k for k in range(3) if k not in (sweep_axes or [])]
        ba, bb = byname[a].bounds, byname[b].bounds
        inter = np.clip(np.minimum(ba[1], bb[1]) - np.maximum(ba[0], bb[0]), 0, None)[fixed].prod()
        small = min((ba[1] - ba[0])[fixed].prod(), (bb[1] - bb[0])[fixed].prod())
        return inter / small if small > 0 else 0

    def iou(a, b):
        fixed = [k for k in range(3) if k not in (sweep_axes or [])]
        ba, bb = byname[a].bounds, byname[b].bounds
        inter = np.clip(np.minimum(ba[1], bb[1]) - np.maximum(ba[0], bb[0]), 0, None)[fixed].prod()
        uni = (ba[1] - ba[0])[fixed].prod() + (bb[1] - bb[0])[fixed].prod() - inter
        return inter / uni if uni > 0 else 0

    for i, a in enumerate(names):
        for b in names[i + 1:]:
            same_stem = stems[a] is not None and stems[a] == stems[b]
            if sweep_axes is None:
                # Montaj bilinmiyor: geometri doğrulanamaz. Ad benzerliği (v1/v2) ya da boyutça ikiz parçalar
                # (Radahn: Torso ↔ Torso_v2 bbox'ı birebir aynı) "olası alternatif" olarak bildirilir; bu gruplar
                # doğrulanmadığı için hiçbir parça dışlanmaz (kullanıcı --variant ile seçmedikçe).
                ea = np.sort(byname[a].bounds[1] - byname[a].bounds[0])
                eb = np.sort(byname[b].bounds[1] - byname[b].bounds[0])
                twin = (bool(np.all(np.abs(ea - eb) <= 0.01 * np.maximum(ea, eb) + 0.05)) and len(names) > 2
                        and not _mirror_names(a, b))
                if same_stem or twin:
                    pairs[(a, b)] = ("ad", -1.0)
                continue
            # Tam montajda konumlar sabit: birleşik/bölünmüş seçenekler (base_merged ⊃ base_part_1) hacim oranı
            # kısıtı olmadan aday. Kısmi modda en iyi örtüşme kaydırılarak arandığı için küçük parça her zaman
            # büyüğün içine sokulabilir; orada benzer hacim ve aynı "yuva"yı paylaşma (bbox IoU) şartı gerekir.
            vr = byname[a].volume / max(byname[b].volume, 1e-9)
            if not same_stem and (containment(a, b) < 0.6
                                  or (sweep_axes and (not 0.4 < vr < 2.5 or iou(a, b) < 0.5))):
                continue
            ov, _ = volume_overlap(fields[a], fields[b], sweep_axes)
            if ov >= min_overlap:
                pairs[(a, b)] = ("ad+geometri" if same_stem else "geometri", ov)

    # Bağlı bileşenler = gruplar
    parent = {n: n for n in names}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        parent[find(a)] = find(b)
    comps: dict[str, list[str]] = {}
    for n in names:
        comps.setdefault(find(n), []).append(n)
    choices_l = {c.lower() for c in (choices or [])}
    groups = []
    for members in comps.values():
        if len(members) < 2:
            continue
        members = sorted(members, key=str.lower)
        conflict = {frozenset(k) for k in pairs if k[0] in members}
        rel = [pairs[(a, b)] for (a, b) in pairs if a in members]
        reasons = {r for r, _ in rel}
        reason = "ad+geometri" if "ad+geometri" in reasons or {"ad", "geometri"} <= reasons else rel[0][0]
        confirmed = reasons != {"ad"}
        if confirmed or any(m.lower() in choices_l for m in members):
            sel = choose_set(members, conflict, _hull_measure(byname), choices_l)
        else:
            sel = list(members)          # doğrulanamadı: hiçbirini dışlama, sadece bildir
        groups.append(VariantGroup(members, sel, reason, max(o for _, o in rel), [tuple(sorted(c)) for c in conflict],
                                   confirmed))
    return groups


def _hull_measure(byname: dict):
    """Bir parça setinin kapladığı alan: birleşik köşelerin dışbükey zarf hacmi. Stüdyolar bölünmüş parçaları
    bazen önceden oyar (Guts: base_ altı boş, base_merged dolu); gerçek hacim bu yüzden yanıltıcıdır."""
    from scipy.spatial import ConvexHull

    rng = np.random.default_rng(0)
    pts = {}

    def measure(s):
        arr = []
        for m in s:
            if m not in pts:
                v = byname[m].lite.vertices
                pts[m] = v[rng.choice(len(v), min(len(v), 3000), replace=False)]
            arr.append(pts[m])
        try:
            return float(ConvexHull(np.vstack(arr)).volume)
        except Exception:
            return 0.0
    return measure


def choose_set(members: list[str], conflict: set, measure, choices: set[str]) -> list[str]:
    """Grup içinden birbiriyle örtüşmeyen en iyi alt küme (maksimal bağımsız küme).

    measure: set → kapladığı hacim (dict verilirse parça hacimleri toplanır).
    Öncelik: kullanıcının seçtiği parçaları içeren → kapladığı hacim en büyüğe %5 yakın → daha çok parçalı
    (bölünmüş sürüm, plate'e daha iyi sığar) → ad sırası (Head_V1 / Head_V2 gibi tekli seçeneklerde V1)."""
    from itertools import combinations

    if isinstance(measure, dict):
        vol_d = measure
        measure = lambda s: sum(vol_d[m] for m in s)  # noqa: E731
    vol = {m: measure([m]) for m in members}

    n = len(members)
    sets = []
    if n <= 12:
        for k in range(1, n + 1):
            for c in combinations(members, k):
                if any(frozenset((a, b)) in conflict for a, b in combinations(c, 2)):
                    continue
                sets.append(list(c))
        # sadece maksimal olanlar
        sets = [s for s in sets if not any(set(s) < set(t) for t in sets)]
    else:   # çok büyük grup: açgözlü
        s = []
        for m in sorted(members, key=lambda m: -vol[m]):
            if all(frozenset((m, x)) not in conflict for x in s):
                s.append(m)
        sets = [s]
    cover = {tuple(s): measure(s) for s in sets}
    vmax = max(cover.values())

    def key(s):
        chosen = sum(m.lower() in choices for m in s)
        return (-chosen, -(cover[tuple(s)] >= 0.95 * vmax), -len(s), sorted(m.lower() for m in s))

    return sorted(min(sets, key=key), key=str.lower)
