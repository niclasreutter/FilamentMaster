"""Guards for the things HACS and hassfest check."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from PIL import Image
import pytest
import yaml

COMPONENT = Path("custom_components/filament_manager")


def load(name: str) -> dict:
    """Read one of the integration's JSON files."""
    return json.loads((COMPONENT / name).read_text(encoding="utf-8"))


def test_manifest_is_shaped_the_way_hassfest_wants_it() -> None:
    manifest = load("manifest.json")
    keys = list(manifest)
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])
    assert manifest["domain"] == "filament_manager"
    assert manifest["config_flow"] is True
    # HACS refuses an integration without a version.
    assert manifest["version"].count(".") == 2
    for key in ("documentation", "issue_tracker", "codeowners"):
        assert manifest[key]


def test_hacs_manifest_exists() -> None:
    hacs = json.loads(Path("hacs.json").read_text(encoding="utf-8"))
    assert hacs["name"]
    assert hacs["content_in_root"] is False


@pytest.mark.parametrize("language", ["en", "de"])
def test_translations_match_strings(language: str) -> None:
    """Both languages carry exactly the keys strings.json declares."""

    def keys(node, prefix="") -> set[str]:
        found = set()
        if isinstance(node, dict):
            for key, value in node.items():
                found.add(f"{prefix}{key}")
                found |= keys(value, f"{prefix}{key}.")
        return found

    assert keys(load(f"translations/{language}.json")) == keys(load("strings.json"))


def test_every_flow_step_is_translated() -> None:
    """A step without a title shows up as a raw key in the UI."""
    source = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    steps = {
        line.split('step_id="', 1)[1].split('"', 1)[0]
        for line in source.splitlines()
        if 'step_id="' in line
    }
    strings = load("strings.json")
    translated = set(strings["config"]["step"]) | set(strings["options"]["step"])
    assert steps <= translated


def test_every_menu_option_is_translated() -> None:
    """A menu entry without a label renders as a raw step id."""
    tree = ast.parse((COMPONENT / "config_flow.py").read_text(encoding="utf-8"))
    strings = load("strings.json")
    menus: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "async_show_menu":
            continue
        arguments = {keyword.arg: keyword.value for keyword in node.keywords}
        step = ast.literal_eval(arguments["step_id"])
        menus[step] = set(ast.literal_eval(arguments["menu_options"]))

    assert menus, "no menus found in the options flow"
    for step, options in menus.items():
        labels = set(strings["options"]["step"][step]["menu_options"])
        assert options == labels, step


def test_services_are_documented_and_translated() -> None:
    documented = set(
        yaml.safe_load((COMPONENT / "services.yaml").read_text(encoding="utf-8"))
    )
    strings = load("strings.json")
    assert documented == set(strings["services"])
    for name in documented:
        fields = (
            yaml.safe_load((COMPONENT / "services.yaml").read_text(encoding="utf-8"))[
                name
            ]
            or {}
        ).get("fields", {})
        translated = strings["services"][name].get("fields", {})
        assert set(fields) == set(translated), name


def test_icons_only_reference_real_translation_keys() -> None:
    icons = load("icons.json")
    strings = load("strings.json")
    for platform, entries in icons["entity"].items():
        assert set(entries) <= set(strings["entity"][platform]), platform
    assert set(icons["services"]) <= set(strings["services"])


def test_bundled_database_is_consistent() -> None:
    database = load("data/filaments.json")
    vendors = {vendor["id"] for vendor in database["vendors"]}
    ids = [item["id"] for item in database["types"]]
    assert len(ids) == len(set(ids)), "duplicate type ids"
    for item in database["types"]:
        assert item["vendor_id"] in vendors, item["id"]
        assert item["material"]
        assert item["diameter"] in (1.75, 2.85)
        assert item["net_weight"] > 0


def test_the_card_and_the_manifest_agree_on_the_version() -> None:
    """The card's cache-busting query string follows the manifest."""
    card = (COMPONENT / "www/filament-manager-card.js").read_text(encoding="utf-8")
    version = load("manifest.json")["version"]
    assert f'CARD_VERSION = "{version}"' in card


def test_the_wasm_reader_is_bundled() -> None:
    """The card falls back to ZXing on iOS, so it has to ship with it."""
    assert (COMPONENT / "www/zxing-reader.js").is_file()
    assert (COMPONENT / "www/zxing_reader.wasm").stat().st_size > 100_000
    assert (COMPONENT / "www/ZXING-WASM-LICENSE").is_file()


def test_brand_assets_ship_with_the_integration() -> None:
    """HACS needs these until the domain is listed in home-assistant/brands."""
    expected = {
        "icon.png": (256, 256),
        "icon@2x.png": (512, 512),
        "logo.png": (256, 256),
        "logo@2x.png": (512, 512),
    }
    for name, size in expected.items():
        path = COMPONENT / "brand" / name
        assert path.is_file(), name
        with Image.open(path) as image:
            assert image.size == size, name
            assert image.mode == "RGBA", name
