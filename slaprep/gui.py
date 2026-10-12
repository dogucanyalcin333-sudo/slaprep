"""SLAPrep masaüstü arayüzü (Tkinter, ui_theme ile modern görünüm).

İşler ayrı bir süreçte `python -m slaprep ...` olarak çalışır; arayüz donmaz, çıktı canlı günlükte görünür.
Klasörü pencereye veya masaüstü kısayoluna sürükleyip bırakmak yeterli (uygulama tek pencere çalışır).

Yerleşim: üstte kit seçimi, solda katlanabilir ayar kartları (en üstte işlem onay kutuları), sağda sekmeler
(Parçalar, Plate'ler, Özet, Montaj kontrolü, Kuyruk), altta işlem çubuğu ve açılır günlük.
"""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections import deque
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import batch as nq
from . import prefs
from .asmcheck import TAG_COLORS as ASM_TAG_COLORS
from .asmcheck import checklist
from .config import PRINTERS, Settings, load_settings, out_dir
from .resin import ResinPrefs, load_prefs, resin_cell, resin_summary, save_prefs, summary_lines
from .ui_theme import C, Card, ScrollFrame, Section, Segmented, TabView, Theme, ThinProgress, Tip, stat_tile

APP_TITLE = "SLAPrep — SLA baskı hazırlığı"
ASSETS = Path(__file__).parent / "assets"
HISTORY = Path.home() / ".slaprep_history.json"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
LOW_PRIORITY = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)   # alt süreçler (pymeshfix havuzu) de miras alır
QUEUE_COLORS = {nq.WAITING: C["muted"], nq.RUNNING: C["accent_text"], nq.DONE: C["ok"], nq.FAILED: C["bad"],
                nq.STOPPED: C["warn"]}
UP_CHOICES = ["otomatik", "+Y", "+Z", "+X", "-Y", "-Z", "-X"]
GOALS = {"En kısa toplam süre": "time", "En az plate": "plates"}
MODE_TXT = {"tam": "tam montajlı", "kısmi": "kısmen kaymış (çözüldü)", "otomatik": "otomatik birleştirildi", "bilinmiyor": "montaj bilinmiyor"}
# Hazırlık işlemi (iki onay kutusundan türetilir: hollow + delik, yön + plate)
STEPS = {"all": "Tam hazırlık", "plates": "Sadece plate'e diz", "hollow": "Sadece hollow + delik"}
STEPS_BUTTON = {"all": "Hazırla", "plates": "Plate'e diz", "hollow": "Hollow + delik"}
FILL_TXT = {"auto": "Otomatik", "solid": "Dolu", "hollow": "Hollow"}     # parça başına doluluk tercihi
CHECK = {True: "☑", False: "☐"}


def _python() -> str:
    exe = Path(sys.executable)
    cand = exe.with_name("python.exe")
    return str(cand if cand.exists() else exe)


def _recent_kits() -> list[str]:
    try:
        return [h["kit"] for h in json.loads(HISTORY.read_text(encoding="utf-8")) if Path(h["kit"]).is_dir()]
    except Exception:
        return []


IMAGE_DIR_HINT = re.compile(r"render|photo|foto|preview|önizleme|image|img|pic|resim|gorsel|görsel", re.IGNORECASE)


def find_kit_images(kit: Path, exts, limit: int = 24) -> list[Path]:
    """Kitle gelen stüdyo görselleri: kit klasörünün içi (çıktı klasörü hariç) ve en çok iki üst klasördeki
    adı render/foto/preview… olan alt klasörler (ör. 'Sanji\\SANJI RENDER' ↔ 'Sanji\\SANJI 1-6\\SANJI 1-6').
    Koleksiyon klasörleri (çok sayıda alt klasör) taranmaz."""
    out: list[Path] = []

    def add_tree(d: Path, depth: int):
        try:
            entries = sorted(d.iterdir())
        except OSError:
            return
        for e in entries:
            if len(out) >= limit:
                return
            if e.is_file() and e.suffix.lower() in exts:
                out.append(e)
            elif e.is_dir() and depth > 0 and e.name != "_slaprep":
                add_tree(e, depth - 1)

    add_tree(kit, 2)
    p = kit
    for _ in range(2):
        p = p.parent
        if p == p.parent:
            break
        try:
            subdirs = [e for e in p.iterdir() if e.is_dir()]
        except OSError:
            break
        if len(subdirs) > 30:                     # koleksiyon klasörü (ör. 'STL Modeller'): dokunma
            break
        for e in sorted(subdirs):
            if IMAGE_DIR_HINT.search(e.name) and e not in kit.parents and e != kit:
                add_tree(e, 2)
    seen, uniq = set(), []
    for f in out:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return uniq[:limit]


def _num(s: str, default=None):
    try:
        return float(str(s).replace(",", "."))
    except ValueError:
        return default


class App:
    def __init__(self, root: tk.Tk, initial: str | None = None, dnd: bool = False):
        self.root = root
        self.kit: Path | None = None
        self.proc: subprocess.Popen | None = None
        self.q: queue.Queue = queue.Queue()
        self.running_cmd = ""
        self.parts_info: dict[str, dict] = {}       # ad → {group, include, fill, separate}
        self.groups: list[str] = []
        self.variant_rows: list[tuple[list[str], tk.StringVar]] = []
        self.folder_vars: dict[str, tk.BooleanVar] = {}
        self.fix_detach: list[str] = []                 # kullanıcının montaj düzeltmeleri
        self.fix_join: list[list[str]] = []             # [çapa, parça]
        self.queue_items: list[nq.QueueItem] = nq.load_queue()     # gece kuyruğu (kullanıcı tercihlerinde saklı)
        self.queue_running = False
        self.queue_current: nq.QueueItem | None = None
        self.running_args: list[str] = []               # son başlatılan işin argümanları (kuyrukla eşleştirme)
        self.run_started = 0.0
        self._tail: deque[str] = deque(maxlen=40)       # son çıktı satırları (hata özetine)
        self._saved_exclude, self._saved_solid, self._saved_hollow, self._saved_separate = [], [], [], []
        root.title(APP_TITLE)
        root.geometry("1380x880")
        root.minsize(1120, 700)
        ico = ASSETS / "slaprep.ico"
        if ico.exists():
            try:
                root.iconbitmap(str(ico))
            except Exception:
                pass
        self.theme = Theme(root)
        root.configure(background=C["page"])
        self._vars()
        self._build()
        if dnd:
            self._enable_dnd()
        root.after(100, self._poll)
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        if initial:
            p = Path(initial)
            self.set_kit(p.parent if p.is_file() else p)

    # ------------------------------------------------------------------ durum değişkenleri
    def _vars(self):
        self.v_kit = tk.StringVar()
        self.v_scale_mode = tk.StringVar(value="orig")
        self.v_height = tk.StringVar(value="150")
        self.v_height_mode = tk.StringVar(value="Tümü (kaide dahil)")
        self.v_scale = tk.StringVar(value="1.0")
        self.v_orig_height = tk.StringVar(value="")
        self.v_wall = tk.StringVar(value="2.0")
        self.v_hole_auto = tk.BooleanVar(value=True)
        self.v_hole_max = tk.StringVar(value="5")
        self.v_hole = tk.StringVar(value="3")
        self.v_min_holes = tk.StringVar(value="")
        self.v_visible = tk.BooleanVar(value=False)
        self.v_hole_bottom = tk.BooleanVar(value=False)
        self.v_hole_wall = tk.BooleanVar(value=False)
        self.v_wall_mount = tk.BooleanVar(value=False)
        self.v_up = tk.StringVar(value="otomatik")
        self.v_front = tk.StringVar(value="otomatik")
        self.v_variant_pat = tk.StringVar(value="")
        self.v_all_variants = tk.BooleanVar(value=False)
        self.v_print_ref = tk.BooleanVar(value=False)
        self.v_printer = tk.StringVar(value="m7")
        self.v_gap = tk.StringVar(value="3")
        self.v_by_folder = tk.StringVar(value="otomatik")
        self.v_plate_goal = tk.StringVar(value="En kısa toplam süre")
        self.v_layer_h = tk.StringVar(value="0.05")
        self.v_layer_t = tk.StringVar(value="6.5")
        self.v_overhead = tk.StringVar(value="15")
        self.v_steps = tk.StringVar(value="all")           # işlem onay kutularından türetilir
        self.v_op_hollow = tk.BooleanVar(value=True)
        self.v_op_plates = tk.BooleanVar(value=True)
        # reçine fiyatları kullanıcıya ait (kite değil): ~/.slaprep_prefs.json
        rp = load_prefs()
        self.v_resin_price = tk.StringVar(value=f"{rp.price_l:g}" if rp.price_l else "")
        self.v_resin_sep_price = tk.StringVar(value=f"{rp.separate_price_l:g}" if rp.separate_price_l else "")
        self.v_resin_bottle = tk.StringVar(value=f"{rp.bottle_ml:g}")
        self.v_resin_extra = tk.StringVar(value=f"{rp.extra_pct:g}")
        self.v_resin_cur = tk.StringVar(value=rp.currency)
        self.v_low_prio = tk.BooleanVar(value=bool(prefs.read("low_priority", True)))
        self.v_low_prio.trace_add("write", lambda *_: self._save_pref("low_priority", bool(self.v_low_prio.get())))
        self.v_queue_summary = tk.StringVar(value="")
        self.v_status = tk.StringVar(value="Bir kit klasörü seçin veya pencereye sürükleyip bırakın.")
        self.v_final_height = tk.StringVar(value="")
        # parça listesi: arama ve toplu seçim çubuğu
        self.v_part_filter = tk.StringVar(value="")
        self.v_part_count = tk.StringVar(value="")
        self.v_sel_count = tk.StringVar(value="")
        self.v_sel_fill = tk.StringVar(value="")
        self.v_sel_sep = tk.StringVar(value="0")
        self.v_sel_inc = tk.StringVar(value="1")
        # özet / plate sayı kartları
        self.st = {k: tk.StringVar(value="–") for k in ("parts", "height", "plates", "time", "resin", "goal",
                                                        "p_plates", "p_time", "p_resin", "p_goal")}
        self.st_sub = {k: tk.StringVar(value="") for k in ("parts", "height", "plates", "resin", "p_resin")}
        # gerçek süre ölçümü (süre tahmini ayarı)
        self.v_cal_plate = tk.StringVar(value="")
        self.v_cal_time = tk.StringVar(value="")
        self.v_cal_info = tk.StringVar(value="")
        self._open_sections = prefs.read("ui_sections", {}) or {}

    # ------------------------------------------------------------------ küçük yardımcılar
    def _help(self, parent, text, bg="card"):
        """Küçük ⓘ işareti: açıklama üzerine gelince görünür (uzun yardım metinleri ekranı kalabalıklaştırmasın)."""
        lb = ttk.Label(parent, image=self.theme.icon("info", C["faint"], 11), cursor="question_arrow",
                       style="Card.TLabel" if bg == "card" else "TLabel")
        Tip(lb, text)
        return lb

    def _field(self, parent, row, label, var, unit="", width=7, help=None, entry_kw=None):
        """Ayar kartında bir satır: solda etiket, sağda alan (+ birim, + açıklama ikonu)."""
        px = self.theme.px
        lab = ttk.Frame(parent, style="Bar.TFrame")
        lab.grid(row=row, column=0, sticky="w", pady=px(3))
        ttk.Label(lab, text=label, style="Card.TLabel").pack(side="left")
        if help:
            self._help(lab, help).pack(side="left", padx=(px(5), 0))
        f = ttk.Frame(parent, style="Bar.TFrame")
        f.grid(row=row, column=1, sticky="e", pady=px(3))
        e = ttk.Entry(f, textvariable=var, width=width, justify="right", **(entry_kw or {}))
        e.pack(side="left")
        ttk.Label(f, text=unit, style="Muted.Card.TLabel", width=3).pack(side="left", padx=(px(6), 0))
        return e

    def _label_row(self, parent, row, label, widget, help=None):
        px = self.theme.px
        lab = ttk.Frame(parent, style="Bar.TFrame")
        lab.grid(row=row, column=0, sticky="w", pady=px(3))
        ttk.Label(lab, text=label, style="Card.TLabel").pack(side="left")
        if help:
            self._help(lab, help).pack(side="left", padx=(px(5), 0))
        widget.grid(row=row, column=1, sticky="e", pady=px(3))
        return widget

    def _subhead(self, parent, row, text, help=None):
        px = self.theme.px
        f = ttk.Frame(parent, style="Bar.TFrame")
        f.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(px(10), px(2)))
        ttk.Label(f, text=text.upper(), style="Faint.Card.TLabel").pack(side="left")
        if help:
            self._help(f, help).pack(side="left", padx=(px(5), 0))
        return f

    def _section(self, parent, title, icon, default_open=True):
        s = Section(parent, self.theme, title, icon=icon, open_=self._open_sections.get(title, default_open),
                    on_toggle=self._remember_section)
        s.pack(fill="x", pady=(0, self.theme.px(10)))
        return s

    def _remember_section(self, title, is_open):
        self._open_sections[title] = is_open
        self._save_pref("ui_sections", self._open_sections)

    # ------------------------------------------------------------------ arayüz
    def _build(self):
        th, px, r = self.theme, self.theme.px, self.root
        self._build_header(r)
        ttk.Frame(r, style="Line.TFrame", height=1).pack(side="top", fill="x")
        self._build_actionbar(r)
        self.pb = ThinProgress(r, height=px(3))
        self.pb.pack(side="bottom", fill="x")
        # günlük (açılır): koyu konsol
        self.log_panel = tk.Frame(r, background=C["console_bg"])
        self.log = tk.Text(self.log_panel, height=10, font=("Consolas", 9), background=C["console_bg"],
                           foreground=C["console_fg"], insertbackground=C["console_fg"], borderwidth=0,
                           highlightthickness=0, padx=px(12), pady=px(8), wrap="none")
        lsb = ttk.Scrollbar(self.log_panel, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=lsb.set)
        lsb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.log_shown = False

        self.body = ttk.Frame(r, padding=(px(14), px(14), px(14), px(10)))
        self.body.pack(side="top", fill="both", expand=True)
        self.body.columnconfigure(1, weight=1)
        self.body.rowconfigure(0, weight=1)
        side = ScrollFrame(self.body, width=px(372))
        side.grid(row=0, column=0, sticky="nsw", padx=(0, px(14)))
        side.canvas.configure(width=px(372))
        self._build_sidebar(side.inner)

        main = Card(self.body, padding=px(2))
        main.grid(row=0, column=1, sticky="nsew")
        self.right = TabView(main, th)
        self.right.pack(fill="both", expand=True, padx=px(1), pady=px(1))
        self._build_parts_tab(self.right.page("Parçalar"))
        self._build_plates_tab(self.right.page("Plate'ler"))
        self._build_summary_tab(self.right.page("Özet"))
        self._build_asm_tab(self.right)
        self._build_queue_tab(self.right)
        self._on_ops()
        self._on_scale_mode()
        self._on_hole_mode()
        self._on_scale_change()

    def _build_header(self, r):
        th, px = self.theme, self.theme.px
        hdr = ttk.Frame(r, style="Bar.TFrame", padding=(px(18), px(10)))
        hdr.pack(side="top", fill="x")
        brand = ttk.Frame(hdr, style="Bar.TFrame")
        brand.pack(side="left")
        logo = ASSETS / "slaprep.png"
        if logo.exists():
            from PIL import Image, ImageTk

            self._logo = ImageTk.PhotoImage(Image.open(logo).resize((px(30), px(30)), Image.LANCZOS), master=r)
            ttk.Label(brand, image=self._logo, style="Card.TLabel").pack(side="left", padx=(0, px(10)))
        tb = ttk.Frame(brand, style="Bar.TFrame")
        tb.pack(side="left")
        ttk.Label(tb, text="SLAPrep", style="H2.Card.TLabel").pack(anchor="w")
        ttk.Label(tb, text="SLA baskı hazırlığı", style="Faint.Card.TLabel").pack(anchor="w")
        ttk.Frame(hdr, style="Line.TFrame", width=1).pack(side="left", fill="y", padx=px(18))
        # sağ: çıktılar
        right = ttk.Frame(hdr, style="Bar.TFrame")
        right.pack(side="right")
        ttk.Button(right, text="Rapor", image=th.icon_states("report"), compound="left",
                   command=self.open_report).pack(side="left")
        self.b_folders = ttk.Button(right, text="Klasörler", image=th.icon_states("folder"), compound="left",
                                    style="Ghost.TButton", command=self._folders_menu)
        self.b_folders.pack(side="left", padx=(px(6), 0))
        self.out_menu = tk.Menu(r, tearoff=0)
        self.out_menu.add_command(label="Plate dosyaları", command=lambda: self.open_dir("plates"))
        self.out_menu.add_command(label="Parça dosyaları", command=lambda: self.open_dir("parts"))
        self.out_menu.add_command(label="Çıktı klasörü (_slaprep)", command=lambda: self.open_dir(""))
        # orta: kit seçimi
        kit = ttk.Frame(hdr, style="Bar.TFrame")
        kit.pack(side="left", fill="x", expand=True, padx=(0, px(18)))
        ttk.Label(kit, image=th.icon("folder_open", C["muted"], 15), style="Card.TLabel").pack(side="left",
                                                                                          padx=(0, px(8)))
        self.cb_kit = ttk.Combobox(kit, textvariable=self.v_kit, values=_recent_kits())
        self.cb_kit.pack(side="left", fill="x", expand=True)
        self.cb_kit.bind("<<ComboboxSelected>>", lambda e: self.set_kit(Path(self.v_kit.get())))
        self.cb_kit.bind("<Return>", lambda e: self.set_kit(Path(self.v_kit.get())))
        ttk.Button(kit, text="Gözat…", command=self.choose_kit).pack(side="left", padx=(px(8), 0))

    def _folders_menu(self):
        b = self.b_folders
        self.out_menu.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height() + self.theme.px(2))

    def _build_actionbar(self, r):
        th, px = self.theme, self.theme.px
        wrap = ttk.Frame(r, style="Bar.TFrame")
        wrap.pack(side="bottom", fill="x")
        ttk.Frame(wrap, style="Line.TFrame", height=1).pack(side="top", fill="x")
        bar = ttk.Frame(wrap, style="Bar.TFrame", padding=(px(18), px(10)))
        bar.pack(fill="x")
        self.b_analyze = ttk.Button(bar, text="Analiz et", image=th.icon_states("search"), compound="left",
                                    style="Big.TButton", command=lambda: self.run("analyze"))
        self.b_analyze.pack(side="left")
        Tip(self.b_analyze, "Montaj, yükseklik, alternatif parçalar ve birleşme yüzeyleri bulunur (dosya yazılmaz).")
        self.b_prep = ttk.Button(bar, text=STEPS_BUTTON["all"], image=th.icon_states("play", "white", "#F2F6FF"),
                                 compound="left", style="Big.Primary.TButton", command=lambda: self.run("prep"))
        self.b_prep.pack(side="left", padx=(px(8), 0))
        self.b_stop = ttk.Button(bar, text="Durdur", image=th.icon_states("stop", C["bad"]), compound="left",
                                 style="Big.TButton", command=self.stop, state="disabled")
        self.b_stop.pack(side="left", padx=(px(8), 0))
        ttk.Frame(bar, style="Line.TFrame", width=1).pack(side="left", fill="y", padx=px(14))
        b = ttk.Button(bar, text="Kuyruğa ekle", image=th.icon_states("add"), compound="left",
                       style="Ghost.TButton", command=self._queue_add)
        b.pack(side="left")
        Tip(b, "Bu kiti şimdiki ayarlarıyla gece kuyruğuna ekler (Kuyruk sekmesi → Hepsini hazırla).")
        # sağ
        self.b_log = ttk.Button(bar, text="Günlük", image=th.icon_states("console", C["muted"]), compound="left",
                                style="Ghost.TButton", command=self._toggle_log)
        self.b_log.pack(side="right")
        sw = ttk.Checkbutton(bar, text="Düşük öncelik", variable=self.v_low_prio, style="Switch.TCheckbutton")
        sw.pack(side="right", padx=(0, px(14)))
        Tip(sw, "İşlem düşük işlemci önceliğiyle çalışır: bilgisayar kullanılabilir kalır, işlem biraz uzayabilir. "
                "Gece kuyruğu da bu ayarı kullanır.")
        st = ttk.Frame(bar, style="Bar.TFrame")
        st.pack(side="left", fill="x", expand=True, padx=(px(14), px(14)))
        self.l_status = ttk.Label(st, textvariable=self.v_status, style="Muted.Card.TLabel", anchor="w")
        self.l_status.pack(fill="x")

    # ------------------------------------------------------------------ kenar çubuğu: ayarlar
    def _build_sidebar(self, parent):
        th, px = self.theme, self.theme.px
        # işlemler: onay kutuları
        s = self._section(parent, "İşlemler", "play")
        b = s.body
        ttk.Checkbutton(b, text="Hollow + delik", variable=self.v_op_hollow, style="Card.TCheckbutton",
                        command=self._on_ops).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(b, text="İçi boşaltılır; delikler sadece birleşme yüzeylerine açılır.",
                  style="Small.Card.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", padx=(px(26), 0))
        ttk.Checkbutton(b, text="Yönlendir + plate'e diz", variable=self.v_op_plates, style="Card.TCheckbutton",
                        command=self._on_ops).grid(row=2, column=0, columnspan=2, sticky="w", pady=(px(6), 0))
        ttk.Label(b, text="Baskı yönü seçilir; parçalar plate dosyalarına dizilir.",
                  style="Small.Card.TLabel").grid(row=3, column=0, columnspan=2, sticky="w", padx=(px(26), 0))
        self.l_ops_note = ttk.Label(b, text="", style="Small.Card.TLabel", wraplength=px(300), justify="left")
        self.l_ops_note.grid(row=4, column=0, columnspan=2, sticky="w", pady=(px(6), 0))

        # boyut
        s = self._section(parent, "Boyut", "ruler")
        b = s.body
        Segmented(b, self.v_scale_mode, [("orig", "Orijinal"), ("height", "Hedef boy"), ("scale", "Ölçek")],
                  command=self._on_scale_mode).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, px(6)))
        self.row_height = ttk.Frame(b, style="Bar.TFrame")
        self.row_height.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.row_height.columnconfigure(1, weight=1)
        self._field(self.row_height, 0, "Hedef yükseklik", self.v_height, "mm")
        Segmented(self.row_height, self.v_height_mode, [("Tümü (kaide dahil)", "Kaide dahil"),
                                                        ("Sadece figür (kaide hariç)", "Sadece figür")]).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(px(2), 0))
        self.row_scale = ttk.Frame(b, style="Bar.TFrame")
        self.row_scale.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.row_scale.columnconfigure(1, weight=1)
        self._field(self.row_scale, 0, "Ölçek", self.v_scale, "×")
        self.l_final = ttk.Label(b, textvariable=self.v_final_height, style="Accent.Card.TLabel", wraplength=px(340),
                                 justify="left")
        self.l_final.grid(row=3, column=0, columnspan=2, sticky="w", pady=(px(6), 0))
        self._field(b, 4, "Orijinal boy", self.v_orig_height, "mm",
                    help="Sadece montaj bulunamazsa gerekir: araç önce parçaları kendisi birleştirmeyi dener. "
                         "Bulamazsa ürün sayfasındaki boyu yazın; hedef yükseklik buna göre ölçeklenir.")
        ttk.Label(b, text="Duvar, delik ve boşluk ölçüleri ölçekle değişmez. Cetvelli görünüm: Montaj kontrolü → "
                          "Boy.", style="Faint.Card.TLabel", wraplength=px(310), justify="left").grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(px(4), 0))
        for v in (self.v_height, self.v_scale, self.v_scale_mode, self.v_height_mode):
            v.trace_add("write", self._on_scale_change)

        # hollow ve delik
        s = self.sec_hollow = self._section(parent, "Hollow ve delik", "package")
        b = s.body
        self._field(b, 0, "Duvar kalınlığı", self.v_wall, "mm")
        self._label_row(b, 1, "Delik çapı", Segmented(b, self.v_hole_auto, [(1, "Otomatik"), (0, "Sabit")],
                                                       command=self._on_hole_mode))
        self.row_hole_auto = ttk.Frame(b, style="Bar.TFrame")
        self.row_hole_auto.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.row_hole_auto.columnconfigure(1, weight=1)
        self._field(self.row_hole_auto, 0, "En büyük çap", self.v_hole_max, "mm",
                    help="Kavite büyüdükçe çap 3 mm'den bu değere kadar büyür.")
        self.row_hole_fixed = ttk.Frame(b, style="Bar.TFrame")
        self.row_hole_fixed.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.row_hole_fixed.columnconfigure(1, weight=1)
        self._field(self.row_hole_fixed, 0, "Çap", self.v_hole, "mm")
        self._field(b, 4, "Kavite başına en az delik", self.v_min_holes, "",
                    help="Boş bırakılırsa kavite hacmine ve uzunluğuna göre otomatik.")
        self._subhead(b, 5, "Ek delik yerleri",
                      help="Delikler varsayılan olarak sadece montajda komşu parçanın kapattığı yüzeylere açılır; "
                           "baskıdan sonra tıkamak gerekmez. Aşağıdakiler dış yüzeye delik açar, sonra tıkanmalıdır.")
        for i, (txt, var) in enumerate((("Kaide alt yüzü (sonra tıkanmalı)", self.v_hole_bottom),
                                        ("Duvar arkası (sonra tıkanmalı)", self.v_hole_wall),
                                        ("Yer yoksa görünür yüzey", self.v_visible))):
            ttk.Checkbutton(b, text=txt, variable=var, style="Card.TCheckbutton").grid(
                row=6 + i, column=0, columnspan=2, sticky="w")

        # montaj
        s = self._section(parent, "Montaj", "layers", default_open=False)
        b = s.body
        f = ttk.Frame(b, style="Bar.TFrame")
        ttk.Checkbutton(f, text="Duvara asılacak", variable=self.v_wall_mount, style="Switch.TCheckbutton").pack(
            side="left")
        self._help(f, "Duvara bakan arka yüzler gizli sayılır (delik için kullanılabilir), zemin tabanı yoktur."
                   ).pack(side="left")
        f.grid(row=0, column=0, columnspan=2, sticky="w")
        self._label_row(b, 1, "Yukarı ekseni", ttk.Combobox(b, textvariable=self.v_up, values=UP_CHOICES,
                                                             state="readonly", width=10),
                        help="Önizlemede figür yan yatıksa buradan düzeltin.")
        self._label_row(b, 2, "Ön yönü", ttk.Combobox(b, textvariable=self.v_front, values=UP_CHOICES,
                                                       state="readonly", width=10),
                        help="Figürün baktığı yön. Destekler birleşme yerlerine, sonra figürün sırtına ve altına "
                             "yönlendirilir; ön ve yanlar korunur. Montaj kontrolündeki 'Önden' görünüm figürün "
                             "sırtını gösteriyorsa buradan düzeltin (otomatik: kitin export yönünden).")
        self._subhead(b, 3, "Alt klasörler", help="İşaretsiz klasördeki parçalar hazırlanmaz.")
        self.f_folders = ttk.Frame(b, style="Bar.TFrame")
        self.f_folders.grid(row=4, column=0, columnspan=2, sticky="w")
        self._subhead(b, 5, "Alternatif parçalar",
                      help="Kitte aynı parçanın birden çok sürümü varsa (V1/V2, birleşik/bölünmüş) hangisinin "
                           "basılacağı. Analizden sonra listelenir.")
        self.f_variants = ttk.Frame(b, style="Bar.TFrame")
        self.f_variants.grid(row=6, column=0, columnspan=2, sticky="ew")
        e = ttk.Entry(b, textvariable=self.v_variant_pat, width=16)
        self._label_row(b, 7, "Desen", e, help="Seçilecek alternatif(ler) için ad deseni, ör. *_Key ya da *BUST* "
                                               "(virgülle birden çok).")
        ttk.Checkbutton(b, text="Seçilmeyen alternatifleri de bas", variable=self.v_all_variants,
                        style="Card.TCheckbutton").grid(row=8, column=0, columnspan=2, sticky="w", pady=(px(4), 0))
        ttk.Checkbutton(b, text="Ölçek referansı / reklam parçalarını da bas", variable=self.v_print_ref,
                        style="Card.TCheckbutton").grid(row=9, column=0, columnspan=2, sticky="w")

        # yazıcı ve plate
        s = self.sec_plates = self._section(parent, "Yazıcı ve plate", "printer", default_open=False)
        b = s.body
        bv = PRINTERS["m7"]
        self._label_row(b, 0, "Yazıcı", ttk.Combobox(b, textvariable=self.v_printer, values=sorted(PRINTERS),
                                                      state="readonly", width=8),
                        help=f"Anycubic Photon Mono M7: {bv[0]:.0f} × {bv[1]:.0f} × {bv[2]:.0f} mm")
        self._label_row(b, 1, "Hedef", Segmented(b, self.v_plate_goal, [("En kısa toplam süre", "En kısa süre"),
                                                                       ("En az plate", "En az plate")]),
                        help="SLA'da plate süresi en yüksek parçaya bağlıdır. 'En kısa süre' uzun parçaları aynı "
                             "plate'e toplar ve puanı yakın daha alçak yönleri kullanır; fazladan plate ancak belirgin "
                             "süre kazandırırsa açılır.")
        self._field(b, 2, "Parça arası boşluk", self.v_gap, "mm")
        self._label_row(b, 3, "Klasörlere ayır", Segmented(b, self.v_by_folder, [("otomatik", "Oto"), ("evet", "Evet"),
                                                                                 ("hayır", "Hayır")]),
                        help="'Oto': alt klasörlere ayırmak süreye ya da plate sayısına mal olmuyorsa ayrılır.")
        self._subhead(b, 4, "Süre tahmini",
                      help="CHITUBOX ayarlarınız: sadece süre tahmini ve yerleşim kararı için (dilimleme CHITUBOX'ta).")
        self._field(b, 5, "Katman kalınlığı", self.v_layer_h, "mm")
        self._field(b, 6, "Katman süresi", self.v_layer_t, "sn",
                    help="Gerçek baskı sürelerini Plate'ler sekmesindeki 'Gerçek süre' satırına girerseniz bu değer "
                         "kendiliğinden ayarlanır.")
        self._field(b, 7, "Plate başına ek", self.v_overhead, "dk")
        for v in (self.v_layer_h, self.v_layer_t, self.v_overhead):
            v.trace_add("write", self._on_time_change)

        # reçine
        s = self._section(parent, "Reçine", "drop", default_open=False)
        b = s.body
        f = ttk.Frame(b, style="Bar.TFrame")
        ttk.Entry(f, textvariable=self.v_resin_price, width=7, justify="right").pack(side="left")
        ttk.Entry(f, textvariable=self.v_resin_cur, width=4).pack(side="left", padx=(px(6), 0))
        self._label_row(b, 0, "1 litre fiyatı", f, help="Boş bırakılırsa yalnız ml gösterilir. Fiyatlar bu bilgisayarda "
                                                         "saklanır, her kitte geçerlidir.")
        self._field(b, 1, "Ayrı plate reçinesi (1 L)", self.v_resin_sep_price, "",
                    help="'Ayrı plate' işaretli parçalar (ör. şeffaf reçine) için. Boş = ana reçineyle aynı fiyat.")
        self._field(b, 2, "Şişe", self.v_resin_bottle, "ml")
        self._field(b, 3, "Destek + kayıp payı", self.v_resin_extra, "%",
                    help="Destekler CHITUBOX'ta eklenir; burada parça hacmine yüzde pay olarak tahmin edilir. Kesin "
                         "miktar için CHITUBOX'un dilimleme sonrası hacmine bakın.")
        for v in (self.v_resin_price, self.v_resin_sep_price, self.v_resin_bottle, self.v_resin_extra,
                  self.v_resin_cur):
            v.trace_add("write", self._on_resin_change)

    def _on_ops(self):
        """İşlem onay kutuları → hazırlık adımı (all / hollow / plates); ilgisiz ayar kartları kapanır, ana düğmenin
        adı değişir. İkisi de kapalıysa hazırlanacak bir şey yoktur."""
        h, p = self.v_op_hollow.get(), self.v_op_plates.get()
        st = "all" if h and p else "hollow" if h else "plates" if p else ""
        self.v_steps.set(st)
        self.sec_hollow.set_enabled(h, "işlem kapalı")
        self.sec_plates.set_enabled(p, "işlem kapalı")
        self.b_prep.configure(text=STEPS_BUTTON.get(st, "Hazırla"))
        self.l_ops_note.configure(
            text="En az bir işlem seçin." if not st else
            "Parçalar dolu kalır; sadece yönlendirilip plate'e dizilir." if st == "plates" else
            "Plate dizilmez: parçalar orijinal duruşlarında ayrı STL olarak çıkar (Klasörler → Parça dosyaları)."
            if st == "hollow" else "",
            style="Bad.Card.TLabel" if not st else "Small.Card.TLabel")
        (self.l_ops_note.grid if st != "all" else self.l_ops_note.grid_remove)()
        self._update_buttons()

    _on_steps = _on_ops

    def _on_scale_mode(self, *_):
        m = self.v_scale_mode.get()
        (self.row_height.grid if m == "height" else self.row_height.grid_remove)()
        (self.row_scale.grid if m == "scale" else self.row_scale.grid_remove)()

    def _on_hole_mode(self, *_):
        auto = bool(self.v_hole_auto.get())
        (self.row_hole_auto.grid if auto else self.row_hole_auto.grid_remove)()
        (self.row_hole_fixed.grid if not auto else self.row_hole_fixed.grid_remove)()

    def _update_buttons(self):
        busy = self.proc is not None
        self.b_analyze.state(["disabled"] if busy else ["!disabled"])
        self.b_prep.state(["disabled"] if busy or not self.v_steps.get() else ["!disabled"])
        if hasattr(self, "b_resolve"):
            self.b_resolve.state(["disabled"] if busy else ["!disabled"])
        self.b_stop.state(["!disabled"] if busy else ["disabled"])

    def _toggle_log(self, show: bool | None = None):
        show = (not self.log_shown) if show is None else show
        if show and not self.log_shown:
            self.log_panel.pack(side="bottom", fill="x", before=self.body)
        elif not show and self.log_shown:
            self.log_panel.pack_forget()
        self.log_shown = show
        self.b_log.configure(text="Günlüğü gizle" if show else "Günlük")

    def _save_pref(self, key, value):
        try:
            prefs.write(key, value)
        except OSError:
            pass

    # ------------------------------------------------------------------ sekmeler: parçalar
    def _build_parts_tab(self, f):
        th, px = self.theme, self.theme.px
        top = ttk.Frame(f, style="Bar.TFrame")
        top.pack(fill="x", pady=(0, px(10)))
        sbox = ttk.Frame(top, style="Bar.TFrame")
        sbox.pack(side="left")
        self.e_filter = ttk.Entry(sbox, textvariable=self.v_part_filter, width=26)
        self.e_filter.pack(side="left")
        self.l_filter_ph = ttk.Label(sbox, text="Parça ara…", style="Faint.Card.TLabel", cursor="xterm")
        self.l_filter_ph.place(in_=self.e_filter, x=px(10), rely=0.5, anchor="w")
        self.l_filter_ph.bind("<Button-1>", lambda e: self.e_filter.focus_set())
        self.v_part_filter.trace_add("write", lambda *_: (self._filter_placeholder(), self._refresh_tree()))
        self.e_filter.bind("<FocusIn>", lambda e: self._filter_placeholder())
        self.e_filter.bind("<FocusOut>", lambda e: self._filter_placeholder())
        ttk.Label(top, textvariable=self.v_part_count, style="Muted.Card.TLabel").pack(side="left", padx=px(14))
        self._help(top, "Tablodaki kutuya tıklayarak parçayı dahil et / hariç tut; 'Ayrı plate' kutusuna tıklayarak "
                        "ayrı plate'e al; 'Doluluk' hücresine tıklayarak Otomatik / Dolu / Hollow seç. Birden çok "
                        "parçayı (Ctrl/Shift) seçip üstteki çubuktan toplu değiştirebilirsiniz.\n"
                        "• Dolu: araç hollow edebilecek olsa da dolu basılır (ör. şeffaf reçine).\n"
                        "• Hollow: hacim eşiğinin altında kalsa da hollow edilir.\n"
                        "• Ayrı plate: diğerleriyle karışmadan kendi plate'lerine dizilir (plates\\ayrı\\…).\n"
                        "• Destek: hazırlıktan sonra, destek gelecek alanın ne kadarının birleşme yeri, sırt ya da "
                        "alt tarafta (görünmeyen yerde) kaldığı.\n"
                        "Tercihler 'Hazırla' ile kite kaydedilir.").pack(side="right")

        # seçim çubuğu: seçili parçalara toplu tercih
        self.selbar = Card(f, padding=(px(14), px(8)))
        self.selbar.pack(fill="x", pady=(0, px(10)))
        self.sel_hint = ttk.Label(self.selbar, text="Parça seçin (Ctrl/Shift ile birden çok): seçilenlerin "
                                                    "doluluk, ayrı plate ve dahil ayarı buradan toplu değişir.",
                                  style="Muted.Card.TLabel")
        self.sel_tools = ttk.Frame(self.selbar, style="Bar.TFrame")
        ttk.Label(self.sel_tools, textvariable=self.v_sel_count, style="Strong.Card.TLabel").pack(side="left")
        ttk.Frame(self.sel_tools, style="Line.TFrame", width=1).pack(side="left", fill="y", padx=px(14))
        ttk.Label(self.sel_tools, text="Doluluk", style="Muted.Card.TLabel").pack(side="left", padx=(0, px(8)))
        Segmented(self.sel_tools, self.v_sel_fill, [("auto", "Otomatik"), ("solid", "Dolu"), ("hollow", "Hollow")],
                  command=lambda: self._set_fill(self.v_sel_fill.get())).pack(side="left")
        ttk.Frame(self.sel_tools, style="Line.TFrame", width=1).pack(side="left", fill="y", padx=px(14))
        ttk.Checkbutton(self.sel_tools, text="Ayrı plate", variable=self.v_sel_sep, onvalue="1", offvalue="0",
                        style="Card.TCheckbutton", command=self._sel_set_separate).pack(side="left")
        ttk.Checkbutton(self.sel_tools, text="Dahil", variable=self.v_sel_inc, onvalue="1", offvalue="0",
                        style="Card.TCheckbutton", command=self._sel_set_include).pack(side="left", padx=(px(14), 0))
        ttk.Button(self.sel_tools, text="Seçimi kaldır", style="Small.Ghost.TButton",
                   command=lambda: self.tree.selection_set(())).pack(side="right")

        tf = ttk.Frame(f, style="Bar.TFrame")
        tf.pack(fill="both", expand=True)
        self.part_cols = ("klasor", "tercih", "ayri", "hacim", "sonuc", "delik", "destek", "sonra", "plate")
        self.tree = ttk.Treeview(tf, columns=self.part_cols, show="tree headings", selectmode="extended")
        self.tree.heading("#0", text="Parça", anchor="w")
        self.tree.column("#0", width=px(200), minwidth=px(150), stretch=True)
        for c, h, w, anc in (("klasor", "Klasör", 90, "w"), ("tercih", "Doluluk  ▾", 88, "w"),
                             ("ayri", "Ayrı plate", 74, "center"), ("hacim", "Hacim", 74, "e"),
                             ("sonuc", "Sonuç", 78, "center"), ("delik", "Delik", 88, "w"),
                             ("destek", "Destek", 104, "w"),
                             ("sonra", "Reçine", 70, "e"), ("plate", "Plate", 96, "center")):
            self.tree.heading(c, text=h, anchor=anc)
            self.tree.column(c, width=px(w), minwidth=px(50), anchor=anc, stretch=False)
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.tree.tag_configure("off", foreground=C["faint"])
        self.tree.bind("<Button-1>", self._tree_click)
        self.tree.bind("<space>", self._toggle_part)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._sync_selbar())
        self.fill_menu = tk.Menu(self.tree, tearoff=0)
        self._fill_targets: list[str] = []
        for k in ("auto", "solid", "hollow"):
            self.fill_menu.add_command(label=FILL_TXT[k], command=lambda k=k: self._apply_fill(self._fill_targets, k))
        self.part_menu = tk.Menu(self.tree, tearoff=0)
        self.part_menu.add_command(label="Dolu bas", command=lambda: self._set_fill("solid"))
        self.part_menu.add_command(label="Hollow et", command=lambda: self._set_fill("hollow"))
        self.part_menu.add_command(label="Otomatik (araç karar versin)", command=lambda: self._set_fill("auto"))
        self.part_menu.add_separator()
        self.part_menu.add_command(label="Ayrı plate'e koy / çıkar", command=self._toggle_separate)
        self.part_menu.add_separator()
        self.part_menu.add_command(label="Dahil et / hariç tut", command=self._toggle_part)
        self.tree.bind("<Button-3>", self._part_menu_popup)
        # boş durum: kit seçilmemişken
        self.empty = ttk.Frame(tf, style="Bar.TFrame")
        ttk.Label(self.empty, image=th.icon("folder_open", C["accent"], 36), style="Card.TLabel").pack()
        ttk.Label(self.empty, text="Bir kit klasörü seçin", style="H2.Card.TLabel").pack(pady=(px(10), px(2)))
        ttk.Label(self.empty, text="Klasörü pencereye sürükleyip bırakın ya da aşağıdaki düğmeyle seçin.",
                  style="Muted.Card.TLabel").pack()
        ttk.Button(self.empty, text="Klasör seç…", style="Primary.TButton", command=self.choose_kit).pack(
            pady=(px(14), 0))
        self._sync_selbar()
        self._show_empty(True)

    def _filter_placeholder(self):
        show = not self.v_part_filter.get() and self.root.focus_get() is not self.e_filter
        if show:
            self.l_filter_ph.place(in_=self.e_filter, x=self.theme.px(10), rely=0.5, anchor="w")
        else:
            self.l_filter_ph.place_forget()

    def _show_empty(self, on: bool):
        if on:
            self.empty.place(relx=0.5, rely=0.42, anchor="center")
        else:
            self.empty.place_forget()

    def _tree_click(self, e):
        """Tablodaki kutular: #0 kutusu dahil/hariç, 'Ayrı plate' kutusu, 'Doluluk' hücresi seçim menüsü.
        Kutuya tıklamak seçimi değiştirmez; seçiliyken tıklanan satır seçimin tamamına uygulanır."""
        if self.tree.identify_region(e.x, e.y) not in ("cell", "tree"):
            return None
        row = self.tree.identify_row(e.y)
        if not row or row not in self.parts_info:
            return None
        col = self.tree.identify_column(e.x)
        sel = list(self.tree.selection())
        targets = sel if row in sel and len(sel) > 1 else [row]
        if col == "#0" and "image" in self.tree.identify_element(e.x, e.y):
            on = not self.parts_info[row]["include"]
            for n in targets:
                self.parts_info[n]["include"] = on
            self._refresh_tree()
            return "break"
        name = self.part_cols[int(col[1:]) - 1] if col != "#0" else ""
        if name == "ayri":
            on = not self.parts_info[row]["separate"]
            for n in targets:
                self.parts_info[n]["separate"] = on
            self._refresh_tree()
            return "break"
        if name == "tercih":
            self._fill_targets = targets
            bbox = self.tree.bbox(row, col)
            x = self.tree.winfo_rootx() + (bbox[0] if bbox else e.x)
            y = self.tree.winfo_rooty() + (bbox[1] + bbox[3] if bbox else e.y)
            self.fill_menu.tk_popup(x, y)
            return "break"
        return None

    def _apply_fill(self, names, kind):
        for n in names:
            if n in self.parts_info:
                self.parts_info[n]["fill"] = kind
        self._refresh_tree()

    def _sync_selbar(self):
        """Seçim çubuğu seçili parçaların ortak durumunu gösterir (karışıksa boş / ara durum)."""
        sel = [n for n in self.tree.selection() if n in self.parts_info]
        if not sel:
            self.sel_tools.pack_forget()
            self.sel_hint.pack(fill="x")
            return
        self.sel_hint.pack_forget()
        self.sel_tools.pack(fill="x")
        self.v_sel_count.set(f"{len(sel)} parça seçili" if len(sel) > 1 else sel[0])
        fills = {self.parts_info[n]["fill"] for n in sel}
        self.v_sel_fill.set(fills.pop() if len(fills) == 1 else "")

        def tri(key):
            vals = {bool(self.parts_info[n][key]) for n in sel}
            return ("1" if vals.pop() else "0") if len(vals) == 1 else ""      # "" → ara durum (karışık)
        self.v_sel_sep.set(tri("separate"))
        self.v_sel_inc.set(tri("include"))

    def _sel_set_separate(self):
        on = self.v_sel_sep.get() == "1"
        for info in self._selected_infos():
            info["separate"] = on
        self._refresh_tree()

    def _sel_set_include(self):
        on = self.v_sel_inc.get() == "1"
        for info in self._selected_infos():
            info["include"] = on
        self._refresh_tree()

    # ------------------------------------------------------------------ sekmeler: plate'ler, özet
    def _stats_row(self, parent, items):
        px = self.theme.px
        row = ttk.Frame(parent, style="Bar.TFrame")
        row.pack(fill="x", pady=(0, px(12)))
        for i, (cap, key, icon, sub) in enumerate(items):
            t = stat_tile(row, self.theme, cap, self.st[key], icon, self.st_sub.get(sub) if sub else None)
            t.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else px(10), 0))
            row.columnconfigure(i, weight=1, uniform="stat")
        return row

    def _build_plates_tab(self, f):
        px = self.theme.px
        self._stats_row(f, (("Plate", "p_plates", "printer", None), ("Tahmini toplam süre", "p_time", "clock", None),
                            ("Reçine", "p_resin", "drop", "p_resin"), ("Yerleşim hedefi", "p_goal", "layers", None)))
        tf = ttk.Frame(f, style="Bar.TFrame")
        tf.pack(fill="both", expand=True)
        self.ptree = ttk.Treeview(tf, columns=("plate", "parcalar", "yukseklik", "sure", "recine", "aralik"),
                                  show="headings")
        for c, h, w in (("plate", "Plate", 140), ("parcalar", "Parçalar", 340), ("yukseklik", "En yüksek", 85),
                        ("sure", "Tahmini süre", 105), ("recine", "Reçine", 140), ("aralik", "En kısa aralık", 105)):
            anc = "w" if c in ("plate", "parcalar") else "e"
            self.ptree.heading(c, text=h, anchor=anc)
            self.ptree.column(c, width=px(w), anchor=anc, stretch=c == "parcalar")
        psb = ttk.Scrollbar(tf, orient="vertical", command=self.ptree.yview)
        self.ptree.configure(yscrollcommand=psb.set)
        self.ptree.pack(side="left", fill="both", expand=True)
        psb.pack(side="left", fill="y")
        self.ptree.bind("<Double-1>", self._open_plate)
        self.ptree.bind("<<TreeviewSelect>>", lambda e: self.ptree.selection() and
                        self.v_cal_plate.set(self.ptree.selection()[0]))
        self.v_plate_total = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.v_plate_total, style="Muted.Card.TLabel", wraplength=px(900),
                  justify="left").pack(anchor="w", pady=(px(8), 0))
        ttk.Label(f, text="Çift tıklayınca plate STL'i varsayılan programla (CHITUBOX) açılır. Süre, plate'teki en "
                          "yüksek parçaya göre tahmindir.", style="Faint.Card.TLabel").pack(anchor="w")
        # gerçek süre → süre tahmini ayarı (timecal)
        cal = Card(f, padding=(px(14), px(8)))
        cal.pack(fill="x", pady=(px(10), 0))
        ttk.Label(cal, text="Gerçek süre", style="Strong.Card.TLabel").pack(side="left")
        self._help(cal, "Bir plate'i bastıktan sonra yazıcının / CHITUBOX'un gösterdiği süreyi girin (ör. 3:31 ya da "
                        "3 sa 31 dk). Süre tahmini buna göre ayarlanır: tek ölçümde katman süresi, yükseklikleri farklı "
                        "birkaç ölçümde plate başına ek süre de bulunur. Ölçümler bu bilgisayarda saklanır, tüm "
                        "kitlerde geçerlidir.").pack(side="left", padx=(px(6), px(12)))
        self.cb_cal_plate = ttk.Combobox(cal, textvariable=self.v_cal_plate, state="readonly", width=16)
        self.cb_cal_plate.pack(side="left")
        e = ttk.Entry(cal, textvariable=self.v_cal_time, width=10)
        e.pack(side="left", padx=px(8))
        e.bind("<Return>", lambda ev: self._save_calibration())
        ttk.Button(cal, text="Kaydet", style="Small.TButton", command=self._save_calibration).pack(side="left")
        ttk.Label(cal, textvariable=self.v_cal_info, style="Small.Card.TLabel", wraplength=px(420),
                  justify="left").pack(side="left", padx=(px(14), 0))

    def _build_summary_tab(self, f):
        px = self.theme.px
        self._stats_row(f, (("Basılacak parça", "parts", "package", "parts"), ("Yükseklik", "height", "ruler", "height"),
                            ("Plate · süre", "plates", "printer", "plates"), ("Reçine", "resin", "drop", "resin")))
        tf = ttk.Frame(f, style="Bar.TFrame")
        tf.pack(fill="both", expand=True)
        self.summary = tk.Text(tf, wrap="word", font=self.theme.font(10), background=C["card"], foreground=C["text"],
                               borderwidth=0, highlightthickness=0, padx=px(4), pady=px(4), spacing1=px(2),
                               spacing3=px(2), cursor="arrow")
        ssb = ttk.Scrollbar(tf, orient="vertical", command=self.summary.yview)
        self.summary.configure(yscrollcommand=ssb.set)
        self.summary.pack(side="left", fill="both", expand=True)
        ssb.pack(side="left", fill="y")
        self.summary.tag_configure("h", font=self.theme.font(11, "semibold"), spacing1=px(10), spacing3=px(4))
        self.summary.tag_configure("key", foreground=C["muted"])
        self.summary.tag_configure("warn", foreground=C["text"], lmargin1=px(4), lmargin2=px(22))
        self.summary.tag_configure("bullet", foreground=C["warn"])

    # ------------------------------------------------------------------ gece kuyruğu
    def _build_queue_tab(self, nb):
        th, px = self.theme, self.theme.px
        f = nb.page("Kuyruk")
        tb = ttk.Frame(f, style="Bar.TFrame")
        tb.pack(fill="x", pady=(0, px(10)))
        ttk.Button(tb, text="Bu kiti ekle", image=th.icon_states("add"), compound="left",
                   command=self._queue_add).pack(side="left")
        ttk.Button(tb, text="Çıkar", image=th.icon_states("remove"), compound="left", style="Ghost.TButton",
                   command=self._queue_remove).pack(side="left", padx=(px(6), 0))
        ttk.Button(tb, text="Bitenleri temizle", image=th.icon_states("delete"), compound="left",
                   style="Ghost.TButton", command=self._queue_clear_done).pack(side="left", padx=(px(2), 0))
        self._help(tb, "Gece kuyruğu: kitleri o anki ayarlarıyla ekleyin (ölçek, hollow, parça tercihleri…), "
                       "'Hepsini hazırla' ile sırayla hazırlansın. Kitler birer birer çalışır; 'Düşük öncelik' "
                       "açıksa bilgisayar kullanılabilir kalır. Sabah sonuçlar bu listede; satıra çift tıklayınca "
                       "kit açılır. Kuyruk uygulama kapansa da saklanır. Bir kit hazırlanırken de başlatabilirsiniz: "
                       "kuyruk o iş bitince başlar.").pack(side="left", padx=px(10))
        self.b_queue = ttk.Button(tb, text="Hepsini hazırla", image=th.icon_states("play", "white", "#F2F6FF"),
                                  compound="left", style="Primary.TButton", command=self._queue_start)
        self.b_queue.pack(side="right")
        tf = ttk.Frame(f, style="Bar.TFrame")
        tf.pack(fill="both", expand=True)
        cols = ("kit", "islem", "durum", "sure", "sonuc")
        self.qtree = ttk.Treeview(tf, columns=cols, show="headings", selectmode="extended", height=8)
        for c, h, w in (("kit", "Kit", 210), ("islem", "İşlem", 130), ("durum", "Durum", 95), ("sure", "Süre", 80),
                        ("sonuc", "Sonuç", 520)):
            self.qtree.heading(c, text=h, anchor="w")
            self.qtree.column(c, width=px(w), anchor="w", stretch=c == "sonuc")
        for st, col in QUEUE_COLORS.items():
            self.qtree.tag_configure(st, foreground=col)
        qsb = ttk.Scrollbar(tf, orient="vertical", command=self.qtree.yview)
        self.qtree.configure(yscrollcommand=qsb.set)
        self.qtree.pack(side="left", fill="both", expand=True)
        qsb.pack(side="left", fill="y")
        self.qtree.bind("<Double-1>", self._queue_open)
        self.q_empty = ttk.Frame(tf, style="Bar.TFrame")
        ttk.Label(self.q_empty, image=th.icon("queue", C["accent"], 30), style="Card.TLabel").pack()
        ttk.Label(self.q_empty, text="Kuyruk boş", style="H2.Card.TLabel").pack(pady=(px(8), px(2)))
        ttk.Label(self.q_empty, text="Bir kit açıp ayarlarını yapın, alttaki 'Kuyruğa ekle' ile ekleyin; sonra "
                                     "'Hepsini hazırla'.", style="Muted.Card.TLabel").pack()
        ttk.Label(f, textvariable=self.v_queue_summary, style="Card.TLabel", wraplength=px(900),
                  justify="left").pack(anchor="w", pady=(px(8), 0))
        self._queue_refresh()

    def _queue_refresh(self):
        from .fmt import fmt_duration

        self.qtree.delete(*self.qtree.get_children())
        for i, it in enumerate(self.queue_items):
            sec = it.seconds or (time.time() - it.started if it.status == nq.RUNNING and it.started else 0)
            self.qtree.insert("", "end", iid=str(i), tags=(it.status,), values=(
                it.name, STEPS.get(it.steps, it.steps), it.status, fmt_duration(sec) if sec else "", it.result))
        waiting = sum(1 for i in self.queue_items if i.status in (nq.WAITING, nq.STOPPED))
        if self.queue_running and self.queue_current is None and self.proc is not None:
            # kullanıcı tek kitlik bir iş sürerken başlattı: o iş bitince kuyruk kendiliğinden başlar
            self.b_queue.configure(text=f"Çalışan iş bitince başlayacak ({waiting})", state="disabled")
        else:
            self.b_queue.configure(text=f"Hepsini hazırla ({waiting})" if waiting else "Hepsini hazırla",
                                   state="normal" if waiting and not self.queue_running else "disabled")
        self.right.set_text(4, f"Kuyruk · {waiting}" if waiting else "Kuyruk")
        if self.queue_items:
            self.q_empty.place_forget()
        else:
            self.q_empty.place(relx=0.5, rely=0.4, anchor="center")

    def _queue_save(self):
        try:
            nq.save_queue(self.queue_items)
        except OSError:
            pass

    def _queue_add(self):
        if not self.kit:
            messagebox.showinfo(APP_TITLE, "Önce bir kit klasörü seçin.")
            return
        if not self.v_steps.get():
            messagebox.showinfo(APP_TITLE, "En az bir işlem seçin (İşlemler kartı).")
            return
        try:
            args = self.build_args("prep")
        except ValueError as e:
            messagebox.showerror(APP_TITLE, str(e))
            return
        it = nq.QueueItem(str(self.kit), args, self.v_steps.get())
        old = [i for i in self.queue_items if i.kit == it.kit and i.status in (nq.WAITING, nq.STOPPED)]
        if old:                                  # aynı kit bekliyorsa yeni ayarlarla güncellenir, iki kez eklenmez
            self.queue_items[self.queue_items.index(old[0])] = it
            self.v_status.set(f"Kuyruktaki {it.name} yeni ayarlarla güncellendi.")
        else:
            self.queue_items.append(it)
            self.v_status.set(f"{it.name} kuyruğa eklendi ({len(self.queue_items)} kit).")
        self._queue_save()
        self._queue_refresh()

    def _queue_remove(self):
        drop = {int(s) for s in self.qtree.selection()}
        self.queue_items = [it for i, it in enumerate(self.queue_items) if i not in drop or it is self.queue_current]
        self._queue_save()
        self._queue_refresh()

    def _queue_clear_done(self):
        self.queue_items = [i for i in self.queue_items if i.status != nq.DONE]
        self._queue_save()
        self._queue_refresh()

    def _queue_open(self, _event=None):
        sel = self.qtree.selection()
        if sel:
            self.set_kit(Path(self.queue_items[int(sel[0])].kit))

    def _queue_start(self):
        """Kuyruğu başlat. Tek kitlik bir iş sürüyorsa kuyruk o iş bitince kendiliğinden başlar (önceden düğme iş
        boyunca kapalıydı ve iş bitince kuyruk hiç başlamıyordu: chiro tek başına hazırlanırken kuyruğa eklenen chiro
        ve Rango sabaha kadar 'bekliyor' kaldı)."""
        if self.queue_running or not nq.next_item(self.queue_items):
            return
        self.queue_running = True
        self.v_queue_summary.set("")
        if self.proc is not None:
            self.v_status.set("Kuyruk, çalışan iş bitince başlayacak.")
            self._queue_refresh()
            return
        self._queue_next()

    def _absorb_finished_run(self):
        """Biten tek kitlik hazırlık, kuyrukta aynı kit + aynı ayarlarla bekleyen öğeyle birebir aynıysa o öğe bitti
        sayılır (aynı kit iki kez hazırlanmasın). Ayarları farklıysa bekler."""
        args = self.running_args
        for it in self.queue_items:
            if it.status in (nq.WAITING, nq.STOPPED) and it.args == args:
                it.status, it.rc = nq.DONE, 0
                it.started, it.seconds = self.run_started, round(time.time() - self.run_started, 1)
                it.result = nq.result_text(Path(it.kit), self._resin_prefs())
        self._queue_save()
        self._queue_refresh()

    def _queue_next(self):
        it = nq.next_item(self.queue_items) if self.queue_running else None
        if it is None:
            self.queue_running = False
            self.queue_current = None
            self._queue_refresh()
            if any(i.status in (nq.DONE, nq.FAILED, nq.STOPPED) for i in self.queue_items):
                self.v_queue_summary.set(nq.morning_summary(self.queue_items))
                self.v_status.set("Kuyruk tamamlandı — sonuçlar Kuyruk sekmesinde.")
                self.right.select(self.right.index("end") - 1)
                try:                                   # sabah pencere öne gelsin
                    self.root.deiconify()
                    self.root.lift()
                    self.root.bell()
                except tk.TclError:
                    pass
            return
        if not Path(it.kit).is_dir():
            it.status, it.result = nq.FAILED, "kit klasörü bulunamadı"
            self._queue_save()
            self.root.after(10, self._queue_next)
            return
        it.status, it.started, it.seconds, it.rc, it.result = nq.RUNNING, time.time(), 0.0, None, ""
        self.queue_current = it
        self._queue_save()
        self._start(it.args, "prep", f"Kuyruk: {it.name} hazırlanıyor…")
        self._queue_refresh()

    def _queue_finished(self, rc) -> None:
        it = self.queue_current
        it.seconds = round(time.time() - it.started, 1)
        it.rc = rc
        if rc == 0:
            it.status = nq.DONE
            it.result = nq.result_text(Path(it.kit), self._resin_prefs())
        else:
            it.status = nq.STOPPED if not self.queue_running else nq.FAILED
            it.result = self._error_line() if self.queue_running else "durduruldu"
        self.queue_current = None
        self._queue_save()
        if self.kit and str(self.kit) == it.kit:
            self._load_results()
        self._queue_refresh()
        self.root.after(500, self._queue_next)

    def _error_line(self) -> str:
        """Başarısız işin günlüğünden anlamlı son satır (Traceback'in son satırı ya da 'Hata' içeren satır)."""
        lines = [x.strip() for x in self._tail if x.strip() and not x.startswith(("│", "┌", "└", "├", "┃"))]
        for x in reversed(lines):
            if re.search(r"Error|Exception|Hata|hata|SystemExit", x):
                return x[:200]
        return (lines[-1][:200] if lines else "bilinmeyen hata")

    # ------------------------------------------------------------------ sekmeler: montaj kontrolü
    def _build_asm_tab(self, nb):
        """Montaj kontrolü: önizleme + parça başına bağlantı/güven + kullanıcı düzeltmeleri."""
        th, px = self.theme, self.theme.px
        f = nb.page("Montaj kontrolü")
        self.v_asm_head = tk.StringVar(value="Henüz analiz yok.")
        ttk.Label(f, textvariable=self.v_asm_head, style="Strong.Card.TLabel", wraplength=px(900),
                  justify="left").pack(anchor="w", pady=(0, px(10)))
        pw = ttk.Frame(f, style="Bar.TFrame")
        pw.pack(fill="both", expand=True)
        pw.columnconfigure(0, weight=5, uniform="asm")
        pw.columnconfigure(1, weight=4, uniform="asm")
        pw.rowconfigure(0, weight=1)
        left = Card(pw, padding=px(10))
        left.grid(row=0, column=0, sticky="nsew", padx=(0, px(10)))
        right = ttk.Frame(pw, style="Bar.TFrame")
        right.grid(row=0, column=1, sticky="nsew")
        top = ttk.Frame(left, style="Bar.TFrame")
        top.pack(fill="x")
        self.v_view = tk.StringVar(value="front")
        Segmented(top, self.v_view, [("front", "Önden"), ("side", "Yandan"), ("height", "Boy"),
                                     ("compare", "Fotoğraf")], command=self._on_view_change).pack(side="left")
        ttk.Button(top, text="Büyük aç", image=th.icon_states("open"), compound="left", style="Small.Ghost.TButton",
                   command=self._open_asm_image).pack(side="right")
        # karşılaştırma: montajın gri görünümü (8 açı) ile stüdyo fotoğrafı yan yana
        self.cmp_bar = ttk.Frame(left, style="Bar.TFrame")
        ttk.Label(self.cmp_bar, text="Açı", style="Muted.Card.TLabel").pack(side="left")
        for d, ic in ((-1, "back"), (1, "forward")):
            ttk.Button(self.cmp_bar, image=th.icon_states(ic), style="Icon.Ghost.TButton",
                       command=lambda d=d: self._cmp_step("angle", d)).pack(side="left")
        ttk.Label(self.cmp_bar, text="Fotoğraf", style="Muted.Card.TLabel").pack(side="left", padx=(px(10), 0))
        for d, ic in ((-1, "back"), (1, "forward")):
            ttk.Button(self.cmp_bar, image=th.icon_states(ic), style="Icon.Ghost.TButton",
                       command=lambda d=d: self._cmp_step("photo", d)).pack(side="left")
        ttk.Button(self.cmp_bar, text="Fotoğraf ekle…", image=th.icon_states("photo"), compound="left",
                   style="Small.TButton", command=self._add_photos).pack(side="left", padx=(px(10), 0))
        ttk.Button(self.cmp_bar, text="Kaldır", style="Small.Ghost.TButton",
                   command=self._remove_photo).pack(side="left", padx=px(4))
        self.v_cmp_info = tk.StringVar(value="")
        self.cmp_info = ttk.Label(left, textvariable=self.v_cmp_info, style="Faint.Card.TLabel", wraplength=px(480),
                                  justify="left")
        self.cmp_angle, self.cmp_photo = 0, 0
        self.asm_img = ttk.Label(left, anchor="center", text="(önizleme yok)", style="Faint.Card.TLabel")
        self.asm_img.pack(fill="both", expand=True, pady=(px(8), 0))
        self.asm_img.bind("<Configure>", lambda e: self.root.after_idle(self._show_asm_image))
        self._asm_photo = None
        self._img_cache: dict = {}

        tf = ttk.Frame(right, style="Bar.TFrame")
        tf.pack(fill="both", expand=True)
        cols = ("parca", "durum", "bagli", "temas")
        self.atree = ttk.Treeview(tf, columns=cols, show="headings", selectmode="extended")
        for c, h, w in (("parca", "Parça", 105), ("durum", "Durum", 150), ("bagli", "Bağlandığı", 95),
                        ("temas", "Temas", 62)):
            self.atree.heading(c, text=h, anchor="e" if c == "temas" else "w")
            self.atree.column(c, width=px(w), anchor="e" if c == "temas" else "w", stretch=c == "durum")
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.atree.yview)
        self.atree.configure(yscrollcommand=sb.set)
        self.atree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        for tag, color in ASM_TAG_COLORS.items():
            self.atree.tag_configure(tag, foreground=color)

        act = Card(right, padding=(px(14), px(10)))
        act.pack(fill="x", pady=(px(10), 0))
        act.columnconfigure(1, weight=1)
        hd = ttk.Frame(act, style="Bar.TFrame")
        hd.grid(row=0, column=0, columnspan=3, sticky="ew")
        ttk.Label(hd, text="Seçili parçayı düzelt", style="H3.Card.TLabel").pack(side="left")
        self._help(hd, "Turuncu/kırmızı satırları önizlemede kontrol edin. Yanlış oturan parçayı seçip 'ayır' deyin "
                       "ya da doğru komşusunu seçip 'Bağla' deyin (bağlantı parçaları orijinde gelen kitlerde ve "
                       "alt klasörleri ayrı koordinatlarda gelen kitlerde uygulanır), sonra montajı yeniden çözün.\n"
                       "Figür kaidenin yanında duruyorsa: figürün kaideye değen parçasını (ayak) seçin, çapa olarak "
                       "kaide parçasını seçip 'Bağla'. Figür kümesi bütün olarak kaidenin üstüne oturur; yanlış "
                       "yerdeyse 'Başka konum dene'. Düzeltmeler kite kaydedilir.").pack(side="left", padx=px(6))
        ttk.Label(act, text="Bağla →", style="Muted.Card.TLabel").grid(row=1, column=0, sticky="w", pady=(px(8), 0))
        self.v_anchor = tk.StringVar()
        self.cb_anchor = ttk.Combobox(act, textvariable=self.v_anchor, state="readonly", width=18)
        self.cb_anchor.grid(row=1, column=1, sticky="ew", pady=(px(8), 0), padx=px(8))
        ttk.Button(act, text="Bağla", image=th.icon_states("link"), compound="left", style="Small.TButton",
                   command=self._fix_join).grid(row=1, column=2, sticky="e", pady=(px(8), 0))
        row = ttk.Frame(act, style="Bar.TFrame")
        row.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(px(8), 0))
        ttk.Button(row, text="Montajdan ayır", image=th.icon_states("cut"), compound="left", style="Small.TButton",
                   command=self._fix_detach).pack(side="left")
        ttk.Button(row, text="Başka konum dene", image=th.icon_states("rotate"), compound="left",
                   style="Small.TButton", command=self._fix_next_pose).pack(side="left", padx=px(6))
        row2 = ttk.Frame(act, style="Bar.TFrame")
        row2.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(px(4), 0))
        ttk.Button(row2, text="Seçilinin düzeltmesini kaldır", style="Small.Ghost.TButton",
                   command=self._fix_remove).pack(side="left")
        ttk.Button(row2, text="Tümünü temizle", style="Small.Ghost.TButton",
                   command=self._fix_clear).pack(side="left")
        self.v_fixes = tk.StringVar(value="Düzeltme yok.")
        ttk.Label(act, textvariable=self.v_fixes, style="Small.Card.TLabel", wraplength=px(420),
                  justify="left").grid(row=4, column=0, columnspan=3, sticky="w", pady=(px(8), 0))
        self.b_resolve = ttk.Button(act, text="Montajı yeniden çöz", image=th.icon_states("refresh", "white",
                                                                                         "#F2F6FF"),
                                    compound="left", style="Primary.TButton", command=lambda: self.run("analyze"))
        self.b_resolve.grid(row=5, column=0, columnspan=3, sticky="w", pady=(px(10), 0))

    def _enable_dnd(self):
        try:
            from tkinterdnd2 import DND_FILES

            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            pass

    def _on_drop(self, event):
        items = [Path(x) for x in self.root.tk.splitlist(event.data)]
        if not items:
            return
        photos = [p for p in items if p.is_file() and p.suffix.lower() in self.PHOTO_EXT]
        if photos and self.kit:                     # fotoğraf bırakıldı: karşılaştırmaya ekle
            self._add_photos(photos)
            return
        p = items[0]
        self.set_kit(p.parent if p.is_file() else p)

    # ------------------------------------------------------------------ kit
    def choose_kit(self):
        d = filedialog.askdirectory(title="Kit klasörünü seçin", initialdir=str(self.kit or Path("E:/3d/STL Modeller")))
        if d:
            self.set_kit(Path(d))

    def set_kit(self, p: Path):
        if not p.is_dir():
            messagebox.showerror(APP_TITLE, f"Klasör bulunamadı:\n{p}")
            return
        self.kit = p
        self.v_kit.set(str(p))
        self._load_settings(load_settings(p))
        self._list_files()
        self._fill_asm({})
        self._show_empty(False)
        self._load_results()
        self.root.title(f"{p.name} — SLAPrep")
        self.v_status.set(f"{p.name}: önce 'Analiz et', sonra '{self.b_prep.cget('text')}'.")

    def _list_files(self):
        from .io import list_stl, matches, part_names, rel_folders

        files = list_stl(self.kit)
        names = part_names(self.kit, files)
        excl = self._saved_exclude
        self.parts_info = {}
        for f in files:
            n, g = names[f]
            fo = rel_folders(self.kit, f)
            fill = ("solid" if matches(n, g, self._saved_solid, f.stem, fo)
                    else "hollow" if matches(n, g, self._saved_hollow, f.stem, fo) else "auto")
            self.parts_info[n] = dict(group=g, include=not matches(n, g, excl, f.stem, fo), fill=fill,
                                      separate=matches(n, g, self._saved_separate, f.stem, fo))
        self.groups = sorted({i["group"] for i in self.parts_info.values() if i["group"]})
        for w in self.f_folders.winfo_children():
            w.destroy()
        self.folder_vars = {}
        if not self.groups:
            ttk.Label(self.f_folders, text="Alt klasör yok", style="Faint.Card.TLabel").pack(anchor="w")
        for g in self.groups:
            v = tk.BooleanVar(value=any(i["include"] for i in self.parts_info.values() if i["group"] == g))
            self.folder_vars[g] = v
            ttk.Checkbutton(self.f_folders, text=g, variable=v, style="Card.TCheckbutton",
                            command=lambda g=g: self._toggle_folder(g)).pack(anchor="w")
        self._fill_tree({})

    def _toggle_folder(self, g):
        on = self.folder_vars[g].get()
        for i in self.parts_info.values():
            if i["group"] == g:
                i["include"] = on
        self._refresh_tree()

    def _toggle_part(self, _event=None):
        for iid in self.tree.selection():
            info = self.parts_info.get(iid)
            if info is not None:
                info["include"] = not info["include"]
        self._refresh_tree()
        return "break"

    def _selected_infos(self) -> list[dict]:
        sel = [self.parts_info[i] for i in self.tree.selection() if i in self.parts_info]
        if not sel:
            self.v_status.set("Önce Parçalar listesinden bir ya da birden çok parça seçin.")
        return sel

    def _set_fill(self, kind: str):
        """Seçili parçaların doluluk tercihi: auto (araç karar verir) / solid (dolu) / hollow (zorla)."""
        if not kind:
            return
        for info in self._selected_infos():
            info["fill"] = kind
        self._refresh_tree()

    def _toggle_separate(self):
        """Seçili parçaları ayrı plate grubuna koy (hepsi zaten ayrıysa çıkar)."""
        sel = self._selected_infos()
        if not sel:
            return
        on = not all(i["separate"] for i in sel)
        for info in sel:
            info["separate"] = on
        self._refresh_tree()

    def _part_menu_popup(self, event):
        row = self.tree.identify_row(event.y)
        if row and row not in self.tree.selection():
            self.tree.selection_set(row)
        if self.tree.selection():
            self.part_menu.tk_popup(event.x_root, event.y_root)

    def _fill_tree(self, results: dict):
        self._results = results
        self._refresh_tree()

    def _refresh_tree(self):
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        res = getattr(self, "_results", {}) or {}
        flt = self.v_part_filter.get().strip().lower()
        shown = 0
        for n, info in self.parts_info.items():
            if flt and flt not in n.lower() and flt not in (info["group"] or "").lower():
                continue
            shown += 1
            r = res.get(n, {})
            holes = r.get("holes") or []
            ds = sorted({h["d"] for h in holes}, reverse=True)
            hole_txt = f"{len(holes)} × {'/'.join(f'{d:g}' for d in ds)} mm" if holes else ("–" if r else "")
            if not r:
                result = ""
            elif not r.get("printed"):
                result = "Basılmaz"
            else:
                result = "Hollow" if r.get("hollowed") else "Dolu" if "hollowed" in r else ""
            sup = r.get("support") if r.get("printed") else None
            sup_txt = "" if not sup else (f"%{(1 - sup['visible']) * 100:.0f} gizli/arka" if sup["visible"] >= 0.01
                                          else "gizli/arka")
            vals = (info["group"] or "–", FILL_TXT[info.get("fill", "auto")], CHECK[bool(info.get("separate"))],
                    f"{r['volume_ml']:.1f} ml" if "volume_ml" in r else "",
                    result, hole_txt if r.get("printed") else "", sup_txt,
                    f"{r['vol_after_ml']:.1f} ml" if r.get("printed") and "vol_after_ml" in r else "",
                    r.get("plate") or "")
            self.tree.insert("", "end", iid=n, text=n, image=self.theme.tree_check[bool(info["include"])],
                             values=vals, tags=("off",) if not info["include"] else ())
        for s in sel:
            if self.tree.exists(s):
                self.tree.selection_add(s)
        n_inc = sum(1 for i in self.parts_info.values() if i["include"])
        n_print = sum(1 for r in res.values() if r.get("printed"))
        txt = f"{len(self.parts_info)} parça · {n_inc} dahil" if self.parts_info else ""
        if n_print:
            txt += f" · {n_print} basılacak"
        if flt:
            txt += f" · {shown} gösteriliyor"
        self.v_part_count.set(txt)
        self._sync_selbar()

    def _load_settings(self, s: Settings):
        self._saved_exclude = list(s.exclude)
        self._saved_solid, self._saved_hollow = list(s.solid), list(s.hollow)
        self._saved_separate = list(s.separate)
        if s.height is not None:
            self.v_scale_mode.set("height")
            self.v_height.set(f"{s.height:g}")
        elif s.scale is not None:
            self.v_scale_mode.set("scale")
            self.v_scale.set(f"{s.scale:g}")
        else:
            self.v_scale_mode.set("orig")
        self.v_height_mode.set("Sadece figür (kaide hariç)" if s.height_mode == "figure" else "Tümü (kaide dahil)")
        self.v_orig_height.set(f"{s.orig_height:g}" if s.orig_height else "")
        self.v_wall.set(f"{s.wall:g}")
        self.v_hole_auto.set(bool(s.hole_auto))
        self.v_hole_max.set(f"{s.hole_max:g}")
        self.v_hole.set(f"{s.hole:g}")
        self.v_min_holes.set(str(s.min_holes) if s.min_holes else "")
        self.v_visible.set(bool(s.allow_visible_holes))
        self.v_hole_bottom.set(bool(s.hole_on_bottom))
        self.v_hole_wall.set(bool(s.hole_on_wall))
        self.v_wall_mount.set(bool(s.wall_mount))
        up = s.up_axis
        self.v_up.set("otomatik" if not up else (up.upper() if up.startswith("-") else "+" + up.upper()))
        fa = s.front_axis
        self.v_front.set("otomatik" if not fa else (fa.upper() if fa.startswith("-") else "+" + fa.upper()))
        self.v_variant_pat.set(", ".join(s.variants))
        self.v_all_variants.set(bool(s.print_all_variants))
        self.v_print_ref.set(bool(s.print_reference))
        self.fix_detach = list(s.asm_detach or [])
        self.fix_join = [list(j) for j in (s.asm_join or [])]
        self.v_printer.set(s.printer)
        self.v_gap.set(f"{s.gap:g}")
        self.v_by_folder.set({None: "otomatik", True: "evet", False: "hayır"}[s.plates_by_folder])
        self.v_plate_goal.set(next((k for k, v in GOALS.items() if v == s.plate_goal), "En kısa toplam süre"))
        self.v_layer_h.set(f"{s.layer_height:g}")
        self.v_layer_t.set(f"{s.layer_time:g}")
        self.v_overhead.set(f"{s.plate_overhead_min:g}")
        self._apply_calibration()               # gerçek süre ölçümleri varsa katman süresi onlardan
        steps = s.steps if s.steps in STEPS else "all"
        self.v_op_hollow.set(steps in ("all", "hollow"))
        self.v_op_plates.set(steps in ("all", "plates"))
        self._on_ops()
        self._on_scale_mode()
        self._on_hole_mode()

    # ------------------------------------------------------------------ sonuçlar
    def _load_results(self):
        if not self.kit:
            return
        o = out_dir(self.kit)
        data = None
        for name in ("prep.json", "analysis.json"):
            p = o / name
            if p.exists():
                cand = json.loads(p.read_text(encoding="utf-8"))
                if data is None or p.stat().st_mtime > data[1]:
                    data = (cand, p.stat().st_mtime, name)
        if data is None:
            self._shown = None                   # önceki kitin plate/özet bilgisi kalmasın
            self._fill_plates({})
            self._set_summary("Henüz analiz yok. Alttaki 'Analiz et' ile başlayın.")
            self._fill_stats({}, "")
            return
        d, _, name = data
        self._fill_tree(d.get("parts", {}))
        self._fill_variants(d.get("assembly", {}).get("variant_groups", []))
        # plate'ler her zaman son hazırlıktan (analiz plate üretmez)
        prep = o / "prep.json"
        plates_d = json.loads(prep.read_text(encoding="utf-8")) if prep.exists() else d
        self._shown = dict(d=d, name=name, plates_d=plates_d)    # reçine fiyatı değişince yeniden hesap için
        self._fill_plates(plates_d)
        self._set_summary(self._summary_text(d, name))
        self._fill_stats(d, name)
        return self._fill_asm(d)

    def _fill_variants(self, groups):
        for w in self.f_variants.winfo_children():
            w.destroy()
        self.variant_rows = []
        if not groups:
            ttk.Label(self.f_variants, text="Yok", style="Faint.Card.TLabel").pack(anchor="w")
            return
        for g in groups:
            row = ttk.Frame(self.f_variants, style="Bar.TFrame")
            row.pack(fill="x", pady=1)
            v = tk.StringVar(value="otomatik")
            ttk.Label(row, text=" + ".join(g["selected"])[:30], style="Card.TLabel").pack(side="left")
            ttk.Combobox(row, textvariable=v, values=["otomatik"] + g["members"], state="readonly",
                         width=16).pack(side="right")
            self.variant_rows.append((g["members"], v))

    def _resin_prefs(self) -> ResinPrefs:
        d = ResinPrefs()
        return ResinPrefs(price_l=max(_num(self.v_resin_price.get(), 0.0) or 0.0, 0.0),
                          bottle_ml=_num(self.v_resin_bottle.get(), d.bottle_ml) or d.bottle_ml,
                          extra_pct=max(_num(self.v_resin_extra.get(), d.extra_pct) or 0.0, 0.0),
                          separate_price_l=max(_num(self.v_resin_sep_price.get(), 0.0) or 0.0, 0.0),
                          currency=self.v_resin_cur.get().strip() or d.currency)

    def _on_resin_change(self, *_):
        """Fiyat/pay değişince kaydet ve plate/özet görünümünü yeniden hesapla (yeniden hazırlamaya gerek yok)."""
        p = self._resin_prefs()
        try:
            save_prefs(p)
        except OSError:
            pass
        shown = getattr(self, "_shown", None)
        if shown:
            self._fill_plates(shown["plates_d"])
            self._set_summary(self._summary_text(shown["d"], shown["name"]))
            self._fill_stats(shown["d"], shown["name"])

    def _time_model(self) -> tuple[float, float, float]:
        """Formdaki süre modeli: (katman mm, katman sn, plate başına ek dk)."""
        return (_num(self.v_layer_h.get(), 0.05) or 0.05, _num(self.v_layer_t.get(), 6.5) or 6.5,
                _num(self.v_overhead.get(), 15.0) or 0.0)

    def _plate_minutes(self, d) -> tuple[dict, float | None]:
        """Plate süreleri güncel süre ayarıyla (kayıtlı en yüksek noktadan): ayar değişince yeniden hazırlamadan
        güncellenir. En yüksek nokta kayıtlı değilse hazırlıktaki tahmin."""
        lh, lt, ov = self._time_model()
        out = {}
        for label, c in (d.get("plates") or {}).items():
            mh = c.get("max_height")
            out[label] = (mh / lh * lt / 60.0 + ov) if mh is not None else c.get("est_min")
        vals = [v for v in out.values() if v is not None]
        return out, (sum(vals) if vals else d.get("print_time_min"))

    def _on_time_change(self, *_):
        shown = getattr(self, "_shown", None)
        if shown:
            self._fill_plates(shown["plates_d"])
            self._set_summary(self._summary_text(shown["d"], shown["name"]))
            self._fill_stats(shown["d"], shown["name"])

    def _apply_calibration(self):
        """Kayıtlı gerçek süre ölçümlerinden katman süresini (ve yeterli ölçüm varsa ek süreyi) forma uygula."""
        from .timecal import describe, fit, load_points

        pts = load_points()
        res = fit(pts, _num(self.v_overhead.get(), 15.0) or 0.0)
        if res:
            self.v_layer_t.set(f"{res[0]:.2f}")
            if len(pts) >= 2:
                self.v_overhead.set(f"{res[1]:.0f}")
        self.v_cal_info.set(describe(pts, res))

    def _save_calibration(self):
        from .timecal import CalPoint, add_point, parse_duration

        shown = getattr(self, "_shown", None)
        plates = (shown or {}).get("plates_d", {}).get("plates") or {}
        label = self.v_cal_plate.get()
        if label not in plates or plates[label].get("max_height") is None:
            messagebox.showinfo(APP_TITLE, "Önce listeden bastığınız plate'i seçin.")
            return
        minutes = parse_duration(self.v_cal_time.get())
        if not minutes or minutes <= 0:
            messagebox.showinfo(APP_TITLE, "Süreyi 3:31 ya da 3 sa 31 dk biçiminde girin.")
            return
        lh = _num(self.v_layer_h.get(), 0.05) or 0.05
        add_point(CalPoint(float(plates[label]["max_height"]), float(minutes), lh,
                           f"{self.kit.name if self.kit else ''} / {label}"))
        self.v_cal_time.set("")
        self._apply_calibration()
        self.v_status.set(f"Süre ayarı güncellendi: {self.v_cal_info.get()}")

    def _fill_plates(self, d):
        from .fmt import fmt_duration

        self.ptree.delete(*self.ptree.get_children())
        plates = d.get("plates") or {}
        rs = resin_summary(d, self._resin_prefs())
        mins, tot = self._plate_minutes(d)
        for label, c in plates.items():
            names = [n for n, p in d["parts"].items() if p.get("plate") == label]
            g = c.get("min_gap")
            mh, est = c.get("max_height"), mins.get(label)
            self.ptree.insert("", "end", iid=label, values=(
                label, ", ".join(names), "–" if mh is None else f"{mh:.0f} mm",
                "–" if est is None else fmt_duration(est * 60), resin_cell(rs["plates"].get(label), rs["currency"]),
                "–" if g is None else f"{g:.1f} mm"))
        self.cb_cal_plate.configure(values=list(plates))
        if self.v_cal_plate.get() not in plates:
            self.v_cal_plate.set(next(iter(plates), ""))
        goal = "en kısa toplam süre" if d.get("plate_goal", "time") != "plates" else "en az plate"
        self.st["p_plates"].set(str(len(plates)) if plates else "–")
        self.st["p_time"].set(fmt_duration(tot * 60) if plates and tot else "–")
        self.st["p_goal"].set("En kısa süre" if goal == "en kısa toplam süre" else "En az plate")
        t = rs["total"]
        self.st["p_resin"].set(f"{t['ml']:.0f} ml" if t["net_ml"] else "–")
        self.st_sub["p_resin"].set(f"{t['cost']:.2f} {rs['currency']}" if t.get("cost") is not None else
                                   ("fiyat: Reçine kartı" if t["net_ml"] else ""))
        txt = ""
        g = d.get("grouping")
        if plates and tot and g and g.get("folders") and g.get("single"):
            f, s1 = g["folders"], g["single"]
            if g["chosen"] == "single":
                txt = (f"Süre için alt klasörlere ayrılmadı (ayrılsaydı {f['n_plates']} plate, "
                       f"~{fmt_duration(f['minutes'] * 60)}).")
            elif s1["n_plates"] < f["n_plates"] or s1["minutes"] < f["minutes"] - 15:
                txt = (f"Klasörsüz dizilseydi: {s1['n_plates']} plate, ~{fmt_duration(s1['minutes'] * 60)} "
                       "(Yazıcı ve plate → Klasörlere ayır: Oto).")
        if not plates and d.get("steps") == "hollow":
            txt = "Bu hazırlıkta plate dizilmedi (sadece hollow + delik): parçalar Klasörler → Parça dosyaları altında."
        self.v_plate_total.set(txt)

    def _fill_stats(self, d, name):
        """Özet sekmesinin sayı kartları."""
        from .fmt import fmt_duration

        parts = d.get("parts", {}) if d else {}
        printed = [p for p in parts.values() if p.get("printed")]
        self.st["parts"].set(f"{len(printed)}" if parts else "–")
        self.st_sub["parts"].set(f"toplam {len(parts)} dosya" if parts else "")
        a = d.get("assembly", {}) if d else {}
        if d and "height_after_mm" in d and d.get("scale"):
            self.st["height"].set(f"{d['height_after_mm']:.0f} mm")
            self.st_sub["height"].set(f"ölçek {d['scale']:.3g} · orijinal {a.get('height_mm', 0):.0f} mm"
                                      if a else f"ölçek {d['scale']:.3g}")
        elif a:
            self.st["height"].set(f"{a.get('height_mm', 0):.0f} mm")
            self.st_sub["height"].set(MODE_TXT.get(a.get("mode"), ""))
        else:
            self.st["height"].set("–")
            self.st_sub["height"].set("")
        plates = d.get("plates") or {} if d else {}
        self.st["plates"].set(str(len(plates)) if plates else "–")
        tot = self._plate_minutes(d)[1] if d else None
        self.st_sub["plates"].set(f"~{fmt_duration(tot * 60)}" if plates and tot else "")
        if name == "prep.json":
            rs = resin_summary(d, self._resin_prefs())
            t = rs["total"]
            self.st["resin"].set(f"{t['ml']:.0f} ml" if t["net_ml"] else "–")
            self.st_sub["resin"].set(f"{t['cost']:.2f} {rs['currency']}" if t.get("cost") is not None else
                                     f"+%{rs['extra_pct']:g} destek dahil")
        else:
            self.st["resin"].set("–")
            self.st_sub["resin"].set("hazırlıktan sonra")

    # ------------------------------------------------------------------ montaj kontrolü
    def _fill_asm(self, d):
        """Kontrol listesi: önce yerleşmeyen/zayıf parçalar. Dönüş: kontrol gerektiren satır sayısı."""
        self.atree.delete(*self.atree.get_children())
        a = d.get("assembly") or {}
        parts = d.get("parts") or {}
        rows = checklist(parts, a)
        for n, text, tag, to, c in rows:
            self.atree.insert("", "end", iid=n, values=(n, text, to, c), tags=(tag,))
        placed = [n for n, p in parts.items() if p.get("placed") and n in (a.get("selected") or [])]
        self.cb_anchor.configure(values=sorted(placed, key=str.lower) +
                                 sorted((n for n in parts if n not in placed), key=str.lower))
        n_check = sum(1 for r in rows if r[2] == "check")
        n_bad = sum(1 for r in rows if r[2] == "bad")
        mode = a.get("mode")
        if not a:
            head = "Henüz analiz yok."
        elif mode == "bilinmiyor":
            head = ("Montaj bulunamadı: boy hesaplanamıyor. Boyut kartında ürün sayfasındaki orijinal boyu girin "
                    "ya da doğrudan ölçek kullanın.")
        else:
            sel = [n for n in parts if n in (a.get("selected") or [])]
            head = (f"{MODE_TXT.get(mode, mode)}: {len(placed)}/{len(sel)} parça yerleşti · montajlı yükseklik "
                    f"{a.get('height_mm', 0):.1f} mm")
            if mode == "tam":
                head += " · konumlar dosyadan, kontrol gerekmez."
            elif n_check or n_bad:
                head += f" · kontrol edilecek: {n_check} · yerleşmeyen: {n_bad}"
            else:
                head += " · tüm ekler güçlü."
        self.v_asm_head.set(head)
        n = n_check + n_bad if mode in ("otomatik", "kısmi") else 0
        self.right.set_text(3, f"Montaj kontrolü · {n}" if n else "Montaj kontrolü")
        self._refresh_fixes_label()
        self._on_scale_change()
        self._show_asm_image()
        return n

    COMPARE_ANGLES = (0, 45, 90, 135, 180, 225, 270, 315)
    PHOTO_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp")

    def _asm_image_path(self) -> Path | None:
        if not self.kit:
            return None
        v = self.v_view.get()
        name = (f"compare_{self.COMPARE_ANGLES[self.cmp_angle % 8]:03d}.png" if v == "compare"
                else "height.png" if v == "height" else f"assembly_{v}.png")
        p = out_dir(self.kit) / "previews" / name
        return p if p.exists() else None

    def _photos(self) -> list[Path]:
        """Karşılaştırma fotoğrafları: kullanıcının eklediği kopyalar + kitle gelen stüdyo görselleri."""
        if not self.kit:
            return []
        d = out_dir(self.kit) / "photos"
        own = sorted(f for f in d.iterdir() if f.suffix.lower() in self.PHOTO_EXT) if d.exists() else []
        if getattr(self, "_kit_images", (None,))[0] != self.kit:
            self._kit_images = (self.kit, find_kit_images(self.kit, self.PHOTO_EXT))
        return own + self._kit_images[1]

    def _load_cropped(self, p: Path):
        """Resmi aç, düz arka plan kenarlarını kırp (önbellekli)."""
        from PIL import Image, ImageChops

        key = (str(p), p.stat().st_mtime)
        if key not in self._img_cache:
            im = Image.open(p).convert("RGB")
            bg = Image.new("RGB", im.size, im.getpixel((0, 0)))       # köşe rengi = arka plan
            box = ImageChops.difference(im, bg).point(lambda x: 255 if x > 12 else 0).getbbox()
            if box:
                im = im.crop((max(0, box[0] - 6), max(0, box[1] - 6), min(im.width, box[2] + 6),
                              min(im.height, box[3] + 6)))
            self._img_cache[key] = im
        return self._img_cache[key]

    def _on_view_change(self):
        if self.v_view.get() == "compare":
            self.cmp_bar.pack(fill="x", pady=(self.theme.px(8), 0), before=self.asm_img)
            self.cmp_info.pack(fill="x", pady=(self.theme.px(4), 0), before=self.asm_img)
        else:
            self.cmp_bar.pack_forget()
            self.cmp_info.pack_forget()
        self._show_asm_image()

    def _cmp_step(self, what, d):
        if what == "angle":
            self.cmp_angle = (self.cmp_angle + d) % 8
        else:
            n = len(self._photos())
            self.cmp_photo = (self.cmp_photo + d) % n if n else 0
        self._show_asm_image()

    def _add_photos(self, files=None):
        """Stüdyo fotoğraflarını kitin çıktı klasörüne kopyala (orijinallere dokunulmaz)."""
        import shutil

        if not self.kit:
            messagebox.showinfo(APP_TITLE, "Önce bir kit klasörü seçin.")
            return
        files = files or filedialog.askopenfilenames(
            title="Stüdyo fotoğrafları", filetypes=[("Resimler", "*.jpg *.jpeg *.png *.webp *.bmp")])
        d = out_dir(self.kit) / "photos"
        d.mkdir(parents=True, exist_ok=True)
        added = 0
        for f in files:
            f = Path(f)
            if f.suffix.lower() not in self.PHOTO_EXT:
                continue
            dst, i = d / f.name, 1
            while dst.exists():
                dst, i = d / f"{f.stem}_{i}{f.suffix}", i + 1
            shutil.copy2(f, dst)
            added += 1
        if added:
            self.cmp_photo = len(self._photos()) - 1
            self.v_view.set("compare")
            self._on_view_change()
            self.right.select(3)

    def _remove_photo(self):
        ph = self._photos()
        if not ph:
            return
        p = ph[self.cmp_photo % len(ph)]
        if out_dir(self.kit) not in p.parents:
            messagebox.showinfo(APP_TITLE, f"{p.name} kitle gelen bir görsel; SLAPrep onu silmez.")
            return
        if messagebox.askyesno(APP_TITLE, f"{p.name} karşılaştırmadan kaldırılsın mı?\n(Sadece SLAPrep'in kopyası "
                                          "silinir, orijinal fotoğrafınıza dokunulmaz.)"):
            p.unlink(missing_ok=True)
            self.cmp_photo = 0
            self._show_asm_image()

    def _compose(self):
        """Gösterilecek PIL resmi: tek önizleme ya da (montaj görünümü | fotoğraf) yan yana."""
        from PIL import Image

        p = self._asm_image_path()
        if self.v_view.get() == "height":
            return self._compose_ruler(p) if p else None
        if self.v_view.get() != "compare":
            return self._load_cropped(p) if p else None
        ph = self._photos()
        photo = ph[self.cmp_photo % len(ph)] if ph else None
        ang = self.COMPARE_ANGLES[self.cmp_angle % 8]
        self.v_cmp_info.set(
            f"Montaj {ang}° (gri, ürün görseli gibi)  |  "
            + (f"Fotoğraf {self.cmp_photo % len(ph) + 1}/{len(ph)}: {photo.name}" if photo else
               "Fotoğraf yok: 'Fotoğraf ekle…' ya da fotoğrafı pencereye sürükleyip bırakın.")
            + "  — Açıyı fotoğrafa uydurup parçaların yerini karşılaştırın; yanlış olanı listeden ayırın/bağlayın.")
        ims = [self._load_cropped(x) for x in (p, photo) if x is not None]
        if not ims:
            return None
        h = max(im.height for im in ims)
        ims = [im.resize((max(1, int(im.width * h / im.height)), h)) for im in ims]
        gap = 12 if len(ims) > 1 else 0
        out = Image.new("RGB", (sum(im.width for im in ims) + gap, h), (255, 255, 255))
        x = 0
        for im in ims:
            out.paste(im, (x, 0))
            x += im.width + gap
        return out

    def _height_meta(self) -> dict | None:
        p = out_dir(self.kit) / "previews" / "height.json" if self.kit else None
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p and p.exists() else None
        except Exception:
            return None

    def _target_heights(self) -> tuple[float, float] | None:
        """Boyut ayarına göre basılacak (toplam boy, kaide hariç figür boyu) mm; bilinmiyorsa None."""
        m = self._height_meta()
        if not m:
            return None
        s0 = m.get("scale") or 1.0
        H0 = m["height_mm"] / s0                                  # orijinal (ölçeksiz) boy
        F0 = (m["height_mm"] - m.get("figure_bottom_mm", 0.0)) / s0
        mode = self.v_scale_mode.get()
        if mode == "scale":
            s = _num(self.v_scale.get(), 1.0) or 1.0
        elif mode == "height":
            t = _num(self.v_height.get())
            if not t or t <= 0:
                return None
            s = t / (F0 if self.v_height_mode.get().startswith("Sadece") else H0)
        else:
            s = 1.0
        return H0 * s, F0 * s

    def _compose_ruler(self, p: Path):
        """Boy görünümü + yanında cetvel (Boyut ayarına göre basılacak mm). Resim montajın kutusuna tam oturur:
        piksel ↔ mm doğrusal (render_height_view)."""
        from PIL import Image, ImageDraw, ImageFont

        from .fmt import nice_step

        im = Image.open(p).convert("RGB")
        hs = self._target_heights()
        if hs is None:
            return im
        total, figure = hs
        try:
            font = ImageFont.truetype("segoeui.ttf", 22)
            bold = ImageFont.truetype("segoeuib.ttf", 26)
        except OSError:
            font = bold = ImageFont.load_default()
        top, rw, pad = 54, 150, 8
        out = Image.new("RGB", (im.width + rw + pad, im.height + top + 20), "white")
        out.paste(im, (rw + pad, top))
        d = ImageDraw.Draw(out)
        y_of = lambda v: top + im.height - v / total * im.height        # noqa: E731
        x = rw - 6
        d.line([(x, y_of(0)), (x, y_of(total))], fill="#333333", width=3)
        step = nice_step(total)
        minor = step / 5 if im.height * (step / 5) / total >= 6 else step / 2
        v = 0.0
        while v <= total + 1e-6:
            major = abs(v / step - round(v / step)) < 1e-6
            d.line([(x - (22 if major else 10), y_of(v)), (x, y_of(v))], fill="#333333", width=2 if major else 1)
            if major:
                d.text((x - 28, y_of(v)), f"{v:g}", fill="#222222", font=font, anchor="rm")
            v += minor
        d.text((rw + pad, 8), f"Basılacak boy: {total:.0f} mm ({total / 10:.1f} cm)", fill=C["accent_text"], font=bold)
        m = self._height_meta() or {}
        fb = m.get("figure_bottom_mm", 0.0) / max(m.get("height_mm", 1.0), 1e-6) * total
        if fb > 0.5:
            yb = y_of(fb)
            for xs in range(rw + pad, out.width, 16):
                d.line([(xs, yb), (xs + 8, yb)], fill="#c0392b", width=2)
            txt = f"kaide hariç figür: {figure:.0f} mm"
            box = d.textbbox((rw + pad + 4, yb - 4), txt, font=font, anchor="lb")
            d.rectangle((box[0] - 4, box[1] - 2, box[2] + 4, box[3] + 2), fill="white")
            d.text((rw + pad + 4, yb - 4), txt, fill="#c0392b", font=font, anchor="lb")
        return out

    def _on_scale_change(self, *_):
        hs = self._target_heights() if self.kit else None
        self.v_final_height.set("" if hs is None else
                                f"Basılacak boy ~{hs[0]:.0f} mm · kaide hariç figür ~{hs[1]:.0f} mm")
        (self.l_final.grid if hs is not None else self.l_final.grid_remove)()
        if self.v_view.get() == "height":
            self._show_asm_image()

    def _show_asm_image(self):
        if self.v_view.get() == "compare" and not self._asm_image_path() and not self._photos():
            self.asm_img.configure(image="", text="Karşılaştırma görünümü yok — önce analiz edin.")
            return
        if self.v_view.get() != "compare" and self._asm_image_path() is None:
            self.asm_img.configure(image="", text="Önizleme yok — önce analiz edin.")
            return
        w, h = max(self.asm_img.winfo_width(), 50), max(self.asm_img.winfo_height(), 50)
        try:
            from PIL import Image, ImageTk

            im = self._compose()
            if im is None:
                self.asm_img.configure(image="", text="Önizleme yok — önce analiz edin.")
                return
            s = min(w / im.width, h / im.height)
            im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.LANCZOS)
            self._asm_photo = ImageTk.PhotoImage(im)
        except Exception as e:  # resim açılamazsa uygulama çökmesin
            self.asm_img.configure(image="", text=f"(resim gösterilemedi: {e})")
            return
        self.asm_img.configure(image=self._asm_photo, text="")

    def _open_asm_image(self):
        p = self._asm_image_path()
        if self.v_view.get() == "compare":
            ph = self._photos()
            p = ph[self.cmp_photo % len(ph)] if ph else p
        if p:
            os.startfile(str(p))

    def _selected_asm_parts(self) -> list[str]:
        sel = list(self.atree.selection())
        if not sel:
            messagebox.showinfo(APP_TITLE, "Önce listeden bir parça seçin.")
        return sel

    def _fix_detach(self):
        for n in self._selected_asm_parts():
            self.fix_join = [j for j in self.fix_join if j[1] != n]
            if n not in self.fix_detach:
                self.fix_detach.append(n)
        self._save_fixes()

    def _fix_join(self):
        anchor = self.v_anchor.get()
        if not anchor:
            messagebox.showinfo(APP_TITLE, "Bağlanacağı parçayı (çapa) açılır listeden seçin.")
            return
        for n in self._selected_asm_parts():
            if n == anchor:
                continue
            self.fix_detach = [x for x in self.fix_detach if x != n]
            self.fix_join = [j for j in self.fix_join if j[1] != n] + [[anchor, n]]
        self._save_fixes()

    def _fix_next_pose(self):
        """Bağlanan parça yanlış yere (ör. diğer bileğe) oturduysa: bir sonraki aday konumu iste (4 aday, döngüsel)."""
        sel = self._selected_asm_parts()
        joined = {j[1]: j for j in self.fix_join}
        if sel and not any(n in joined for n in sel):
            messagebox.showinfo(APP_TITLE, "Bu sadece sizin bağladığınız parçalarda çalışır: önce parçayı doğru "
                                           "komşusuna bağlayın.")
            return
        for n in sel:
            if n in joined:
                j = joined[n]
                r = ((j[2] if len(j) > 2 else 0) + 1) % 4
                self.fix_join = [x for x in self.fix_join if x[1] != n] + [[j[0], n] + ([r] if r else [])]
        self._save_fixes()

    def _fix_remove(self):
        for n in self._selected_asm_parts():
            self.fix_detach = [x for x in self.fix_detach if x != n]
            self.fix_join = [j for j in self.fix_join if n not in j]
        self._save_fixes()

    def _fix_clear(self):
        self.fix_detach, self.fix_join = [], []
        self._save_fixes()

    def _save_fixes(self):
        """Düzeltmeler hemen kite kaydedilir (slaprep.yaml); bir sonraki analizde uygulanır."""
        if self.kit:
            from .config import save_settings

            s = load_settings(self.kit)
            s.asm_detach, s.asm_join = list(self.fix_detach), [list(j) for j in self.fix_join]
            save_settings(self.kit, s)
        self._refresh_fixes_label(pending=True)

    def _refresh_fixes_label(self, pending=False):
        items = [f"ayır: {n}" for n in self.fix_detach] + [
            f"bağla: {j[1]} → {j[0]}" + (f" (konum {j[2] + 1})" if len(j) > 2 and j[2] else "") for j in self.fix_join]
        if not items:
            self.v_fixes.set("Düzeltme yok.")
        else:
            self.v_fixes.set("Düzeltmeler: " + "; ".join(items)
                             + ("  — uygulamak için 'Montajı yeniden çöz'e basın." if pending else ""))

    def _summary_text(self, d, name):
        a = d.get("assembly", {})
        parts = d.get("parts", {})
        printed = [p for p in parts.values() if p.get("printed")]
        lines = [f"Kaynak: {name}", f"Parça: {len(parts)} (basılacak {len(printed)})"]
        if a:
            lines.append(f"Montaj: {MODE_TXT.get(a.get('mode'), a.get('mode'))}, yukarı {'+' if a.get('up_sign', 1) > 0 else '-'}"
                         f"{a.get('up_axis', '?').upper()}")
            lines.append(f"Montajlı yükseklik: {a.get('height_mm', 0):.1f} mm")
            for g in a.get("variant_groups", []):
                if set(g["selected"]) == set(g["members"]):
                    lines.append(f"Olası alternatif (hepsi basılacak): {' / '.join(g['members'])}")
                    continue
                lines.append(f"Alternatif: {' + '.join(g['selected'])}  (diğer: "
                             f"{', '.join(m for m in g['members'] if m not in g['selected'])})")
        if "scale" in d:
            lines.append(f"Ölçek: {d['scale']:.4f} → yükseklik {d.get('height_after_mm', 0):.1f} mm")
            before = sum(p.get("vol_before_ml", p["volume_ml"]) for p in printed)
            after = sum(p.get("vol_after_ml", p["volume_ml"]) for p in printed)
            lines.append(f"Reçine (dolu → hazır): {before:.1f} ml → {after:.1f} ml")
            if name == "prep.json":
                lines += summary_lines(resin_summary(d, self._resin_prefs()))
            lines.append(f"Plate sayısı: {len(d.get('plates') or {})}")
            tot = self._plate_minutes(d)[1] if d.get("plates") else None
            if tot:
                from .fmt import fmt_duration

                lines.append(f"Tahmini baskı süresi: {fmt_duration(tot * 60)} (plate başına en yüksek parçaya göre)")
        warns = list(d.get("warnings", []))
        for n, p in parts.items():
            if p.get("printed"):
                warns += [f"{n}: {w}" for w in p.get("warnings", []) if "onarıldı" not in w]
        if warns:
            lines.append("\nUyarılar:")
            lines += [f"  • {w}" for w in warns]
        return "\n".join(lines)

    def _set_summary(self, text):
        """Özet metni biçimli yazılır: 'Anahtar: değer' satırlarında anahtar soluk, uyarılar madde imli."""
        t = self.summary
        t.configure(state="normal")
        t.delete("1.0", "end")
        for line in text.split("\n"):
            s = line.strip()
            if not s:
                continue
            if s == "Uyarılar:":
                t.insert("end", "Uyarılar\n", "h")
            elif line.startswith("  • "):
                t.insert("end", "●  ", "bullet")
                t.insert("end", line[4:] + "\n", "warn")
            elif ":" in s and not line.startswith(" ") and s.index(":") < 40:
                k, v = s.split(":", 1)
                t.insert("end", k + ":", "key")
                t.insert("end", v + "\n")
            else:
                t.insert("end", line + "\n")
        t.configure(state="disabled")

    # ------------------------------------------------------------------ çalıştırma
    def _excluded(self) -> list[str]:
        out = []
        for g in self.groups:
            members = [n for n, i in self.parts_info.items() if i["group"] == g]
            if members and not any(self.parts_info[n]["include"] for n in members):
                out.append(g)
        out += [n for n, i in self.parts_info.items() if not i["include"] and i["group"] not in out]
        return out

    def _variants(self) -> list[str]:
        out = [x.strip() for x in self.v_variant_pat.get().split(",") if x.strip()]
        out += [v.get() for _, v in self.variant_rows if v.get() != "otomatik"]
        return out

    def build_args(self, cmd: str) -> list[str]:
        args = [cmd, str(self.kit), "--no-open", "--reset"]     # ayarların tamamı formdan gelir
        args += ["--exclude", ",".join(self._excluded())]
        for v in self._variants():
            args += ["--variant", v]
        if self.v_up.get() != "otomatik":
            u = self.v_up.get().lower()
            args += ["--up-axis", u.lstrip("+")]
        if self.v_front.get() != "otomatik":
            args += ["--front-axis", self.v_front.get().lower().lstrip("+")]
        if self.v_wall_mount.get():
            args += ["--wall-mount"]
        if self.fix_detach:
            args += ["--detach", ",".join(self.fix_detach)]
        for j in self.fix_join:
            args += ["--join", f"{j[0]}:{j[1]}" + (f":{j[2] + 1}" if len(j) > 2 and j[2] else "")]
        if cmd == "analyze":
            return args
        if not self.v_steps.get():
            raise ValueError("En az bir işlem seçin (İşlemler kartı).")
        args += ["--steps", self.v_steps.get()]
        # parça başına tercihler (form --reset ile gittiği için her seferinde açıkça)
        for opt, names in (("--solid", [n for n, i in self.parts_info.items() if i.get("fill") == "solid"]),
                           ("--hollow", [n for n, i in self.parts_info.items() if i.get("fill") == "hollow"]),
                           ("--separate", [n for n, i in self.parts_info.items() if i.get("separate")])):
            if names:
                args += [opt, ",".join(names)]
        mode = self.v_scale_mode.get()
        if mode == "height":
            h = _num(self.v_height.get())
            if not h or h <= 0:
                raise ValueError("Hedef yükseklik geçersiz.")
            args += ["--height", f"{h:g}", "--height-mode",
                     "figure" if self.v_height_mode.get().startswith("Sadece") else "all"]
            oh = _num(self.v_orig_height.get())
            if oh and oh > 0:
                args += ["--orig-height", f"{oh:g}"]
        elif mode == "scale":
            f = _num(self.v_scale.get())
            if not f or f <= 0:
                raise ValueError("Ölçek geçersiz.")
            args += ["--scale", f"{f:g}"]
        args += ["--wall", f"{_num(self.v_wall.get(), 2.0):g}"]
        if self.v_hole_auto.get():
            args += ["--hole-max", f"{_num(self.v_hole_max.get(), 5.0):g}"]
        else:
            args += ["--hole", f"{_num(self.v_hole.get(), 3.0):g}"]
        mh = _num(self.v_min_holes.get())
        if mh:
            args += ["--holes", str(int(mh))]
        if self.v_visible.get():
            args += ["--allow-visible-holes"]
        if self.v_hole_bottom.get():
            args += ["--holes-on-bottom"]
        if self.v_hole_wall.get():
            args += ["--holes-on-wall-back"]
        if self.v_all_variants.get():
            args += ["--print-all-variants"]
        if self.v_print_ref.get():
            args += ["--print-reference"]
        args += ["--printer", self.v_printer.get(), "--gap", f"{_num(self.v_gap.get(), 3.0):g}"]
        args += ["--plate-goal", GOALS.get(self.v_plate_goal.get(), "time"),
                 "--layer-height", f"{_num(self.v_layer_h.get(), 0.05) or 0.05:g}",
                 "--layer-time", f"{_num(self.v_layer_t.get(), 6.5) or 6.5:g}",
                 "--plate-overhead", f"{_num(self.v_overhead.get(), 15.0):g}"]
        bf = self.v_by_folder.get()
        if bf == "evet":
            args += ["--plates-by-folder"]
        elif bf == "hayır":
            args += ["--no-plates-by-folder"]
        return args

    def run(self, cmd: str):
        if not self.kit:
            messagebox.showinfo(APP_TITLE, "Önce bir kit klasörü seçin.")
            return
        if self.proc is not None:
            return
        try:
            args = self.build_args(cmd)
        except ValueError as e:
            messagebox.showerror(APP_TITLE, str(e))
            return
        self._start(args, cmd, "Analiz ediliyor…" if cmd == "analyze" else "Hazırlanıyor…")

    def _start(self, args: list[str], cmd: str, msg: str):
        """`python -m slaprep` alt sürecini başlatır (tek kit düğmeleri ve gece kuyruğu ortak)."""
        self.log.delete("1.0", "end")
        self._tail.clear()
        self._log("> slaprep " + " ".join(f'"{a}"' if " " in a else a for a in args) + "\n\n")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", COLUMNS="118")
        flags = NO_WINDOW | (LOW_PRIORITY if self.v_low_prio.get() else 0)
        self.proc = subprocess.Popen([_python(), "-m", "slaprep", *args], stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                     env=env, creationflags=flags)
        self.running_cmd = cmd
        self.running_args, self.run_started = list(args), time.time()
        self._busy(True, msg)
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()

    def _reader(self, proc):
        for line in proc.stdout:
            self.q.put(("line", line))
        proc.wait()
        self.q.put(("done", proc.returncode))

    def _poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "line":
                    self._log(val)
                    self._tail.append(val)
                    m = re.search(r"\[(\d+)/(\d+)\]\s+(.+?) hazırlanıyor", val)
                    if m:
                        i, n = int(m.group(1)), int(m.group(2))
                        self.pb.stop()
                        self.pb.configure(mode="determinate", maximum=n, value=i - 1)
                        who = f"{self.queue_current.name} — " if self.queue_current else ""
                        self.v_status.set(f"Hazırlanıyor: {who}{m.group(3)} ({i}/{n})")
                    elif val.strip() and not val.startswith(("│", "┌", "└", "├", "┃")):
                        if self.pb["mode"] == "indeterminate":
                            self.v_status.set(val.strip()[:120])
                elif kind == "open":
                    path, done = val
                    done.set()
                    self._open_forwarded(path)
                else:
                    self._finished(val)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _open_forwarded(self, path: str):
        """Başka bir açılıştan iletilen klasör (kısayola bırakılan): bu pencerede aç, pencereyi öne getir."""
        if path:
            p = Path(path)
            self.set_kit(p.parent if p.is_file() else p)
            if self.proc is not None:
                self.v_status.set(f"{self.kit.name} açıldı — bir iş sürüyor; bitince hazırlayabilir ya da kuyruğa "
                                  "ekleyebilirsiniz.")
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(300, lambda: self.root.attributes("-topmost", False))
            self.root.focus_force()
        except tk.TclError:
            pass

    def _finished(self, rc):
        cmd = self.running_cmd
        self.proc = None
        self._busy(False)
        if self.queue_current is not None:
            return self._queue_finished(rc)
        if rc == 0:
            done = {"all": "Hazırlık tamamlandı: plate'ler ve rapor hazır.",
                    "plates": "Plate dizimi tamamlandı (parçalar dolu): plate'ler ve rapor hazır.",
                    "hollow": "Hollow + delik tamamlandı: parçalar Klasörler → Parça dosyaları altında (plate yok)."}
            self.v_status.set("Analiz tamamlandı." if cmd == "analyze" else done.get(self.v_steps.get(), done["all"]))
            if cmd == "prep":
                self._absorb_finished_run()
            n_check = self._load_results()
            self.cb_kit.configure(values=_recent_kits())
            # montajda kontrol edilecek parça varsa önce onu göster
            self.right.select(3 if cmd == "analyze" and n_check else 2)
            if cmd == "analyze" and n_check:
                self.v_status.set(f"Analiz tamamlandı — montajda kontrol edilecek {n_check} parça var "
                                  "(Montaj kontrolü sekmesi).")
        else:
            self.v_status.set(f"Hata oluştu (kod {rc}). Ayrıntı günlükte.")
            self._toggle_log(True)
        if self.queue_running:                   # iş sürerken başlatılan kuyruk şimdi başlar
            self.root.after(500, self._queue_next)
            return
        waiting = sum(1 for i in self.queue_items if i.status in (nq.WAITING, nq.STOPPED))
        if waiting:
            self.v_status.set(self.v_status.get() + f"  ·  Kuyrukta {waiting} kit bekliyor (Kuyruk → Hepsini "
                                                    "hazırla).")

    def _busy(self, on: bool, msg: str = ""):
        self._update_buttons()
        self._queue_refresh()
        if on:
            self.pb.start(12)
            self.v_status.set(msg)
        else:
            self.pb.stop()
            self.pb.configure(mode="determinate", value=0)

    def stop(self):
        self.queue_running = False               # kuyruk da durur; çalışan kit "yarım kaldı" olur
        if self.proc is not None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)], capture_output=True,
                           creationflags=NO_WINDOW)
            self._log("\n[Durduruldu]\n")

    def _log(self, text):
        self.log.insert("end", text)
        self.log.see("end")

    # ------------------------------------------------------------------ açma
    def open_report(self):
        if self.kit:
            cands = [p for p in (out_dir(self.kit) / "report.html", out_dir(self.kit) / "analysis.html") if p.exists()]
            if cands:
                os.startfile(str(max(cands, key=lambda p: p.stat().st_mtime)))
            else:
                messagebox.showinfo(APP_TITLE, "Rapor henüz yok. Önce analiz edin veya hazırlayın.")
        else:
            messagebox.showinfo(APP_TITLE, "Önce bir kit klasörü seçin.")

    def open_dir(self, sub):
        if self.kit:
            p = out_dir(self.kit) / sub
            if p.exists():
                os.startfile(str(p))
            else:
                messagebox.showinfo(APP_TITLE, "Klasör henüz yok.")

    def _open_plate(self, _event=None):
        sel = self.ptree.selection()
        if sel and self.kit:
            p = out_dir(self.kit) / "plates" / f"{sel[0]}.stl"
            if p.exists():
                os.startfile(str(p))

    def _on_close(self):
        if self.proc is not None and not messagebox.askyesno(
                APP_TITLE, "İşlem sürüyor. Durdurup çıkılsın mı?" + (
                    "\n(Kuyruk saklanır; yarım kalan kit sonra yeniden başlatılabilir.)" if self.queue_running else "")):
            return
        self.stop()
        if self.queue_current is not None:
            self.queue_current.status, self.queue_current.result = nq.STOPPED, "uygulama kapatıldı"
            self._queue_save()
        self.root.destroy()


INSTANCE_PORT = 47613          # tek pencere: ikinci açılış klasörü çalışan pencereye iletir (sadece 127.0.0.1)
INSTANCE_MAGIC = "SLAPREP1"


def forward_to_running(path: str | None, port: int | None = None) -> bool:
    """Açık bir SLAPrep penceresi varsa klasörü ona ilet (o pencere kiti açar ve öne gelir). Dönüş: iletildi mi."""
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port or INSTANCE_PORT), timeout=1.0) as s:
            s.sendall(f"{INSTANCE_MAGIC}\n{path or ''}\n".encode("utf-8"))
            s.settimeout(4.0)
            return s.recv(8).startswith(b"OK")
    except OSError:
        return False


def serve_instance(app: "App", port: int | None = None) -> bool:
    """Bu pencereyi tek örnek yap: sonraki açılışlar (kısayola klasör bırakmak) yeni pencere açmaz, klasörü buraya
    iletir. Ayrı pencerelerin her biri kuyruğun kendi kopyasını tutup kayıtlı kuyruğu eziyordu. Port başka bir
    programdaysa sessizce vazgeçer (uygulama yine çalışır)."""
    import socket

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        srv.bind(("127.0.0.1", port or INSTANCE_PORT))
        srv.listen(4)
    except OSError:
        srv.close()
        return False

    def loop():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            with c:
                try:
                    c.settimeout(2.0)
                    data = b""
                    while data.count(b"\n") < 2:
                        chunk = c.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                    lines = data.decode("utf-8", "replace").split("\n")
                    if lines[0] == INSTANCE_MAGIC:
                        # "tamam" ancak pencerenin olay döngüsü isteği gerçekten işleyince: pencere donmuşsa yeni
                        # açılış kendi penceresini açar (yoksa donmuş pencereye iletip sessizce kapanıyordu)
                        done = threading.Event()
                        app.q.put(("open", (lines[1] if len(lines) > 1 else "", done)))
                        if done.wait(3.0):
                            c.sendall(b"OK")
                except OSError:
                    pass

    threading.Thread(target=loop, daemon=True).start()
    app._instance_server = srv
    return True


def main():
    initial = sys.argv[1] if len(sys.argv) > 1 else None
    if forward_to_running(initial):
        return                                  # açık pencere kiti açtı
    from .ui_theme import enable_dpi_awareness

    enable_dpi_awareness()
    dnd = False
    try:
        from tkinterdnd2 import TkinterDnD

        root = TkinterDnD.Tk()
        dnd = True
    except Exception:
        root = tk.Tk()
    app = App(root, initial, dnd)
    serve_instance(app)
    root.mainloop()


if __name__ == "__main__":
    main()
