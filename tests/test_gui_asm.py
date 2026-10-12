"""Montaj kontrolü sekmesi: ekran görüntüsü gerektirmeden (kilitli ekranda da) davranış testi.

Tk penceresi açılamıyorsa (ekransız ortam) atlanır.
"""
import pytest


@pytest.fixture()
def app(tmp_path):
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk penceresi açılamıyor")
    root.withdraw()
    from slaprep.gui import App

    kit = tmp_path / "kit"
    (kit / "A").mkdir(parents=True)
    import trimesh

    for n in ("Body", "Head", "Arm", "Sword"):
        trimesh.creation.box(extents=[10, 10, 10]).export(kit / "A" / f"{n}.stl")
    a = App(root, str(kit))
    yield a, kit
    root.destroy()


def _data():
    return {"parts": {"Body": {"placed": True}, "Head": {"placed": True}, "Arm": {"placed": True},
                      "Sword": {"placed": False}},
            "assembly": {"mode": "otomatik", "height_mm": 123.4, "selected": ["Body", "Head", "Arm", "Sword"],
                         "links": {"Body": {"kind": "kök", "contact": 0, "to": None},
                                   "Head": {"kind": "ek yüzeyi", "contact": 900, "to": "Body"},
                                   "Arm": {"kind": "ek yüzeyi", "contact": 80, "to": "Body"}}}}


def test_checklist_rows_and_header(app):
    a, _ = app
    n = a._fill_asm(_data())
    rows = a.atree.get_children()
    assert rows[0] == "Sword"                                  # yerleşmeyen önce
    assert a.atree.item("Arm", "tags") == ("check",)            # zayıf ek
    assert a.atree.item("Head", "tags") == ("ok",)
    assert n == 2
    head = a.v_asm_head.get()
    assert "3/4" in head and "123.4" in head and "kontrol edilecek: 1" in head


def test_fixes_saved_and_passed_to_cli(app):
    from slaprep.config import load_settings

    a, kit = app
    a._fill_asm(_data())
    a.atree.selection_set("Arm")
    a._fix_detach()
    a.atree.selection_set("Sword")
    a.v_anchor.set("Head")
    a._fix_join()
    s = load_settings(kit)
    assert s.asm_detach == ["Arm"] and s.asm_join == [["Head", "Sword"]]
    args = a.build_args("analyze")
    assert args[args.index("--detach") + 1] == "Arm"
    assert args[args.index("--join") + 1] == "Head:Sword"
    assert "Sword → Head" in a.v_fixes.get()
    # bağlanan parça artık ayrılmış sayılmaz; kaldırma ikisini de temizler
    a.atree.selection_set("Arm")
    a.v_anchor.set("Body")
    a._fix_join()
    assert a.fix_detach == [] and ["Body", "Arm"] in a.fix_join
    # başka konum dene: sıra 2, 3, 4, sonra başa döner
    a.atree.selection_set("Sword")
    a._fix_next_pose()
    args = a.build_args("analyze")
    assert "Head:Sword:2" in args and "(konum 2)" in a.v_fixes.get()
    for _ in range(3):
        a._fix_next_pose()
    assert ["Head", "Sword"] in a.fix_join
    a._fix_clear()
    assert load_settings(kit).asm_join == [] and "--join" not in a.build_args("analyze")


def test_preview_is_cropped(app, tmp_path):
    from PIL import Image

    a, kit = app
    prev = kit / "_slaprep" / "previews"
    prev.mkdir(parents=True)
    im = Image.new("RGB", (800, 600), (255, 255, 255))
    im.paste((200, 30, 30), (300, 200, 500, 400))                # beyaz zemin üstünde 200×200 kutu
    im.save(prev / "assembly_front.png")
    a._show_asm_image()
    src = a._load_cropped(prev / "assembly_front.png")
    assert src.width <= 215 and src.height <= 215                # kenar boşlukları kırpıldı


def test_height_ruler_follows_scale_settings(app):
    import json

    from PIL import Image

    a, kit = app
    prev = kit / "_slaprep" / "previews"
    prev.mkdir(parents=True)
    Image.new("RGB", (300, 900), (200, 200, 200)).save(prev / "height.png")
    (prev / "height.json").write_text(json.dumps(dict(height_mm=250.0, width_mm=83.3, figure_bottom_mm=50.0,
                                                      scale=1.0)), encoding="utf-8")
    a.v_view.set("height")
    a._on_view_change()
    a.v_scale_mode.set("orig")
    assert "~250 mm" in a.v_final_height.get() and "~200 mm" in a.v_final_height.get()
    a.v_scale_mode.set("height")
    a.v_height.set("300")                                        # toplam 300 → ölçek 1.2, figür 240
    assert a._target_heights() == (300.0, 240.0)
    a.v_height_mode.set("Sadece figür (kaide hariç)")            # figür 300 → ölçek 1.5, toplam 375
    assert a._target_heights() == (375.0, 300.0)
    a.v_scale_mode.set("scale")
    a.v_scale.set("0.5")
    assert a._target_heights() == (125.0, 100.0)
    img = a._compose()
    assert img.width > 300 and img.height > 900                  # cetvel + başlık eklendi


def test_photo_compare(app, tmp_path):
    from PIL import Image

    a, kit = app
    prev = kit / "_slaprep" / "previews"
    prev.mkdir(parents=True)
    for ang in a.COMPARE_ANGLES:
        Image.new("RGB", (300, 400), (190, 190, 190)).save(prev / f"compare_{ang:03d}.png")
    photo = tmp_path / "studyo.jpg"
    Image.new("RGB", (600, 800), (40, 60, 90)).save(photo)
    a._add_photos([photo])
    assert photo.exists()                                        # orijinal yerinde
    assert [p.name for p in a._photos()] == ["studyo.jpg"]       # kopyası kitin _slaprep\photos klasöründe
    assert a.v_view.get() == "compare"
    out = a._compose()
    assert out.width > out.height                                # montaj | fotoğraf yan yana
    a._cmp_step("angle", 3)
    assert a._asm_image_path().name == "compare_135.png"
    assert "Fotoğraf 1/1" in a.v_cmp_info.get()
