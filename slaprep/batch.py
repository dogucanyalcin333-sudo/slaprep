"""Gece kuyruğu: birden çok kit sırayla hazırlanır, sabah her kitin sonucu tek listede görülür.

Kuyruk ~/.slaprep_prefs.json'da saklanır (uygulama kapansa da kaybolmaz). Her öğe, eklendiği andaki form ayarlarıyla
(komut argümanları) çalışır; sonradan formu değiştirmek kuyruktaki işi etkilemez.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import prefs
from .config import out_dir
from .i18n import t
from .resin import ResinPrefs, resin_summary

WAITING, RUNNING, DONE, FAILED, STOPPED = "bekliyor", "çalışıyor", "bitti", "hata", "yarım kaldı"


def status_text(status: str) -> str:
    """Durum anahtarının ekranda görünen karşılığı (anahtarın kendisi prefs.json'da saklanır, çevrilmez)."""
    return {WAITING: t("bekliyor"), RUNNING: t("çalışıyor"), DONE: t("bitti"),
            FAILED: t("hata"), STOPPED: t("yarım kaldı")}.get(status, status)


@dataclass
class QueueItem:
    kit: str
    args: list[str]                       # `python -m slaprep` argümanları (prep komutu dahil)
    steps: str = "all"
    status: str = WAITING
    added: float = field(default_factory=time.time)
    started: float = 0.0
    seconds: float = 0.0
    rc: int | None = None
    result: str = ""                      # kısa sonuç satırı (plate, süre, reçine, uyarı sayısı)

    @property
    def name(self) -> str:
        return Path(self.kit).name


def load_queue() -> list[QueueItem]:
    """Kayıtlı kuyruk. Çalışırken kapanmış öğe 'yarım kaldı' olur (yeniden başlatılabilir)."""
    known = {f.name for f in fields(QueueItem)}
    out = []
    for d in prefs.read("queue", []) or []:
        try:
            it = QueueItem(**{k: v for k, v in d.items() if k in known})
        except (TypeError, AttributeError):
            continue
        if it.status == RUNNING:
            it.status = STOPPED
        out.append(it)
    return out


def save_queue(items: list[QueueItem]):
    prefs.write("queue", [asdict(i) for i in items])


def next_item(items: list[QueueItem]) -> QueueItem | None:
    return next((i for i in items if i.status in (WAITING, STOPPED)), None)


def result_text(kit: Path, prefs: ResinPrefs | None = None) -> str:
    """Bir kitin son hazırlığından sabah okunacak tek satır: plate sayısı, süre, reçine, dikkat edilecekler."""
    from .fmt import fmt_duration

    p = out_dir(Path(kit)) / "prep.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return t("prep.json okunamadı")
    parts = d.get("parts", {})
    printed = {n: x for n, x in parts.items() if x.get("printed")}
    bits = [t("{n} parça").format(n=len(printed))]
    plates = d.get("plates") or {}
    if plates:
        bits.append(t("{n} plate").format(n=len(plates)))
    if d.get("print_time_min"):
        bits.append(f"~{fmt_duration(d['print_time_min'] * 60)}")
    from .resin import load_prefs, resin_cell

    rs = resin_summary(d, prefs or load_prefs())
    if rs["total"]["net_ml"]:
        bits.append(t("reçine") + " " + resin_cell(rs["total"], rs["currency"]))
    n_warn = len(d.get("warnings", [])) + sum(len(x.get("warnings", [])) for x in printed.values())
    failed = [n for n, x in parts.items() if x.get("repair_failed") and x.get("printed")]
    unplaced = [n for n, x in printed.items() if not x.get("plate")] if plates else []
    if failed:
        bits.append(t("onarılamayan: {parts}").format(parts=", ".join(failed)))
    if unplaced:
        bits.append(t("plate'e sığmayan: {parts}").format(parts=", ".join(unplaced)))
    mode = (d.get("assembly") or {}).get("mode")
    if mode == "bilinmiyor":
        bits.append(t("montaj bulunamadı"))
    if n_warn:
        bits.append(t("{n} uyarı").format(n=n_warn))
    return ", ".join(bits)


def morning_summary(items: list[QueueItem]) -> str:
    """Kuyruk bitince gösterilen özet."""
    from .fmt import fmt_duration

    done = [i for i in items if i.status == DONE]
    bad = [i for i in items if i.status in (FAILED, STOPPED)]
    lines = [t("Kuyruk: {n} kit hazır").format(n=len(done))
             + (t(", {n} kit sorunlu").format(n=len(bad)) if bad else "")
             + " " + t("(toplam {dur})").format(dur=fmt_duration(sum(i.seconds for i in items)))]
    for i in items:
        if i.status in (DONE, FAILED, STOPPED):
            lines.append(f"  • {i.name}: {status_text(i.status)}" + (f" — {i.result}" if i.result else ""))
    return "\n".join(lines)
