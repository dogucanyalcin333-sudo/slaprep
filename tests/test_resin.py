from slaprep.resin import ResinPrefs, load_prefs, resin_summary, save_prefs, summary_lines

D = {"parts": {
    "Body": dict(printed=True, vol_after_ml=300.0, plate="plate_01"),
    "Arm": dict(printed=True, vol_after_ml=100.0, plate="plate_02"),
    "Bottle": dict(printed=True, vol_after_ml=50.0, plate="ayrı/plate_01"),   # şeffaf reçine
    "Ref": dict(printed=False, vol_after_ml=999.0, plate=None),               # basılmayan parça sayılmaz
}}


def test_plate_group_and_total():
    rs = resin_summary(D, ResinPrefs(price_l=1000.0, separate_price_l=2000.0, extra_pct=10.0, bottle_ml=500.0))
    assert rs["plates"]["plate_01"]["ml"] == 330.0 and rs["plates"]["plate_01"]["cost"] == 330.0
    main, sep = rs["groups"]["ana"], rs["groups"]["ayrı"]
    assert main["ml"] == 440.0 and main["bottles"] == 1 and main["cost"] == 440.0
    assert sep["ml"] == 55.0 and sep["cost"] == 110.0                        # ayrı reçinenin kendi fiyatı
    assert rs["total"]["net_ml"] == 450.0 and rs["total"]["cost"] == 550.0
    text = "\n".join(summary_lines(rs))
    assert "ayrı plate reçinesi" in text and "550.00 TL" in text


def test_without_price_only_ml():
    rs = resin_summary(D, ResinPrefs())
    assert rs["total"]["cost"] is None and rs["plates"]["plate_02"]["cost"] is None
    assert rs["groups"]["ana"]["bottles"] == 1                                # 460 ml < 1 L
    assert any("litre fiyatını" in x for x in summary_lines(rs))


def test_separate_uses_main_price_when_blank():
    rs = resin_summary(D, ResinPrefs(price_l=1000.0, extra_pct=0.0))
    assert rs["groups"]["ayrı"]["cost"] == 50.0


def test_prefs_roundtrip_keeps_other_keys(tmp_path, monkeypatch):
    p = tmp_path / "prefs.json"
    monkeypatch.setattr("slaprep.prefs.PREFS", tmp_path / "yok.json")
    assert load_prefs() == ResinPrefs()
    monkeypatch.setattr("slaprep.prefs.PREFS", p)
    p.write_text('{"other": 1, "resin": {"price_l": 5, "unknown_key": 3}}', encoding="utf-8")
    assert load_prefs().price_l == 5                                          # bilinmeyen anahtar yok sayılır
    save_prefs(ResinPrefs(price_l=899.0, currency="₺"))
    assert load_prefs().currency == "₺" and '"other": 1' in p.read_text(encoding="utf-8")
