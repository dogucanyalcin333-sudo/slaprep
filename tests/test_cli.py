"""Komut satırı: montaj düzeltme bayrakları ayarlara doğru yansımalı ve kaydedilip geri okunabilmeli."""
import pytest

from slaprep.cli import build_parser, settings_from_args
from slaprep.config import load_settings, save_settings


def test_fix_flags_roundtrip(tmp_path):
    args = build_parser().parse_args(["analyze", str(tmp_path), "--reset", "--detach", "Arm_Right,Sword_1",
                                      "--join", "Head_v2:Miquella_Torso", "--join", "Arms:Sword_2"])
    s = settings_from_args(tmp_path, args)
    assert s.asm_detach == ["Arm_Right", "Sword_1"]
    assert s.asm_join == [["Head_v2", "Miquella_Torso"], ["Arms", "Sword_2"]]
    save_settings(tmp_path, s)
    s2 = load_settings(tmp_path)
    assert s2.asm_detach == s.asm_detach and s2.asm_join == s.asm_join
    # kayıtlıyken --clear-fixes siler
    s3 = settings_from_args(tmp_path, build_parser().parse_args(["analyze", str(tmp_path), "--clear-fixes"]))
    assert s3.asm_detach == [] and s3.asm_join == []


def test_join_requires_anchor_and_part(tmp_path):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["analyze", str(tmp_path), "--join", "Head_v2"])


def test_join_rank(tmp_path):
    a = build_parser().parse_args(["analyze", str(tmp_path), "--join", "Torso:Hand_L:2", "--join", "Torso:Cape:1"])
    assert a.join == [["Torso", "Hand_L", 1], ["Torso", "Cape"]]       # 1. aday = varsayılan, sıra yazılmaz
