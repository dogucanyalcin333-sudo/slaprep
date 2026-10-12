"""Varsayılan parametreler ve kit başına slaprep.yaml."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import yaml

PRINTERS = {
    # ad: (X, Y, Z) mm — X/Y plate, Z yükseklik
    "m7": (223.0, 126.0, 230.0),
}

OUT_DIR = "_slaprep"
SEPARATE_GROUP = "ayrı"          # kullanıcının "ayrı plate" dediği parçaların plate grubu (plates\ayrı\plate_01.stl)


@dataclass
class Settings:
    # Ölçek
    height: float | None = None          # hedef montaj yüksekliği (mm)
    height_mode: str = "all"             # all | figure
    base: list[str] = field(default_factory=list)
    scale: float | None = None
    orig_height: float | None = None     # montaj bulunamazsa: modelin bilinen orijinal boyu (mm), ölçek = height/orig
    # Varyantlar
    variants: list[str] = field(default_factory=list)
    print_all_variants: bool = False
    print_reference: bool = False        # ölçek/boy referansı (HEIGHT_PILLAR gibi) ve reklam sanılan parçaları da bas
    # Hollow / delik (mutlak mm, ölçekle değişmez)
    wall: float = 2.0
    hole: float = 3.0                    # temel çap; hole_auto ise büyük kavitelerde hole_max'a kadar büyür
    hole_min: float = 2.0
    hole_max: float = 5.0
    hole_auto: bool = True               # --hole verilince False: çap sabit
    min_holes: int | None = None         # kavite başına en az delik (--holes)
    max_holes: int = 8
    ml_per_hole: float = 20.0            # her bu kadar ml kavite için +1 delik
    mm_per_hole: float = 50.0            # kavitenin her bu kadar mm uzunluğu için +1 delik
    hole_margin: float = 1.0
    contact_tol: float = 0.4
    hollow_min_ml: float = 3.0
    hollow_min_frac: float = 0.25
    min_cavity_ml: float = 0.5
    max_cavities: int = 4
    solid: list[str] = field(default_factory=list)
    hollow: list[str] = field(default_factory=list)
    separate: list[str] = field(default_factory=list)   # kendi plate'lerine dizilecek parçalar (ör. şeffaf reçine)
    allow_visible_holes: bool = False
    hole_on_bottom: bool = False         # kaidenin alt yüzüne de delik (dış yüzey: baskıdan sonra tıkanmalı)
    hole_on_wall: bool = False           # duvar modunda arka yüze de delik (dış yüzey: baskıdan sonra tıkanmalı)
    # Montaj
    up_axis: str | None = None           # x | y | z | None (otomatik)
    front_axis: str | None = None        # figürün baktığı yön (ör. "-y"); None: export kuralından (render.default_views)
    wall_mount: bool = False             # duvara asılacak: duvara bakan arka yüzler gizli, zemin tabanı yok
    wall_side: str | None = None         # duvar yönü (ör. "-z"); None: otomatik
    # Kullanıcının montaj düzeltmeleri (uygulamadaki Montaj sekmesi ya da --detach / --join)
    asm_detach: list[str] = field(default_factory=list)       # yanlış yere oturan parçalar: montaja alınmaz
    asm_join: list[list[str]] = field(default_factory=list)   # [çapa, parça]: parça çapaya bağlanır (otomatik)
    # Yönlendirme / yerleşim
    tilt_min: float = 30.0
    tilt_max: float = 45.0
    support_height: float = 5.0
    gap: float = 3.0
    printer: str = "m7"
    build_volume: tuple[float, float, float] = PRINTERS["m7"]
    plates: int | None = None
    plates_by_folder: bool | None = None # None: kitte birden çok alt klasör varsa otomatik açık
    plate_goal: str = "time"             # time: en kısa toplam baskı süresi | plates: en az plate
    steps: str = "all"                   # all: hollow + delik + plate | plates: sadece plate'e diz (dolu) |
    #                                      hollow: sadece hollow + delik (plate yok, parçalar orijinal duruşta)
    layer_height: float = 0.05           # mm (süre tahmini için; CHITUBOX'taki değerinizi girin)
    layer_time: float = 6.5              # sn/katman: pozlama + kaldırma/indirme (M7 tipik)
    plate_overhead_min: float = 15.0     # dk/plate: alt katmanlar, hazırlık, temizlik
    only: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)   # kitle ilgisiz dosyalar (logo, reklam vb.)

    # Kalıcı kaydedilmeyecek alanlar (her çalıştırmaya özel)
    _volatile = ("only",)

    def to_yaml_dict(self) -> dict:
        d = asdict(self)
        for k in self._volatile:
            d.pop(k, None)
        d["build_volume"] = list(self.build_volume)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        names = {f.name for f in fields(cls)}
        s = cls(**{k: v for k, v in d.items() if k in names})
        s.build_volume = tuple(float(x) for x in s.build_volume)
        return s


def out_dir(kit: Path) -> Path:
    return Path(kit) / OUT_DIR


def load_settings(kit: Path) -> Settings:
    p = out_dir(kit) / "slaprep.yaml"
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return Settings.from_dict(yaml.safe_load(f) or {})
    return Settings()


def save_settings(kit: Path, s: Settings) -> Path:
    p = out_dir(kit) / "slaprep.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(s.to_yaml_dict(), f, allow_unicode=True, sort_keys=False)
    return p
