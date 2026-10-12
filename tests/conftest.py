import pytest


@pytest.fixture(autouse=True)
def _isolated_user_prefs(tmp_path, monkeypatch):
    """Testler kullanıcının ~/.slaprep_prefs.json dosyasını (reçine fiyatı, gece kuyruğu) okumaz/yazmaz."""
    monkeypatch.setattr("slaprep.prefs.PREFS", tmp_path / "user_prefs.json")
