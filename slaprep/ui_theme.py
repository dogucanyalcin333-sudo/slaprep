"""SLAPrep arayüz teması: düz, yuvarlatılmış, ferah (Windows 11 / Fluent esinli) bir ttk teması ve bileşenleri.

Ek kütüphane gerektirmez: düğme, alan, onay kutusu, anahtar, sekme, kart ve kaydırma çubuğu arka planları PIL ile
(kenar yumuşatmalı) çizilip ttk "image" öğesi olarak kaydedilir; ikonlar Windows'un Segoe MDL2 Assets fontundan
üretilir. Renkler tek yerde (C), piksel ölçüleri ekran DPI'sına göre ölçeklenir (px).
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageFont, ImageTk

C = dict(
    page="#F3F4F7", card="#FFFFFF", border="#E3E7ED", border_strong="#C9D0DA", divider="#EDF0F4",
    text="#1A1F27", muted="#5E6878", faint="#98A1AF",
    accent="#2563EB", accent_hover="#1E55D6", accent_press="#1A47B8", accent_soft="#EAF1FE", accent_text="#1D4ED8",
    accent_dis="#A9C2F7",
    hover="#F1F3F6", press="#E6E9EE", field_dis="#F4F5F8", seg="#ECEFF3", seg_hover="#E2E6EC",
    ok="#15803D", warn="#B45309", bad="#C62828", ok_soft="#E7F5EC", warn_soft="#FEF3E2", bad_soft="#FDECEC",
    console_bg="#151A21", console_fg="#D5DBE3", tip_bg="#1F2530", tip_fg="#F5F7FA",
)
FAMILY = "Segoe UI"
SEMI = "Segoe UI Semibold"
MONO = "Consolas"
_MDL2 = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "segmdl2.ttf")

# Segoe MDL2 Assets ikon kodları
ICONS = dict(
    folder=0xF12B, folder_open=0xED25, report=0xE9F9, open=0xE8A7, play=0xE768, stop=0xE71A, search=0xE721, refresh=0xE72C, add=0xE710,
    delete=0xE74D, remove=0xE738, settings=0xE713, info=0xE946, warning=0xE7BA, error=0xE783, done=0xE930,
    check=0xE73E, doc=0xE8A5, chevron_down=0xE70D, chevron_up=0xE70E, chevron_right=0xE76C, clock=0xE823,
    list=0xE8FD, link=0xE71B, cut=0xE8C6, camera=0xE722, photo=0xE8B9, console=0xE756, ruler=0xED5E,
    view=0xE890, printer=0xE749, package=0xE7B8, rotate=0xE7AD, back=0xE76B, forward=0xE76C, filter=0xE71C,
    queue=0xE81C, layers=0xE81E, cube=0xF158, drop=0xEB42, more=0xE712,
)


def enable_dpi_awareness():
    """Yüksek DPI ekranlarda Windows pencereyi bulanık büyütmesin (Tk oluşturulmadan önce çağrılır)."""
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


class Theme:
    """Bir Tk kökü için tema: stilleri kurar, resimleri (çöp toplanmasınlar diye) saklar, ikon üretir."""

    def __init__(self, root: tk.Misc):
        self.root = root
        try:
            self.scale = max(1.0, float(root.winfo_fpixels("1i")) / 96.0)
        except tk.TclError:
            self.scale = 1.0
        self._keep: list = []
        self._icons: dict = {}
        self.style = ttk.Style(root)
        self.style.theme_use("clam")
        self._fonts()
        self._elements()
        self._styles()

    # ------------------------------------------------------------------ yardımcılar
    def px(self, v: float) -> int:
        return int(round(v * self.scale))

    def font(self, size: float = 10, weight: str = "normal", family: str | None = None):
        fam = family or (SEMI if weight == "semibold" else FAMILY)
        return (fam, int(round(size)), "bold" if weight == "bold" else "normal")

    def _photo(self, im: Image.Image) -> ImageTk.PhotoImage:
        p = ImageTk.PhotoImage(im, master=self.root)
        self._keep.append(p)
        return p

    def _rr(self, w, h, r, fill, border=None, bw=1.0, ss=4) -> Image.Image:
        """Yuvarlatılmış dikdörtgen (kenar yumuşatmalı, saydam köşeler). fill None → içi saydam."""
        W, H = int(w * ss), int(h * ss)
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        if border:
            d.rounded_rectangle([0, 0, W - 1, H - 1], radius=r * ss, fill=border)
            b = int(round(bw * ss))
            d.rounded_rectangle([b, b, W - 1 - b, H - 1 - b], radius=max(r * ss - b, 0),
                                fill=fill if fill else (0, 0, 0, 0))
        elif fill:
            d.rounded_rectangle([0, 0, W - 1, H - 1], radius=r * ss, fill=fill)
        return im.resize((w, h), Image.LANCZOS)

    @staticmethod
    def _pad(im: Image.Image, right: int) -> Image.Image:
        """Resmin sağına saydam boşluk (gösterge ile yazı arası)."""
        out = Image.new("RGBA", (im.width + right, im.height), (0, 0, 0, 0))
        out.paste(im, (0, 0))
        return out

    @staticmethod
    def _sides(border) -> tuple[int, int, int, int]:
        """ttk kenar belirtimi → (sol, üst, sağ, alt)."""
        if isinstance(border, int):
            return border, border, border, border
        if len(border) == 2:
            return border[0], border[1], border[0], border[1]
        return tuple(border)

    @staticmethod
    def _nine(im: Image.Image, sides, center: int) -> Image.Image:
        """Küçük kalıbı 9 parçalı büyük resme genişlet: köşeler aynen, kenarlar ve orta `center` piksele esnetilir.
        ttk resmin ortasını ESNETMEZ, DÖŞER: ortası 2-4 piksel olan resimle büyük bir kart her çizimde on binlerce
        saydamlı parçayla boyanıyor, pencere ilk açılışta 20+ sn donuyordu."""
        l, t, r, b = sides
        w, h = im.size
        cw, ch = max(w - l - r, 1), max(h - t - b, 1)
        W, H = l + center + r, t + center + b
        out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        xs = [(0, l, 0, l), (l, l + cw, l, l + center), (w - r, w, W - r, W)]
        ys = [(0, t, 0, t), (t, t + ch, t, t + center), (h - b, h, H - b, H)]
        for sx0, sx1, dx0, dx1 in xs:
            for sy0, sy1, dy0, dy1 in ys:
                if sx1 <= sx0 or sy1 <= sy0 or dx1 <= dx0 or dy1 <= dy0:
                    continue
                piece = im.crop((sx0, sy0, sx1, sy1)).resize((dx1 - dx0, dy1 - dy0), Image.NEAREST)
                out.paste(piece, (dx0, dy0))
        return out

    def _element(self, name, images: dict, border, sticky="nsew", padding=0, center: int | None = 160):
        """images: {"": normal, "hover": .., "pressed": .., "disabled": .., "focus": .., "selected": ..} PIL resimleri
        → durum eşlemeli ttk image öğesi. padding varsayılan 0: yoksa ttk 9'lu dilim kenarını iç boşluk sayıp
        düğme/alanları gereksiz yüksek yapıyor; iç boşluk stilin padding'inden gelir. center: esnetilen öğelerde
        resmin ortası bu kadar büyütülür (döşeme sayısı az kalsın), öğenin istenen boyutu yine kenar payları kadardır;
        None: tek parça çizilen gösterge (onay kutusu vb.)."""
        kw = dict(border=border, sticky=sticky, padding=padding)
        if center:
            sd = self._sides(border)
            images = {k: self._nine(v, sd, center) for k, v in images.items()}
            kw.update(width=sd[0] + sd[2] + 1, height=sd[1] + sd[3] + 1)
        p = {k: self._photo(v) for k, v in images.items()}
        order = [("disabled selected", "disabled_selected"), ("disabled", "disabled"),
                 ("pressed", "pressed"), ("selected active", "selected_hover"), ("selected", "selected"),
                 ("alternate", "alternate"), ("focus", "focus"), ("active", "hover")]
        specs = [(st, p[k]) for st, k in order if k in p]
        try:
            self.style.element_create(name, "image", p[""], *specs, **kw)
        except tk.TclError:
            pass                                    # aynı yorumlayıcıda zaten var

    # ------------------------------------------------------------------ yazılar
    def _fonts(self):
        from tkinter import font as tkfont

        for name, size in (("TkDefaultFont", 10), ("TkTextFont", 10), ("TkMenuFont", 10), ("TkHeadingFont", 9),
                           ("TkTooltipFont", 9)):
            try:
                tkfont.nametofont(name, root=self.root).configure(family=FAMILY, size=size)
            except tk.TclError:
                pass
        self.root.option_add("*TCombobox*Listbox.font", "TkDefaultFont")
        self.root.option_add("*TCombobox*Listbox.background", C["card"])
        self.root.option_add("*TCombobox*Listbox.foreground", C["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", C["accent_soft"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", C["text"])
        self.root.option_add("*TCombobox*Listbox.borderWidth", 0)
        self.root.option_add("*Menu.background", C["card"])
        self.root.option_add("*Menu.foreground", C["text"])
        self.root.option_add("*Menu.activeBackground", C["accent_soft"])
        self.root.option_add("*Menu.activeForeground", C["text"])
        self.root.option_add("*Menu.relief", "flat")

    # ------------------------------------------------------------------ resim öğeleri
    def _elements(self):
        px = self.px
        r = px(6)
        s = 2 * r + px(8)
        bb = r + px(2)

        def btn(fill, border=None):
            return self._rr(s, s, r, fill, border)

        # düğmeler
        self._element("Primary.Button.bg", {"": btn(C["accent"]), "hover": btn(C["accent_hover"]),
                                             "pressed": btn(C["accent_press"]), "disabled": btn(C["accent_dis"])}, bb)
        self._element("Secondary.Button.bg", {"": btn(C["card"], C["border_strong"]),
                                               "hover": btn(C["hover"], C["border_strong"]),
                                               "pressed": btn(C["press"], C["border_strong"]),
                                               "disabled": btn(C["field_dis"], C["border"])}, bb)
        self._element("Ghost.Button.bg", {"": btn(None), "hover": btn(C["hover"]), "pressed": btn(C["press"]),
                                           "disabled": btn(None)}, bb)
        # yazı alanları
        fr = px(6)
        fs = 2 * fr + px(8)
        self._element("Field.bg", {"": self._rr(fs, fs, fr, C["card"], C["border_strong"]),
                                    "hover": self._rr(fs, fs, fr, C["card"], "#AEB7C4"),
                                    "focus": self._rr(fs, fs, fr, C["card"], C["accent"], bw=1.6),
                                    "disabled": self._rr(fs, fs, fr, C["field_dis"], C["border"])}, fr + px(3))
        # kartlar ve yüzeyler
        cr = px(10)
        cs = 2 * cr + px(8)
        self._element("Card.bg", {"": self._rr(cs, cs, cr, C["card"], C["border"])}, cr + px(2))
        self._element("Soft.bg", {"": self._rr(cs, cs, cr, C["accent_soft"], "#D5E2FB")}, cr + px(2))
        self._element("Seg.bg", {"": self._rr(cs, cs, px(8), C["seg"])}, px(10))
        sr = px(6)
        ss_ = 2 * sr + px(8)
        self._element("SegItem.bg", {"": self._rr(ss_, ss_, sr, None), "hover": self._rr(ss_, ss_, sr, C["seg_hover"]),
                                      "selected": self._rr(ss_, ss_, sr, C["card"], "#D8DDE5"),
                                      "selected_hover": self._rr(ss_, ss_, sr, C["card"], "#C9D0DA"),
                                      "disabled": self._rr(ss_, ss_, sr, None),
                                      "disabled_selected": self._rr(ss_, ss_, sr, "#F7F8FA", C["border"])}, sr + px(2))

        # sekme: alt çizgi
        tw, th = px(16), px(16)

        def tab(bar, h):
            im = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
            if bar:
                b = self._rr(tw, h * 2, h, bar)
                im.alpha_composite(b.crop((0, 0, tw, h)), (0, th - h))
            return im
        self._element("Tab.bg", {"": tab(None, 1), "hover": tab(C["border_strong"], px(2)),
                                  "selected": tab(C["accent"], px(3)), "selected_hover": tab(C["accent"], px(3))},
                      (px(4), px(4), px(4), px(4)))
        # tablo başlığı: beyaz + alt çizgi
        hw = px(8)
        head = Image.new("RGBA", (hw, hw), C["card"])
        ImageDraw.Draw(head).line([(0, hw - 1), (hw, hw - 1)], fill=C["border"], width=1)
        head_h = Image.new("RGBA", (hw, hw), C["hover"])
        ImageDraw.Draw(head_h).line([(0, hw - 1), (hw, hw - 1)], fill=C["border"], width=1)
        self._element("Head.bg", {"": head, "hover": head_h}, (px(2), px(2), px(2), px(2)))
        # kaydırma çubuğu tutamağı
        tr = px(4)
        self._element("VThumb.bg", {"": self._rr(px(8), px(30), tr, "#C8CED7"),
                                     "hover": self._rr(px(8), px(30), tr, "#A9B2BF"),
                                     "pressed": self._rr(px(8), px(30), tr, "#939DAB")}, (tr, tr + px(2)), center=None)
        self._element("HThumb.bg", {"": self._rr(px(30), px(8), tr, "#C8CED7"),
                                     "hover": self._rr(px(30), px(8), tr, "#A9B2BF"),
                                     "pressed": self._rr(px(30), px(8), tr, "#939DAB")}, (tr + px(2), tr), center=None)

        # onay kutusu, radyo, anahtar
        k = px(18)

        def box(fill, border, mark=None, bw=1.4):
            im = self._rr(k, k, px(4), fill, border, bw=bw)
            if mark:
                big = Image.new("RGBA", (k * 4, k * 4), (0, 0, 0, 0))
                d = ImageDraw.Draw(big)
                w4 = max(int(k * 4 * 0.11), 4)
                if mark == "check":
                    d.line([(k * 4 * 0.25, k * 4 * 0.52), (k * 4 * 0.43, k * 4 * 0.70), (k * 4 * 0.76, k * 4 * 0.32)],
                           fill="white", width=w4, joint="curve")
                else:
                    d.line([(k * 4 * 0.28, k * 4 * 0.5), (k * 4 * 0.72, k * 4 * 0.5)], fill="white", width=w4)
                im.alpha_composite(big.resize((k, k), Image.LANCZOS))
            return im
        gap = px(8)
        self._element("Check.ind", {k_: self._pad(v, gap) for k_, v in {
                                    "": box(C["card"], C["border_strong"]), "hover": box(C["card"], C["accent"]),
                                     "selected": box(C["accent"], C["accent"], "check"),
                                     "selected_hover": box(C["accent_hover"], C["accent_hover"], "check"),
                                     "alternate": box(C["accent"], C["accent"], "dash"),
                                     "disabled": box(C["field_dis"], C["border"]),
                                     "disabled_selected": box(C["accent_dis"], C["accent_dis"], "check")}.items()},
                      0, sticky="", center=None)

        def radio(ring, dot=None, fill=C["card"]):
            ss = 4
            big = Image.new("RGBA", (k * ss, k * ss), (0, 0, 0, 0))
            d = ImageDraw.Draw(big)
            d.ellipse([0, 0, k * ss - 1, k * ss - 1], fill=ring)
            b = int(1.4 * ss)
            d.ellipse([b, b, k * ss - 1 - b, k * ss - 1 - b], fill=fill)
            if dot:
                c = k * ss * 0.27
                d.ellipse([c, c, k * ss - 1 - c, k * ss - 1 - c], fill=dot)
            return big.resize((k, k), Image.LANCZOS)
        self._element("Radio.ind", {k_: self._pad(v, gap) for k_, v in {
                                    "": radio(C["border_strong"]), "hover": radio(C["accent"]),
                                     "selected": radio(C["accent"], "white", C["accent"]),
                                     "selected_hover": radio(C["accent_hover"], "white", C["accent_hover"]),
                                     "disabled": radio(C["border"], None, C["field_dis"]),
                                     "disabled_selected": radio(C["accent_dis"], "white", C["accent_dis"])}.items()},
                      0, sticky="", center=None)

        sw, sh = px(38), px(20)

        def switch(on, track, border, knob, ss=4):
            big = Image.new("RGBA", (sw * ss, sh * ss), (0, 0, 0, 0))
            d = ImageDraw.Draw(big)
            d.rounded_rectangle([0, 0, sw * ss - 1, sh * ss - 1], radius=sh * ss / 2, fill=border)
            b = int(1.3 * ss)
            d.rounded_rectangle([b, b, sw * ss - 1 - b, sh * ss - 1 - b], radius=sh * ss / 2 - b,
                                fill=track if track else (0, 0, 0, 0))
            kr = sh * ss * 0.29
            cx = sw * ss - sh * ss / 2 if on else sh * ss / 2
            cy = sh * ss / 2
            d.ellipse([cx - kr, cy - kr, cx + kr, cy + kr], fill=knob)
            return big.resize((sw, sh), Image.LANCZOS)
        self._element("Switch.ind", {k_: self._pad(v, px(10)) for k_, v in {
                                     "": switch(False, C["card"], "#7A8494", "#5E6878"),
                                      "hover": switch(False, C["hover"], "#5E6878", "#3F4754"),
                                      "selected": switch(True, C["accent"], C["accent"], "white"),
                                      "selected_hover": switch(True, C["accent_hover"], C["accent_hover"], "white"),
                                      "disabled": switch(False, C["field_dis"], C["border"], C["border_strong"]),
                                      "disabled_selected": switch(True, C["accent_dis"], C["accent_dis"], "white")}.items()},
                      0, sticky="", center=None)

        # tablo satırındaki onay kutusu (Treeview'a resim olarak)
        self.tree_check = {True: self._photo(self._pad(box(C["accent"], C["accent"], "check"), px(10))),
                           False: self._photo(self._pad(box(C["card"], C["border_strong"]), px(10)))}

    # ------------------------------------------------------------------ stiller
    def _styles(self):
        st, px = self.style, self.px
        base = self.font(10)
        st.configure(".", background=C["page"], foreground=C["text"], font=base, borderwidth=0,
                     focuscolor=C["accent"], troughcolor=C["page"], selectbackground=C["accent_soft"],
                     selectforeground=C["text"], insertcolor=C["text"])
        st.map(".", foreground=[("disabled", C["faint"])])
        for surf, bg in (("", C["page"]), ("Card.", C["card"]), ("Soft.", C["accent_soft"])):
            st.configure(f"{surf}TFrame", background=bg)
            st.configure(f"{surf}TLabel", background=bg, foreground=C["text"])
            st.configure(f"Muted.{surf}TLabel", foreground=C["muted"])
            st.configure(f"Small.{surf}TLabel", foreground=C["muted"], font=self.font(9))
            st.configure(f"Faint.{surf}TLabel", foreground=C["faint"], font=self.font(9))
            st.configure(f"Strong.{surf}TLabel", font=self.font(10, "semibold"))
            st.configure(f"Accent.{surf}TLabel", foreground=C["accent_text"], font=self.font(10, "semibold"))
            st.configure(f"Warn.{surf}TLabel", foreground=C["warn"], font=self.font(9))
            st.configure(f"Bad.{surf}TLabel", foreground=C["bad"], font=self.font(9))
            st.configure(f"{surf}TCheckbutton", background=bg, foreground=C["text"], padding=(0, px(3)))
            st.configure(f"{surf}TRadiobutton", background=bg, foreground=C["text"], padding=(0, px(3)))
            st.map(f"{surf}TCheckbutton", background=[("active", bg)], foreground=[("disabled", C["faint"])])
            st.map(f"{surf}TRadiobutton", background=[("active", bg)], foreground=[("disabled", C["faint"])])
        st.configure("H1.Card.TLabel", font=self.font(15, "semibold"))
        st.configure("H2.Card.TLabel", font=self.font(11.5, "semibold"))
        st.configure("H3.Card.TLabel", font=self.font(10.5, "semibold"))
        st.configure("Stat.Card.TLabel", font=self.font(17, "semibold"))
        st.configure("Section.TLabel", foreground=C["muted"], font=self.font(8.5, "semibold"))
        st.configure("Icon.Card.TLabel", foreground=C["muted"], font=("Segoe MDL2 Assets", 11))

        # çerçeveler
        st.layout("Card.TFrame", [("Card.bg", {"sticky": "nsew"})])
        st.configure("Card.TFrame", background=C["page"])           # köşelerin ardındaki zemin
        st.layout("SoftCard.TFrame", [("Soft.bg", {"sticky": "nsew"})])
        st.configure("SoftCard.TFrame", background=C["card"])
        st.layout("Seg.TFrame", [("Seg.bg", {"sticky": "nsew"})])
        st.configure("Seg.TFrame", background=C["card"])
        st.configure("Bar.TFrame", background=C["card"])
        st.configure("Line.TFrame", background=C["border"])
        st.configure("Divider.TFrame", background=C["divider"])

        # düğmeler
        for name, bg_el, fg, fg_dis, font in (
                ("Primary.TButton", "Primary.Button.bg", "white", "#F2F6FF", self.font(10, "semibold")),
                ("TButton", "Secondary.Button.bg", C["text"], C["faint"], base),
                ("Ghost.TButton", "Ghost.Button.bg", C["text"], C["faint"], base)):
            st.layout(name, [(bg_el, {"sticky": "nsew", "children": [
                ("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]})]})])
            st.configure(name, background=C["card"], foreground=fg, font=font, padding=(px(14), px(6)),
                         anchor="center", relief="flat")
            st.map(name, foreground=[("disabled", fg_dis)], background=[("active", C["card"])])
        st.configure("Small.TButton", padding=(px(10), px(4)), font=self.font(9))
        st.configure("Small.Ghost.TButton", padding=(px(8), px(4)), font=self.font(9))
        st.configure("Icon.Ghost.TButton", padding=(px(6), px(5)))
        st.configure("Danger.TButton", foreground=C["bad"])
        st.configure("Big.Primary.TButton", padding=(px(20), px(8)), font=self.font(10.5, "semibold"))
        st.configure("Big.TButton", padding=(px(16), px(8)))

        # onay kutusu / radyo / anahtar
        for prefix in ("", "Card.", "Soft."):
            st.layout(f"{prefix}TCheckbutton", [("Checkbutton.padding", {"sticky": "nsew", "children": [
                ("Check.ind", {"side": "left", "sticky": ""}),
                ("Checkbutton.label", {"side": "left", "sticky": "w"})]})])
            st.layout(f"{prefix}TRadiobutton", [("Radiobutton.padding", {"sticky": "nsew", "children": [
                ("Radio.ind", {"side": "left", "sticky": ""}),
                ("Radiobutton.label", {"side": "left", "sticky": "w"})]})])
        st.layout("Switch.TCheckbutton", [("Checkbutton.padding", {"sticky": "nsew", "children": [
            ("Switch.ind", {"side": "left", "sticky": ""}), ("Checkbutton.label", {"side": "left", "sticky": "w"})]})])
        st.configure("Switch.TCheckbutton", background=C["card"], padding=(0, px(2)))
        st.map("Switch.TCheckbutton", background=[("active", C["card"])], foreground=[("disabled", C["faint"])])

        # segmentli seçici ve sekmeler
        st.layout("Seg.TRadiobutton", [("SegItem.bg", {"sticky": "nsew", "children": [
            ("Radiobutton.padding", {"sticky": "nsew", "children": [("Radiobutton.label", {"sticky": "nsew"})]})]})])
        st.configure("Seg.TRadiobutton", background=C["seg"], foreground=C["muted"], padding=(px(10), px(4)),
                     anchor="center", font=self.font(9.5))
        st.map("Seg.TRadiobutton", foreground=[("disabled", C["faint"]), ("selected", C["text"])],
               background=[("active", C["seg"])], font=[("selected", self.font(9.5, "semibold"))])
        st.layout("Tab.TRadiobutton", [("Tab.bg", {"sticky": "nsew", "children": [
            ("Radiobutton.padding", {"sticky": "nsew", "children": [("Radiobutton.label", {"sticky": "nsew"})]})]})])
        st.configure("Tab.TRadiobutton", background=C["card"], foreground=C["muted"], padding=(px(12), px(9)),
                     anchor="center", font=self.font(10))
        st.map("Tab.TRadiobutton", foreground=[("selected", C["text"]), ("active", C["text"])],
               background=[("active", C["card"])], font=[("selected", self.font(10, "semibold"))])

        # yazı alanları
        fl = [("Field.bg", {"sticky": "nsew", "children": [
            ("Entry.padding", {"sticky": "nsew", "children": [("Entry.textarea", {"sticky": "nsew"})]})]})]
        st.layout("TEntry", fl)
        st.configure("TEntry", background=C["card"], foreground=C["text"], padding=(px(8), px(5)),
                     insertcolor=C["text"], fieldbackground=C["card"])
        st.map("TEntry", foreground=[("disabled", C["faint"])])
        st.layout("TCombobox", [("Field.bg", {"sticky": "nsew", "children": [
            ("Combobox.downarrow", {"side": "right", "sticky": "ns"}),
            ("Combobox.padding", {"sticky": "nsew", "children": [("Combobox.textarea", {"sticky": "nsew"})]})]})])
        st.configure("TCombobox", background=C["card"], foreground=C["text"], padding=(px(8), px(5), px(4), px(5)),
                     arrowcolor=C["muted"], arrowsize=px(12), bordercolor=C["card"], lightcolor=C["card"],
                     darkcolor=C["card"], fieldbackground=C["card"], selectbackground=C["card"],
                     selectforeground=C["text"])
        st.map("TCombobox", fieldbackground=[("readonly", C["card"]), ("disabled", C["field_dis"])],
               background=[("active", C["card"]), ("pressed", C["card"]), ("disabled", C["field_dis"])],
               foreground=[("disabled", C["faint"])], arrowcolor=[("disabled", C["faint"])],
               selectbackground=[("readonly", C["card"])], selectforeground=[("readonly", C["text"])])

        # tablo
        st.layout("Treeview", [("Treeview.treearea", {"sticky": "nsew"})])
        st.configure("Treeview", background=C["card"], fieldbackground=C["card"], foreground=C["text"],
                     rowheight=px(32), font=base, borderwidth=0, indent=0)
        st.map("Treeview", background=[("selected", C["accent_soft"])], foreground=[("selected", C["text"])])
        st.layout("Treeview.Heading", [("Head.bg", {"sticky": "nsew", "children": [
            ("Treeheading.padding", {"sticky": "nsew", "children": [
                ("Treeheading.image", {"side": "right", "sticky": ""}),
                ("Treeheading.text", {"sticky": "we"})]})]})])
        st.configure("Treeview.Heading", background=C["card"], foreground=C["muted"], font=self.font(9, "semibold"),
                     padding=(px(8), px(7)))
        st.map("Treeview.Heading", foreground=[("active", C["text"])])
        st.layout("Item", [("Treeitem.padding", {"sticky": "nsew", "children": [
            ("Treeitem.image", {"side": "left", "sticky": ""}),
            ("Treeitem.text", {"side": "left", "sticky": "w"})]})])
        st.configure("Item", padding=(px(6), 0))

        # kaydırma çubukları
        st.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("VThumb.bg", {"expand": "1", "sticky": "nsew"})]})])
        st.layout("Horizontal.TScrollbar", [("Horizontal.Scrollbar.trough", {"sticky": "we", "children": [
            ("HThumb.bg", {"expand": "1", "sticky": "nsew"})]})])
        for o in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
            st.configure(o, troughcolor=C["card"], background=C["card"], bordercolor=C["card"], arrowsize=px(10),
                         lightcolor=C["card"], darkcolor=C["card"], gripcount=0)
        st.configure("Page.Vertical.TScrollbar", troughcolor=C["page"], background=C["page"])
        st.layout("Page.Vertical.TScrollbar", st.layout("Vertical.TScrollbar"))

        # ilerleme, ayırıcı, bölme
        st.configure("Horizontal.TProgressbar", troughcolor=C["card"], background=C["accent"],
                     bordercolor=C["card"], lightcolor=C["accent"], darkcolor=C["accent"], thickness=px(3))
        st.configure("TSeparator", background=C["border"])
        st.configure("TPanedwindow", background=C["page"])
        st.configure("Sash", sashthickness=px(8), gripcount=0, background=C["page"], lightcolor=C["page"],
                     bordercolor=C["page"])

    # ------------------------------------------------------------------ ikonlar
    def icon(self, name: str, color: str | None = None, size: float = 14):
        """Segoe MDL2 Assets ikonu (PhotoImage); font yoksa ''."""
        color = color or C["text"]
        key = (name, color, size)
        if key not in self._icons:
            try:
                f = ImageFont.truetype(_MDL2, self.px(size))
            except OSError:
                self._icons[key] = ""
                return ""
            n = self.px(size) + self.px(4)
            im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
            ImageDraw.Draw(im).text((n / 2, n / 2), chr(ICONS[name]), font=f, fill=color, anchor="mm")
            self._icons[key] = self._photo(im)
        return self._icons[key]

    def icon_states(self, name: str, color: str | None = None, disabled: str | None = None, size: float = 14):
        """Düğme için durum eşlemeli ikon: (normal, 'disabled', soluk)."""
        n = self.icon(name, color, size)
        d = self.icon(name, disabled or C["faint"], size)
        return (n, "disabled", d) if n else ""


# ---------------------------------------------------------------------- bileşenler
class Tip:
    """Üzerine gelince görünen koyu açıklama kutusu."""

    def __init__(self, widget, text: str, delay: int = 350):
        self.w, self.text, self.tw, self.delay, self._job = widget, text, None, delay, None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def _schedule(self, _e=None):
        self._job = self.w.after(self.delay, self.show)

    def show(self, _e=None):
        if self.tw is not None or not self.text:
            return
        x = self.w.winfo_rootx() + 8
        y = self.w.winfo_rooty() + self.w.winfo_height() + 6
        self.tw = tw = tk.Toplevel(self.w)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.configure(background=C["tip_bg"])
        tk.Label(tw, text=self.text, justify="left", background=C["tip_bg"], foreground=C["tip_fg"],
                 wraplength=380, font=(FAMILY, 9), padx=10, pady=7, borderwidth=0).pack()

    def hide(self, _e=None):
        if self._job:
            self.w.after_cancel(self._job)
            self._job = None
        if self.tw is not None:
            self.tw.destroy()
            self.tw = None


class Segmented(ttk.Frame):
    """Birbirini dışlayan seçenekler için yan yana düğme grubu (iOS/Windows 11 tarzı)."""

    def __init__(self, parent, variable: tk.Variable, options: list[tuple[str, str]], command=None, **kw):
        super().__init__(parent, style="Seg.TFrame", padding=3, **kw)
        self.buttons = []
        for i, (value, text) in enumerate(options):
            b = ttk.Radiobutton(self, text=text, value=value, variable=variable, style="Seg.TRadiobutton",
                                command=command, takefocus=False)
            b.grid(row=0, column=i, sticky="nsew")
            self.columnconfigure(i, weight=1, uniform="seg")
            self.buttons.append(b)

    def set_state(self, enabled: bool):
        for b in self.buttons:
            b.state(["!disabled"] if enabled else ["disabled"])


class Card(ttk.Frame):
    """Beyaz, yuvarlak köşeli, ince kenarlı yüzey."""

    def __init__(self, parent, padding=14, **kw):
        super().__init__(parent, style="Card.TFrame", padding=padding, **kw)


class Section(ttk.Frame):
    """Kenar çubuğundaki katlanabilir ayar kartı: başlık (+ ikon, özet) ve gövde. set_enabled(False) gövdeyi gizler
    ve nedenini yazar (ör. ilgili işlem seçili değil)."""

    def __init__(self, parent, theme: Theme, title: str, icon: str | None = None, open_: bool = True,
                 on_toggle=None):
        super().__init__(parent, style="Card.TFrame", padding=(theme.px(14), theme.px(10)))
        self.theme, self.title, self.on_toggle = theme, title, on_toggle
        self.is_open, self.enabled = open_, True
        head = ttk.Frame(self, style="Bar.TFrame")
        head.pack(fill="x")
        self.head = head
        if icon:
            self.ic = ttk.Label(head, image=theme.icon(icon, C["accent"], 13), style="Card.TLabel")
            self.ic.pack(side="left", padx=(0, theme.px(8)))
        self.lbl = ttk.Label(head, text=title, style="H3.Card.TLabel")
        self.lbl.pack(side="left")
        self.note = ttk.Label(head, text="", style="Faint.Card.TLabel")
        self.note.pack(side="left", padx=(theme.px(8), 0))
        self.chev = ttk.Label(head, style="Card.TLabel")
        self.chev.pack(side="right")
        self.body = ttk.Frame(self, style="Bar.TFrame")
        self.body.columnconfigure(1, weight=1)
        for w in (head, self.lbl, self.chev, self.note) + ((self.ic,) if icon else ()):
            w.bind("<Button-1>", lambda e: self.toggle())
            w.configure(cursor="hand2")
        self._apply()

    def toggle(self):
        if not self.enabled:
            return
        self.is_open = not self.is_open
        self._apply()
        if self.on_toggle:
            self.on_toggle(self.title, self.is_open)

    def set_enabled(self, on: bool, why: str = ""):
        self.enabled = on
        self.note.configure(text="" if on else why)
        self.lbl.configure(style="H3.Card.TLabel" if on else "Faint.Card.TLabel")
        self._apply()

    def _apply(self):
        show = self.is_open and self.enabled
        if show:
            self.body.pack(fill="x", pady=(self.theme.px(10), 0))
        else:
            self.body.pack_forget()
        name = "chevron_up" if show else "chevron_down"
        self.chev.configure(image=self.theme.icon(name, C["faint"] if not self.enabled else C["muted"], 10))


class ScrollFrame(ttk.Frame):
    """Dikey kaydırılabilir alan (kenar çubuğu); fare tekerleği üstündeyken kaydırır."""

    def __init__(self, parent, bg: str = C["page"], **kw):
        super().__init__(parent, **kw)
        self.canvas = tk.Canvas(self, background=bg, highlightthickness=0, borderwidth=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview,
                                 style="Page.Vertical.TScrollbar")
        self.inner = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self._on_scroll)
        # kaydırma çubuğu gizlenince genişlik değişmesin: aynı genişlikte boşluk yer tutar
        self.spacer = tk.Frame(self, width=self.vsb.winfo_reqwidth(), background=bg, highlightthickness=0)
        self.spacer.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.bind_all_wheel()

    def _on_scroll(self, a, b):
        self.vsb.set(a, b)
        if float(a) <= 0.0 and float(b) >= 1.0:
            if self.vsb.winfo_manager():
                self.vsb.pack_forget()
                self.spacer.pack(side="right", fill="y", before=self.canvas)
        elif not self.vsb.winfo_manager():
            self.spacer.pack_forget()
            self.vsb.pack(side="right", fill="y", before=self.canvas)

    def bind_all_wheel(self):
        def wheel(e):
            if self.canvas.winfo_height() < self.inner.winfo_reqheight():
                self.canvas.yview_scroll(int(-e.delta / 120) * 3, "units")
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))


class TabView(ttk.Frame):
    """Alt çizgili sekmeler + içerik alanı (ttk.Notebook yerine; görünümü tam denetlenir).
    API: add(sayfa, metin) → indeks; select(indeks|sayfa); index('end'); current()."""

    def __init__(self, parent, theme: Theme, on_change=None):
        super().__init__(parent, style="Bar.TFrame")
        self.theme, self.on_change = theme, on_change
        self.bar = ttk.Frame(self, style="Bar.TFrame")
        self.bar.pack(fill="x", padx=theme.px(10))
        ttk.Frame(self, style="Line.TFrame", height=1).pack(fill="x")
        self.area = ttk.Frame(self, style="Bar.TFrame")
        self.area.pack(fill="both", expand=True)
        self.area.rowconfigure(0, weight=1)
        self.area.columnconfigure(0, weight=1)
        self.var = tk.IntVar(value=0)
        self.pages: list = []
        self.buttons: list = []
        self.texts: list[str] = []

    def page(self, text: str, padding=12):
        f = ttk.Frame(self.area, style="Bar.TFrame", padding=padding)
        self.add(f, text)
        return f

    def add(self, page, text: str) -> int:
        i = len(self.pages)
        b = ttk.Radiobutton(self.bar, text=text, value=i, variable=self.var, style="Tab.TRadiobutton",
                            command=lambda: self.select(i), takefocus=False)
        b.pack(side="left")
        page.grid(row=0, column=0, sticky="nsew", in_=self.area)
        self.pages.append(page)
        self.buttons.append(b)
        self.texts.append(text)
        if i == 0:
            page.tkraise()
        else:
            page.lower()
        return i

    def set_text(self, i: int, text: str):
        self.buttons[i].configure(text=text)

    def index(self, what="end") -> int:
        return len(self.pages) if what == "end" else self.pages.index(what)

    def current(self) -> int:
        return self.var.get()

    def select(self, what):
        i = what if isinstance(what, int) else self.pages.index(what)
        self.var.set(i)
        self.pages[i].tkraise()
        if self.on_change:
            self.on_change(i)


class ThinProgress(tk.Frame):
    """İnce ilerleme çizgisi (clam'da ttk.Progressbar kalınlığı ayarlanamıyor). ttk.Progressbar'ın kullanılan
    arayüzü: configure(mode=, maximum=, value=), pb["mode"], start(ms), stop()."""

    _KEYS = ("mode", "maximum", "value")

    def __init__(self, parent, height: int = 3):
        super().__init__(parent, height=height, background=C["card"], highlightthickness=0, borderwidth=0)
        self.bar = tk.Frame(self, background=C["accent"], borderwidth=0, highlightthickness=0)
        self._o = dict(mode="determinate", maximum=100.0, value=0.0)
        self._job = None
        self._pos = 0.0

    def configure(self, cnf=None, **kw):
        mine = {k: kw.pop(k) for k in list(kw) if k in self._KEYS}
        if cnf or kw:
            super().configure(cnf, **kw)
        if mine:
            self._o.update(mine)
            self._draw()

    config = configure

    def __getitem__(self, k):
        return self._o[k] if k in self._KEYS else super().__getitem__(k)

    def _draw(self):
        if self._job is not None:
            return
        frac = max(0.0, min(1.0, float(self._o["value"]) / max(float(self._o["maximum"]), 1e-9)))
        if frac <= 0:
            self.bar.place_forget()
        else:
            self.bar.place(x=0, y=0, relheight=1, relwidth=frac)

    def start(self, interval: int = 12):
        self.stop()
        self._o["mode"] = "indeterminate"
        self._tick(interval)

    def _tick(self, interval):
        self._pos = (self._pos + 0.01) % 1.35
        self.bar.place(relx=self._pos - 0.35, y=0, relheight=1, relwidth=0.35)
        self._job = self.after(interval, self._tick, interval)

    def stop(self):
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None
        self.bar.place_forget()


def stat_tile(parent, theme: Theme, caption: str, var: tk.StringVar, icon: str | None = None, sub=None):
    """Küçük sayı kartı: başlık + büyük değer (+ alt satır)."""
    f = ttk.Frame(parent, style="Card.TFrame", padding=(theme.px(14), theme.px(10)))
    top = ttk.Frame(f, style="Bar.TFrame")
    top.pack(fill="x")
    if icon:
        ttk.Label(top, image=theme.icon(icon, C["muted"], 11), style="Card.TLabel").pack(side="left",
                                                                                       padx=(0, theme.px(6)))
    ttk.Label(top, text=caption, style="Small.Card.TLabel").pack(side="left")
    ttk.Label(f, textvariable=var, style="Stat.Card.TLabel").pack(anchor="w", pady=(theme.px(2), 0))
    if sub is not None:
        ttk.Label(f, textvariable=sub, style="Faint.Card.TLabel").pack(anchor="w")
    return f
