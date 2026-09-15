"""Service layer — the scripting interface to the inventory."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .const import (
    ATTR_AMOUNT,
    ATTR_GROSS_WEIGHT,
    ATTR_LENGTH,
    ATTR_REMAINING_WEIGHT,
    ATTR_RFID_UID,
    ATTR_SLOT,
    ATTR_SPOOL_ID,
    ATTR_TAG_ID,
    ATTR_TYPE_ID,
    ATTR_VENDOR_ID,
    DIAMETERS,
    DOMAIN,
    SERVICE_ADD_SPOOL,
    SERVICE_ADD_TYPE,
    SERVICE_ADD_VENDOR,
    SERVICE_ARCHIVE_SPOOL,
    SERVICE_ASSIGN_SLOT,
    SERVICE_CLEAR_SLOT,
    SERVICE_CONSUME,
    SERVICE_CORRECT_WEIGHT,
    SERVICE_DELETE_SPOOL,
    SERVICE_DUPLICATE_SPOOL,
    SERVICE_EXPORT_CONTRIBUTION,
    SERVICE_EXPORT_DB,
    SERVICE_IMPORT_DB,
    SERVICE_LEARN_RFID,
    SERVICE_LINK_TAG,
    SERVICE_RELOAD_DB,
    SERVICE_RESTORE_SPOOL,
    SERVICE_UNLINK_TAG,
    SERVICE_UPDATE_SPOOL,
    SOURCE_MANUAL,
    SPOOL_TYPES,
    TAG_KIND_SLOT,
    TAG_KIND_SPOOL,
)
from .coordinator import FilamentCoordinator
from .models import FilamentType, Vendor

_LOGGER = logging.getLogger(__name__)

SPOOL_ID_SCHEMA = vol.Schema({vol.Required(ATTR_SPOOL_ID): cv.string})

ADD_SPOOL_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_TYPE_ID): cv.string,
        vol.Optional("initial_weight"): vol.Coerce(float),
        vol.Optional(ATTR_REMAINING_WEIGHT): vol.Coerce(float),
        vol.Optional("purchase_date"): cv.string,
        vol.Optional("price"): vol.Coerce(float),
        vol.Optional("location"): cv.string,
        vol.Optional("note"): cv.string,
        vol.Optional("low_stock_threshold"): vol.Coerce(float),
        vol.Optional("count", default=1): vol.All(int, vol.Range(min=1, max=50)),
    }
)

UPDATE_SPOOL_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SPOOL_ID): cv.string,
        vol.Optional("location"): cv.string,
        vol.Optional("price"): vol.Coerce(float),
        vol.Optional("purchase_date"): cv.string,
        vol.Optional("note"): cv.string,
        vol.Optional("low_stock_threshold"): vol.Coerce(float),
        vol.Optional("spool_weight"): vol.Coerce(float),
        vol.Optional(ATTR_TYPE_ID): cv.string,
    }
)

CONSUME_SCHEMA = vol.Schema(
    vol.All(
        {
            vol.Optional(ATTR_SPOOL_ID): cv.string,
            vol.Optional(ATTR_SLOT): vol.Coerce(int),
            vol.Optional(ATTR_AMOUNT): vol.All(vol.Coerce(float), vol.Range(min=0)),
            vol.Optional(ATTR_LENGTH): vol.All(vol.Coerce(float), vol.Range(min=0)),
        },
        cv.has_at_least_one_key(ATTR_SPOOL_ID, ATTR_SLOT),
        cv.has_at_least_one_key(ATTR_AMOUNT, ATTR_LENGTH),
    )
)

ASSIGN_SLOT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SLOT): vol.Coerce(int),
        vol.Optional(ATTR_SPOOL_ID): vol.Any(cv.string, None),
    }
)

CLEAR_SLOT_SCHEMA = vol.Schema({vol.Required(ATTR_SLOT): vol.Coerce(int)})

CORRECT_WEIGHT_SCHEMA = vol.Schema(
    vol.All(
        {
            vol.Required(ATTR_SPOOL_ID): cv.string,
            vol.Optional(ATTR_GROSS_WEIGHT): vol.Coerce(float),
            vol.Optional(ATTR_REMAINING_WEIGHT): vol.Coerce(float),
        },
        cv.has_at_least_one_key(ATTR_GROSS_WEIGHT, ATTR_REMAINING_WEIGHT),
    )
)

ADD_VENDOR_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Optional(ATTR_VENDOR_ID): cv.string,
        vol.Optional("website"): cv.string,
    }
)

ADD_TYPE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_VENDOR_ID): cv.string,
        vol.Required("name"): cv.string,
        vol.Required("material"): cv.string,
        vol.Optional(ATTR_TYPE_ID): cv.string,
        vol.Optional("color_name"): cv.string,
        vol.Optional("color_hex"): cv.string,
        vol.Optional("diameter", default=1.75): vol.In(DIAMETERS),
        vol.Optional("density"): vol.Coerce(float),
        vol.Optional("net_weight", default=1000): vol.Coerce(float),
        vol.Optional("spool_weight", default=0): vol.Coerce(float),
        vol.Optional("spool_type", default="plastic"): vol.In(SPOOL_TYPES),
        vol.Optional("nozzle_temp_min"): vol.Coerce(int),
        vol.Optional("nozzle_temp_max"): vol.Coerce(int),
        vol.Optional("bed_temp"): vol.Coerce(int),
        vol.Optional("gtin"): vol.All(cv.ensure_list, [cv.string]),
    }
)

LINK_TAG_SCHEMA = vol.Schema(
    vol.All(
        {
            vol.Required(ATTR_TAG_ID): cv.string,
            vol.Optional(ATTR_SPOOL_ID): cv.string,
            vol.Optional(ATTR_SLOT): vol.Coerce(int),
        },
        cv.has_at_least_one_key(ATTR_SPOOL_ID, ATTR_SLOT),
    )
)

UNLINK_TAG_SCHEMA = vol.Schema({vol.Required(ATTR_TAG_ID): cv.string})

LEARN_RFID_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SPOOL_ID): cv.string,
        vol.Required(ATTR_RFID_UID): cv.string,
        vol.Optional(ATTR_SLOT): vol.Coerce(int),
    }
)

IMPORT_DB_SCHEMA = vol.Schema(
    vol.All(
        {
            vol.Optional("filename"): cv.string,
            vol.Optional("data"): dict,
            vol.Optional("overwrite", default=False): cv.boolean,
        },
        cv.has_at_least_one_key("filename", "data"),
    )
)

EXPORT_DB_SCHEMA = vol.Schema(
    {
        vol.Optional("filename"): cv.string,
        vol.Optional("only_user", default=False): cv.boolean,
    }
)

EXPORT_CONTRIBUTION_SCHEMA = vol.Schema({vol.Optional("filename"): cv.string})


@callback
def async_get_coordinator(hass: HomeAssistant) -> FilamentCoordinator:
    """Return the coordinator of the loaded config entry."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    raise HomeAssistantError("Filament Manager is not set up")


def _resolve_path(hass: HomeAssistant, filename: str) -> Path:
    """Return a path this integration is allowed to read or write.

    A relative name lands in the config directory, which the integration
    already owns; an absolute one has to be covered by
    ``allowlist_external_dirs``.
    """
    path = Path(filename)
    if path.is_absolute():
        if not hass.config.is_allowed_path(str(path.parent)):
            raise ServiceValidationError(
                f"{path.parent} is not in allowlist_external_dirs"
            )
        return path
    config_dir = Path(hass.config.config_dir).resolve()
    resolved = Path(hass.config.path(filename)).resolve()
    if not resolved.is_relative_to(config_dir):
        raise ServiceValidationError(
            f"{filename} points outside the configuration directory"
        )
    return resolved


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register every service exactly once."""
    if hass.services.has_service(DOMAIN, SERVICE_ADD_SPOOL):
        return

    async def add_spool(call: ServiceCall) -> ServiceResponse:
        """Add one or several identical rolls."""
        coordinator = async_get_coordinator(hass)
        ids: list[str] = []
        for _ in range(call.data.get("count", 1)):
            spool = coordinator.async_add_spool(
                call.data[ATTR_TYPE_ID],
                initial_weight=call.data.get("initial_weight"),
                remaining_weight=call.data.get(ATTR_REMAINING_WEIGHT),
                purchase_date=call.data.get("purchase_date"),
                price=call.data.get("price"),
                location=call.data.get("location"),
                note=call.data.get("note"),
                low_stock_threshold=call.data.get("low_stock_threshold"),
            )
            ids.append(spool.id)
        return {"spool_ids": ids, ATTR_SPOOL_ID: ids[0]}

    async def update_spool(call: ServiceCall) -> None:
        """Change the bookkeeping fields of a roll."""
        coordinator = async_get_coordinator(hass)
        changes = {
            key: value for key, value in call.data.items() if key != ATTR_SPOOL_ID
        }
        if coordinator.store.update_spool(call.data[ATTR_SPOOL_ID], **changes) is None:
            raise ServiceValidationError(f"Unknown spool: {call.data[ATTR_SPOOL_ID]}")

    async def duplicate_spool(call: ServiceCall) -> ServiceResponse:
        """Add another roll of the same type."""
        coordinator = async_get_coordinator(hass)
        spool = coordinator.async_duplicate_spool(call.data[ATTR_SPOOL_ID])
        return {ATTR_SPOOL_ID: spool.id}

    async def consume(call: ServiceCall) -> ServiceResponse:
        """Subtract filament, by weight or by length."""
        coordinator = async_get_coordinator(hass)
        spool_id = call.data.get(ATTR_SPOOL_ID)
        if spool_id is None:
            slot = int(call.data[ATTR_SLOT])
            state = coordinator.store.slots.get(slot)
            if state is None or state.spool_id is None:
                raise ServiceValidationError(f"No roll assigned to slot {slot}")
            spool_id = state.spool_id
        grams = call.data.get(ATTR_AMOUNT)
        if grams is None:
            spool = coordinator.store.get_spool(spool_id)
            filament_type = coordinator.type_of(spool) if spool else None
            if filament_type is None:
                raise ServiceValidationError(
                    "Cannot convert a length without a known filament type"
                )
            metres = float(call.data[ATTR_LENGTH])
            radius_cm = (filament_type.diameter / 10) / 2
            volume_cm3 = 3.141592653589793 * radius_cm**2 * metres * 100
            grams = volume_cm3 * filament_type.effective_density
        applied = coordinator.async_consume(spool_id, float(grams), SOURCE_MANUAL)
        return {ATTR_SPOOL_ID: spool_id, "consumed": applied}

    async def assign_slot(call: ServiceCall) -> None:
        """Put a roll into a slot."""
        coordinator = async_get_coordinator(hass)
        coordinator.async_assign_slot(
            int(call.data[ATTR_SLOT]), call.data.get(ATTR_SPOOL_ID), SOURCE_MANUAL
        )

    async def clear_slot(call: ServiceCall) -> None:
        """Empty a slot."""
        coordinator = async_get_coordinator(hass)
        coordinator.async_assign_slot(int(call.data[ATTR_SLOT]), None, SOURCE_MANUAL)

    async def archive_spool(call: ServiceCall) -> None:
        """Archive a roll."""
        async_get_coordinator(hass).async_archive_spool(call.data[ATTR_SPOOL_ID], True)

    async def restore_spool(call: ServiceCall) -> None:
        """Bring an archived roll back."""
        async_get_coordinator(hass).async_archive_spool(call.data[ATTR_SPOOL_ID], False)

    async def delete_spool(call: ServiceCall) -> None:
        """Delete a roll for good."""
        async_get_coordinator(hass).async_delete_spool(call.data[ATTR_SPOOL_ID])

    async def correct_weight(call: ServiceCall) -> ServiceResponse:
        """Correct a roll after weighing it."""
        coordinator = async_get_coordinator(hass)
        spool = coordinator.async_correct_weight(
            call.data[ATTR_SPOOL_ID],
            gross_weight=call.data.get(ATTR_GROSS_WEIGHT),
            remaining_weight=call.data.get(ATTR_REMAINING_WEIGHT),
        )
        return {ATTR_SPOOL_ID: spool.id, ATTR_REMAINING_WEIGHT: spool.remaining_weight}

    async def add_vendor(call: ServiceCall) -> ServiceResponse:
        """Add a vendor to the user database."""
        coordinator = async_get_coordinator(hass)
        vendor = await coordinator.db.async_add_vendor(
            Vendor(
                id=call.data.get(ATTR_VENDOR_ID, ""),
                name=call.data["name"],
                website=call.data.get("website"),
            )
        )
        coordinator.async_touch()
        return {ATTR_VENDOR_ID: vendor.id}

    async def add_type(call: ServiceCall) -> ServiceResponse:
        """Add a filament type to the user database."""
        coordinator = async_get_coordinator(hass)
        payload = dict(call.data)
        payload.setdefault("id", payload.pop(ATTR_TYPE_ID, ""))
        payload["id"] = payload.get("id") or ""
        filament_type = await coordinator.db.async_add_type(
            FilamentType.from_dict(payload)
        )
        coordinator.async_touch()
        return {ATTR_TYPE_ID: filament_type.id}

    async def link_tag(call: ServiceCall) -> None:
        """Link an NFC tag to a roll or to a slot."""
        coordinator = async_get_coordinator(hass)
        if (spool_id := call.data.get(ATTR_SPOOL_ID)) is not None:
            if coordinator.store.get_spool(spool_id) is None:
                raise ServiceValidationError(f"Unknown spool: {spool_id}")
            coordinator.store.link_tag(call.data[ATTR_TAG_ID], TAG_KIND_SPOOL, spool_id)
            return
        slot = int(call.data[ATTR_SLOT])
        if slot < 1 or slot > coordinator.slot_count:
            raise ServiceValidationError(f"Slot {slot} does not exist")
        coordinator.store.link_tag(call.data[ATTR_TAG_ID], TAG_KIND_SLOT, slot)

    async def unlink_tag(call: ServiceCall) -> None:
        """Forget a tag mapping."""
        coordinator = async_get_coordinator(hass)
        if not coordinator.store.unlink_tag(call.data[ATTR_TAG_ID]):
            raise ServiceValidationError(f"Unknown tag: {call.data[ATTR_TAG_ID]}")

    async def learn_rfid(call: ServiceCall) -> None:
        """Teach a Bambu tag UID to a roll.

        Bambu spools carry one tag per side; run this once per side and the
        roll is recognised whichever way round it goes in.
        """
        coordinator = async_get_coordinator(hass)
        spool_id = call.data[ATTR_SPOOL_ID]
        if coordinator.store.learn_rfid(spool_id, call.data[ATTR_RFID_UID]) is None:
            raise ServiceValidationError(f"Unknown spool: {spool_id}")
        if (slot := call.data.get(ATTR_SLOT)) is not None:
            coordinator.async_assign_slot(int(slot), spool_id, "rfid")

    async def import_db(call: ServiceCall) -> ServiceResponse:
        """Merge a filament database into the user database."""
        coordinator = async_get_coordinator(hass)
        document = call.data.get("data")
        if document is None:
            path = _resolve_path(hass, call.data["filename"])

            def _read() -> dict[str, Any]:
                with path.open(encoding="utf-8") as handle:
                    return json.load(handle)

            try:
                document = await hass.async_add_executor_job(_read)
            except (OSError, ValueError) as err:
                raise ServiceValidationError(f"Could not read {path}: {err}") from err
        vendors, types = await coordinator.db.async_import(
            document, call.data.get("overwrite", False)
        )
        coordinator.async_touch()
        return {"vendors": vendors, "types": types}

    async def export_db(call: ServiceCall) -> ServiceResponse:
        """Return the filament database, optionally writing it to a file."""
        coordinator = async_get_coordinator(hass)
        document = coordinator.db.export(only_user=call.data.get("only_user", False))
        if filename := call.data.get("filename"):
            path = _resolve_path(hass, filename)

            def _write() -> None:
                with path.open("w", encoding="utf-8") as handle:
                    json.dump(document, handle, indent=2, ensure_ascii=False)
                    handle.write("\n")

            try:
                await hass.async_add_executor_job(_write)
            except OSError as err:
                raise ServiceValidationError(f"Could not write {path}: {err}") from err
        return {"database": document}

    async def export_contribution(call: ServiceCall) -> ServiceResponse:
        """Return the user's own types as a pull request ready snippet."""
        coordinator = async_get_coordinator(hass)
        snippet = coordinator.db.contribution_snippet()
        if filename := call.data.get("filename"):
            path = _resolve_path(hass, filename)

            def _write() -> None:
                path.write_text(f"{snippet}\n", encoding="utf-8")

            try:
                await hass.async_add_executor_job(_write)
            except OSError as err:
                raise ServiceValidationError(f"Could not write {path}: {err}") from err
        return {"snippet": snippet}

    async def reload_db(call: ServiceCall) -> None:
        """Re-read both master data layers from disk."""
        coordinator = async_get_coordinator(hass)
        await coordinator.db.async_reload()
        coordinator.async_touch()

    registrations: list[tuple[str, Any, vol.Schema | None, SupportsResponse]] = [
        (SERVICE_ADD_SPOOL, add_spool, ADD_SPOOL_SCHEMA, SupportsResponse.OPTIONAL),
        (
            SERVICE_UPDATE_SPOOL,
            update_spool,
            UPDATE_SPOOL_SCHEMA,
            SupportsResponse.NONE,
        ),
        (
            SERVICE_DUPLICATE_SPOOL,
            duplicate_spool,
            SPOOL_ID_SCHEMA,
            SupportsResponse.OPTIONAL,
        ),
        (SERVICE_CONSUME, consume, CONSUME_SCHEMA, SupportsResponse.OPTIONAL),
        (SERVICE_ASSIGN_SLOT, assign_slot, ASSIGN_SLOT_SCHEMA, SupportsResponse.NONE),
        (SERVICE_CLEAR_SLOT, clear_slot, CLEAR_SLOT_SCHEMA, SupportsResponse.NONE),
        (SERVICE_ARCHIVE_SPOOL, archive_spool, SPOOL_ID_SCHEMA, SupportsResponse.NONE),
        (SERVICE_RESTORE_SPOOL, restore_spool, SPOOL_ID_SCHEMA, SupportsResponse.NONE),
        (SERVICE_DELETE_SPOOL, delete_spool, SPOOL_ID_SCHEMA, SupportsResponse.NONE),
        (
            SERVICE_CORRECT_WEIGHT,
            correct_weight,
            CORRECT_WEIGHT_SCHEMA,
            SupportsResponse.OPTIONAL,
        ),
        (SERVICE_ADD_VENDOR, add_vendor, ADD_VENDOR_SCHEMA, SupportsResponse.OPTIONAL),
        (SERVICE_ADD_TYPE, add_type, ADD_TYPE_SCHEMA, SupportsResponse.OPTIONAL),
        (SERVICE_LINK_TAG, link_tag, LINK_TAG_SCHEMA, SupportsResponse.NONE),
        (SERVICE_UNLINK_TAG, unlink_tag, UNLINK_TAG_SCHEMA, SupportsResponse.NONE),
        (SERVICE_LEARN_RFID, learn_rfid, LEARN_RFID_SCHEMA, SupportsResponse.NONE),
        (SERVICE_IMPORT_DB, import_db, IMPORT_DB_SCHEMA, SupportsResponse.OPTIONAL),
        (SERVICE_EXPORT_DB, export_db, EXPORT_DB_SCHEMA, SupportsResponse.ONLY),
        (
            SERVICE_EXPORT_CONTRIBUTION,
            export_contribution,
            EXPORT_CONTRIBUTION_SCHEMA,
            SupportsResponse.ONLY,
        ),
        (SERVICE_RELOAD_DB, reload_db, None, SupportsResponse.NONE),
    ]
    for name, handler, schema, supports_response in registrations:
        hass.services.async_register(
            DOMAIN, name, handler, schema=schema, supports_response=supports_response
        )


@callback
def async_unregister_services(hass: HomeAssistant) -> None:
    """Remove the services when the last entry goes away."""
    for service in list(hass.services.async_services_for_domain(DOMAIN)):
        hass.services.async_remove(DOMAIN, service)
