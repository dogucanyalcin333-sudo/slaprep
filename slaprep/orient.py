"""Baskı yönlendirmesi.

Hedef: (1) gizli yüzey plate'e baksın (destekler gizli yüzeye gelir, delikler kavitenin alt ucunda kalır),
(2) büyük düz kesitler plate'e paralel olmasın (peel kuvveti), (3) parça build volume'e sığsın.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import trimesh

from .geom import DistanceField

ALT_DELTA = 0.6     # alternatif yönler için en iyi puandan izin verilen kayıp (gizli-yüz-aşağı oranında ~%20)
UNVENTED_PENALTY = 2.0   # deliklenemeyen vantuz çanağı başına puan cezası (baskı hatası: görünür destekten ağır basar)
MAX_PEEL_GROWTH = 1.5    # süre/yer alternatifinin en büyük katman kesiti en iyi yönün en çok bu katı olabilir
# görünürlük ağırlıklı destek alanı (toplam yüzeye oranı) puan katsayısı. Tarama (scans/diag_support.py --w):
# Rango ön/yan desteklenen alan 1946 → 1079 mm², Catwoman 2274 → 649 mm², toplam baskı yüksekliği değişmeden;
# 20'de parçalar uzamaya başlıyor (Rango toplam yükseklik +%3)
SUPPORT_W = 12.0
MAX_SUPPORT_GROWTH = 1.25  # alternatif yön görünür destek alanını en çok bu katına (+%0.5 yüzey) çıkarabilir
LOW_VIS = 0.45           # bu ağırlığın altı "arka/alt" sayılır (rapordaki destek dağılımı)


def support_need(nz: np.ndarray) -> np.ndarray:
    """Baskı yönünde yüzün destek isteme derecesi: plate'e ~55°'den dik bakan yüzler (nz < −0.55) kademeli, ~30°
    içinde aşağı bakanlar (nz < −0.85) tam. Eskiden sert eşik (nz < −0.707) kullanılıyordu."""
    return np.clip((-nz - 0.55) / 0.30, 0.0, 1.0)


@dataclass
class Orientation:
    R: np.ndarray            # 4x4: montaj (ölçekli) → baskı koordinatı, z yukarı, min z = support_height
    down: np.ndarray         # parça koordinatında plate'e bakan yön
    tilt: float              # derece, gizli normalden sapma
    fits: bool
    score: float
    terms: dict
    footprint: tuple[float, float]
    height: float
    alternatives: list = None      # [best, alt1, ...] plate verimi için (sadece en iyide dolu)

    def describe(self) -> str:
        t = self.terms
        yon = f"eğim {self.tilt:.0f}°" if self.tilt <= 60 else "serbest yön"
        s = f"{yon}, gizli yüz aşağı %{t.get('hidden_down', 0) * 100:.0f}"
        if "sup_vis" in t:
            s += (f"; destek: gizli %{t['sup_hidden'] * 100:.0f} · arka/alt %{t['sup_low'] * 100:.0f} · "
                  f"ön/yan %{t['sup_vis'] * 100:.0f}")
        if not self.fits:
            s += " (SIĞMIYOR)"
        return s


def rot_to_down(d: np.ndarray) -> np.ndarray:
    """d vektörünü -Z'ye götüren 3x3 dönme."""
    d = d / np.linalg.norm(d)
    return trimesh.geometry.align_vectors(d, np.array([0.0, 0.0, -1.0]))[:3, :3]


def _tilted(d0: np.ndarray, deg: float, az: float) -> np.ndarray:
    a = np.array([1.0, 0, 0]) if abs(d0[0]) < 0.9 else np.array([0, 1.0, 0])
    u = np.cross(d0, a)
    u /= np.linalg.norm(u)
    v = np.cross(d0, u)
    t = np.radians(deg)
    w = np.cos(az) * u + np.sin(az) * v
    return np.cos(t) * d0 + np.sin(t) * w


def min_area_rect(xy: np.ndarray) -> tuple[float, float, float]:
    """2B noktaların en küçük alanlı dikdörtgeni: (genişlik, derinlik, açı rad)."""
    try:
        from scipy.spatial import ConvexHull

        h = xy[ConvexHull(xy).vertices]
    except Exception:
        h = xy
    best = (np.inf, 0, 0, 0)
    e = np.roll(h, -1, axis=0) - h
    for ang in np.unique(np.round(np.arctan2(e[:, 1], e[:, 0]) % (np.pi / 2), 4)):
        c, s = np.cos(-ang), np.sin(-ang)
        r = h @ np.array([[c, -s], [s, c]]).T
        w, dd = np.ptp(r[:, 0]), np.ptp(r[:, 1])
        if w * dd < best[0]:
            best = (w * dd, w, dd, ang)
    return best[1], best[2], best[3]


def interior_samples(mesh: trimesh.Trimesh, n_target: int = 40000):
    """Katman kesit alanı tahmini için iç voksel noktaları."""
    vol = max(abs(mesh.volume), 1.0)
    g = max(0.4, (vol / n_target) ** (1 / 3))
    lo, hi = mesh.bounds
    axes = [np.arange(lo[k] + g / 2, hi[k], g) for k in range(3)]
    P = np.stack(np.meshgrid(*axes, indexing="ij"), -1).reshape(-1, 3)
    if len(P) > 3_000_000:
        P = P[np.random.default_rng(0).choice(len(P), 3_000_000, replace=False)]
    occ = DistanceField(mesh).occupancy(P)
    return P[occ], g


def max_layer_area(z: np.ndarray, g: float) -> float:
    """İç örneklerin baskı yönündeki yükseklikleri → katman başına en büyük kesit alanı (mm²). Bölmeler +0.5 kaydırılır:
    ızgara düzlemine denk gelen yönlerde (z-zmin)/g tam sayıya çok yakın çıkar, kayan nokta 1.9999→1 yuvarlaması iki
    katmanı tek bölmede toplayıp kesiti iki kat gösteriyordu (teşhis betiği Rango pelerinini 659 yerine 1292 mm² ölçtü;
    tam dilimleme 659 mm²)."""
    if not len(z):                       # iç örnek yoksa (kalınlıksız/çok ince parça) soyulma alanı 0
        return 0.0
    return float(np.bincount(((z - z.min()) / g + 0.5).astype(int)).max() * g * g)


def orient_part(mesh: trimesh.Trimesh, hidden: np.ndarray, pin: np.ndarray, up_hint: np.ndarray,
                build: tuple[float, float, float], support_h: float, gap: float,
                tilt_range=(30.0, 45.0), cand=None, cavities=None, wall: float = 2.0,
                interior=None, n_random: int = 16, max_alts: int = 3, vis: np.ndarray | None = None,
                front: np.ndarray | None = None) -> Orientation:
    """mesh: lite (ölçekli, montaj koordinatı). hidden/pin: yüz etiketleri. cand: HoleCandidates (veya None).
    vis: yüz başına destek izinin göze batma ağırlığı (joints.visibility_weights; yoksa gizli 0, diğerleri 1).
    front: figürün baktığı yön (sırtı plate'e çeviren aday yönler için)."""
    A = mesh.area_faces
    N = mesh.face_normals
    V = mesh.vertices
    Atot = A.sum()
    Ah = A[hidden].sum()
    if vis is None or len(vis) != len(A):
        vis = (~hidden).astype(float)
    if Ah > 0:
        d0 = (N[hidden] * A[hidden, None]).sum(0)
        d0 = d0 / np.linalg.norm(d0) if np.linalg.norm(d0) > 1e-9 else -up_hint
    else:
        d0 = -up_hint / np.linalg.norm(up_hint)
    dirs = [(d0, 0.0)]
    tilts = np.arange(tilt_range[0], tilt_range[1] + 0.1, 5.0)
    for t in tilts:
        for az in np.linspace(0, 2 * np.pi, 8, endpoint=False):
            dirs.append((_tilted(d0, t, az), t))
    rng = np.random.default_rng(1)
    for _ in range(n_random):
        v = rng.normal(size=3)
        v /= np.linalg.norm(v)
        dirs.append((v, float(np.degrees(np.arccos(np.clip(v @ d0, -1, 1))))))
    # yönlü sınır kutusunun 6 yüz yönü: yazıcıya ancak sığan büyük parçada sığdıran yön genelde bunlardan biri
    try:
        axes = mesh.bounding_box_oriented.primitive.transform[:3, :3]
    except Exception:
        axes = np.eye(3)
    for k in range(3):
        for sgn in (1.0, -1.0):
            v = axes[:, k] * sgn
            dirs.append((v, float(np.degrees(np.arccos(np.clip(v @ d0, -1, 1))))))
    # sırtı ya da altı plate'e çeviren yönler (destekler görünmeyen tarafa): kendileri ve 35° eğik dördü
    for base in ([-np.asarray(front, float)] if front is not None else []) + [-np.asarray(up_hint, float)]:
        nb = np.linalg.norm(base)
        if nb < 1e-9:
            continue
        base = base / nb
        for v in [base] + [_tilted(base, 35.0, az) for az in np.linspace(0, 2 * np.pi, 4, endpoint=False)]:
            dirs.append((v, float(np.degrees(np.arccos(np.clip(v @ d0, -1, 1))))))
    if interior is None:
        interior = interior_samples(mesh)
    ip, g = interior
    W, D, Z = build
    diag = np.linalg.norm(mesh.extents)
    rows = []
    for d, tilt in dirs:
        R3 = rot_to_down(d)
        nz = N @ R3[2]
        zv = V @ R3[2]
        height = float(np.ptp(zv))
        xy = V @ R3[:2].T
        w, dd, ang = min_area_rect(xy[np.random.default_rng(0).choice(len(xy), min(len(xy), 20000), replace=False)])
        fits = (height + support_h <= Z) and (
            (w + 2 * gap <= W and dd + 2 * gap <= D) or (w + 2 * gap <= D and dd + 2 * gap <= W))
        hidden_down = float(A[hidden & (nz < -0.3)].sum() / Ah) if Ah > 0 else 0.0
        need = A * support_need(nz)                  # destek gelecek alan (yüz başına)
        support_vis = float((need * vis).sum() / Atot)
        need_tot = float(need.sum()) or 1.0
        sup_hidden = float(need[hidden].sum() / need_tot)
        sup_low = float(need[~hidden & (vis < LOW_VIS)].sum() / need_tot)
        pin_down = float(A[pin & (nz < -0.7)].sum() / Atot)
        peel = max_layer_area(ip @ R3[2], g)
        hole_ok = None
        cups_n = unvented = 0
        if cand is not None and cavities:
            from .holes import _basin_hits, vent_plan

            zc = cand.pts @ R3[2]
            oks = []
            for ci, cav in enumerate(cavities):
                m = np.nonzero(cand.cavity == ci)[0]
                vm, cups, _ = vent_plan(cav, R3, pockets=False)
                if not len(m):
                    oks.append(0.0)
                    cups_n += len(cups)
                    unvented += len(cups)
                    continue
                # bu yönde kavitenin vantuz çanaklarından kaçına gizli bölgeden delik açılabiliyor
                hits = [len(_basin_hits(cand, m, vm, b)) > 0 for b in cups]
                cups_n += len(cups)
                unvented += len(cups) - int(sum(hits))
                cov = np.mean(hits) if cups else 1.0
                zcav = float((cav.mesh.vertices @ R3[2]).min())
                gapz = float(zc[m].min()) - zcav - wall
                oks.append(0.8 * float(cov) + 0.2 * float(np.exp(-max(gapz, 0.0) / 5.0)))
            hole_ok = float(np.mean(oks))
        rows.append(dict(d=d, tilt=tilt, R3=R3, height=height, fits=fits, hidden_down=hidden_down,
                         support_vis=support_vis, pin_down=pin_down, peel=peel, hole_ok=hole_ok,
                         cups=cups_n, unvented=unvented, rect=(w, dd, ang), sup_hidden=sup_hidden,
                         sup_low=sup_low, sup_vis=max(0.0, 1.0 - sup_hidden - sup_low)))
    pmax = max(r["peel"] for r in rows) or 1.0
    for r in rows:
        # destek izinin göze batması: gizli yüzler 0, sırt/alt az, ön/yan çok (joints.visibility_weights)
        s = (3.0 * r["hidden_down"] - SUPPORT_W * r["support_vis"] - 1.0 * r["peel"] / pmax
             - 0.5 * r["height"] / diag - 2.0 * r["pin_down"])
        if r["hole_ok"] is not None:
            s += 4.0 * r["hole_ok"]
        # deliklenemeyen her vantuz çanağı baskı hatası riskidir (Lion kaidesi: puanı aynı iki yönden biri 6, diğeri 4
        # deliksiz çanak bırakıyordu): oransal değil, çanak başına ceza
        s -= UNVENTED_PENALTY * r["unvented"]
        if 0 < r["tilt"] < tilt_range[0] - 0.1 or r["tilt"] == 0.0:
            s -= 0.3       # düz kesit plate'e paralel: peel riski
        r["score"] = s if r["fits"] else s - 100.0
    def make(row) -> Orientation:
        R3 = row["R3"]
        # yatay dönme: en küçük alanlı dikdörtgeni eksenlere hizala, uzun kenar X'e
        w, dd, ang = row["rect"]
        c, s_ = np.cos(-ang), np.sin(-ang)
        Rz = np.array([[c, -s_, 0], [s_, c, 0], [0, 0, 1.0]])
        if dd > w:
            Rz = np.array([[0, -1.0, 0], [1.0, 0, 0], [0, 0, 1.0]]) @ Rz
            w, dd = dd, w
        Rf = Rz @ R3
        P = V @ Rf.T
        T = np.eye(4)
        T[:3, :3] = Rf
        T[:3, 3] = -np.array([(P[:, 0].min() + P[:, 0].max()) / 2, (P[:, 1].min() + P[:, 1].max()) / 2,
                              P[:, 2].min() - support_h])
        terms = {k: row[k] for k in ("hidden_down", "support_vis", "pin_down", "peel", "hole_ok", "height", "cups",
                                     "unvented", "sup_hidden", "sup_low", "sup_vis") if k in row}
        return Orientation(T, row["d"], row["tilt"], row["fits"], row["score"], terms, (w, dd), row["height"])

    ranked = sorted(rows, key=lambda r: -r["score"])
    best = make(ranked[0])
    best.rows = rows                     # teşhis için: tüm aday yönlerin ölçümleri
    best.alternatives = [best] + [make(r) for r in select_alternatives(ranked, max_alts)[1:]]
    return best


def select_alternatives(ranked: list[dict], max_alts: int = 3) -> list[dict]:
    """Puana göre sıralı aday satırlarından [en iyi, alternatifler...]: global yerleşim (pipeline) plate sayısını ya da
    baskı süresini düşürmek için gerekirse bunlardan birini seçer."""
    top = ranked[0]

    def safe(r) -> bool:
        """Alternatif baskı güvenliğinden ödün vermemeli: en iyi yönden fazla deliksiz vantuz çanağı bırakmaz, katman
        başına en büyük kesiti (soyulma kuvveti) en çok MAX_PEEL_GROWTH katına çıkarır. Rango pelerini: süre için
        seçilen 41 mm'lik yön kesiti 473 → 1052 mm²'ye (tam dilimleme), aşağı bakan düz yüzeyi %1 → %4'e çıkarıyordu.
        Destekleri de görünür tarafa kaydıramaz: görünürlük ağırlıklı destek alanı en çok MAX_SUPPORT_GROWTH katı."""
        sv_ok = r.get("support_vis", 0.0) <= MAX_SUPPORT_GROWTH * top.get("support_vis", 0.0) + 0.005
        return (r["unvented"] <= top["unvented"] and r["peel"] <= MAX_PEEL_GROWTH * max(top["peel"], 1.0)
                and sv_ok)

    def distinct(r, alts) -> bool:
        return all(np.degrees(np.arccos(np.clip(r["d"] @ a["d"], -1, 1))) >= 15 for a in alts)

    near = [r for r in ranked[1:] if r["fits"] and r["score"] >= top["score"] - ALT_DELTA]
    # plate verimi için: "aşağı" yönü belirgin farklı ve taban izi daha küçük yönler
    alts = [top]
    for r in near:
        if len(alts) >= max_alts + 1:
            break
        if r["rect"][0] * r["rect"][1] < 0.92 * top["rect"][0] * top["rect"][1] and distinct(r, alts) and safe(r):
            alts.append(r)
    # baskı süresi için: belirgin şekilde daha ALÇAK yönler (SLA'da plate'in süresi en uzun parçasıyla belirlenir; o
    # parçayı alçaltmak bütün plate'i kısaltır)
    n_fp = len(alts)
    for r in sorted(near, key=lambda r: r["height"]):
        if len(alts) >= n_fp + max_alts or r["height"] > 0.9 * top["height"]:
            break
        if distinct(r, alts) and safe(r):
            alts.append(r)
    return alts
