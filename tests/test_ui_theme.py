"""Tema: esnetilen resimlerin ortası büyük olmalı. ttk resmin ortasını döşer; ortası 2-4 piksel olan kart resmi
büyük pencerede on binlerce parçayla boyanıyor, uygulama ilk açılışta 20+ sn 'yanıt vermiyor' oluyordu."""
from PIL import Image

from slaprep.ui_theme import Theme


def test_nine_slice_keeps_corners_and_grows_center():
    im = Image.new("RGBA", (20, 20), (255, 255, 255, 255))
    im.putpixel((0, 0), (255, 0, 0, 255))                 # sol üst köşe
    im.putpixel((19, 19), (0, 0, 255, 255))               # sağ alt köşe
    out = Theme._nine(im, (8, 8, 8, 8), 160)
    assert out.size == (176, 176)
    assert out.getpixel((0, 0)) == (255, 0, 0, 255) and out.getpixel((175, 175)) == (0, 0, 255, 255)
    assert Theme._sides(6) == (6, 6, 6, 6) and Theme._sides((4, 6)) == (4, 6, 4, 6)


def test_stretched_element_images_are_large():
    import tkinter as tk

    import pytest

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk yok")
    root.withdraw()
    try:
        th = Theme(root)
        big = [p for p in th._keep if p.width() >= 150 and p.height() >= 150]
        assert len(big) >= 10                              # kart, düğme, alan, sekme, başlık… resimleri
    finally:
        root.destroy()
