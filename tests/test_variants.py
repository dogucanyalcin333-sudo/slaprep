from slaprep.variants import choose_set, name_stem


def test_name_stem():
    assert name_stem("Head_V1") == name_stem("Head_V2") == "head"
    assert name_stem("Head") is None          # "d" son harfi varyant eki sayılmamalı
    assert name_stem("Tombstone_1") is None   # sayı tek başına varyant değil
    assert name_stem("Left_ShoePin") is None


def test_mirror_names():
    from slaprep.variants import _mirror_names

    assert _mirror_names("Foot_Left", "Foot_Right")
    assert _mirror_names("BRAZO_IZQ", "BRAZO_DER")
    assert _mirror_names("Leg_L", "Leg_R")
    assert not _mirror_names("Torso", "Torso_v2")
    assert not _mirror_names("Head_v1", "Head_v2")


def test_choose_single_alternatives_by_name():
    conflict = {frozenset(("Head_V1", "Head_V2"))}
    assert choose_set(["Head_V1", "Head_V2"], conflict, {"Head_V1": 70.2, "Head_V2": 71.8}, set()) == ["Head_V1"]
    assert choose_set(["Head_V1", "Head_V2"], conflict, {"Head_V1": 70.2, "Head_V2": 71.8}, {"head_v2"}) == ["Head_V2"]


def test_choose_split_over_merged():
    m = ["base_merged", "base_part_1", "base_part_2"]
    conflict = {frozenset(("base_merged", "base_part_1")), frozenset(("base_merged", "base_part_2"))}
    vol = {"base_merged": 100.0, "base_part_1": 55.0, "base_part_2": 45.0}
    assert choose_set(m, conflict, vol, set()) == ["base_part_1", "base_part_2"]
    assert choose_set(m, conflict, vol, {"base_merged"}) == ["base_merged"]
