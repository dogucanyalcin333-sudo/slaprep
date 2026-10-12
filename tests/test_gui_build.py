"""Arayüz duman testi: pencere görünmeden kurulur; işlem seçimi sekmeleri ve komut argümanlarını doğru değiştirir."""
import tkinter as tk

import pytest
import trimesh


@pytest.fixture
def app(tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk yok")
    root.withdraw()
    from slaprep.gui import App

    kit = tmp_path / "kit"
    kit.mkdir()
    trimesh.creation.box(extents=[10, 10, 10]).export(kit / "a.stl")
    a = App(root)
    a.set_kit(kit)
    yield a
    root.destroy()


def test_operation_checkboxes_drive_steps(app):
    """İşlemler onay kutuları: ikisi açık = tam hazırlık; biri kapalıysa ilgili ayar kartı kapanır, ana düğmenin
    adı değişir; ikisi de kapalıysa hazırlanacak bir şey yoktur (düğme kapalı)."""
    app.v_op_hollow.set(False)
    app._on_ops()
    assert app.v_steps.get() == "plates" and not app.sec_hollow.enabled and app.sec_plates.enabled
    assert app.b_prep.cget("text") == "Plate'e diz"
    args = app.build_args("prep")
    assert args[args.index("--steps") + 1] == "plates"

    app.v_op_hollow.set(True)
    app.v_op_plates.set(False)
    app._on_ops()
    assert app.v_steps.get() == "hollow" and app.sec_hollow.enabled and not app.sec_plates.enabled
    assert app.build_args("prep")[app.build_args("prep").index("--steps") + 1] == "hollow"
    assert "--steps" not in app.build_args("analyze")

    app.v_front.set("-Y")                                      # ön yönü elle: destek tarafı ve önizleme
    assert app.build_args("prep")[app.build_args("prep").index("--front-axis") + 1] == "-y"
    assert "--front-axis" in app.build_args("analyze")
    app.v_front.set("otomatik")
    app.v_op_hollow.set(False)
    app._on_ops()
    assert app.v_steps.get() == "" and app.b_prep.instate(["disabled"])
    with pytest.raises(ValueError):
        app.build_args("prep")
    from slaprep.config import Settings
    s = Settings()
    s.steps = "plates"
    app._load_settings(s)                                      # kayıtlı işlem kutulara yansır
    assert not app.v_op_hollow.get() and app.v_op_plates.get() and app.v_steps.get() == "plates"


def test_selection_bar_bulk_and_mixed_state(app, tmp_path):
    """Seçim çubuğu: seçili parçaların ortak durumunu gösterir (karışıksa ara durum), toplu değiştirir; arama
    kutusu listeyi süzer."""
    import trimesh as tm

    for n in ("b", "c"):
        tm.creation.box(extents=[5, 5, 5]).export(app.kit / f"{n}.stl")
    app.set_kit(app.kit)
    app.parts_info["a"]["separate"] = True
    app._refresh_tree()
    app.tree.selection_set(["a", "b"])
    app._sync_selbar()
    assert app.v_sel_count.get() == "2 parça seçili" and app.v_sel_sep.get() == "" and app.v_sel_inc.get() == "1"
    app.v_sel_sep.set("1")
    app._sel_set_separate()
    assert app.parts_info["a"]["separate"] and app.parts_info["b"]["separate"] and not app.parts_info["c"]["separate"]
    app.v_sel_fill.set("solid")
    app._set_fill("solid")
    assert app.tree.set("b", "tercih") == "Dolu" and app.tree.set("c", "tercih") == "Otomatik"
    app._apply_fill(["c"], "hollow")                           # Doluluk hücresi menüsü
    assert app.parts_info["c"]["fill"] == "hollow"
    app.v_part_filter.set("c")
    assert app.tree.get_children() == ("c",) and "gösteriliyor" in app.v_part_count.get()
    app.v_part_filter.set("")
    assert len(app.tree.get_children()) == 3


def test_part_preferences_to_args(app, tmp_path):
    """Parça başına tercihler: seçili parça 'dolu' ve 'ayrı plate' yapılınca komuta --solid / --separate gider;
    kitteki kayıtlı tercihler yüklenince tabloda görünür."""
    app.tree.selection_set("a")
    app._set_fill("solid")
    app._toggle_separate()
    assert app.tree.set("a", "tercih") == "Dolu" and app.tree.set("a", "ayri") == "☑"
    args = app.build_args("prep")
    assert args[args.index("--solid") + 1] == "a" and args[args.index("--separate") + 1] == "a"
    assert "--hollow" not in args
    app._toggle_separate()                                     # ikinci basış: ayrı plate'ten çıkar
    app._set_fill("auto")
    args = app.build_args("prep")
    assert "--solid" not in args and "--separate" not in args

    from slaprep.config import Settings, save_settings
    s = Settings()
    s.hollow, s.separate = ["a"], ["a"]
    save_settings(app.kit, s)
    app.set_kit(app.kit)
    assert app.parts_info["a"]["fill"] == "hollow" and app.parts_info["a"]["separate"]


def test_resin_price_updates_plates_without_rerun(app, tmp_path):
    """Reçine fiyatı girilince plate tablosu ve özet yeniden hazırlamadan güncellenir; fiyat kullanıcıya kaydedilir."""
    import json

    from slaprep.config import out_dir
    from slaprep.resin import load_prefs

    d = {"parts": {"a": dict(printed=True, volume_ml=1.0, vol_before_ml=100.0, vol_after_ml=40.0,
                             plate="plate_01")},
         "plates": {"plate_01": dict(max_height=50.0, est_min=120.0, min_gap=None, inside=True)},
         "print_time_min": 120.0, "scale": 1.0}
    o = out_dir(app.kit)
    o.mkdir(parents=True, exist_ok=True)
    (o / "prep.json").write_text(json.dumps(d), encoding="utf-8")
    app._load_results()
    assert app.ptree.set("plate_01", "recine") == "≈ 46 ml"            # 40 ml + %15 destek payı
    # para birimi açıkça verilir: varsayılanı işletim sisteminin bölgesine göre değişir (TL / USD / EUR …)
    app.v_resin_cur.set("TL")
    app.v_resin_price.set("1000")
    assert app.ptree.set("plate_01", "recine") == "≈ 46 ml · 46.00 TL"
    assert "46.00 TL" in app.summary.get("1.0", "end")
    assert load_prefs().price_l == 1000.0 and load_prefs().currency == "TL"


def test_real_time_entry_calibrates_estimates(app):
    """Plate'ler → Gerçek süre: Bottle plate'i (82.7 mm) 3:31 sürdü → katman süresi ayarlanır, o plate'in tahmini
    yeniden hazırlamadan 3 sa 31 dk olur."""
    import json

    from slaprep.config import out_dir

    d = {"parts": {"a": dict(printed=True, volume_ml=1.0, vol_after_ml=1.0, plate="ayrı/plate_01")},
         "plates": {"ayrı/plate_01": dict(max_height=82.7, est_min=194.2, min_gap=None, inside=True)},
         "print_time_min": 194.2, "scale": 1.0}
    o = out_dir(app.kit)
    o.mkdir(parents=True, exist_ok=True)
    (o / "prep.json").write_text(json.dumps(d), encoding="utf-8")
    app._load_results()
    assert app.ptree.set("ayrı/plate_01", "sure") == "3 sa 14 dk"
    app.v_cal_plate.set("ayrı/plate_01")
    app.v_cal_time.set("3:31")
    app._save_calibration()
    assert float(app.v_layer_t.get()) == pytest.approx(7.11, abs=0.01)
    assert app.ptree.set("ayrı/plate_01", "sure") == "3 sa 31 dk"
    assert "1 ölçümden" in app.v_cal_info.get()
    app.set_kit(app.kit)                                       # başka kit açılınca da ayar geçerli
    assert float(app.v_layer_t.get()) == pytest.approx(7.11, abs=0.01)


def test_night_queue_runs_items_in_order(app, monkeypatch):
    """Gece kuyruğu: kit o anki ayarlarıyla eklenir (aynı kit iki kez eklenmez), sırayla çalışır, sonuç satırı ve
    sabah özeti yazılır, durum kullanıcı tercihlerinde saklanır."""
    import json

    from slaprep import batch as nq
    from slaprep.config import out_dir

    started = []

    def fake_start(args, cmd, msg):
        started.append(args)
        app.proc = object()                      # çalışıyor gibi

    monkeypatch.setattr(app, "_start", fake_start)
    app.v_wall.set("2.5")
    app._queue_add()
    app.v_wall.set("3")
    app._queue_add()                             # aynı kit: yeni ayarlarla güncellenir
    assert len(app.queue_items) == 1 and app.queue_items[0].status == nq.WAITING
    app._queue_start()
    assert started and started[0][0] == "prep" and started[0][started[0].index("--wall") + 1] == "3"
    assert app.queue_items[0].status == nq.RUNNING
    d = {"parts": {"a": dict(printed=True, volume_ml=1.0, vol_after_ml=1.0, plate="plate_01", warnings=[])},
         "plates": {"plate_01": dict(max_height=10.0, est_min=60.0, min_gap=None, inside=True)},
         "print_time_min": 60.0, "scale": 1.0}
    o = out_dir(app.kit)
    o.mkdir(parents=True, exist_ok=True)
    (o / "prep.json").write_text(json.dumps(d), encoding="utf-8")
    app.proc = None
    app._finished(0)
    app._queue_next()                            # (normalde root.after ile)
    it = app.queue_items[0]
    assert it.status == nq.DONE and "1 plate" in it.result and not app.queue_running
    assert "1 kit hazır" in app.v_queue_summary.get()
    assert nq.load_queue()[0].status == nq.DONE   # kalıcı


def test_queue_started_during_single_run(app, monkeypatch, tmp_path):
    """Dün gece: chiro '2) Hazırla' ile tek başına hazırlanırken chiro ve Rango kuyruğa eklendi; 'Hepsini hazırla'
    iş boyunca kapalıydı, iş bitince kuyruk hiç başlamadı (ikisi de sabaha kadar 'bekliyor'). Şimdi: kuyruk iş
    sürerken başlatılabilir, iş bitince kendiliğinden başlar; biten iş kuyruktaki aynı kit + ayarla eşleşirse o
    öğe tekrar hazırlanmaz."""
    import json

    from slaprep import batch as nq
    from slaprep.config import out_dir

    started = []

    def fake_start(args, cmd, msg):
        started.append(list(args))
        app.running_cmd, app.running_args, app.run_started = cmd, list(args), 0.0
        app.proc = object()

    monkeypatch.setattr(app, "_start", fake_start)
    kit_a = app.kit
    kit_b = tmp_path / "kitB"
    kit_b.mkdir()
    trimesh.creation.box(extents=[10, 10, 10]).export(kit_b / "b.stl")
    app.run("prep")                                        # tek kitlik iş (A) sürüyor
    app._queue_add()                                       # A, aynı ayarlarla kuyruğa
    app.set_kit(kit_b)
    app._queue_add()                                       # B
    app._queue_start()                                     # iş sürerken: kuyruk bekler
    assert app.queue_running and len(started) == 1
    assert str(app.b_queue.cget("state")) == "disabled" and "bitince" in app.b_queue.cget("text")
    d = {"parts": {"a": dict(printed=True, volume_ml=1.0, vol_after_ml=1.0, plate="plate_01")},
         "plates": {"plate_01": dict(max_height=10.0, est_min=60.0, min_gap=None, inside=True)},
         "print_time_min": 60.0, "scale": 1.0}
    out_dir(kit_a).mkdir(parents=True, exist_ok=True)
    (out_dir(kit_a) / "prep.json").write_text(json.dumps(d), encoding="utf-8")
    app.proc = None
    app._finished(0)                                       # A bitti
    a, b = app.queue_items
    assert a.status == nq.DONE and "1 plate" in a.result   # tekrar hazırlanmayacak
    app._queue_next()                                      # (normalde root.after ile)
    assert b.status == nq.RUNNING and started[-1] == b.args and len(started) == 2


def test_second_launch_opens_kit_in_running_window(app, tmp_path):
    """Kısayola klasör bırakmak yeni pencere açmaz: klasör açık pencereye iletilir (ayrı pencerelerin her biri
    kuyruğun kendi kopyasını tutup kayıtlı kuyruğu eziyordu)."""
    import socket

    from slaprep.gui import forward_to_running, serve_instance

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert serve_instance(app, port)
    kit_b = tmp_path / "kitB"
    kit_b.mkdir()
    trimesh.creation.box(extents=[10, 10, 10]).export(kit_b / "b.stl")
    import threading
    import time

    res = {}
    th = threading.Thread(target=lambda: res.setdefault("ok", forward_to_running(str(kit_b), port)))
    th.start()
    t0 = time.time()
    while th.is_alive() and time.time() - t0 < 6:          # pencerenin olay döngüsü isteği işler
        app._poll()
        time.sleep(0.02)
    assert res.get("ok") and app.kit == kit_b
    # pencere donmuşsa (olay döngüsü işlemiyor) "tamam" denmez: yeni açılış kendi penceresini açar
    t0 = time.time()
    assert not forward_to_running(str(tmp_path), port) and time.time() - t0 > 2.5
    app._instance_server.close()
    assert not forward_to_running(str(kit_b), port)       # pencere yoksa: yeni pencere açılır


def test_queue_interrupted_item_resumes():
    from slaprep import batch as nq

    nq.save_queue([nq.QueueItem("C:/yok/kit", ["prep"], status=nq.RUNNING)])
    items = nq.load_queue()
    assert items[0].status == nq.STOPPED and nq.next_item(items) is items[0]


def test_log_toggle(app):
    frame = app.log_panel                      # günlük paneli (konsol + kaydırma çubuğu)
    assert not app.log_shown and frame.winfo_manager() == ""
    app._toggle_log()
    assert app.log_shown and frame.winfo_manager() == "pack"
    app._toggle_log()
    assert not app.log_shown and frame.winfo_manager() == ""
