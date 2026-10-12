"""Hafif kit taraması (sadece kutu + üçgen sayısı)."""
from pathlib import Path

import numpy as np

from slaprep.scan import KitScan, StlInfo, classify


def _f(name, lo, hi, tri):
    return StlInfo(Path(name), tri, np.array([lo, hi], float))


def test_scan_ignores_ad_plates():
    """İndirme sitesinin reklam plakaları (Frieren: 'Torso.STL' 595×83×17 mm 5 bin üçgen, 'Miniatures' kalınlıksız)
    montaj sınıflandırmasına katılmamalı; figürün düz ama yoğun parçası (kaide plakası) katılmalı."""
    files = [_f("Body.stl", [60, 50, 120], [140, 150, 250], 700_000),
             _f("Head.stl", [50, 60, 120], [160, 210, 270], 550_000),
             _f("Base.stl", [0, 90, 0], [200, 250, 138], 340_000),
             _f("Plinth.stl", [0, 80, 0], [210, 260, 8], 300_000),             # düz ama yoğun: gerçek parça
             _f("Torso.STL", [-300, -18, 0], [295, 65, 17], 5_150),
             _f("Miniatures.STL", [-146, -41, 0], [166, 29, 0], 12_140)]
    kit = classify(KitScan(Path("kit"), files))
    assert kit.status == "tam montajlı" and kit.connected_frac == 1.0
    assert kit.junk == ["Miniatures.STL", "Torso.STL"]
    assert kit.extent.max() < 300
