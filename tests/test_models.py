"""Data model behaviour."""

from __future__ import annotations

from custom_components.filament_manager.models import (
    FilamentType,
    Spool,
    normalize_uid,
    slugify_id,
)


def test_type_normalises_input() -> None:
    filament_type = FilamentType(
        id="x",
        vendor_id="v",
        name="Basic",
        material="pla",
        color_hex="#00ff00",
        diameter="1.75",
        gtin=["123", "  ", "456"],
    )
    assert filament_type.material == "PLA"
    assert filament_type.color_hex == "00FF00"
    assert filament_type.diameter == 1.75
    assert filament_type.gtin == ["123", "456"]


def test_density_falls_back_to_the_material() -> None:
    assert (
        FilamentType(id="x", vendor_id="v", name="n", material="PETG").effective_density
        == 1.27
    )
    assert (
        FilamentType(id="x", vendor_id="v", name="n", density=1.5).effective_density
        == 1.5
    )


def test_weight_to_length_matches_the_known_figure() -> None:
    """One kilo of 1.75 mm PLA is about 335 metres."""
    filament_type = FilamentType(id="x", vendor_id="v", name="n", material="PLA")
    assert 334 < filament_type.weight_to_length(1000) < 337


def test_spool_percentage_and_value() -> None:
    filament_type = FilamentType(id="x", vendor_id="v", name="n")
    spool = Spool(
        id="s", type_id="x", remaining_weight=250, initial_weight=1000, price=24.0
    )
    assert spool.remaining_percent == 25.0
    assert spool.value(filament_type) == 6.0
    assert spool.value(None) == 6.0


def test_spool_uppercases_rfid_uids() -> None:
    spool = Spool(id="s", type_id="x", rfid_uids=["ab12", " cd34 ", ""])
    assert spool.rfid_uids == ["AB12", "CD34"]


def test_from_dict_ignores_unknown_keys() -> None:
    spool = Spool.from_dict({"id": "s", "type_id": "t", "who": "knows"})
    assert spool.id == "s"


def test_slugify() -> None:
    assert slugify_id("Bambu Lab  PLA+ Basic") == "bambu_lab_pla_basic"


def test_normalize_uid() -> None:
    assert normalize_uid("04:a1:b2") == "04A1B2"
    assert normalize_uid(" 04-a1 b2 ") == "04A1B2"
    # An all-zero or all-F UID means "no tag", not "a tag to learn".
    assert normalize_uid("0000000000000000") is None
    assert normalize_uid("FFFFFFFF") is None
    assert normalize_uid("") is None
    assert normalize_uid(None) is None
