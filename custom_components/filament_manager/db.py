"""Master data: vendors and filament types.

Two layers are merged at startup and the user wins:

1. ``data/filaments.json`` ships with the integration and is refreshed by
   HACS updates.
2. ``/config/filament_db.json`` holds the user's own additions and overrides
   the bundle. It lives in the config directory, so it is covered by the HA
   backup and can be exported as a pull request snippet for the shared
   database.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .const import (
    BUNDLED_DB_FILE,
    DB_SPEC_VERSION,
    USER_DB_FILE,
)
from .models import FilamentType, Vendor, slugify_id

_LOGGER = logging.getLogger(__name__)

SOURCE_BUNDLED = "bundled"
SOURCE_USER = "user"


def _read_json(path: Path) -> dict[str, Any]:
    """Read a JSON file, returning an empty document when it is missing."""
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as err:
        raise HomeAssistantError(f"Could not read {path}: {err}") from err
    if not isinstance(data, dict):
        raise HomeAssistantError(f"{path} must contain a JSON object")
    return data


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """Write a JSON file atomically enough for a config directory."""
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False, sort_keys=False)
            handle.write("\n")
        tmp.replace(path)
    except OSError as err:
        tmp.unlink(missing_ok=True)
        raise HomeAssistantError(f"Could not write {path}: {err}") from err


class FilamentDatabase:
    """Merged view of the bundled and user owned master data."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise the database."""
        self.hass = hass
        self.vendors: dict[str, Vendor] = {}
        self.types: dict[str, FilamentType] = {}
        self._user_data: dict[str, Any] = {}
        self._bundled_path = Path(__file__).parent / BUNDLED_DB_FILE
        self._user_path = Path(hass.config.path(USER_DB_FILE))

    @property
    def user_path(self) -> Path:
        """Return the path of the user owned database."""
        return self._user_path

    # -- loading -----------------------------------------------------------

    async def async_load(self) -> None:
        """Load and merge both layers."""
        bundled, user = await self.hass.async_add_executor_job(self._load_files)
        self._user_data = user
        self._merge(bundled, user)
        _LOGGER.debug(
            "Loaded %s vendors and %s filament types (%s user defined)",
            len(self.vendors),
            len(self.types),
            sum(1 for t in self.types.values() if t.source == SOURCE_USER),
        )

    def _load_files(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Read both JSON files from disk (executor)."""
        bundled = _read_json(self._bundled_path)
        user = _read_json(self._user_path)
        for name, data in (("bundled", bundled), ("user", user)):
            spec = data.get("spec_version", DB_SPEC_VERSION)
            if spec > DB_SPEC_VERSION:
                _LOGGER.warning(
                    "The %s filament database declares spec_version %s but this "
                    "integration understands %s — unknown fields are ignored",
                    name,
                    spec,
                    DB_SPEC_VERSION,
                )
        return bundled, user

    @callback
    def _merge(self, bundled: dict[str, Any], user: dict[str, Any]) -> None:
        """Merge both layers, letting the user override the bundle."""
        self.vendors = {}
        self.types = {}
        for layer, source in ((bundled, SOURCE_BUNDLED), (user, SOURCE_USER)):
            for raw in layer.get("vendors", []):
                if not raw.get("id"):
                    continue
                self.vendors[raw["id"]] = Vendor.from_dict(raw)
            for raw in layer.get("types", []):
                if not raw.get("id"):
                    continue
                merged = dict(raw)
                merged["source"] = source
                self.types[raw["id"]] = FilamentType.from_dict(merged)

    async def async_reload(self) -> None:
        """Re-read both layers from disk."""
        await self.async_load()

    # -- lookups -----------------------------------------------------------

    @callback
    def get_type(self, type_id: str) -> FilamentType | None:
        """Return one filament type."""
        return self.types.get(type_id)

    @callback
    def vendor_name(self, type_id: str) -> str:
        """Return the vendor name for a type, or an empty string."""
        filament_type = self.types.get(type_id)
        if filament_type is None:
            return ""
        vendor = self.vendors.get(filament_type.vendor_id)
        return vendor.name if vendor else ""

    @callback
    def describe_type(self, type_id: str) -> str:
        """Return a human readable label for a type."""
        filament_type = self.types.get(type_id)
        if filament_type is None:
            return type_id
        vendor = self.vendor_name(type_id)
        parts = [part for part in (vendor, filament_type.name) if part]
        label = " ".join(parts)
        if filament_type.color_name:
            label = f"{label} ({filament_type.color_name})"
        return label

    @callback
    def find_by_gtin(self, code: str) -> list[FilamentType]:
        """Return every type answering to a barcode.

        A GTIN identifies the *product*, not the roll — every black PLA from
        the same vendor carries the same code. EAN-13 and UPC-A describe the
        same product with and without a leading zero, so both spellings are
        looked up in one pass.
        """
        code = str(code).strip()
        if not code:
            return []
        candidates = {code}
        if len(code) == 13 and code.startswith("0"):
            candidates.add(code[1:])
        elif len(code) == 12:
            candidates.add(f"0{code}")
        return [
            filament_type
            for filament_type in self.types.values()
            if candidates.intersection(filament_type.gtin)
        ]

    @callback
    def sorted_types(self, recent: list[str] | None = None) -> list[FilamentType]:
        """Return types with the recently used ones first."""
        recent = recent or []
        order = {type_id: index for index, type_id in enumerate(recent)}
        return sorted(
            self.types.values(),
            key=lambda t: (order.get(t.id, len(order)), t.sort_key),
        )

    # -- writing -----------------------------------------------------------

    async def async_add_vendor(self, vendor: Vendor) -> Vendor:
        """Store a vendor in the user database."""
        if not vendor.id:
            vendor.id = slugify_id(vendor.name)
        self.vendors[vendor.id] = vendor
        await self._async_upsert("vendors", vendor.to_dict())
        return vendor

    async def async_add_type(self, filament_type: FilamentType) -> FilamentType:
        """Store a filament type in the user database."""
        if not filament_type.id:
            base = slugify_id(
                f"{filament_type.vendor_id} {filament_type.name} "
                f"{filament_type.color_name or ''}"
            )
            candidate = base
            suffix = 2
            while candidate in self.types:
                candidate = f"{base}_{suffix}"
                suffix += 1
            filament_type.id = candidate
        filament_type.source = SOURCE_USER
        self.types[filament_type.id] = filament_type
        payload = filament_type.to_dict()
        payload.pop("source", None)
        await self._async_upsert("types", payload)
        return filament_type

    async def _async_upsert(self, section: str, payload: dict[str, Any]) -> None:
        """Insert or replace one entry in the user database."""
        entries: list[dict[str, Any]] = self._user_data.setdefault(section, [])
        for index, entry in enumerate(entries):
            if entry.get("id") == payload["id"]:
                entries[index] = payload
                break
        else:
            entries.append(payload)
        await self._async_write_user()

    async def _async_write_user(self) -> None:
        """Persist the user database."""
        self._user_data.setdefault("spec_version", DB_SPEC_VERSION)
        self._user_data.setdefault("vendors", [])
        self._user_data.setdefault("types", [])
        await self.hass.async_add_executor_job(
            _write_json, self._user_path, self._user_data
        )

    # -- import / export ---------------------------------------------------

    @callback
    def export(self, only_user: bool = False) -> dict[str, Any]:
        """Return the database as a serialisable document."""
        if only_user:
            vendor_ids = {
                filament_type.vendor_id
                for filament_type in self.types.values()
                if filament_type.source == SOURCE_USER
            }
            vendors = [
                vendor.to_dict()
                for vendor in self.vendors.values()
                if vendor.id in vendor_ids
            ]
            types = [
                {
                    key: value
                    for key, value in filament_type.to_dict().items()
                    if key != "source"
                }
                for filament_type in self.types.values()
                if filament_type.source == SOURCE_USER
            ]
        else:
            vendors = [vendor.to_dict() for vendor in self.vendors.values()]
            types = [
                {
                    key: value
                    for key, value in filament_type.to_dict().items()
                    if key != "source"
                }
                for filament_type in self.types.values()
            ]
        return {
            "spec_version": DB_SPEC_VERSION,
            "vendors": sorted(vendors, key=lambda item: item["id"]),
            "types": sorted(types, key=lambda item: item["id"]),
        }

    async def async_import(
        self, document: dict[str, Any], overwrite: bool = False
    ) -> tuple[int, int]:
        """Merge a document into the user database.

        Returns the number of imported vendors and types.
        """
        if not isinstance(document, dict):
            raise HomeAssistantError("The imported database must be a JSON object")
        vendors = document.get("vendors") or []
        types = document.get("types") or []
        if not isinstance(vendors, list) or not isinstance(types, list):
            raise HomeAssistantError("'vendors' and 'types' must be lists")

        existing_vendors = {
            entry.get("id") for entry in self._user_data.get("vendors", [])
        }
        existing_types = {entry.get("id") for entry in self._user_data.get("types", [])}

        added_vendors = 0
        for raw in vendors:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            if raw["id"] in existing_vendors and not overwrite:
                continue
            vendor = Vendor.from_dict(raw)
            self.vendors[vendor.id] = vendor
            entries = self._user_data.setdefault("vendors", [])
            _replace_or_append(entries, vendor.to_dict())
            added_vendors += 1

        added_types = 0
        for raw in types:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            if raw["id"] in existing_types and not overwrite:
                continue
            filament_type = FilamentType.from_dict({**raw, "source": SOURCE_USER})
            self.types[filament_type.id] = filament_type
            payload = filament_type.to_dict()
            payload.pop("source", None)
            entries = self._user_data.setdefault("types", [])
            _replace_or_append(entries, payload)
            added_types += 1

        await self._async_write_user()
        return added_vendors, added_types

    @callback
    def contribution_snippet(self) -> str:
        """Return a pull request ready JSON snippet of the user's additions.

        The shared database grows through its users: this is the "contribute"
        button's payload, ready to be pasted into a PR against the bundled
        file.
        """
        return json.dumps(self.export(only_user=True), indent=2, ensure_ascii=False)


def _replace_or_append(entries: list[dict[str, Any]], payload: dict[str, Any]) -> None:
    """Replace an entry with the same id, or append it."""
    for index, entry in enumerate(entries):
        if entry.get("id") == payload["id"]:
            entries[index] = payload
            return
    entries.append(payload)
