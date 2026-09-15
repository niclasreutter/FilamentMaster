"""Config and options flow.

Home Assistant renders these forms itself, which is exactly why they are the
primary way to maintain the inventory: they work in any browser and in the
companion app, need no camera permission, and no frontend update can break
them.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import (
    BooleanSelector,
    ColorRGBSelector,
    DateSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .color import rgb_to_hex
from .const import (
    CONF_ACTIVE_TRAY_ENTITY,
    CONF_AUTO_ASSIGN_HEURISTIC,
    CONF_AUTO_ASSIGN_RFID,
    CONF_AUTO_CONSUME,
    CONF_COLOR_TOLERANCE,
    CONF_CURRENCY,
    CONF_LOW_STOCK_THRESHOLD,
    CONF_NOTIFY_SERVICE,
    CONF_PRINT_STATE_ENTITY,
    CONF_PRINT_WEIGHT_ENTITY,
    CONF_SHOPPING_LIST_ENTITY,
    CONF_SLOT_COUNT,
    CONF_SPLIT_STRATEGY,
    CONF_TRAY_ENTITIES,
    DEFAULT_COLOR_TOLERANCE,
    DEFAULT_CURRENCY,
    DEFAULT_LOW_STOCK_THRESHOLD,
    DEFAULT_SLOT_COUNT,
    DEFAULT_SPLIT_STRATEGY,
    DIAMETERS,
    DOMAIN,
    MATERIAL_DENSITIES,
    MAX_SLOT_COUNT,
    MIN_SLOT_COUNT,
    SPLIT_STRATEGIES,
    SPOOL_TYPES,
    TAG_KIND_SLOT,
    TAG_KIND_SPOOL,
)
from .models import FilamentType, Vendor
from .resolver import async_discover_tray_entities

_LOGGER = logging.getLogger(__name__)

NEW_VENDOR = "__new__"


def _number(
    minimum: float, maximum: float, step: float = 1, unit: str | None = None
) -> NumberSelector:
    """Return a box style number selector."""
    config: dict[str, Any] = {
        "min": minimum,
        "max": maximum,
        "step": step,
        "mode": NumberSelectorMode.BOX,
    }
    if unit is not None:
        config["unit_of_measurement"] = unit
    return NumberSelector(NumberSelectorConfig(**config))


def _select(
    options: list[SelectOptionDict] | list[str],
    *,
    custom_value: bool = False,
    sort: bool = False,
) -> SelectSelector:
    """Return a dropdown selector."""
    return SelectSelector(
        SelectSelectorConfig(
            options=options,  # type: ignore[arg-type]
            mode=SelectSelectorMode.DROPDOWN,
            custom_value=custom_value,
            sort=sort,
        )
    )


class FilamentManagerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = 1
    MINOR_VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set the integration up.

        One entry is enough: the inventory is global, and the printer
        specific entities are configured in the options.
        """
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        discovered = async_discover_tray_entities(self.hass)
        if user_input is not None:
            options: dict[str, Any] = {
                CONF_SLOT_COUNT: int(user_input[CONF_SLOT_COUNT]),
                CONF_LOW_STOCK_THRESHOLD: float(user_input[CONF_LOW_STOCK_THRESHOLD]),
                CONF_COLOR_TOLERANCE: DEFAULT_COLOR_TOLERANCE,
                CONF_CURRENCY: user_input[CONF_CURRENCY],
                CONF_AUTO_ASSIGN_RFID: True,
                CONF_AUTO_ASSIGN_HEURISTIC: True,
                CONF_AUTO_CONSUME: True,
                CONF_SPLIT_STRATEGY: DEFAULT_SPLIT_STRATEGY,
                CONF_TRAY_ENTITIES: {
                    str(slot): entity_id
                    for slot, entity_id in discovered.items()
                    if slot <= int(user_input[CONF_SLOT_COUNT])
                },
            }
            return self.async_create_entry(
                title="Filament Manager", data={}, options=options
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SLOT_COUNT, default=DEFAULT_SLOT_COUNT): _number(
                        MIN_SLOT_COUNT, MAX_SLOT_COUNT
                    ),
                    vol.Required(
                        CONF_LOW_STOCK_THRESHOLD,
                        default=DEFAULT_LOW_STOCK_THRESHOLD,
                    ): _number(0, 2000, 10, "g"),
                    vol.Required(
                        CONF_CURRENCY, default=DEFAULT_CURRENCY
                    ): TextSelector(),
                }
            ),
            description_placeholders={
                "discovered": (
                    ", ".join(
                        f"{slot}: {entity_id}" for slot, entity_id in discovered.items()
                    )
                    or "none"
                )
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> FilamentManagerOptionsFlow:
        """Return the options flow."""
        return FilamentManagerOptionsFlow()


class FilamentManagerOptionsFlow(OptionsFlow):
    """Maintain the inventory and the settings."""

    def __init__(self) -> None:
        """Initialise the flow."""
        self._selected_spool: str | None = None
        self._pending_vendor: str | None = None

    # -- helpers -----------------------------------------------------------

    @property
    def coordinator(self):
        """Return the running coordinator."""
        return self.config_entry.runtime_data

    @callback
    def _options(self) -> dict[str, Any]:
        """Return a mutable copy of the current options."""
        return dict(self.config_entry.options)

    @callback
    def _spool_options(self, include_archived: bool = False) -> list[SelectOptionDict]:
        """Return the roll picker options."""
        coordinator = self.coordinator
        spools = (
            list(coordinator.store.spools.values())
            if include_archived
            else coordinator.store.active_spools()
        )
        return [
            SelectOptionDict(
                value=spool.id,
                label=(
                    f"{coordinator.spool_name(spool)} — "
                    f"{spool.remaining_weight:.0f} g"
                    + (" (archived)" if spool.archived else "")
                ),
            )
            for spool in sorted(spools, key=coordinator.spool_name)
        ]

    @callback
    def _type_options(self) -> list[SelectOptionDict]:
        """Return the type picker, most recently used first."""
        coordinator = self.coordinator
        return [
            SelectOptionDict(value=item.id, label=coordinator.db.describe_type(item.id))
            for item in coordinator.db.sorted_types(coordinator.store.recent_types)
        ]

    @callback
    def _slot_options(self, include_empty: bool = True) -> list[SelectOptionDict]:
        """Return the slot picker."""
        options = [
            SelectOptionDict(value=str(slot), label=f"Slot {slot}")
            for slot in range(1, self.coordinator.slot_count + 1)
        ]
        if include_empty:
            options.insert(0, SelectOptionDict(value="", label="— empty —"))
        return options

    @callback
    def _finish(self) -> ConfigFlowResult:
        """Close the flow without touching the options.

        Unchanged options do not fire the update listener, so managing the
        inventory never reloads the integration.
        """
        return self.async_create_entry(title="", data=self._options())

    # -- menu --------------------------------------------------------------

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the main menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "add_spool",
                "manage_spool",
                "add_type",
                "add_vendor",
                "printer",
                "settings",
                "tools",
                "done",
            ],
        )

    async def async_step_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Close the dialog."""
        return self._finish()

    # -- spools ------------------------------------------------------------

    async def async_step_add_spool(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add one or more rolls of a known type.

        The frequent case, so it asks for as little as possible: pick a type,
        then purchase date, price and where it lives.
        """
        types = self._type_options()
        if not types:
            return self.async_show_form(
                step_id="add_spool",
                data_schema=vol.Schema({}),
                errors={"base": "no_types"},
            )
        if user_input is not None:
            weight = user_input.get("initial_weight")
            for _ in range(int(user_input.get("count", 1))):
                self.coordinator.async_add_spool(
                    user_input["type_id"],
                    initial_weight=float(weight) if weight else None,
                    purchase_date=user_input.get("purchase_date"),
                    price=(
                        float(user_input["price"])
                        if user_input.get("price") not in (None, "")
                        else None
                    ),
                    location=user_input.get("location") or None,
                    low_stock_threshold=(
                        float(user_input["low_stock_threshold"])
                        if user_input.get("low_stock_threshold") not in (None, "")
                        else None
                    ),
                )
            return await self.async_step_init()

        return self.async_show_form(
            step_id="add_spool",
            data_schema=vol.Schema(
                {
                    vol.Required("type_id", default=types[0]["value"]): _select(types),
                    vol.Optional("count", default=1): _number(1, 50),
                    vol.Optional("purchase_date"): DateSelector(),
                    vol.Optional("price"): _number(0, 1000, 0.01),
                    vol.Optional("location"): TextSelector(),
                    vol.Optional("initial_weight"): _number(0, 10000, 1, "g"),
                    vol.Optional("low_stock_threshold"): _number(0, 2000, 10, "g"),
                }
            ),
        )

    async def async_step_manage_spool(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a roll to work on."""
        options = self._spool_options(include_archived=True)
        if not options:
            return self.async_show_form(
                step_id="manage_spool",
                data_schema=vol.Schema({}),
                errors={"base": "no_spools"},
            )
        if user_input is not None:
            self._selected_spool = user_input["spool_id"]
            return await self.async_step_spool_actions()
        return self.async_show_form(
            step_id="manage_spool",
            data_schema=vol.Schema(
                {vol.Required("spool_id"): _select(options, sort=False)}
            ),
        )

    async def async_step_spool_actions(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer what can be done with the selected roll."""
        spool = self.coordinator.store.get_spool(self._selected_spool or "")
        if spool is None:
            return await self.async_step_init()
        return self.async_show_menu(
            step_id="spool_actions",
            menu_options=[
                "edit_spool",
                "assign_spool",
                "link_spool_tag",
                "duplicate_spool",
                "archive_spool",
                "delete_spool",
                "init",
            ],
            description_placeholders={
                "spool": self.coordinator.spool_name(spool),
                "remaining": f"{spool.remaining_weight:.0f}",
                "state": "archived" if spool.archived else "in stock",
            },
        )

    async def async_step_edit_spool(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the bookkeeping fields of a roll."""
        spool = self.coordinator.store.get_spool(self._selected_spool or "")
        if spool is None:
            return await self.async_step_init()
        if user_input is not None:
            self.coordinator.store.update_spool(
                spool.id,
                location=user_input.get("location") or None,
                price=(
                    float(user_input["price"])
                    if user_input.get("price") not in (None, "")
                    else None
                ),
                purchase_date=user_input.get("purchase_date") or None,
                note=user_input.get("note") or None,
                remaining_weight=float(user_input["remaining_weight"]),
                initial_weight=float(user_input["initial_weight"]),
                low_stock_threshold=(
                    float(user_input["low_stock_threshold"])
                    if user_input.get("low_stock_threshold") not in (None, "")
                    else None
                ),
            )
            return await self.async_step_init()

        schema: dict[Any, Any] = {
            vol.Required("remaining_weight", default=spool.remaining_weight): _number(
                0, 10000, 0.1, "g"
            ),
            vol.Required("initial_weight", default=spool.initial_weight): _number(
                0, 10000, 1, "g"
            ),
            vol.Optional(
                "location", description={"suggested_value": spool.location}
            ): TextSelector(),
            vol.Optional(
                "price", description={"suggested_value": spool.price}
            ): _number(0, 1000, 0.01),
            vol.Optional(
                "purchase_date",
                description={"suggested_value": spool.purchase_date},
            ): DateSelector(),
            vol.Optional(
                "low_stock_threshold",
                description={"suggested_value": spool.low_stock_threshold},
            ): _number(0, 2000, 10, "g"),
            vol.Optional(
                "note", description={"suggested_value": spool.note}
            ): TextSelector(TextSelectorConfig(multiline=True)),
        }
        return self.async_show_form(
            step_id="edit_spool",
            data_schema=vol.Schema(schema),
            description_placeholders={"spool": self.coordinator.spool_name(spool)},
        )

    async def async_step_assign_spool(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Put the roll into a slot by hand."""
        spool = self.coordinator.store.get_spool(self._selected_spool or "")
        if spool is None:
            return await self.async_step_init()
        if user_input is not None:
            raw = user_input.get("slot") or ""
            current = self.coordinator.store.slot_of(spool.id)
            if raw == "" and current is not None:
                self.coordinator.async_assign_slot(current, None)
            elif raw != "":
                self.coordinator.async_assign_slot(int(raw), spool.id)
            return await self.async_step_init()

        current = self.coordinator.store.slot_of(spool.id)
        return self.async_show_form(
            step_id="assign_spool",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "slot", default=str(current) if current else ""
                    ): _select(self._slot_options()),
                }
            ),
            description_placeholders={"spool": self.coordinator.spool_name(spool)},
        )

    async def async_step_link_spool_tag(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Bind an NFC sticker or a Bambu RFID UID to the roll."""
        spool = self.coordinator.store.get_spool(self._selected_spool or "")
        if spool is None:
            return await self.async_step_init()
        errors: dict[str, str] = {}
        if user_input is not None:
            tag_id = (user_input.get("tag_id") or "").strip()
            rfid_uid = (user_input.get("rfid_uid") or "").strip()
            if not tag_id and not rfid_uid:
                errors["base"] = "tag_required"
            else:
                if tag_id:
                    self.coordinator.store.link_tag(tag_id, TAG_KIND_SPOOL, spool.id)
                if rfid_uid:
                    self.coordinator.store.learn_rfid(spool.id, rfid_uid)
                return await self.async_step_init()

        return self.async_show_form(
            step_id="link_spool_tag",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "tag_id", description={"suggested_value": spool.nfc_tag_id}
                    ): TextSelector(),
                    vol.Optional("rfid_uid"): TextSelector(),
                }
            ),
            errors=errors,
            description_placeholders={
                "spool": self.coordinator.spool_name(spool),
                "uids": ", ".join(spool.rfid_uids) or "none",
            },
        )

    async def async_step_duplicate_spool(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add another roll of the same type."""
        if self._selected_spool:
            self.coordinator.async_duplicate_spool(self._selected_spool)
        return await self.async_step_init()

    async def async_step_archive_spool(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Archive the roll, or bring it back."""
        spool = self.coordinator.store.get_spool(self._selected_spool or "")
        if spool is not None:
            self.coordinator.async_archive_spool(spool.id, not spool.archived)
        return await self.async_step_init()

    async def async_step_delete_spool(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Delete the roll for good."""
        spool = self.coordinator.store.get_spool(self._selected_spool or "")
        if spool is None:
            return await self.async_step_init()
        if user_input is not None:
            if user_input.get("confirm"):
                self.coordinator.async_delete_spool(spool.id)
            return await self.async_step_init()
        return self.async_show_form(
            step_id="delete_spool",
            data_schema=vol.Schema(
                {vol.Required("confirm", default=False): BooleanSelector()}
            ),
            description_placeholders={"spool": self.coordinator.spool_name(spool)},
        )

    # -- master data -------------------------------------------------------

    async def async_step_add_vendor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a manufacturer to the user database."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                vendor = await self.coordinator.db.async_add_vendor(
                    Vendor(
                        id="",
                        name=user_input["name"].strip(),
                        website=user_input.get("website") or None,
                    )
                )
            except HomeAssistantError as err:
                _LOGGER.error("Could not save the vendor: %s", err)
                errors["base"] = "write_failed"
            else:
                self.coordinator.async_touch()
                self._pending_vendor = vendor.id
                return await self.async_step_add_type()
        return self.async_show_form(
            step_id="add_vendor",
            data_schema=vol.Schema(
                {
                    vol.Required("name"): TextSelector(),
                    vol.Optional("website"): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.URL)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_add_type(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a filament type — the rare form with all the fields.

        It lands in ``/config/filament_db.json``, which is what the contribute
        export later turns into a pull request snippet.
        """
        coordinator = self.coordinator
        vendors = [
            SelectOptionDict(value=vendor.id, label=vendor.name)
            for vendor in sorted(
                coordinator.db.vendors.values(), key=lambda item: item.sort_key
            )
        ]
        vendors.append(SelectOptionDict(value=NEW_VENDOR, label="+ new manufacturer"))
        errors: dict[str, str] = {}

        if user_input is not None:
            if user_input["vendor_id"] == NEW_VENDOR:
                return await self.async_step_add_vendor()
            color = user_input.get("color")
            payload = {
                "id": "",
                "vendor_id": user_input["vendor_id"],
                "name": user_input["name"].strip(),
                "material": user_input["material"].strip().upper(),
                "color_name": user_input.get("color_name") or None,
                "color_hex": rgb_to_hex(tuple(color)) if color else None,
                "diameter": float(user_input["diameter"]),
                "density": (
                    float(user_input["density"])
                    if user_input.get("density") not in (None, "")
                    else None
                ),
                "net_weight": float(user_input["net_weight"]),
                "spool_weight": float(user_input["spool_weight"]),
                "spool_type": user_input["spool_type"],
                "nozzle_temp_min": _optional_int(user_input.get("nozzle_temp_min")),
                "nozzle_temp_max": _optional_int(user_input.get("nozzle_temp_max")),
                "bed_temp": _optional_int(user_input.get("bed_temp")),
                "gtin": [
                    code.strip()
                    for code in (user_input.get("gtin") or "").split(",")
                    if code.strip()
                ],
            }
            try:
                filament_type = await coordinator.db.async_add_type(
                    FilamentType.from_dict(payload)
                )
            except HomeAssistantError as err:
                _LOGGER.error("Could not save the filament type: %s", err)
                errors["base"] = "write_failed"
            else:
                coordinator.async_touch()
                if user_input.get("add_spool"):
                    coordinator.async_add_spool(filament_type.id)
                return await self.async_step_init()

        materials = sorted(MATERIAL_DENSITIES)
        return self.async_show_form(
            step_id="add_type",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "vendor_id",
                        default=self._pending_vendor or vendors[0]["value"],
                    ): _select(vendors),
                    vol.Required("name"): TextSelector(),
                    vol.Required("material", default="PLA"): _select(
                        materials, custom_value=True
                    ),
                    vol.Optional("color_name"): TextSelector(),
                    vol.Optional("color"): ColorRGBSelector(),
                    vol.Required("diameter", default="1.75"): _select(
                        [str(value) for value in DIAMETERS]
                    ),
                    vol.Optional("density"): _number(0.5, 3, 0.01),
                    vol.Required("net_weight", default=1000): _number(0, 10000, 1, "g"),
                    vol.Required("spool_weight", default=0): _number(0, 2000, 1, "g"),
                    vol.Required("spool_type", default="plastic"): _select(
                        list(SPOOL_TYPES)
                    ),
                    vol.Optional("nozzle_temp_min"): _number(100, 500, 1, "°C"),
                    vol.Optional("nozzle_temp_max"): _number(100, 500, 1, "°C"),
                    vol.Optional("bed_temp"): _number(0, 200, 1, "°C"),
                    vol.Optional("gtin"): TextSelector(),
                    vol.Optional("add_spool", default=True): BooleanSelector(),
                }
            ),
            errors=errors,
        )

    # -- printer & settings ------------------------------------------------

    async def async_step_printer(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Point the integration at the printer's entities."""
        options = self._options()
        slot_count = self.coordinator.slot_count
        discovered = async_discover_tray_entities(self.hass)
        tray_entities: dict[str, str] = dict(options.get(CONF_TRAY_ENTITIES) or {})

        if user_input is not None:
            new_trays = {
                str(slot): user_input[f"tray_{slot}"]
                for slot in range(1, slot_count + 1)
                if user_input.get(f"tray_{slot}")
            }
            options[CONF_TRAY_ENTITIES] = new_trays
            for key in (
                CONF_PRINT_STATE_ENTITY,
                CONF_PRINT_WEIGHT_ENTITY,
                CONF_ACTIVE_TRAY_ENTITY,
            ):
                value = user_input.get(key)
                if value:
                    options[key] = value
                else:
                    options.pop(key, None)
            return self.async_create_entry(title="", data=options)

        schema: dict[Any, Any] = {}
        for slot in range(1, slot_count + 1):
            current = tray_entities.get(str(slot)) or discovered.get(slot)
            schema[
                vol.Optional(f"tray_{slot}", description={"suggested_value": current})
            ] = EntitySelector(EntitySelectorConfig(domain="sensor"))
        for key in (
            CONF_PRINT_STATE_ENTITY,
            CONF_PRINT_WEIGHT_ENTITY,
            CONF_ACTIVE_TRAY_ENTITY,
        ):
            schema[
                vol.Optional(key, description={"suggested_value": options.get(key)})
            ] = EntitySelector(EntitySelectorConfig(domain="sensor"))

        return self.async_show_form(
            step_id="printer",
            data_schema=vol.Schema(schema),
            description_placeholders={
                "discovered": (
                    ", ".join(
                        f"{slot}: {entity_id}" for slot, entity_id in discovered.items()
                    )
                    or "none"
                )
            },
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the global behaviour."""
        options = self._options()
        if user_input is not None:
            options.update(
                {
                    CONF_SLOT_COUNT: int(user_input[CONF_SLOT_COUNT]),
                    CONF_LOW_STOCK_THRESHOLD: float(
                        user_input[CONF_LOW_STOCK_THRESHOLD]
                    ),
                    CONF_COLOR_TOLERANCE: float(user_input[CONF_COLOR_TOLERANCE]),
                    CONF_CURRENCY: user_input[CONF_CURRENCY],
                    CONF_AUTO_ASSIGN_RFID: user_input[CONF_AUTO_ASSIGN_RFID],
                    CONF_AUTO_ASSIGN_HEURISTIC: user_input[CONF_AUTO_ASSIGN_HEURISTIC],
                    CONF_AUTO_CONSUME: user_input[CONF_AUTO_CONSUME],
                    CONF_SPLIT_STRATEGY: user_input[CONF_SPLIT_STRATEGY],
                }
            )
            for key in (CONF_NOTIFY_SERVICE, CONF_SHOPPING_LIST_ENTITY):
                if value := user_input.get(key):
                    options[key] = value
                else:
                    options.pop(key, None)
            return self.async_create_entry(title="", data=options)

        notify_services = [
            SelectOptionDict(value=f"notify.{service}", label=f"notify.{service}")
            for service in sorted(self.hass.services.async_services().get("notify", {}))
        ]
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SLOT_COUNT,
                        default=options.get(CONF_SLOT_COUNT, DEFAULT_SLOT_COUNT),
                    ): _number(MIN_SLOT_COUNT, MAX_SLOT_COUNT),
                    vol.Required(
                        CONF_LOW_STOCK_THRESHOLD,
                        default=options.get(
                            CONF_LOW_STOCK_THRESHOLD, DEFAULT_LOW_STOCK_THRESHOLD
                        ),
                    ): _number(0, 2000, 10, "g"),
                    vol.Required(
                        CONF_COLOR_TOLERANCE,
                        default=options.get(
                            CONF_COLOR_TOLERANCE, DEFAULT_COLOR_TOLERANCE
                        ),
                    ): _number(0, 100, 1),
                    vol.Required(
                        CONF_CURRENCY,
                        default=options.get(CONF_CURRENCY, DEFAULT_CURRENCY),
                    ): TextSelector(),
                    vol.Optional(
                        CONF_NOTIFY_SERVICE,
                        description={
                            "suggested_value": options.get(CONF_NOTIFY_SERVICE)
                        },
                    ): _select(notify_services, custom_value=True),
                    vol.Optional(
                        CONF_SHOPPING_LIST_ENTITY,
                        description={
                            "suggested_value": options.get(CONF_SHOPPING_LIST_ENTITY)
                        },
                    ): EntitySelector(EntitySelectorConfig(domain="todo")),
                    vol.Required(
                        CONF_AUTO_ASSIGN_RFID,
                        default=options.get(CONF_AUTO_ASSIGN_RFID, True),
                    ): BooleanSelector(),
                    vol.Required(
                        CONF_AUTO_ASSIGN_HEURISTIC,
                        default=options.get(CONF_AUTO_ASSIGN_HEURISTIC, True),
                    ): BooleanSelector(),
                    vol.Required(
                        CONF_AUTO_CONSUME,
                        default=options.get(CONF_AUTO_CONSUME, True),
                    ): BooleanSelector(),
                    vol.Required(
                        CONF_SPLIT_STRATEGY,
                        default=options.get(
                            CONF_SPLIT_STRATEGY, DEFAULT_SPLIT_STRATEGY
                        ),
                    ): _select(list(SPLIT_STRATEGIES)),
                }
            ),
        )

    # -- tools -------------------------------------------------------------

    async def async_step_tools(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Import, export and contribute master data."""
        return self.async_show_menu(
            step_id="tools",
            menu_options=[
                "import_db",
                "export_db",
                "contribute",
                "link_slot_tag",
                "init",
            ],
        )

    async def async_step_import_db(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Merge another filament database into the user's."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                document = json.loads(user_input["document"])
                vendors, types = await self.coordinator.db.async_import(
                    document, user_input.get("overwrite", False)
                )
            except json.JSONDecodeError:
                errors["document"] = "invalid_json"
            except HomeAssistantError as err:
                _LOGGER.error("Import failed: %s", err)
                errors["base"] = "import_failed"
            else:
                _LOGGER.info("Imported %s vendors and %s types", vendors, types)
                self.coordinator.async_touch()
                return await self.async_step_init()
        return self.async_show_form(
            step_id="import_db",
            data_schema=vol.Schema(
                {
                    vol.Required("document"): TextSelector(
                        TextSelectorConfig(multiline=True)
                    ),
                    vol.Optional("overwrite", default=False): BooleanSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_export_db(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the whole database for copying out."""
        if user_input is not None:
            return await self.async_step_init()
        document = json.dumps(
            self.coordinator.db.export(), indent=2, ensure_ascii=False
        )
        return self.async_show_form(
            step_id="export_db",
            data_schema=vol.Schema({}),
            description_placeholders={"document": _clip(document)},
        )

    async def async_step_contribute(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the user's own types as a pull request ready snippet."""
        if user_input is not None:
            return await self.async_step_init()
        return self.async_show_form(
            step_id="contribute",
            data_schema=vol.Schema({}),
            description_placeholders={
                "snippet": _clip(self.coordinator.db.contribution_snippet())
            },
        )

    async def async_step_link_slot_tag(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Bind an NFC sticker to an AMS slot for the two-sticker workflow."""
        if user_input is not None:
            self.coordinator.store.link_tag(
                user_input["tag_id"].strip(), TAG_KIND_SLOT, int(user_input["slot"])
            )
            return await self.async_step_init()
        linked = self.coordinator.store.slot_tags()
        return self.async_show_form(
            step_id="link_slot_tag",
            data_schema=vol.Schema(
                {
                    vol.Required("tag_id"): TextSelector(),
                    vol.Required("slot", default="1"): _select(
                        self._slot_options(include_empty=False)
                    ),
                }
            ),
            description_placeholders={
                "linked": (
                    ", ".join(f"{tag} → slot {slot}" for tag, slot in linked.items())
                    or "none"
                )
            },
        )


def _optional_int(value: Any) -> int | None:
    """Return ``value`` as int, or ``None`` when it is empty."""
    if value in (None, ""):
        return None
    return int(float(value))


def _clip(text: str, limit: int = 3500) -> str:
    """Keep a form description short enough to render."""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n… ({len(text) - limit} more characters)"
