# Filament Manager

[![HACS](https://img.shields.io/badge/HACS-custom-41BDF5.svg)](https://hacs.xyz)
[![Validate](https://github.com/niclasreutter/FilamentMaster/actions/workflows/validate.yml/badge.svg)](https://github.com/niclasreutter/FilamentMaster/actions/workflows/validate.yml)
[![Test](https://github.com/niclasreutter/FilamentMaster/actions/workflows/test.yml/badge.svg)](https://github.com/niclasreutter/FilamentMaster/actions/workflows/test.yml)

Filament inventory and consumption tracking for 3D printing, entirely inside
Home Assistant. No extra server, no add-on, no container — everything lives in
`.storage` and therefore in every Home Assistant backup.

*[Diese Anleitung auf Deutsch](README.de.md)*

---

## What it does

- **Full filament inventory** — manufacturers, product types and the physical
  rolls on your shelf, with price, location, purchase date and remaining weight.
- **Automatic slot assignment** — the integration works out which roll is in
  which AMS slot, by RFID where the printer can read it, by material and colour
  where it cannot, and by NFC sticker as the fallback that always works.
- **Consumption booked at print end** — the job weight the printer reports is
  subtracted from the rolls that were actually loaded, split across slots when
  the AMS switched mid-print.
- **Re-weighing that works** — put the roll on a kitchen scale, type in the
  gross weight, and the empty spool weight is subtracted for you.
- **A dashboard card** with barcode scanning that works on iOS too, bundled
  with the integration — no second HACS repository, no dashboard resource.
- **German and English** throughout.

### Deliberately out of scope

| Topic | Why |
|---|---|
| Spoolman code, schema, API or database | Clean independence for a public release |
| Custom RFID readers (ESP32/PN532) | Too much hardware support for a public integration |
| Self-written Bambu RFID tags | Fragile against firmware updates, legally grey |

The data model (manufacturer → filament type → roll) is ordinary database
modelling. No third-party master data set is adopted; the bundled database was
written from the manufacturers' own published specifications.

---

## Installation

### HACS (recommended)

1. HACS → three-dot menu → **Custom repositories**
2. Add `https://github.com/niclasreutter/FilamentMaster` as an **Integration**
3. Install **Filament Manager** and restart Home Assistant
4. **Settings → Devices & services → Add integration → Filament Manager**

### Manual

Copy `custom_components/filament_manager` into your `config/custom_components`
directory and restart Home Assistant.

### First steps

Setup asks only for the things that shape the entities: how many AMS slots to
track, the low stock threshold, and the currency. AMS tray sensors exposed by
[`ha-bambulab`](https://github.com/greghesp/ha-bambulab) are detected and
pre-filled.

Everything else lives under **Configure**:

- **Add spool** — the frequent form: pick a type, add price, date and location
- **Manage spools** — edit, assign a slot, link a tag, duplicate, archive, delete
- **Add filament type / manufacturer** — the rare form, with every field
- **Printer entities** — tray sensors, print status, print weight, active tray
- **Settings** — thresholds, colour tolerance, notification service, automation toggles
- **Import, export, contribute**

---

## The data model

Three levels:

**Vendor** — `id`, `name`, `website`.

**FilamentType** — the product you can buy again: material, colour name and hex
value, diameter, density, nominal weight, **empty spool weight**, spool type,
temperatures, and a list of barcodes.

**Spool** — the physical roll: remaining and initial weight, purchase date,
price, location, archived flag, NFC tag, a *list* of Bambu RFID UIDs, and which
slot it currently sits in, including how that was decided.

The empty spool weight is the field worth measuring yourself. It is what turns
a kitchen scale reading into a remaining filament figure, and the bundled
values are the manufacturers' published ones, not measurements.

### The filament database

Two layers are merged at startup and **the user wins**:

1. `custom_components/filament_manager/data/filaments.json` — ships with the
   integration, refreshed by HACS updates (12 manufacturers, 45 types).
2. `/config/filament_db.json` — your own additions and overrides. It lives in
   the config directory, so it is covered by the Home Assistant backup.

Both carry a `spec_version` field for later format changes.

**Contributing back:** *Configure → Import, export, contribute → Contribution
snippet* (or the `filament_manager.export_contribution` service) turns your own
entries into a pull-request-ready JSON snippet. The shared database grows
through its users.

---

## Adding stock

### By hand (the primary route)

Through the options flow, with schemas Home Assistant renders itself. That
works in any browser, in the iOS app, without HTTPS camera permissions, and no
frontend update can break the input fields.

Two forms of very different frequency:

- **Add filament type** (rare): every field. Lands in `/config/filament_db.json`.
- **Add spool** (often): a dropdown of known types sorted by most recently used,
  then just date, price and location.

Plus a **Duplicate** button on every roll's device — a re-buy is one click.

### By barcode

The card scans EAN/UPC codes with the camera.

> A barcode identifies the **product**, not the roll. Every black PLA from the
> same manufacturer carries the same code — a hit means "add another roll of
> this type", never "this exact spool".

```
Scan → look the code up
  ├─ known   → add a new roll of that type
  └─ unknown → open the type form, prefilled
               → saving writes the type to /config/filament_db.json
```

`BarcodeDetector` is used where it exists (Chrome, Edge, the Android companion
app) because it is faster. It does not exist on iOS at all, so the card falls
back to a **bundled ZXing WebAssembly build**, fetched only the first time you
actually scan something. Both paths need HTTPS; without it the card says so and
offers a text field.

**Fallback without the card:** scan with the iOS camera, share sheet → Home
Assistant → the `mobile_app.share` event carries the code in its `text` field.

---

## Matching rolls to slots

The resolver works in descending order of confidence.

### 1. RFID — Bambu filament (certain, hands-off)

Every spool holder on the AMS Lite has an RFID reader, and `ha-bambulab`
attaches the tag UID to the tray entity.

```
Tray attribute changes
  ├─ UID known    → assign the roll, done, no interaction
  └─ UID unknown  → "New roll in slot 2, which one is this?"
                    → teach it once → automatic from then on
```

Two things worth knowing:

- The attribute carries the **tag UID**, not the spool serial number from Bambu
  Studio. The integration maps consistently onto the UID.
- Bambu spools carry **two tags** — one per side, with different UIDs — so the
  reader works whichever way round the roll goes in. Teach both to the same
  roll and flipping it never triggers another question. That is why
  `rfid_uids` is a list.

### 2. Heuristic — third-party filament (probable)

Third-party rolls have nothing to read. But once material and colour are set in
the slicer, the printer reports `tray_type` and `tray_color` over MQTT.

```
tray_type/tray_color changes
  → match against the non-archived inventory
     (material + diameter + colour within tolerance)
  ├─ exactly one hit → assign automatically
  ├─ several hits    → ask, with buttons
  └─ no hit          → wait for NFC
```

Colours are compared in **CIE L\*a\*b\*** rather than RGB: a euclidean distance
in RGB makes dark blue and dark green neighbours, which is exactly the kind of
silent mis-assignment that later subtracts grams from the wrong roll. The
default tolerance is 25 and is configurable.

Two opened rolls of the same material *and* the same colour are rare, so this
covers most cases without any interaction.

### 3. NFC — manual choice (the fallback)

An NTAG213 on the roll. Home Assistant tags only store a UUID; the mapping from
tag to roll lives in this integration's store, so the stickers are reusable and
independent of any manufacturer's format.

Two ways to assign a slot:

- **Actionable notification** after the scan, with one button per slot
- **Two stickers**: one per roll, four at the AMS. Scan the roll, scan the slot,
  linked within 30 seconds — no screen involved at all

> **iOS quirk:** Android fires `tag_scanned` the moment you hold the phone
> against the tag. On an iPhone a banner notification appears first and has to
> be tapped. It works, it is just one tap more — this is not a bug.

### Traceability

Every assignment records its `assignment_source` (`rfid`, `heuristic`, `nfc` or
`manual`), and the UI keeps "detected automatically" apart from "chosen by
hand". Without that distinction, a wrong guess silently subtracts grams from
the wrong roll.

---

## Consumption

A finished print triggers the deduction. The weight the printer reports for the
job is booked against whatever was loaded.

- **One slot** → the whole job goes to that roll.
- **Several slots** (the AMS filament backup case, where a roll runs empty and
  the AMS quietly moves on) → the job is split by how long each slot was
  active. `last_slot` books it all on the slot that ran longest instead;
  paused time never counts.
- **A failed or cancelled print books nothing**, because the reported weight
  covers the whole job, not the part that actually printed. Correct it by hand
  with `filament_manager.consume` if it matters.

Safeguards:

- A `number` entity per roll for correcting after re-weighing
  (gross weight − empty spool weight = remaining filament)
- The `filament_manager.consume` service for your own automations
- A plausibility check: a deduction larger than what is left raises a repair
  issue instead of writing a negative weight, and clears once you correct it

---

## Entities

One device per non-archived roll:

| Entity | Type | Note |
|---|---|---|
| Remaining filament | `sensor` | grams, `device_class: weight` |
| Consumed in total | `sensor` | `state_class: total_increasing` → long term statistics for free |
| Remaining | `sensor` | percent |
| Remaining length | `sensor` | metres, converted via the type's density |
| Low stock | `binary_sensor` | threshold configurable, globally or per roll |
| Loaded | `binary_sensor` | whether the roll sits in a slot |
| Measured gross weight | `number` | for re-weighing |
| Set remaining filament | `number` | disabled by default |
| Low stock threshold | `number` | disabled by default |
| Duplicate / Archive | `button` | |

Plus, on the integration's own device: one `sensor` per AMS slot (which roll is
where, and how that was decided), the total filament in stock, the inventory
value in your currency, the number of rolls, and how many are running out.

**Archived rolls** lose their entities and keep their data.

> **Recorder:** with ~20 rolls × several entities, exclude the consumption
> sensors from your `recorder` configuration or the database grows for nothing:
>
> ```yaml
> recorder:
>   exclude:
>     entity_globs:
>       - sensor.*_consumed_in_total
>       - sensor.*_remaining_length
> ```

---

## Services

| Service | What it does |
|---|---|
| `add_spool` | Add one or more rolls of a known type |
| `update_spool` | Change the bookkeeping fields |
| `duplicate_spool` | Another roll of the same type |
| `consume` | Subtract filament, by weight or by length |
| `assign_slot` / `clear_slot` | Record what is loaded where |
| `archive_spool` / `restore_spool` / `delete_spool` | Lifecycle |
| `correct_weight` | Correct from a gross or a net weight |
| `add_vendor` / `add_type` | Extend the filament database |
| `link_tag` / `unlink_tag` | Bind an NFC sticker to a roll or a slot |
| `learn_rfid` | Teach a Bambu tag UID to a roll |
| `import_db` / `export_db` / `export_contribution` / `reload_db` | Master data |

Example — book a print you ran from the SD card:

```yaml
action: filament_manager.consume
data:
  slot: 1
  amount: 42.5
```

### Events

`filament_manager_spool_assigned`, `filament_manager_spool_consumed`,
`filament_manager_spool_empty`, `filament_manager_unknown_tag` and
`filament_manager_ambiguous_match` — enough to build your own actionable
notifications or a shopping list automation.

### WebSocket commands

`filament_manager/list`, `/types`, `/slots`, `/scan`, `/subscribe`,
`/add_spool`, `/add_type`, `/assign_slot`, `/consume`, `/correct_weight`,
`/archive`, `/link_tag`, `/contribution`.

The scan goes over the WebSocket rather than through a service so the card can
react immediately instead of polling a sensor for the answer.

---

## The card

The integration registers the card itself, so there is nothing to add under
Settings → Dashboards → Resources. Add it from the card picker, or in YAML:

```yaml
type: custom:filament-manager-card
title: Filament
show_stats: true
show_slots: true
show_archived: false
show_scanner: true
columns: 0     # 0 = one per row
```

It is written against plain DOM APIs — nothing is imported from the Home
Assistant frontend bundle — so a rename inside the frontend cannot turn the
input fields invisible.

---

## Troubleshooting

**No slots are detected.** Point the integration at the tray sensors by hand
under *Configure → Printer entities*. Discovery looks for sensors from the
`bambu_lab` platform whose id contains `tray`.

**Nothing is subtracted after a print.** The print status and print weight
entities have to be set under *Configure → Printer entities*, and the slot has
to have a roll assigned. The log says which of the two was missing.

**An unknown RFID tag keeps asking.** Teach it once with
`filament_manager.learn_rfid`, or tap the button in the notification. Remember
that Bambu rolls have a tag on each side.

**Debug logging:**

```yaml
logger:
  logs:
    custom_components.filament_manager: debug
```

---

## Publishing checklist

- [x] `custom_components/filament_manager/` repository layout
- [x] `manifest.json` with `version`, `documentation`, `issue_tracker`, `codeowners`
- [x] `hacs.json` in the repository root
- [x] `config_flow: true`
- [x] README with installation and the recorder hint
- [x] German and English translations
- [x] hassfest and HACS validation in CI
- [x] Brand assets under `custom_components/filament_manager/brand/`
- [ ] **Repository description and topics** — HACS validation fails without
      them, and they can only be set in the GitHub UI (repository → About →
      the gear icon). Suggested topics: `home-assistant`, `hacs`,
      `custom-component`, `3d-printing`, `filament`, `bambulab`, `ams`
- [ ] GitHub release with a tag
- [ ] Pull request against [`home-assistant/brands`](https://github.com/home-assistant/brands) for the icon, which then replaces the bundled assets
- [ ] Apply for inclusion in the HACS default store

---

## Development

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements_test.txt
pytest
ruff check . && ruff format --check .
```

---

## Credits

Barcode reading falls back to [zxing-wasm](https://github.com/Sec-ant/zxing-wasm)
(MIT), bundled in `custom_components/filament_manager/www/`. This project is
MIT licensed; see [LICENSE](LICENSE).
