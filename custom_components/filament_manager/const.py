"""Constants for the Filament Manager integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "filament_manager"
MANUFACTURER: Final = "Filament Manager"

# --- Persistence -----------------------------------------------------------
STORAGE_KEY: Final = f"{DOMAIN}.data"
STORAGE_VERSION: Final = 1
STORAGE_MINOR_VERSION: Final = 1

# Bundled master data shipped with the integration (updated via HACS).
BUNDLED_DB_FILE: Final = "data/filaments.json"
# User owned master data in the HA config directory (wins over the bundle).
USER_DB_FILE: Final = "filament_db.json"
DB_SPEC_VERSION: Final = 1

# --- Config entry ----------------------------------------------------------
CONF_SLOT_COUNT: Final = "slot_count"
CONF_TRAY_ENTITIES: Final = "tray_entities"
CONF_PRINT_STATE_ENTITY: Final = "print_state_entity"
CONF_PRINT_WEIGHT_ENTITY: Final = "print_weight_entity"
CONF_ACTIVE_TRAY_ENTITY: Final = "active_tray_entity"
CONF_NOTIFY_SERVICE: Final = "notify_service"
CONF_LOW_STOCK_THRESHOLD: Final = "low_stock_threshold"
CONF_COLOR_TOLERANCE: Final = "color_tolerance"
CONF_AUTO_ASSIGN_RFID: Final = "auto_assign_rfid"
CONF_AUTO_ASSIGN_HEURISTIC: Final = "auto_assign_heuristic"
CONF_AUTO_CONSUME: Final = "auto_consume"
CONF_CURRENCY: Final = "currency"
CONF_SPLIT_STRATEGY: Final = "split_strategy"

DEFAULT_SLOT_COUNT: Final = 4
DEFAULT_LOW_STOCK_THRESHOLD: Final = 150.0
# Maximum CIE76 distance in Lab space that still counts as the same colour.
DEFAULT_COLOR_TOLERANCE: Final = 25.0
DEFAULT_CURRENCY: Final = "EUR"

SPLIT_PROPORTIONAL: Final = "proportional"
SPLIT_LAST_SLOT: Final = "last_slot"
SPLIT_STRATEGIES: Final = [SPLIT_PROPORTIONAL, SPLIT_LAST_SLOT]
DEFAULT_SPLIT_STRATEGY: Final = SPLIT_PROPORTIONAL

MIN_SLOT_COUNT: Final = 1
MAX_SLOT_COUNT: Final = 16

# --- Assignment ------------------------------------------------------------
SOURCE_RFID: Final = "rfid"
SOURCE_HEURISTIC: Final = "heuristic"
SOURCE_NFC: Final = "nfc"
SOURCE_MANUAL: Final = "manual"
ASSIGNMENT_SOURCES: Final = [SOURCE_RFID, SOURCE_HEURISTIC, SOURCE_NFC, SOURCE_MANUAL]

# Tag kinds stored in the tag map.
TAG_KIND_SPOOL: Final = "spool"
TAG_KIND_SLOT: Final = "slot"

# A spool tag scan stays "armed" this long while waiting for a slot tag scan.
TAG_PAIR_TIMEOUT: Final = 30

# --- Dispatcher signals ----------------------------------------------------
SIGNAL_SPOOLS_CHANGED: Final = f"{DOMAIN}_spools_changed"
SIGNAL_SPOOL_UPDATED: Final = f"{DOMAIN}_spool_updated"

# --- Events ----------------------------------------------------------------
EVENT_UNKNOWN_TAG: Final = f"{DOMAIN}_unknown_tag"
EVENT_SPOOL_ASSIGNED: Final = f"{DOMAIN}_spool_assigned"
EVENT_SPOOL_CONSUMED: Final = f"{DOMAIN}_spool_consumed"
EVENT_SPOOL_EMPTY: Final = f"{DOMAIN}_spool_empty"
EVENT_AMBIGUOUS_MATCH: Final = f"{DOMAIN}_ambiguous_match"

# --- Services --------------------------------------------------------------
SERVICE_ADD_SPOOL: Final = "add_spool"
SERVICE_UPDATE_SPOOL: Final = "update_spool"
SERVICE_DUPLICATE_SPOOL: Final = "duplicate_spool"
SERVICE_CONSUME: Final = "consume"
SERVICE_ASSIGN_SLOT: Final = "assign_slot"
SERVICE_CLEAR_SLOT: Final = "clear_slot"
SERVICE_ARCHIVE_SPOOL: Final = "archive_spool"
SERVICE_RESTORE_SPOOL: Final = "restore_spool"
SERVICE_DELETE_SPOOL: Final = "delete_spool"
SERVICE_CORRECT_WEIGHT: Final = "correct_weight"
SERVICE_ADD_TYPE: Final = "add_type"
SERVICE_ADD_VENDOR: Final = "add_vendor"
SERVICE_LINK_TAG: Final = "link_tag"
SERVICE_UNLINK_TAG: Final = "unlink_tag"
SERVICE_LEARN_RFID: Final = "learn_rfid"
SERVICE_IMPORT_DB: Final = "import_db"
SERVICE_EXPORT_DB: Final = "export_db"
SERVICE_EXPORT_CONTRIBUTION: Final = "export_contribution"
SERVICE_RELOAD_DB: Final = "reload_db"

# --- Attributes / service fields ------------------------------------------
ATTR_SPOOL_ID: Final = "spool_id"
ATTR_TYPE_ID: Final = "type_id"
ATTR_VENDOR_ID: Final = "vendor_id"
ATTR_SLOT: Final = "slot"
ATTR_AMOUNT: Final = "amount"
ATTR_LENGTH: Final = "length"
ATTR_SOURCE: Final = "source"
ATTR_TAG_ID: Final = "tag_id"
ATTR_RFID_UID: Final = "rfid_uid"
ATTR_GROSS_WEIGHT: Final = "gross_weight"
ATTR_REMAINING_WEIGHT: Final = "remaining_weight"

# --- Materials -------------------------------------------------------------
# Densities in g/cm3, used when a filament type does not carry its own value.
MATERIAL_DENSITIES: Final[dict[str, float]] = {
    "PLA": 1.24,
    "PLA+": 1.24,
    "PLA-CF": 1.22,
    "PETG": 1.27,
    "PETG-CF": 1.30,
    "ABS": 1.04,
    "ABS-GF": 1.12,
    "ASA": 1.07,
    "ASA-CF": 1.15,
    "TPU": 1.21,
    "PC": 1.20,
    "PA": 1.14,
    "PA-CF": 1.17,
    "PVA": 1.23,
    "HIPS": 1.04,
    "PET-CF": 1.39,
    "PPS-CF": 1.43,
    "PP": 0.91,
    "SUPPORT": 1.22,
}
DEFAULT_DENSITY: Final = 1.24
DEFAULT_DIAMETER: Final = 1.75
DIAMETERS: Final = [1.75, 2.85]
SPOOL_TYPES: Final = ["plastic", "cardboard", "refill", "unknown"]

# --- Repairs ---------------------------------------------------------------
ISSUE_OVERDRAW: Final = "overdraw"

# --- Frontend --------------------------------------------------------------
FRONTEND_URL_BASE: Final = f"/{DOMAIN}_frontend"
FRONTEND_CARD_FILENAME: Final = "filament-manager-card.js"
