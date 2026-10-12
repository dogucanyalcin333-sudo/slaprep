from types import SimpleNamespace

import pytest

from slaprep.config import Settings
from slaprep.scale import compute_scale


def _run(mode, height_before=0.0, **kw):
    s = Settings(**kw)
    return SimpleNamespace(settings=s, asm=SimpleNamespace(mode=mode), height_before=height_before,
                           figure_height_before=height_before * 0.8)


def test_height_from_assembly():
    sc, _ = compute_scale(_run("kısmi", 300.0, height=150.0))
    assert abs(sc - 0.5) < 1e-9


def test_unknown_assembly_needs_orig_height():
    with pytest.raises(SystemExit):
        compute_scale(_run("bilinmiyor", height=150.0))
    sc, w = compute_scale(_run("bilinmiyor", height=300.0, orig_height=400.0))
    assert abs(sc - 0.75) < 1e-9 and any("orijinal boy" in x for x in w)
