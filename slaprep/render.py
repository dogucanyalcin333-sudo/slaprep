"""Önizleme görselleri: matplotlib ile ortografik, gölgeli, painter's algoritması (OpenGL gerekmez)."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import trimesh  # noqa: E402
from matplotlib.collections import PolyCollection  # noqa: E402

from .fmt import nice_step  # noqa: E402
from .io import simplify  # noqa: E402

PALETTE = [
    "#4C78A8", "#F58518", "#54A24B", "#E45756", "#72B7B2", "#EECA3B", "#B279A2", "#FF9DA6",
    "#9D755D", "#BAB0AC", "#2E86AB", "#A23B72", "#F18F01", "#6A994E", "#C73E1D", "#3B1F2B",
]


def view_basis(up: np.ndarray, cam: np.ndarray):
    up = up / np.linalg.norm(up)
    cam = cam / np.linalg.norm(cam)
    right = np.cross(up, cam)
    right /= np.linalg.norm(right)
    up = np.cross(cam, right)
    return right, up, cam


def parse_axis(s: str | None) -> np.ndarray | None:
    """'x', '-y', '+z' → birim vektör; boş/yanlışsa None."""
    if not s:
        return None
    t = s.strip().lower()
    k = t.lstrip("+-")
    if k not in ("x", "y", "z"):
        return None
    v = np.zeros(3)
    v["xyz".index(k)] = -1.0 if t.startswith("-") else 1.0
    return v


def default_views(up_axis: int, up_sign: int, front_override: np.ndarray | None = None
                  ) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Ön ve yan kamera yönleri (nesneden kameraya = figürün baktığı yön). Kit export kuralı: Z yukarıysa figür −Y'ye,
    Y yukarıysa +Z'ye bakar (Blender / ZBrush). Dosya baş aşağı export edildiyse (yukarı −Y: Atsu, Griffith) model
    yatay eksen etrafında 180° dönmüştür, ön de ters döner: 'önden' görünüm eskiden figürün sırtını gösteriyordu.
    front_override: kullanıcının verdiği ön yön (--front-axis)."""
    up = np.zeros(3)
    up[up_axis] = up_sign
    others = [k for k in range(3) if k != up_axis]
    if up_axis == 2:
        front = np.array([0.0, -1.0, 0.0])
        side = np.array([1.0, 0.0, 0.0])
    else:
        front = np.eye(3)[2] if 2 in others else np.eye(3)[others[1]]
        side = np.eye(3)[others[0]]
    front = front * up_sign
    if front_override is not None and abs(float(np.dot(front_override, up))) < 0.5:
        front = np.asarray(front_override, float)
        side = np.cross(up, front)
    return {"front": (up, front), "side": (up, side)}


def draw(ax, items, up, cam, light_extra=None, edge=False):
    """items: [(mesh, renk veya yüz başına renk dizisi)]"""
    right, up, cam = view_basis(np.asarray(up, float), np.asarray(cam, float))
    L = cam + 0.6 * up + 0.4 * right
    L /= np.linalg.norm(L)
    polys, cols, depth = [], [], []
    for mesh, color in items:
        n = mesh.face_normals
        vis = n @ cam > -0.05
        if not vis.any():
            continue
        tri = mesh.vertices[mesh.faces[vis]]
        p2 = np.stack([tri @ right, tri @ up], axis=-1)
        dep = (tri @ cam).mean(1)
        shade = 0.30 + 0.70 * np.clip(n[vis] @ L, 0, 1)
        if isinstance(color, np.ndarray) and color.ndim == 2:
            base = color[vis][:, :3]
        else:
            base = np.tile(matplotlib.colors.to_rgb(color), (vis.sum(), 1))
        polys.append(p2)
        cols.append(np.clip(base * shade[:, None], 0, 1))
        depth.append(dep)
    if not polys:
        return
    P = np.concatenate(polys)
    C = np.concatenate(cols)
    D = np.concatenate(depth)
    o = np.argsort(D)
    pc = PolyCollection(P[o], facecolors=C[o], edgecolors=C[o] if not edge else "k", linewidths=0.15,
                        antialiaseds=False)
    ax.add_collection(pc)
    ax.set_xlim(P[..., 0].min() - 2, P[..., 0].max() + 2)
    ax.set_ylim(P[..., 1].min() - 2, P[..., 1].max() + 2)
    ax.set_aspect("equal")
    ax.axis("off")


def preview_mesh(mesh: trimesh.Trimesh, faces: int = 12000) -> trimesh.Trimesh:
    if len(mesh.faces) <= faces:
        return mesh
    pv = simplify(mesh, faces)
    # kenar daraltma bazı mesh'lerde çöküyor (Radahn Hair_5 önizlemede blok gibi): yüzeye sapma testi, tutmazsa
    # voxel kümelemeli yol
    from .geom import DistanceField
    from .io import _cluster_simplify

    pts, _ = trimesh.sample.sample_surface(pv, 3000, seed=0)
    d = DistanceField(mesh).unsigned(pts)
    if np.percentile(d, 95) > max(0.6, 0.005 * float(np.linalg.norm(mesh.extents))):
        pv = _cluster_simplify(mesh, faces)
    return pv


LABEL_COLORS = {
    "visible": (0.74, 0.74, 0.74),
    "outer_hidden": (0.55, 0.72, 0.92),   # gizli ama dış yüzey (kaide altı, duvar arkası): delik yok
    "hidden": (0.45, 0.80, 0.45),         # birleşme yüzeyi: delik açılabilir
    "safe": (0.10, 0.55, 0.20),
    "pin": (0.97, 0.60, 0.10),
}


def label_colors(hidden, safe, pin, allowed=None) -> np.ndarray:
    col = np.tile(LABEL_COLORS["visible"], (len(hidden), 1))
    if allowed is not None:
        col[hidden & ~allowed] = LABEL_COLORS["outer_hidden"]
        hidden = hidden & allowed
    col[hidden] = LABEL_COLORS["hidden"]
    if safe is not None:
        col[safe] = LABEL_COLORS["safe"]
    col[pin] = LABEL_COLORS["pin"]
    return col


def transfer_face_values(src: trimesh.Trimesh, dst: trimesh.Trimesh, values: np.ndarray) -> np.ndarray:
    """Sadeleştirilmiş önizleme mesh'ine yüz etiketlerini en yakın yüz merkezi ile aktar."""
    from scipy.spatial import cKDTree

    _, i = cKDTree(src.triangles_center).query(dst.triangles_center, k=1)
    return values[i]


def render_part(mesh: trimesh.Trimesh, colors: np.ndarray, out: Path, title: str, focus: np.ndarray | None,
                up: np.ndarray, holes: list | None = None, faces: int = 30000) -> Path:
    """Parçayı iki yönden çiz: gizli bölgeye bakan taraf ve karşı taraf. holes: [(nokta, normal, çap)]"""
    pv = preview_mesh(mesh, faces)
    col = transfer_face_values(mesh, pv, colors) if pv is not mesh else colors
    cam = focus if focus is not None and np.linalg.norm(focus) > 1e-6 else np.array([0.3, -1.0, 0.4])
    cam = cam / np.linalg.norm(cam)
    upv = np.asarray(up, float)
    if abs(cam @ upv) > 0.95:
        upv = np.array([0.0, 0.0, 1.0]) if abs(cam[2]) < 0.9 else np.array([0.0, 1.0, 0.0])
    fig, axes = plt.subplots(1, 2, figsize=(8, 4.2), dpi=100)
    for ax, c, lbl in ((axes[0], cam, "gizli taraf"), (axes[1], -cam, "karşı taraf")):
        draw(ax, [(pv, col)], upv, c)
        if holes:
            right, u2, c2 = view_basis(upv, c)
            for p, n, d in holes:
                if n @ c2 > -0.2:
                    ax.add_patch(plt.Circle((p @ right, p @ u2), max(d / 2, 0.8), color="#e01b24", zorder=10))
        ax.set_title(lbl, fontsize=8)
    fig.suptitle(title, fontsize=10)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return out


def render_plate(items: list[tuple[str, trimesh.Trimesh]], plate_xy: tuple[float, float], out: Path,
                 title: str) -> Path:
    """Plate'in üstten görünümü (baskı koordinatları, plate merkezli)."""
    W, D = plate_xy
    fig, ax = plt.subplots(figsize=(8, 8 * D / W + 0.8), dpi=100)
    colored = [(preview_mesh(m, 25000), PALETTE[i % len(PALETTE)]) for i, (_, m) in enumerate(items)]
    draw(ax, colored, np.array([0, 1.0, 0]), np.array([0, 0, 1.0]))
    ax.add_patch(plt.Rectangle((-W / 2, -D / 2), W, D, fill=False, lw=1.5, ec="#333"))
    for i, (n, m) in enumerate(items):
        c = m.bounds.mean(0)
        ax.text(c[0], c[1], n, fontsize=7, ha="center", va="center",
                bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.7))
    ax.set_xlim(-W / 2 - 3, W / 2 + 3)
    ax.set_ylim(-D / 2 - 3, D / 2 + 3)
    ax.set_title(title, fontsize=10)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return out


def render_height_view(items: list[tuple[str, trimesh.Trimesh]], base_names: list[str], up_axis: int,
                       up_sign: int, out_dir: Path, scale: float = 1.0, front=None) -> dict[str, Path]:
    """Boy görünümü: önden, perspektifsiz, resim tam olarak montajın kutusuna oturur (piksel ↔ mm doğrusal) —
    uygulama cetveli bunun yanına hedef boya göre canlı çizer. height.json eşleme bilgisini tutar; rapor için
    cetvelli bir kopya (height_ruler.png) da üretilir. items ölçeklenmiş (mm) mesh'ler."""
    import json

    up, front = default_views(up_axis, up_sign, front)["front"]
    right, up2, _ = view_basis(up, front)
    allv = np.vstack([m.vertices for _, m in items])
    lo, hi = float((allv @ up2).min()), float((allv @ up2).max())
    xl, xh = float((allv @ right).min()), float((allv @ right).max())
    H, W = max(hi - lo, 1e-6), max(xh - xl, 1e-6)
    base = [m for n, m in items if n in base_names]
    figs = [m for n, m in items if n not in base_names]
    base_top = float(max((m.vertices @ up2).max() for m in base)) if base else None
    fig_lo = float(min((m.vertices @ up2).min() for m in figs)) if figs else lo
    cols = [(m, "#a9a9a9" if n in base_names else "#c8c8c8") for n, m in items]
    hpx = 900
    wpx = int(np.clip(hpx * W / H, 120, 1400))
    fig = plt.figure(figsize=(wpx / 100, hpx / 100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    draw(ax, cols, up, front)
    ax.set_aspect("auto")                    # sınırlar = montaj kutusu: piksel ↔ mm doğrusal
    ax.set_xlim(xl, xh)
    ax.set_ylim(lo, hi)
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "height.png"
    fig.savefig(p, facecolor="white")
    plt.close(fig)
    meta = dict(height_mm=H, width_mm=W, base_top_mm=None if base_top is None else base_top - lo,
                figure_bottom_mm=fig_lo - lo, scale=scale)
    (out_dir / "height.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    # rapor için cetvelli kopya
    img = plt.imread(p)
    fig, ax = plt.subplots(figsize=(max(3.0, 6.0 * W / H) + 1.2, 6.6), dpi=100)
    ax.imshow(img, extent=[0, W, 0, H])
    step = nice_step(H)
    ax.set_yticks(np.arange(0, H + 1e-6, step))
    ax.set_yticks(np.arange(0, H + 1e-6, step / 5), minor=True)
    ax.set_xticks([])
    ax.set_ylabel("mm")
    ax.grid(axis="y", which="major", color="#dddddd", lw=0.6)
    if base and figs and fig_lo - lo > 0.5:
        # özetteki "figür yüksekliği (kaide hariç)" ile aynı tanım: figür parçalarının en alt noktasından tepeye
        ax.axhline(fig_lo - lo, color="#c0392b", ls="--", lw=1)
        ax.text(0, fig_lo - lo, f" kaide hariç figür: {hi - fig_lo:.0f} mm", va="bottom", ha="left",
                fontsize=8, color="#c0392b", backgroundcolor="white")
    ax.set_title(f"Boy: {H:.1f} mm ({H / 10:.1f} cm)", fontsize=11)
    for s in ("top", "right", "bottom"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    pr = out_dir / "height_ruler.png"
    fig.savefig(pr)
    plt.close(fig)
    return {"height": p, "height_ruler": pr}


def render_turntable(items: list[tuple[str, trimesh.Trimesh]], up_axis: int, up_sign: int, out_dir: Path,
                     n: int = 8, elev: float = 12.0, front=None) -> list[Path]:
    """Stüdyo fotoğraflarıyla karşılaştırma için tek renk (gri, ürün görseli gibi) görünümler: yukarı ekseni
    etrafında n açı, hafif yukarıdan. Dosyalar: compare_000.png, compare_045.png …"""
    up, front = default_views(up_axis, up_sign, front)["front"]
    side = np.cross(up, front)
    gray = [(m, "#bdbdbd") for _, m in items]
    e = np.radians(elev)
    out = []
    out_dir.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        a = 2 * np.pi * i / n
        cam = np.cos(e) * (np.cos(a) * front + np.sin(a) * side) + np.sin(e) * up
        fig, ax = plt.subplots(figsize=(4.5, 6), dpi=100)
        draw(ax, gray, up, cam)
        fig.subplots_adjust(0, 0, 1, 1)
        p = out_dir / f"compare_{round(360 * i / n):03d}.png"
        fig.savefig(p, facecolor="white")
        plt.close(fig)
        out.append(p)
    return out


def render_assembly(items: list[tuple[str, trimesh.Trimesh]], up_axis: int, up_sign: int, out_dir: Path,
                    title: str = "", front=None) -> dict[str, Path]:
    """Montajın önden ve yandan görünümü. items: [(ad, ötelenmiş mesh)]."""
    out = {}
    views = default_views(up_axis, up_sign, front)
    colored = [(m, PALETTE[i % len(PALETTE)]) for i, (_, m) in enumerate(items)]
    for vname, (up, cam) in views.items():
        fig, ax = plt.subplots(figsize=(7, 9), dpi=110)
        draw(ax, colored, up, cam)
        ax.set_title(f"{title} — {'önden' if vname == 'front' else 'yandan'}", fontsize=10)
        handles = [plt.Line2D([], [], marker="s", ls="", color=PALETTE[i % len(PALETTE)], label=n)
                   for i, (n, _) in enumerate(items)]
        ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(1.0, 1.0), fontsize=7, frameon=False)
        fig.tight_layout()
        p = out_dir / f"assembly_{vname}.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(p)
        plt.close(fig)
        out[vname] = p
    return out
