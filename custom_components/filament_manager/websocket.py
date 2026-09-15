"""WebSocket commands for the Lovelace card.

The card talks over the WebSocket rather than through services so that a
barcode scan can be answered immediately, instead of firing a service and
then polling a sensor for the result.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
import voluptuous as vol

from .const import DOMAIN, SOURCE_MANUAL, TAG_KIND_SLOT, TAG_KIND_SPOOL
from .coordinator import FilamentCoordinator
from .models import FilamentType
from .services import async_get_coordinator

WS_LIST = f"{DOMAIN}/list"
WS_TYPES = f"{DOMAIN}/types"
WS_SLOTS = f"{DOMAIN}/slots"
WS_SCAN = f"{DOMAIN}/scan"
WS_SUBSCRIBE = f"{DOMAIN}/subscribe"
WS_ADD_SPOOL = f"{DOMAIN}/add_spool"
WS_ADD_TYPE = f"{DOMAIN}/add_type"
WS_ASSIGN_SLOT = f"{DOMAIN}/assign_slot"
WS_CONSUME = f"{DOMAIN}/consume"
WS_CORRECT_WEIGHT = f"{DOMAIN}/correct_weight"
WS_ARCHIVE = f"{DOMAIN}/archive"
WS_LINK_TAG = f"{DOMAIN}/link_tag"
WS_CONTRIBUTION = f"{DOMAIN}/contribution"


@callback
def async_register_websocket_api(hass: HomeAssistant) -> None:
    """Register every WebSocket command."""
    for handler in (
        ws_list,
        ws_types,
        ws_slots,
        ws_scan,
        ws_subscribe,
        ws_add_spool,
        ws_add_type,
        ws_assign_slot,
        ws_consume,
        ws_correct_weight,
        ws_archive,
        ws_link_tag,
        ws_contribution,
    ):
        websocket_api.async_register_command(hass, handler)


def _coordinator(hass: HomeAssistant) -> FilamentCoordinator:
    """Return the coordinator or raise a WebSocket friendly error."""
    return async_get_coordinator(hass)


def _type_payload(
    coordinator: FilamentCoordinator, item: FilamentType
) -> dict[str, Any]:
    """Return one filament type as the card wants it."""
    return {
        **item.to_dict(),
        "vendor_name": coordinator.db.vendor_name(item.id),
        "label": coordinator.db.describe_type(item.id),
        "density": item.effective_density,
    }


def _handle_errors(func):
    """Turn integration errors into WebSocket errors instead of tracebacks."""

    def wrapper(hass: HomeAssistant, connection, msg):
        try:
            return func(hass, connection, msg)
        except HomeAssistantError as err:
            connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, str(err))
            return None

    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper


@websocket_api.websocket_command({vol.Required("type"): WS_LIST})
@callback
@_handle_errors
def ws_list(hass: HomeAssistant, connection, msg) -> None:
    """Return the whole inventory."""
    coordinator = _coordinator(hass)
    snapshot = coordinator.build_snapshot()
    connection.send_result(
        msg["id"],
        {
            **snapshot,
            "types": [
                _type_payload(coordinator, item)
                for item in coordinator.db.sorted_types(coordinator.store.recent_types)
            ],
            "vendors": [vendor.to_dict() for vendor in coordinator.db.vendors.values()],
        },
    )


@websocket_api.websocket_command({vol.Required("type"): WS_TYPES})
@callback
@_handle_errors
def ws_types(hass: HomeAssistant, connection, msg) -> None:
    """Return the master data, most recently used first."""
    coordinator = _coordinator(hass)
    connection.send_result(
        msg["id"],
        {
            "types": [
                _type_payload(coordinator, item)
                for item in coordinator.db.sorted_types(coordinator.store.recent_types)
            ],
            "vendors": [vendor.to_dict() for vendor in coordinator.db.vendors.values()],
        },
    )


@websocket_api.websocket_command({vol.Required("type"): WS_SLOTS})
@callback
@_handle_errors
def ws_slots(hass: HomeAssistant, connection, msg) -> None:
    """Return the current slot assignment."""
    coordinator = _coordinator(hass)
    connection.send_result(msg["id"], {"slots": coordinator.build_snapshot()["slots"]})


@websocket_api.websocket_command(
    {vol.Required("type"): WS_SCAN, vol.Required("code"): str}
)
@callback
@_handle_errors
def ws_scan(hass: HomeAssistant, connection, msg) -> None:
    """Look a barcode up in the master data.

    A GTIN identifies the product, never the individual roll, so a hit means
    "add another instance of this type", not "this exact spool".
    """
    coordinator = _coordinator(hass)
    matches = coordinator.db.find_by_gtin(msg["code"])
    connection.send_result(
        msg["id"],
        {
            "code": msg["code"],
            "known": bool(matches),
            "types": [_type_payload(coordinator, item) for item in matches],
            "spools": [
                coordinator.spool_payload(spool)
                for item in matches
                for spool in coordinator.store.spools_of_type(item.id)
                if not spool.archived
            ],
        },
    )


@websocket_api.websocket_command({vol.Required("type"): WS_SUBSCRIBE})
@callback
@_handle_errors
def ws_subscribe(hass: HomeAssistant, connection, msg) -> None:
    """Push inventory updates to the card."""
    coordinator = _coordinator(hass)

    @callback
    def _forward() -> None:
        connection.send_message(
            websocket_api.event_message(msg["id"], coordinator.build_snapshot())
        )

    connection.subscriptions[msg["id"]] = coordinator.async_add_listener(_forward)
    connection.send_result(msg["id"], coordinator.build_snapshot())


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_ADD_SPOOL,
        vol.Required("type_id"): str,
        vol.Optional("initial_weight"): vol.Coerce(float),
        vol.Optional("remaining_weight"): vol.Coerce(float),
        vol.Optional("purchase_date"): vol.Any(str, None),
        vol.Optional("price"): vol.Any(vol.Coerce(float), None),
        vol.Optional("location"): vol.Any(str, None),
        vol.Optional("note"): vol.Any(str, None),
        vol.Optional("count", default=1): vol.All(int, vol.Range(min=1, max=50)),
    }
)
@callback
@_handle_errors
def ws_add_spool(hass: HomeAssistant, connection, msg) -> None:
    """Add one or more rolls of a known type."""
    coordinator = _coordinator(hass)
    ids = [
        coordinator.async_add_spool(
            msg["type_id"],
            initial_weight=msg.get("initial_weight"),
            remaining_weight=msg.get("remaining_weight"),
            purchase_date=msg.get("purchase_date"),
            price=msg.get("price"),
            location=msg.get("location"),
            note=msg.get("note"),
        ).id
        for _ in range(msg["count"])
    ]
    connection.send_result(msg["id"], {"spool_ids": ids})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_ADD_TYPE,
        vol.Required("filament_type"): dict,
        vol.Optional("add_spool", default=False): bool,
    }
)
@websocket_api.async_response
async def ws_add_type(hass: HomeAssistant, connection, msg) -> None:
    """Store a new filament type in the user database."""
    try:
        coordinator = _coordinator(hass)
        payload = dict(msg["filament_type"])
        payload.setdefault("id", "")
        filament_type = await coordinator.db.async_add_type(
            FilamentType.from_dict(payload)
        )
        spool_id = None
        if msg["add_spool"]:
            spool_id = coordinator.async_add_spool(filament_type.id).id
        coordinator.async_touch()
    except HomeAssistantError as err:
        connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, str(err))
        return
    connection.send_result(
        msg["id"], {"type_id": filament_type.id, "spool_id": spool_id}
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_ASSIGN_SLOT,
        vol.Required("slot"): vol.Coerce(int),
        vol.Optional("spool_id"): vol.Any(str, None),
    }
)
@callback
@_handle_errors
def ws_assign_slot(hass: HomeAssistant, connection, msg) -> None:
    """Put a roll into a slot from the card."""
    coordinator = _coordinator(hass)
    coordinator.async_assign_slot(msg["slot"], msg.get("spool_id"), SOURCE_MANUAL)
    connection.send_result(msg["id"], {"slot": msg["slot"]})


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_CONSUME,
        vol.Required("spool_id"): str,
        vol.Required("amount"): vol.All(vol.Coerce(float), vol.Range(min=0)),
    }
)
@callback
@_handle_errors
def ws_consume(hass: HomeAssistant, connection, msg) -> None:
    """Subtract filament from the card."""
    coordinator = _coordinator(hass)
    applied = coordinator.async_consume(msg["spool_id"], msg["amount"], SOURCE_MANUAL)
    connection.send_result(msg["id"], {"consumed": applied})


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_CORRECT_WEIGHT,
        vol.Required("spool_id"): str,
        vol.Optional("gross_weight"): vol.Coerce(float),
        vol.Optional("remaining_weight"): vol.Coerce(float),
    }
)
@callback
@_handle_errors
def ws_correct_weight(hass: HomeAssistant, connection, msg) -> None:
    """Correct a roll after re-weighing it."""
    coordinator = _coordinator(hass)
    spool = coordinator.async_correct_weight(
        msg["spool_id"],
        gross_weight=msg.get("gross_weight"),
        remaining_weight=msg.get("remaining_weight"),
    )
    connection.send_result(msg["id"], {"remaining_weight": spool.remaining_weight})


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_ARCHIVE,
        vol.Required("spool_id"): str,
        vol.Optional("archived", default=True): bool,
    }
)
@callback
@_handle_errors
def ws_archive(hass: HomeAssistant, connection, msg) -> None:
    """Archive or restore a roll."""
    coordinator = _coordinator(hass)
    spool = coordinator.async_archive_spool(msg["spool_id"], msg["archived"])
    connection.send_result(msg["id"], {"archived": spool.archived})


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_LINK_TAG,
        vol.Required("tag_id"): str,
        vol.Optional("spool_id"): str,
        vol.Optional("slot"): vol.Coerce(int),
    }
)
@callback
@_handle_errors
def ws_link_tag(hass: HomeAssistant, connection, msg) -> None:
    """Link an NFC tag to a roll or a slot."""
    coordinator = _coordinator(hass)
    if spool_id := msg.get("spool_id"):
        coordinator.store.link_tag(msg["tag_id"], TAG_KIND_SPOOL, spool_id)
    elif (slot := msg.get("slot")) is not None:
        coordinator.store.link_tag(msg["tag_id"], TAG_KIND_SLOT, int(slot))
    else:
        connection.send_error(
            msg["id"], websocket_api.ERR_INVALID_FORMAT, "spool_id or slot is required"
        )
        return
    connection.send_result(msg["id"], {"tag_id": msg["tag_id"]})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): WS_CONTRIBUTION})
@callback
@_handle_errors
def ws_contribution(hass: HomeAssistant, connection, msg) -> None:
    """Return the user's own types as a pull request ready snippet."""
    coordinator = _coordinator(hass)
    connection.send_result(
        msg["id"], {"snippet": coordinator.db.contribution_snippet()}
    )
