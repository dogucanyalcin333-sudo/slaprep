"""Arayüzden gerçek bir 'Analiz et' çalıştırır, bitince durum ve günlüğü yazdırır. python tests/gui_run.py <kit>"""
import sys
import tkinter as tk

from slaprep.gui import App

root = tk.Tk()
app = App(root, sys.argv[1])
app.run("analyze")


def check():
    if app.proc is None and app.running_cmd:
        print("durum:", app.v_status.get())
        log = app.log.get("1.0", "end").strip().splitlines()
        print("günlük satırı:", len(log), "| son:", log[-1][:100] if log else "")
        print("özet:", app.summary.get("1.0", "end").strip().splitlines()[:4])
        root.destroy()
    else:
        root.after(500, check)


root.after(1000, check)
root.mainloop()
