"""Coordinator tying the store, the master data and the printer together."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.issue_registry import (
    IssueSeverity,
    async_create_issue,
    async_delete_issue,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    ATTR_AMOUNT,
    ATTR_SLOT,
    ATTR_SOURCE,
    ATTR_SPOOL_ID,
    CONF_COLOR_TOLERANCE,
    CONF_CURRENCY,
    CONF_LOW_STOCK_THRESHOLD,
    CONF_SLOT_COUNT,
    DEFAULT_COLOR_TOLERANCE,
    DEFAULT_CURRENCY,
    DEFAULT_LOW_STOCK_THRESHOLD,
    DEFAULT_SLOT_COUNT,
    DOMAIN,
    EVENT_SPOOL_ASSIGNED,
    EVENT_SPOOL_CONSUMED,
    EVENT_SPOOL_EMPTY,
    ISSUE_OVERDRAW,
    SIGNAL_SPOOLS_CHANGED,
    SOURCE_MANUAL,
)
from .db import FilamentDatabase
from .models import FilamentType, Spool, new_id, utcnow_iso
from .store import SpoolStore

if TYPE_CHECKING:
    from .consumption import ConsumptionTracker
    from .notifications import Notifier
    from .resolver import SpoolResolver
    from .shopping import ShoppingList

_LOGGER = logging.getLogger(__name__)

type FilamentConfigEntry = ConfigEntry[FilamentCoordinator]


class FilamentCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Owns the inventory and publishes derived snapshots."""

    config_entry: FilamentConfigEntry

    def __init__(self, hass: HomeAssistant, entry: FilamentConfigEntry) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            # Push based: the printer and the store drive updates, there is
            # nothing to poll.
            update_interval=None,
        )
        self.store = SpoolStore(hass)
        self.db = FilamentDatabase(hass)
        # Wired up by ``async_setup_entry`` once the helpers exist.
        self.resolver: SpoolResolver | None = None
        self.tracker: ConsumptionTracker | None = None
        self.notifier: Notifier | None = None
        self.shopping: ShoppingList | None = None
        self._known_spools: set[str] = set()

    # -- lifecycle ---------------------------------------------------------

    async def async_setup(self) -> None:
        """Load persisted data and build the first snapshot."""
        await self.store.async_load()
        await self.db.async_load()
        self.store.async_add_listener(self._handle_store_change)
        self._known_spools = {spool.id for spool in self.store.active_spools()}
        self.async_set_updated_data(self.build_snapshot())

    async def _async_update_data(self) -> dict[str, Any]:
        """Return the current snapshot (no I/O — the store is the truth)."""
        return self.build_snapshot()

    @callback
    def _handle_store_change(self) -> None:
        """React to any mutation of the inventory."""
        self.async_set_updated_data(self.build_snapshot())
        current = {spool.id for spool in self.store.active_spools()}
        if current != self._known_spools:
            self._known_spools = current
            async_dispatcher_send(self.hass, SIGNAL_SPOOLS_CHANGED)
            self.config_entry.async_create_task(
                self.hass, self._async_prune_devices(), "filament_manager_prune"
            )

    # -- options -----------------------------------------------------------

    @property
    def options(self) -> dict[str, Any]:
        """Return the entry options."""
        return dict(self.config_entry.options)

    @property
    def slot_count(self) -> int:
        """Return how many AMS slots are tracked."""
        return int(self.options.get(CONF_SLOT_COUNT, DEFAULT_SLOT_COUNT))

    @property
    def low_stock_threshold(self) -> float:
        """Return the default low stock threshold in grams."""
        return float(
            self.options.get(CONF_LOW_STOCK_THRESHOLD, DEFAULT_LOW_STOCK_THRESHOLD)
        )

    @property
    def color_tolerance(self) -> float:
        """Return the colour distance that still counts as a match."""
        return float(self.options.get(CONF_COLOR_TOLERANCE, DEFAULT_COLOR_TOLERANCE))

    @property
    def currency(self) -> str:
        """Return the currency used for the inventory value."""
        return str(self.options.get(CONF_CURRENCY, DEFAULT_CURRENCY))

    # -- derived data ------------------------------------------------------

    @callback
    def type_of(self, spool: Spool) -> FilamentType | None:
        """Return the filament type of a spool."""
        return self.db.get_type(spool.type_id)

    @callback
    def spool_name(self, spool: Spool) -> str:
        """Return the display name of a spool."""
        label = self.db.describe_type(spool.type_id)
        if spool.location:
            return f"{label} · {spool.location}"
        return label

    @callback
    def spool_weight_for(self, spool: Spool) -> float:
        """Return what this roll's empty spool weighs.

        A refill has no spool of its own and ends up on one the user already
        owned, so the per-roll value wins over the type's.
        """
        if spool.spool_weight is not None:
            return spool.spool_weight
        filament_type = self.type_of(spool)
        return filament_type.spool_weight if filament_type else 0.0

    @callback
    def threshold_for(self, spool: Spool) -> float:
        """Return the low stock threshold that applies to a spool."""
        if spool.low_stock_threshold is not None:
            return spool.low_stock_threshold
        return self.low_stock_threshold

    @callback
    def spool_payload(self, spool: Spool) -> dict[str, Any]:
        """Return one spool enriched with its type information."""
        filament_type = self.type_of(spool)
        remaining_length = (
            round(filament_type.weight_to_length(spool.remaining_weight), 1)
            if filament_type
            else None
        )
        return {
            "id": spool.id,
            "type_id": spool.type_id,
            "name": self.spool_name(spool),
            "type_name": filament_type.name if filament_type else spool.type_id,
            "vendor": self.db.vendor_name(spool.type_id),
            "material": filament_type.material if filament_type else None,
            "color_name": filament_type.color_name if filament_type else None,
            "color_hex": filament_type.color_hex if filament_type else None,
            "diameter": filament_type.diameter if filament_type else None,
            "spool_weight": self.spool_weight_for(spool),
            "remaining_weight": spool.remaining_weight,
            "initial_weight": spool.initial_weight,
            "remaining_percent": spool.remaining_percent,
            "remaining_length": remaining_length,
            "total_consumed": spool.total_consumed,
            "price": spool.price,
            "value": spool.value(filament_type),
            "location": spool.location,
            "purchase_date": spool.purchase_date,
            "archived": spool.archived,
            "slot": self.store.slot_of(spool.id),
            "assignment_source": spool.assignment_source,
            "assignment_time": spool.assignment_time,
            "nfc_tag_id": spool.nfc_tag_id,
            "rfid_uids": list(spool.rfid_uids),
            "low_stock": spool.remaining_weight <= self.threshold_for(spool),
            "low_stock_threshold": self.threshold_for(spool),
            "note": spool.note,
            "first_used": spool.first_used,
            "last_used": spool.last_used,
        }

    @callback
    def build_snapshot(self) -> dict[str, Any]:
        """Return the full state as handed to entities and the card."""
        spools = [
            self.spool_payload(spool)
            for spool in sorted(
                self.store.spools.values(), key=lambda item: self.spool_name(item)
            )
        ]
        active = [spool for spool in spools if not spool["archived"]]
        slots = []
        for index in range(1, self.slot_count + 1):
            state = self.store.slots.get(index)
            spool_id = state.spool_id if state else None
            slots.append(
                {
                    "slot": index,
                    "spool_id": spool_id,
                    "source": state.source if state else None,
                    "since": state.since if state else None,
                    "reported_material": state.reported_material if state else None,
                    "reported_color": state.reported_color if state else None,
                    "reported_uid": state.reported_uid if state else None,
                    "spool": next(
                        (item for item in spools if item["id"] == spool_id), None
                    ),
                }
            )
        total_value = round(
            sum(item["value"] or 0 for item in active),
            2,
        )
        return {
            "spools": spools,
            "slots": slots,
            "stats": {
                "spool_count": len(active),
                "archived_count": len(spools) - len(active),
                "total_remaining": round(
                    sum(item["remaining_weight"] for item in active), 1
                ),
                "total_value": total_value,
                "currency": self.currency,
                "low_stock_count": sum(1 for item in active if item["low_stock"]),
            },
            "slot_count": self.slot_count,
        }

    # -- inventory operations ---------------------------------------------

    @callback
    def async_add_spool(
        self,
        type_id: str,
        *,
        remaining_weight: float | None = None,
        initial_weight: float | None = None,
        purchase_date: str | None = None,
        price: float | None = None,
        location: str | None = None,
        note: str | None = None,
        low_stock_threshold: float | None = None,
    ) -> Spool:
        """Add a roll to the inventory."""
        filament_type = self.db.get_type(type_id)
        if filament_type is None:
            raise HomeAssistantError(f"Unknown filament type: {type_id}")
        initial = (
            initial_weight if initial_weight is not None else filament_type.net_weight
        )
        remaining = remaining_weight if remaining_weight is not None else initial
        spool = Spool(
            id=new_id(),
            type_id=type_id,
            remaining_weight=remaining,
            initial_weight=initial,
            purchase_date=purchase_date,
            price=price,
            location=location,
            note=note,
            low_stock_threshold=low_stock_threshold,
        )
        return self.store.add_spool(spool)

    @callback
    def async_duplicate_spool(self, spool_id: str) -> Spool:
        """Clone a roll — the one-click "I bought another one" path."""
        source = self.store.get_spool(spool_id)
        if source is None:
            raise HomeAssistantError(f"Unknown spool: {spool_id}")
        filament_type = self.type_of(source)
        initial = filament_type.net_weight if filament_type else source.initial_weight
        return self.async_add_spool(
            source.type_id,
            initial_weight=initial,
            price=source.price,
            location=source.location,
            low_stock_threshold=source.low_stock_threshold,
        )

    @callback
    def async_consume(
        self,
        spool_id: str,
        grams: float,
        source: str = SOURCE_MANUAL,
    ) -> float:
        """Subtract filament from a roll, guarding against overdraw."""
        spool = self.store.get_spool(spool_id)
        if spool is None:
            raise HomeAssistantError(f"Unknown spool: {spool_id}")
        if grams < 0:
            raise HomeAssistantError("Consumption must not be negative")
        applied, overdraw = self.store.consume(spool_id, grams)
        self.hass.bus.async_fire(
            EVENT_SPOOL_CONSUMED,
            {
                ATTR_SPOOL_ID: spool_id,
                ATTR_AMOUNT: applied,
                ATTR_SOURCE: source,
                "remaining_weight": spool.remaining_weight,
            },
        )
        if overdraw:
            self._async_raise_overdraw(spool, grams, overdraw)
        if spool.is_empty:
            self.hass.bus.async_fire(EVENT_SPOOL_EMPTY, {ATTR_SPOOL_ID: spool_id})
        return applied

    @callback
    def _async_raise_overdraw(
        self, spool: Spool, requested: float, overdraw: float
    ) -> None:
        """Report an implausible deduction instead of going negative."""
        _LOGGER.warning(
            "Consumption of %.1f g for %s exceeds the remaining %.1f g by %.1f g",
            requested,
            self.spool_name(spool),
            spool.remaining_weight,
            overdraw,
        )
        async_create_issue(
            self.hass,
            DOMAIN,
            f"{ISSUE_OVERDRAW}_{spool.id}",
            is_fixable=False,
            severity=IssueSeverity.WARNING,
            translation_key=ISSUE_OVERDRAW,
            translation_placeholders={
                "spool": self.spool_name(spool),
                "requested": f"{requested:.1f}",
                "overdraw": f"{overdraw:.1f}",
            },
        )

    @callback
    def async_correct_weight(
        self,
        spool_id: str,
        *,
        gross_weight: float | None = None,
        remaining_weight: float | None = None,
    ) -> Spool:
        """Correct the remaining weight after putting the roll on a scale."""
        spool = self.store.get_spool(spool_id)
        if spool is None:
            raise HomeAssistantError(f"Unknown spool: {spool_id}")
        if gross_weight is not None:
            empty = self.spool_weight_for(spool)
            if not empty:
                _LOGGER.warning(
                    "No empty spool weight is known for %s, so the gross "
                    "weight is used as-is",
                    self.spool_name(spool),
                )
            remaining_weight = max(0.0, gross_weight - empty)
        if remaining_weight is None:
            raise HomeAssistantError(
                "Either gross_weight or remaining_weight is required"
            )
        async_delete_issue(self.hass, DOMAIN, f"{ISSUE_OVERDRAW}_{spool_id}")
        corrected = self.store.set_remaining(spool_id, remaining_weight)
        assert corrected is not None
        return corrected

    @callback
    def async_assign_slot(
        self,
        slot: int,
        spool_id: str | None,
        source: str = SOURCE_MANUAL,
        **reported: Any,
    ) -> None:
        """Put a roll into an AMS slot."""
        if slot < 1 or slot > self.slot_count:
            raise HomeAssistantError(
                f"Slot {slot} is outside the configured range 1-{self.slot_count}"
            )
        if spool_id is not None and self.store.get_spool(spool_id) is None:
            raise HomeAssistantError(f"Unknown spool: {spool_id}")
        if spool_id is not None:
            spool = self.store.get_spool(spool_id)
            assert spool is not None
            if spool.archived:
                raise HomeAssistantError(
                    f"{self.spool_name(spool)} is archived and cannot be loaded"
                )
        self.store.assign_slot(slot, spool_id, source, **reported)
        self.hass.bus.async_fire(
            EVENT_SPOOL_ASSIGNED,
            {ATTR_SLOT: slot, ATTR_SPOOL_ID: spool_id, ATTR_SOURCE: source},
        )

    @callback
    def async_archive_spool(self, spool_id: str, archived: bool = True) -> Spool:
        """Archive or restore a roll."""
        spool = self.store.set_archived(spool_id, archived)
        if spool is None:
            raise HomeAssistantError(f"Unknown spool: {spool_id}")
        if archived:
            async_delete_issue(self.hass, DOMAIN, f"{ISSUE_OVERDRAW}_{spool_id}")
        return spool

    @callback
    def async_delete_spool(self, spool_id: str) -> None:
        """Remove a roll and its history."""
        if not self.store.remove_spool(spool_id):
            raise HomeAssistantError(f"Unknown spool: {spool_id}")
        async_delete_issue(self.hass, DOMAIN, f"{ISSUE_OVERDRAW}_{spool_id}")

    # -- registry housekeeping --------------------------------------------

    @callback
    def spool_device_identifier(self, spool_id: str) -> tuple[str, str]:
        """Return the device registry identifier of a spool."""
        return (DOMAIN, f"spool_{spool_id}")

    @property
    def hub_identifier(self) -> tuple[str, str]:
        """Return the device registry identifier of the hub device."""
        return (DOMAIN, f"hub_{self.config_entry.entry_id}")

    async def _async_prune_devices(self) -> None:
        """Drop devices and entities of archived or deleted spools.

        Archived rolls keep their data in the store; only their entities go
        away, so the recorder does not keep writing history for a roll that
        is finished.
        """
        device_reg = dr.async_get(self.hass)
        entity_reg = er.async_get(self.hass)
        wanted = {
            self.spool_device_identifier(spool.id)
            for spool in self.store.active_spools()
        } | {self.hub_identifier}
        for device in dr.async_entries_for_config_entry(
            device_reg, self.config_entry.entry_id
        ):
            if device.identifiers & wanted:
                continue
            for entity in er.async_entries_for_device(
                entity_reg, device.id, include_disabled_entities=True
            ):
                entity_reg.async_remove(entity.entity_id)
            device_reg.async_update_device(
                device.id, remove_config_entry_id=self.config_entry.entry_id
            )

    @callback
    def async_touch(self) -> None:
        """Force a snapshot refresh (used after master data changes)."""
        self.async_set_updated_data(self.build_snapshot())


__all__ = ["FilamentConfigEntry", "FilamentCoordinator", "utcnow_iso"]
