"""Hedef yüksekliğe göre tek tip ölçek. Duvar, delik çapı, kenar payları mm olarak sabit kalır (ölçeklenmez)."""
from __future__ import annotations

from .i18n import t

PEG_CLEARANCE = 0.4    # mm, tipik pim-yuva boşluğu (Rango'da ölçüldü)
TIGHT_LIMIT = 0.25     # mm, bunun altında pimler sıkı olabilir


def compute_scale(run) -> tuple[float, list[str]]:
    """Dönüş: (ölçek, uyarılar)."""
    s = run.settings
    asm = run.asm
    warns = []
    if s.scale is not None:
        scale = float(s.scale)
    elif s.height is not None:
        if asm is None or asm.mode == "bilinmiyor":
            if not s.orig_height:
                raise SystemExit(t("Montaj bulunamadı: modelin boyu hesaplanamıyor. Ürün sayfasındaki orijinal boyu "
                                   "--orig-height ile verin (ölçek = hedef / orijinal) ya da --scale kullanın."))
            warns.append(t("Montaj bulunamadı: ölçek, verilen orijinal boy ({mm} mm) üzerinden hesaplandı.").format(
                mm=f"{s.orig_height:g}"))
            scale = s.height / s.orig_height
            return scale, warns + _peg_warning(scale)
        cur = run.figure_height_before if s.height_mode == "figure" else run.height_before
        if cur <= 0:
            raise SystemExit(t("Montaj yüksekliği hesaplanamadı."))
        scale = s.height / cur
    else:
        scale = 1.0
    if scale <= 0:
        raise SystemExit(t("Ölçek pozitif olmalı."))
    return scale, warns + _peg_warning(scale)


def _peg_warning(scale: float) -> list[str]:
    if scale < 1.0:
        gap = PEG_CLEARANCE * scale
        if gap < TIGHT_LIMIT:
            return [t("Ölçek {scale}: pim-yuva boşluğu ~{orig} → ~{gap} mm'ye düşer, "
                      "pimler sıkı olabilir (gerekirse zımparalayın).").format(
                          scale=f"{scale:.2f}", orig=PEG_CLEARANCE, gap=f"{gap:.2f}")]
    return []
