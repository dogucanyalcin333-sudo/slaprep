"""Uçtan uca hazırlık (küçük yapay kit): yazıcıya sığmayan parça kaybolmamalı, ölçek önerisiyle bildirilmeli."""
import json

import numpy as np
import trimesh

from slaprep.config import Settings
from slaprep.pipeline import Run


def _box(lo, hi):
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    m = trimesh.creation.box(extents=hi - lo)
    m.apply_translation((lo + hi) / 2)
    return m


def _small_kit(tmp_path):
    kit = tmp_path / "kit"
    kit.mkdir()
    _box([0, 0, 0], [60, 40, 10]).export(kit / "Base.stl")
    _box([10, 5, 10], [50, 35, 70]).export(kit / "Body.stl")       # 40×30×60 mm: hollow edilebilir
    return kit


def _prep(kit, **kw):
    s = Settings()
    for k, v in kw.items():
        setattr(s, k, v)
    run = Run(kit, s, log=lambda m: None)
    run.load()
    run.assemble()
    run.prepare()
    run.save_json("prep.json")
    return run


def test_steps_plates_only(tmp_path):
    """Sadece plate'e diz: parçalar dolu kalır (hollow/delik yok), plate dosyaları yazılır."""
    kit = _small_kit(tmp_path)
    run = _prep(kit, steps="plates")
    assert not any(r.hollowed or r.holes for r in run.results.values())
    assert list((kit / "_slaprep" / "plates").glob("*.stl"))
    assert all(r.plate for r in run.results.values() if r.printed)


def test_steps_hollow_only(tmp_path):
    """Sadece hollow + delik: plate yok; parça hollow edilir ve orijinal duruşunda (dönmeden) yazılır."""
    import trimesh

    kit = _small_kit(tmp_path)
    run = _prep(kit, steps="hollow")
    out = kit / "_slaprep"
    assert not list((out / "plates").glob("*.stl")) and run.plate_checks == {}
    assert run.results["Body"].hollowed
    body = trimesh.load(out / "parts" / "Body.stl", force="mesh")
    assert np.allclose(body.bounds, [[10, 5, 10], [50, 35, 70]], atol=0.05)    # orijinal yerinde, dönmemiş
    assert body.volume < 0.9 * 40 * 30 * 60
    d = json.loads((out / "prep.json").read_text(encoding="utf-8"))
    assert d["steps"] == "hollow" and d["plates"] == {}


def test_part_on_separate_plate_and_forced_solid(tmp_path):
    """Kullanıcı seçimleri: 'ayrı plate' işaretli parça (ör. şeffaf reçineyle basılacak şişe) kendi plate grubuna
    dizilir (plates/ayrı/), 'dolu' işaretli parça hollow edilmez."""
    kit = _small_kit(tmp_path)
    run = _prep(kit, separate=["Body"], solid=["Body"])
    out = kit / "_slaprep"
    assert run.results["Body"].plate == "ayrı/plate_01", run.results["Body"].plate
    assert run.results["Base"].plate == "plate_01"
    assert (out / "plates" / "ayrı" / "plate_01.stl").exists() and (out / "plates" / "plate_01.stl").exists()
    assert not run.results["Body"].hollowed and "--solid" in run.results["Body"].hollow_reason


def test_oversize_part_is_exported_and_reported(tmp_path):
    kit = tmp_path / "kit"
    kit.mkdir()
    _box([0, 0, 0], [240, 140, 60]).export(kit / "Base.stl")         # M7 plate'i 223 × 126: sığmaz
    _box([100, 50, 60], [130, 80, 100]).export(kit / "Body.stl")
    s = Settings()
    s.solid = ["*"]                                                  # hollow gerekmiyor, test hızlı kalsın
    run = Run(kit, s, log=lambda m: None)
    run.load()
    run.assemble()
    run.prepare()
    run.save_json("prep.json")
    w = " ".join(run.warnings)
    assert "sığmayan" in w and "Base" in w and "--scale" in w, run.warnings
    assert (kit / "_slaprep" / "parts" / "Base.stl").exists()
    d = json.loads((kit / "_slaprep" / "prep.json").read_text(encoding="utf-8"))
    assert d["parts"]["Body"]["plate"] and not d["parts"]["Base"]["plate"]
    # önerilen ölçekle gerçekten sığmalı (yön aramasında sınır kutusu yönleri de var)
    k = float(w.split("--scale ")[1].split(",")[0].split(")")[0])
    s.scale = k
    run = Run(kit, s, log=lambda m: None)
    run.load()
    run.assemble()
    run.prepare()
    assert not any("sığmayan" in x for x in run.warnings), run.warnings
    assert run.results["Base"].plate
