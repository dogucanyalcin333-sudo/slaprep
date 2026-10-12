"""Hollow: kavite üretimi ve güvenli geri dönüşler."""
import trimesh

from slaprep import hollow


def _ball(r):
    return trimesh.creation.icosphere(subdivisions=4, radius=r)


def test_hollow_ball_has_one_cavity():
    m = _ball(15.0)                                              # ~14 ml
    h = hollow.hollow_part(m, 2.0, min_cavity_ml=0.5, min_ml=3.0, min_frac=0.25, max_cavities=4, res=0.6)
    assert h.hollowed and len(h.cavities) == 1
    assert 0.4 * m.volume < h.cavities[0].volume < m.volume


def test_tiny_part_is_not_hollowed():
    """Kavitesi eşiğe hiç ulaşamayacak küçük parça (OUISTCRAM harfleri) hollow denenmeden dolu kalır."""
    h = hollow.hollow_part(_ball(5.0), 1.0, min_cavity_ml=0.5, min_ml=3.0, min_frac=0.25, max_cavities=4)
    assert not h.hollowed and "ml" in h.reason


def test_inner_shell_retried_before_giving_up(monkeypatch):
    """İç kabuk ilk denemede geçersiz çıksa da (Atsu pantolonu: marching cubes 12 açık kenar bıraktı) parça hemen
    dolu bırakılmaz: kabuk onarılıp / yeniden hesaplanıp hollow edilir."""
    real = hollow.to_manifold
    calls = []

    def flaky(m, *a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("manifold değil: Error.NotManifold")
        return real(m, *a, **k)
    monkeypatch.setattr(hollow, "to_manifold", flaky)
    h = hollow.hollow_part(_ball(15.0), 2.0, min_cavity_ml=0.5, min_ml=3.0, min_frac=0.25, max_cavities=4, res=0.6)
    assert h.hollowed and len(calls) >= 2


def test_invalid_inner_shell_keeps_part_solid(monkeypatch):
    """Marching cubes geçersiz (manifold olmayan) iç kabuk üretirse çökme değil, parça dolu."""
    def bad(*a, **k):
        raise ValueError("manifold değil: Error.NotManifold")
    monkeypatch.setattr(hollow, "to_manifold", bad)
    h = hollow.hollow_part(_ball(15.0), 2.0, min_cavity_ml=0.5, min_ml=3.0, min_frac=0.25, max_cavities=4, res=0.6)
    assert not h.hollowed and "geçersiz" in h.reason
