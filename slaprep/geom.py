"""open3d tabanlı hızlı mesafe sorguları ve yüzey örnekleme."""
from __future__ import annotations

import numpy as np
import open3d as o3d
import trimesh


class DistanceField:
    """Bir mesh için signed distance / occupancy sorguları (open3d RaycastingScene)."""

    def __init__(self, mesh: trimesh.Trimesh):
        self.mesh = mesh
        self.bounds = mesh.bounds.copy()
        self.scene = o3d.t.geometry.RaycastingScene()
        tm = o3d.t.geometry.TriangleMesh()
        tm.vertex.positions = o3d.core.Tensor(np.asarray(mesh.vertices, dtype=np.float32))
        tm.triangle.indices = o3d.core.Tensor(np.asarray(mesh.faces, dtype=np.int32))
        self.scene.add_triangles(tm)

    def signed(self, pts: np.ndarray, chunk: int = 2_000_000) -> np.ndarray:
        """Negatif = mesh'in içinde."""
        pts = np.asarray(pts, dtype=np.float32)
        out = np.empty(len(pts), dtype=np.float32)
        for i in range(0, len(pts), chunk):
            out[i:i + chunk] = self.scene.compute_signed_distance(
                o3d.core.Tensor(pts[i:i + chunk])).numpy()
        return out

    def unsigned(self, pts: np.ndarray, chunk: int = 4_000_000) -> np.ndarray:
        pts = np.asarray(pts, dtype=np.float32)
        out = np.empty(len(pts), dtype=np.float32)
        for i in range(0, len(pts), chunk):
            out[i:i + chunk] = self.scene.compute_distance(o3d.core.Tensor(pts[i:i + chunk])).numpy()
        return out

    def occupancy(self, pts: np.ndarray, chunk: int = 4_000_000) -> np.ndarray:
        pts = np.asarray(pts, dtype=np.float32)
        out = np.empty(len(pts), dtype=bool)
        for i in range(0, len(pts), chunk):
            out[i:i + chunk] = self.scene.compute_occupancy(
                o3d.core.Tensor(pts[i:i + chunk]), nsamples=3).numpy() > 0.5
        return out


def sample_surface(mesh: trimesh.Trimesh, spacing_mm2: float = 0.1, cap: int = 80_000, seed: int = 1):
    """Alan-orantılı yüzey örnekleri. Dönüş: (noktalar, normaller, nokta başına alan)."""
    n = int(np.clip(mesh.area / spacing_mm2, 200, cap))
    pts, fi = trimesh.sample.sample_surface(mesh, n, seed=seed)
    return pts, mesh.face_normals[fi], mesh.area / n


def boxes_overlap(a: np.ndarray, b: np.ndarray, axes, margin: float = 0.0) -> bool:
    axes = list(axes)
    return bool(np.all(a[0][axes] - margin <= b[1][axes]) and np.all(b[0][axes] - margin <= a[1][axes]))
