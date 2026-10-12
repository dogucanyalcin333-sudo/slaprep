"""Montaj kontrol listesi: her parça için 'nasıl yerleşti, ne kadar güvenilir' (uygulama ve rapor ortak kullanır).

Girdi analysis.json / prep.json'daki biçimdir: parça sözlüğü ({"placed": ...}) ve "assembly" sözlüğü
(mode, selected, links, unattached_groups).
"""
from __future__ import annotations

# Bu modül hafif kalmalı (uygulama açılırken yüklenir): autoassemble'ı içe aktarmaz.
STRONG_CONTACT = 300.0     # mm²: otomatik montajın 1. aşama ("güçlü ek") eşiği = min_contact 60 × STRICT_FACTOR 5
STRONG_DOCK = 1000.0                       # mm²: organik temas (kenetlenme) bunun üstünde güvenilir sayılır

# renk etiketi: ok (güvenilir), check (kontrol edin), bad (yerleşmedi), user (kullanıcı düzeltmesi), off (ilgisiz)
TAG_COLORS = {"ok": "#1b7f3a", "check": "#b26a00", "bad": "#c0392b", "user": "#1f5fbf", "off": "#888888"}


def part_status(name: str, part: dict, a: dict) -> tuple[str, str, int]:
    """(durum metni, renk etiketi, sıra — küçük olan listede önce)."""
    mode = a.get("mode")
    link = (a.get("links") or {}).get(name, {})
    kind, c = link.get("kind"), link.get("contact", 0) or 0
    if kind == "referans":
        return "ölçek referansı (basılmaz)", "off", 6
    if kind == "reklam":
        return "reklam/filigran dosyası (basılmaz)", "off", 6
    if name not in (a.get("selected") or [name]):
        return "alternatif (seçilmedi)", "off", 6
    if mode == "bilinmiyor":
        return "montaj yok", "off", 5
    if kind == "kullanıcı ayırdı":
        return "ayrıldı (sizin düzeltmeniz)", "user", 3
    if kind == "ayrıldı":
        return "ayrıldı: zayıf temas + gömülüyordu", "check", 1
    if not part.get("placed"):
        if any(name in g for g in a.get("unattached_groups") or []):
            return "ayrı grup: montaja eklenemedi", "check", 1
        return "yerleşmedi (ayrı basılacak)", "bad", 0
    if kind == "kök":
        return "referans parça", "ok", 4
    if kind == "kullanıcı":
        cand = link.get("cand")
        return "bağlandı (sizin düzeltmeniz" + (f", konum {cand}" if cand else "") + ")", "user", 3
    if kind == "kenetlenme":
        return ("organik temas, güçlü", "ok", 4) if c >= STRONG_DOCK else ("organik temas — kontrol edin", "check", 2)
    if kind == "ek yüzeyi":
        return ("güçlü ek", "ok", 4) if c >= STRONG_CONTACT else ("zayıf ek — kontrol edin", "check", 2)
    if kind == "dosyadan":
        return "dosyadan (montajlı kit)", "ok", 4
    if kind == "orijinden":
        return "dosyada orijindeydi, yerine oturtuldu — kontrol edin", "check", 2
    if kind == "kayma taraması":
        return "kayma çözüldü", "ok", 4
    return "yerleşti", "ok", 4


def assembly_dict(asm) -> dict:
    """Assembly nesnesinden part_status'un beklediği sözlük (rapor için)."""
    return dict(mode=asm.mode, selected=list(asm.selected), links=getattr(asm, "links", None) or {},
                unattached_groups=[sorted(g) for g in getattr(asm, "groups", None) or []])


def checklist(parts: dict, a: dict) -> list[tuple[str, str, str, str, object]]:
    """Sıralı liste: [(parça, durum, etiket, bağlandığı, temas)] — önce yerleşmeyen/kontrol edilecekler."""
    rows = []
    for n, p in parts.items():
        text, tag, order = part_status(n, p, a)
        link = (a.get("links") or {}).get(n, {})
        rows.append((order, n, text, tag, link.get("to") or "", link.get("contact") or ""))
    rows.sort(key=lambda r: (r[0], r[1].lower()))
    return [r[1:] for r in rows]
