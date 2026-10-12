"""Arayüz duman testi: pencereyi açar, kit seçer, komut argümanlarını ve sonuç tablosunu kontrol eder,
ekran görüntüsü alıp kapanır. Kullanım: python tests/gui_smoke.py <kit> <çıktı.png> [sekme]"""
import sys
import tkinter as tk
from pathlib import Path

from PIL import ImageGrab

from slaprep.gui import App

kit, png = Path(sys.argv[1]), sys.argv[2]
tab = int(sys.argv[3]) if len(sys.argv) > 3 else 0
root = tk.Tk()
app = App(root, str(kit))
app.v_scale_mode.set("height")
app.v_height.set("150")
print("analyze:", app.build_args("analyze"))
print("prep:", app.build_args("prep"))
print("parçalar:", len(app.parts_info), "gruplar:", app.groups, "tabloda:", len(app.tree.get_children()))
print("plate satırı:", len(app.ptree.get_children()))
app.right.select(tab)


def shot():
    root.update()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(png)
    print("ekran görüntüsü:", png)
    root.destroy()


root.after(1500, shot)
root.mainloop()
