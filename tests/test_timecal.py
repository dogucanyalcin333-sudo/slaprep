"""Süre tahmini ayarı: kullanıcının ölçtüğü gerçek plate süreleri (Rango Bottle: 82.7 mm, 3 sa 31 dk)."""
import pytest

from slaprep.timecal import CalPoint, add_point, fit, load_points, parse_duration


@pytest.mark.parametrize("text,minutes", [("3:31", 211), ("3 sa 31 dk", 211), ("3s 31d", 211), ("211", 211),
                                          ("211 dk", 211), ("3.5 sa", 210), ("45 dk", 45), ("1:02:30", 62.5)])
def test_parse_duration(text, minutes):
    assert parse_duration(text) == pytest.approx(minutes)


def test_parse_duration_rejects_garbage():
    assert parse_duration("") is None and parse_duration("yarım gün") is None


def test_single_measurement_fixes_overhead():
    """Tek ölçüm: plate başına ek süre kullanıcınınki (15 dk) kalır, katman süresi bulunur. Bottle: 1654 katmanda
    (211 − 15) dk → 7.11 sn/katman (eski 6.5 sn ile tahmin 3 sa 14 dk idi)."""
    lt, ov = fit([CalPoint(82.7, 211.0, 0.05)], 15.0)
    assert ov == 15.0 and lt == pytest.approx(7.11, abs=0.01)
    assert 82.7 / 0.05 * lt / 60 + ov == pytest.approx(211.0, abs=0.1)


def test_two_heights_fit_both():
    true_lt, true_ov = 7.4, 6.0
    pts = [CalPoint(h, h / 0.05 * true_lt / 60 + true_ov, 0.05) for h in (40.0, 120.0)]
    lt, ov = fit(pts, 15.0)
    assert lt == pytest.approx(true_lt, abs=1e-6) and ov == pytest.approx(true_ov, abs=1e-6)


def test_same_plate_replaced_not_doubled():
    add_point(CalPoint(82.7, 200.0, 0.05, "Rango / ayrı/plate_01"))
    add_point(CalPoint(82.7, 211.0, 0.05, "Rango / ayrı/plate_01"))
    pts = load_points()
    assert len(pts) == 1 and pts[0].minutes == 211.0


def test_cli_uses_calibration_unless_given(tmp_path):
    from argparse import Namespace

    from slaprep.cli import settings_from_args

    add_point(CalPoint(82.7, 211.0, 0.05, "x"))
    s = settings_from_args(tmp_path, Namespace(reset=True, cmd="prep"))
    assert s.layer_time == pytest.approx(7.11, abs=0.01)
    s = settings_from_args(tmp_path, Namespace(reset=True, cmd="prep", layer_time=6.0))
    assert s.layer_time == 6.0
