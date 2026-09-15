"""Matching physical rolls to AMS slots.

This is the part that decides which spool is sitting where, in descending
order of confidence:

1. RFID — Bambu rolls carry a tag the AMS reader picks up. Certain.
2. Heuristic — material and colour reported over MQTT, matched against the
   inventory. Probable.
3. NFC — a sticker on the roll plus a manual (or sticker based) choice of
   slot. The fallback that always works.

Every assignment records where it came from, so a wrong guess can be told
apart from a deliberate choice before it starts subtracting grams from the
wrong roll.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
import re
import time
from typing import Any

from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event

from .color import color_distance, normalize_hex
from .const import (
    ATTR_SLOT,
    CONF_AUTO_ASSIGN_HEURISTIC,
    CONF_AUTO_ASSIGN_RFID,
    CONF_TRAY_ENTITIES,
    EVENT_AMBIGUOUS_MATCH,
    EVENT_UNKNOWN_TAG,
    SOURCE_HEURISTIC,
    SOURCE_NFC,
    SOURCE_RFID,
    TAG_KIND_SLOT,
    TAG_KIND_SPOOL,
    TAG_PAIR_TIMEOUT,
    TRAYS_PER_AMS,
)
from .coordinator import FilamentCoordinator
from .models import Spool, normalize_uid
from .notifications import (
    VERB_ASSIGN,
    VERB_IGNORE,
    VERB_LEARN,
    Notifier,
    build_action,
    parse_action,
)

_LOGGER = logging.getLogger(__name__)

BAMBU_PLATFORMS = ("bambu_lab", "bambulab")

# ``ams_1_tray_2`` or plain ``tray_3``; the AMS index turns into a global
# slot number so that a second AMS continues at 5.
_SLOT_PATTERN = re.compile(r"(?:ams[_\s]*(\d+)[_\s]*)?tray[_\s]*(\d+)", re.IGNORECASE)

# The same fact is called different things across integrations and versions,
# so every reading tries a list of candidates.
_UID_KEYS = (
    "tag_uid",
    "spool_serial_number",
    "rfid_tag",
    "rfid_uid",
    "tray_uuid",
    "tray_tag_uid",
    "tray_uid",
)
_MATERIAL_KEYS = ("type", "tray_type", "filament_type", "material", "tray_sub_brands")
_COLOR_KEYS = ("color", "tray_color", "colour", "color_hex")
_EMPTY_KEYS = ("empty", "tray_empty")
_DIAMETER_KEYS = ("diameter", "tray_diameter", "filament_diameter")
_REMAIN_KEYS = ("remaining", "remain", "tray_remain", "remaining_percent")

_MATERIAL_ALIASES = {
    "PLA+": "PLA",
    "PLAPLUS": "PLA",
    "PLA PLUS": "PLA",
    "PET-G": "PETG",
    "PET G": "PETG",
    "ABS+": "ABS",
    "TPU95A": "TPU",
    "TPU 95A": "TPU",
    "TPU95": "TPU",
    "NYLON": "PA",
    "PA6": "PA",
    "PA12": "PA",
}

_EMPTY_MATERIALS = {"", "EMPTY", "UNKNOWN", "NONE", "?"}


def canonical_material(value: str | None) -> str | None:
    """Return a comparable material name."""
    if not value:
        return None
    text = str(value).strip().upper()
    if text in _EMPTY_MATERIALS:
        return None
    return _MATERIAL_ALIASES.get(text, text)


def _normalize_key(key: str) -> str:
    """Return an attribute name as ``snake_case``."""
    return re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")


def _pick(attributes: dict[str, Any], keys: tuple[str, ...]) -> Any:
    """Return the first attribute matching one of ``keys``."""
    normalized = {_normalize_key(key): value for key, value in attributes.items()}
    for key in keys:
        value = normalized.get(key)
        if value not in (None, "", "unknown", "unavailable"):
            return value
    return None


@dataclass(slots=True)
class TrayReading:
    """What the printer says about one slot."""

    uid: str | None = None
    material: str | None = None
    color: str | None = None
    diameter: float | None = None
    remaining_percent: float | None = None
    empty: bool = False

    @property
    def has_content(self) -> bool:
        """Return whether the reading describes a loaded roll."""
        return not self.empty and bool(self.uid or self.material or self.color)


def read_tray(attributes: dict[str, Any], state: str | None = None) -> TrayReading:
    """Turn a tray entity's attributes into a :class:`TrayReading`.

    The Bambu integration reports the *tag* UID here, not the spool serial
    number shown in Bambu Studio — mapping consistently onto the UID is what
    keeps the two from being confused.
    """
    uid = normalize_uid(_pick(attributes, _UID_KEYS))
    material = canonical_material(_pick(attributes, _MATERIAL_KEYS))
    if material is None and state:
        material = canonical_material(state)
    color = normalize_hex(_pick(attributes, _COLOR_KEYS))
    empty_raw = _pick(attributes, _EMPTY_KEYS)
    empty = bool(empty_raw) if empty_raw is not None else False
    if not empty and material is None and uid is None and color is None:
        empty = True

    diameter = _pick(attributes, _DIAMETER_KEYS)
    remaining = _pick(attributes, _REMAIN_KEYS)
    try:
        diameter_value = float(diameter) if diameter is not None else None
    except (TypeError, ValueError):
        diameter_value = None
    try:
        remaining_value = float(remaining) if remaining is not None else None
    except (TypeError, ValueError):
        remaining_value = None
    # The AMS reports -1 when it has no reading at all.
    if remaining_value is not None and remaining_value < 0:
        remaining_value = None

    return TrayReading(
        uid=uid,
        material=material,
        color=color,
        diameter=diameter_value,
        remaining_percent=remaining_value,
        empty=empty,
    )


@callback
def async_discover_tray_entities(hass: HomeAssistant) -> dict[int, str]:
    """Find AMS tray entities exposed by the Bambu Lab integration."""
    registry = er.async_get(hass)
    found: dict[int, str] = {}
    for entry in registry.entities.values():
        if entry.platform not in BAMBU_PLATFORMS or entry.domain != "sensor":
            continue
        haystack = f"{entry.unique_id or ''} {entry.entity_id}"
        match = _SLOT_PATTERN.search(haystack)
        if not match:
            continue
        ams_index = int(match.group(1) or 1)
        tray_index = int(match.group(2))
        slot = (ams_index - 1) * TRAYS_PER_AMS + tray_index
        found.setdefault(slot, entry.entity_id)
    return dict(sorted(found.items()))


class SpoolResolver:
    """Keeps the slot assignments in sync with what the printer reports."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: FilamentCoordinator,
        notifier: Notifier,
    ) -> None:
        """Initialise the resolver."""
        self.hass = hass
        self.coordinator = coordinator
        self.notifier = notifier
        self._unsubscribes: list[Callable[[], None]] = []
        self._tray_unsub: Callable[[], None] | None = None
        self._tray_map: dict[str, int] = {}
        self._pending_spool: tuple[str, float] | None = None
        self._pending_slot: tuple[int, float] | None = None

    # -- lifecycle ---------------------------------------------------------

    async def async_setup(self) -> None:
        """Start listening."""
        self.async_refresh_tray_tracking()
        self._unsubscribes.append(
            self.hass.bus.async_listen("tag_scanned", self._handle_tag_scanned)
        )
        self._unsubscribes.append(
            self.hass.bus.async_listen(
                "mobile_app_notification_action", self._handle_notification_action
            )
        )

    @callback
    def async_shutdown(self) -> None:
        """Stop listening."""
        if self._tray_unsub:
            self._tray_unsub()
            self._tray_unsub = None
        for unsubscribe in self._unsubscribes:
            unsubscribe()
        self._unsubscribes.clear()

    @callback
    def async_refresh_tray_tracking(self) -> None:
        """(Re-)subscribe to the configured or discovered tray entities."""
        if self._tray_unsub:
            self._tray_unsub()
            self._tray_unsub = None
        self._tray_map = self._build_tray_map()
        if not self._tray_map:
            _LOGGER.debug("No AMS tray entities configured or discovered")
            return
        _LOGGER.debug("Tracking AMS tray entities: %s", self._tray_map)
        self._tray_unsub = async_track_state_change_event(
            self.hass, list(self._tray_map), self._handle_tray_change
        )
        for entity_id in self._tray_map:
            if state := self.hass.states.get(entity_id):
                self._process_tray(entity_id, state.state, dict(state.attributes))

    @callback
    def _build_tray_map(self) -> dict[str, int]:
        """Return ``entity_id -> slot`` from options, falling back to discovery."""
        configured = self.coordinator.options.get(CONF_TRAY_ENTITIES) or {}
        mapping: dict[str, int] = {}
        for slot, entity_id in configured.items():
            if entity_id:
                mapping[str(entity_id)] = int(slot)
        if mapping:
            return mapping
        return {
            entity_id: slot
            for slot, entity_id in async_discover_tray_entities(self.hass).items()
            if slot <= self.coordinator.slot_count
        }

    # -- tray handling -----------------------------------------------------

    @callback
    def _handle_tray_change(self, event: Event[EventStateChangedData]) -> None:
        """React to a tray entity update."""
        new_state = event.data["new_state"]
        if new_state is None or new_state.state in ("unavailable", "unknown"):
            return
        self._process_tray(
            event.data["entity_id"], new_state.state, dict(new_state.attributes)
        )

    @callback
    def _process_tray(
        self, entity_id: str, state: str, attributes: dict[str, Any]
    ) -> None:
        """Apply one tray reading to the matching slot."""
        slot = self._tray_map.get(entity_id)
        if slot is None:
            return
        reading = read_tray(attributes, state)
        slot_state = self.coordinator.store.get_slot(slot)
        unchanged = (
            slot_state.reported_uid == reading.uid
            and slot_state.reported_material == reading.material
            and slot_state.reported_color == reading.color
        )
        if unchanged and (slot_state.spool_id is not None or not reading.has_content):
            return

        reported = {
            "reported_uid": reading.uid,
            "reported_material": reading.material,
            "reported_color": reading.color,
        }

        if not reading.has_content:
            if slot_state.spool_id is not None:
                _LOGGER.debug("Slot %s reports empty, clearing assignment", slot)
                self.coordinator.async_assign_slot(slot, None, SOURCE_RFID, **reported)
            else:
                self.coordinator.store.assign_slot(slot, None, SOURCE_RFID, **reported)
            return

        if reading.uid:
            self._resolve_by_rfid(slot, reading, reported)
            return
        self._resolve_by_heuristic(slot, reading, reported)

    @callback
    def _resolve_by_rfid(
        self, slot: int, reading: TrayReading, reported: dict[str, Any]
    ) -> None:
        """Assign by RFID tag, or ask once which roll this tag belongs to."""
        assert reading.uid is not None
        spool = self.coordinator.store.spool_by_rfid(reading.uid)
        if spool is not None:
            if not self.coordinator.options.get(CONF_AUTO_ASSIGN_RFID, True):
                return
            self.coordinator.async_assign_slot(slot, spool.id, SOURCE_RFID, **reported)
            self.notifier.async_dismiss(f"unknown_tag_{reading.uid}")
            return

        self.coordinator.store.assign_slot(slot, None, SOURCE_RFID, **reported)
        candidates = self.match_candidates(reading)
        self.hass.bus.async_fire(
            EVENT_UNKNOWN_TAG,
            {
                "uid": reading.uid,
                ATTR_SLOT: slot,
                "material": reading.material,
                "color": reading.color,
                "candidates": [spool.id for spool in candidates],
            },
        )
        self.hass.async_create_task(
            self._async_ask_unknown_tag(slot, reading, candidates)
        )

    async def _async_ask_unknown_tag(
        self, slot: int, reading: TrayReading, candidates: list[Spool]
    ) -> None:
        """Ask the user which roll the unknown tag belongs to."""
        assert reading.uid is not None
        lines = [
            f"An unknown filament tag was read in slot {slot}.",
            "",
            f"Tag UID: `{reading.uid}`",
        ]
        if reading.material:
            lines.append(f"Material: {reading.material}")
        if reading.color:
            lines.append(f"Colour: #{reading.color}")
        lines.append("")
        if candidates:
            lines.append("Matching rolls in your inventory:")
            lines.extend(
                f"- {self.coordinator.spool_name(spool)} "
                f"(`{spool.id}`, {spool.remaining_weight:.0f} g left)"
                for spool in candidates
            )
        else:
            lines.append("No roll in the inventory matches this reading.")
        lines += [
            "",
            "Teach the tag once and every future load is automatic:",
            "",
            "```yaml",
            "action: filament_manager.learn_rfid",
            "data:",
            f'  rfid_uid: "{reading.uid}"',
            "  spool_id: <id from above>",
            f"  slot: {slot}",
            "```",
        ]
        actions = [
            (
                build_action(VERB_LEARN, spool.id, reading.uid, slot),
                self.coordinator.spool_name(spool)[:30],
            )
            for spool in candidates[:2]
        ]
        actions.append((build_action(VERB_IGNORE, reading.uid), "Ignore"))
        await self.notifier.async_prompt(
            notification_id=f"unknown_tag_{reading.uid}",
            title=f"New roll in slot {slot}",
            message="\n".join(lines),
            actions=actions,
        )

    @callback
    def _resolve_by_heuristic(
        self, slot: int, reading: TrayReading, reported: dict[str, Any]
    ) -> None:
        """Assign third party filament by what the slicer told the printer."""
        if not self.coordinator.options.get(CONF_AUTO_ASSIGN_HEURISTIC, True):
            self.coordinator.store.assign_slot(slot, None, SOURCE_HEURISTIC, **reported)
            return
        candidates = self.match_candidates(reading, exclude_slot=slot)
        if len(candidates) == 1:
            self.coordinator.async_assign_slot(
                slot, candidates[0].id, SOURCE_HEURISTIC, **reported
            )
            return

        self.coordinator.store.assign_slot(slot, None, SOURCE_HEURISTIC, **reported)
        if not candidates:
            _LOGGER.debug(
                "Slot %s reports %s/%s, no inventory match — waiting for NFC",
                slot,
                reading.material,
                reading.color,
            )
            return

        self.hass.bus.async_fire(
            EVENT_AMBIGUOUS_MATCH,
            {
                ATTR_SLOT: slot,
                "material": reading.material,
                "color": reading.color,
                "candidates": [spool.id for spool in candidates],
            },
        )
        self.hass.async_create_task(
            self._async_ask_ambiguous(slot, reading, candidates)
        )

    async def _async_ask_ambiguous(
        self, slot: int, reading: TrayReading, candidates: list[Spool]
    ) -> None:
        """Ask which of several identical looking rolls was loaded."""
        lines = [
            f"Slot {slot} reports {reading.material or 'filament'}"
            + (f" in #{reading.color}" if reading.color else "")
            + ", and several rolls match:",
            "",
        ]
        lines.extend(
            f"- {self.coordinator.spool_name(spool)} "
            f"(`{spool.id}`, {spool.remaining_weight:.0f} g left)"
            for spool in candidates
        )
        lines += [
            "",
            "```yaml",
            "action: filament_manager.assign_slot",
            "data:",
            f"  slot: {slot}",
            "  spool_id: <id from above>",
            "```",
        ]
        await self.notifier.async_prompt(
            notification_id=f"ambiguous_slot_{slot}",
            title=f"Which roll is in slot {slot}?",
            message="\n".join(lines),
            actions=[
                (
                    build_action(VERB_ASSIGN, spool.id, slot),
                    self.coordinator.spool_name(spool)[:30],
                )
                for spool in candidates
            ],
        )

    # -- matching ----------------------------------------------------------

    @callback
    def match_candidates(
        self, reading: TrayReading, exclude_slot: int | None = None
    ) -> list[Spool]:
        """Return inventory rolls that plausibly produced this reading.

        Two opened rolls of the same material *and* the same colour are rare,
        so this covers most of the cases without asking anything.
        """
        tolerance = self.coordinator.color_tolerance
        matches: list[tuple[float, Spool]] = []
        for spool in self.coordinator.store.active_spools():
            if spool.is_empty:
                continue
            slot = self.coordinator.store.slot_of(spool.id)
            if slot is not None and slot != exclude_slot:
                # Already sitting in a different slot.
                continue
            filament_type = self.coordinator.type_of(spool)
            if filament_type is None:
                continue
            if (
                reading.material
                and canonical_material(filament_type.material) != reading.material
            ):
                continue
            if (
                reading.diameter
                and abs(filament_type.diameter - reading.diameter) > 0.1
            ):
                continue
            distance = color_distance(reading.color, filament_type.color_hex)
            if distance is None:
                # One side has no colour: keep it as a weak candidate rather
                # than claiming a match.
                matches.append((tolerance, spool))
                continue
            if distance > tolerance:
                continue
            matches.append((distance, spool))
        matches.sort(key=lambda item: item[0])
        return [spool for _, spool in matches]

    # -- NFC ---------------------------------------------------------------

    @callback
    def _handle_tag_scanned(self, event: Event) -> None:
        """Handle an HA tag scan.

        HA tags only carry a UUID; the mapping to a roll or a slot lives in
        our store, which keeps the stickers reusable and independent of any
        vendor format.
        """
        tag_id = str(event.data.get("tag_id") or "")
        if not tag_id:
            return
        mapping = self.coordinator.store.resolve_tag(tag_id)
        if mapping is None:
            self.hass.async_create_task(self._async_ask_unknown_sticker(tag_id))
            return

        kind = mapping.get("kind")
        target = mapping.get("target")
        now = time.monotonic()
        if kind == TAG_KIND_SPOOL:
            spool = self.coordinator.store.get_spool(str(target))
            if spool is None:
                return
            if self._pending_slot and self._pending_slot[1] > now:
                slot = self._pending_slot[0]
                self._pending_slot = None
                self.coordinator.async_assign_slot(slot, spool.id, SOURCE_NFC)
                return
            self._pending_spool = (spool.id, now + TAG_PAIR_TIMEOUT)
            self.hass.async_create_task(self._async_ask_slot(spool))
            return

        if kind == TAG_KIND_SLOT:
            slot = int(target)  # type: ignore[arg-type]
            if self._pending_spool and self._pending_spool[1] > now:
                spool_id = self._pending_spool[0]
                self._pending_spool = None
                self.coordinator.async_assign_slot(slot, spool_id, SOURCE_NFC)
                self.notifier.async_dismiss(f"pick_slot_{spool_id}")
                return
            self._pending_slot = (slot, now + TAG_PAIR_TIMEOUT)

    async def _async_ask_slot(self, spool: Spool) -> None:
        """Offer the slot buttons after a roll sticker was scanned."""
        slot_count = self.coordinator.slot_count
        message = "\n".join(
            [
                f"**{self.coordinator.spool_name(spool)}** was scanned.",
                "",
                f"Scan an AMS slot sticker within {TAG_PAIR_TIMEOUT} seconds, "
                "or pick a slot:",
                "",
                "```yaml",
                "action: filament_manager.assign_slot",
                "data:",
                f'  spool_id: "{spool.id}"',
                "  slot: 1",
                "```",
            ]
        )
        await self.notifier.async_prompt(
            notification_id=f"pick_slot_{spool.id}",
            title="Which slot?",
            message=message,
            actions=[
                (build_action(VERB_ASSIGN, spool.id, slot), f"Slot {slot}")
                for slot in range(1, slot_count + 1)
            ],
        )

    async def _async_ask_unknown_sticker(self, tag_id: str) -> None:
        """Explain how to bind a fresh NFC sticker."""
        message = "\n".join(
            [
                f"The tag `{tag_id}` is not linked to anything yet.",
                "",
                "Link it to a roll:",
                "",
                "```yaml",
                "action: filament_manager.link_tag",
                "data:",
                f'  tag_id: "{tag_id}"',
                "  spool_id: <spool id>",
                "```",
                "",
                "…or to an AMS slot, for the two-sticker workflow:",
                "",
                "```yaml",
                "action: filament_manager.link_tag",
                "data:",
                f'  tag_id: "{tag_id}"',
                "  slot: 1",
                "```",
            ]
        )
        await self.notifier.async_prompt(
            notification_id=f"unknown_sticker_{tag_id}",
            title="Unknown NFC tag",
            message=message,
        )

    # -- actionable notifications -----------------------------------------

    @callback
    def _handle_notification_action(self, event: Event) -> None:
        """Handle a button press on a companion app notification."""
        parsed = parse_action(str(event.data.get("action") or ""))
        if parsed is None:
            return
        verb, parts = parsed
        try:
            if verb == VERB_ASSIGN:
                spool_id, slot = parts[0], int(parts[1])
                self.coordinator.async_assign_slot(slot, spool_id, SOURCE_NFC)
                self.notifier.async_dismiss(f"pick_slot_{spool_id}")
                self.notifier.async_dismiss(f"ambiguous_slot_{slot}")
            elif verb == VERB_LEARN:
                spool_id, uid = parts[0], parts[1]
                slot = int(parts[2]) if len(parts) > 2 else None
                self.coordinator.store.learn_rfid(spool_id, uid)
                if slot is not None:
                    self.coordinator.async_assign_slot(slot, spool_id, SOURCE_RFID)
                self.notifier.async_dismiss(f"unknown_tag_{uid}")
            elif verb == VERB_IGNORE:
                self.notifier.async_dismiss(f"unknown_tag_{parts[0]}")
        except (IndexError, ValueError):
            _LOGGER.warning("Could not handle notification action %s", event.data)
