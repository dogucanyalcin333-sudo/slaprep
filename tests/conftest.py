import pytest


@pytest.fixture(autouse=True)
def _isolated_user_prefs(tmp_path, monkeypatch):
    """Testler kullanıcının ~/.slaprep_prefs.json dosyasını (reçine fiyatı, gece kuyruğu) okumaz/yazmaz."""
    monkeypatch.setattr("slaprep.prefs.PREFS", tmp_path / "user_prefs.json")


@pytest.fixture(autouse=True)
def _fixed_language(monkeypatch):
    """Testlerdeki metin karşılaştırmaları dile bağlı olmasın: her yerde Türkçe (çevirisiz).

    İşletim sistemi dili İngilizce olan bir makinede de testler aynı metinleri görür.
    """
    monkeypatch.setenv("SLAPREP_LANG", "tr")
    from slaprep import i18n

    i18n.set_lang("tr")
