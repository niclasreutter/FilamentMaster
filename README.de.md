# Filament Manager

[![HACS](https://img.shields.io/badge/HACS-custom-41BDF5.svg)](https://hacs.xyz)
[![Validate](https://github.com/niclasreutter/FilamentMaster/actions/workflows/validate.yml/badge.svg)](https://github.com/niclasreutter/FilamentMaster/actions/workflows/validate.yml)
[![Test](https://github.com/niclasreutter/FilamentMaster/actions/workflows/test.yml/badge.svg)](https://github.com/niclasreutter/FilamentMaster/actions/workflows/test.yml)

Filamentbestand und Verbrauchsverfolgung für den 3D-Druck, komplett in Home
Assistant. Ohne Zusatzserver, ohne Add-on, ohne Container — alles liegt in
`.storage` und damit in jedem Home-Assistant-Backup.

*[This guide in English](README.md)*

---

## Was es kann

- **Vollständige Filamentverwaltung** — Hersteller, Produkttypen und die
  physischen Rollen im Regal, mit Preis, Lagerort, Kaufdatum und Restgewicht.
- **Automatische Slot-Zuordnung** — die Integration ermittelt, welche Rolle in
  welchem AMS-Slot steckt: per RFID, wo der Drucker lesen kann, per Material
  und Farbe, wo er es nicht kann, und per NFC-Sticker als Rückfallebene, die
  immer funktioniert.
- **Verbrauch am Druckende** — das gemeldete Auftragsgewicht wird von den
  tatsächlich eingelegten Rollen abgezogen, bei einem Slot-Wechsel mitten im
  Druck anteilig aufgeteilt.
- **Nachwiegen, das funktioniert** — Rolle auf die Küchenwaage, Bruttogewicht
  eintragen, das Leergewicht der Spule wird abgezogen.
- **Eine Dashboard-Karte** mit Barcode-Scan, der auch auf iOS läuft, direkt in
  der Integration — kein zweites HACS-Repository, keine Dashboard-Ressource.
- **Ein Einkaufslisten-Eintrag**, sobald die letzte Rolle eines Typs zur Neige geht.
- **Deutsch und Englisch** durchgängig.

### Bewusst außen vor

| Thema | Begründung |
|---|---|
| Spoolman-Code, -Schema, -API, -Datenbank | Saubere Unabhängigkeit für die öffentliche Veröffentlichung |
| Eigene RFID-Leser (ESP32/PN532) | Zu viel Hardware-Support-Aufwand für eine öffentliche Integration |
| Selbstgeschriebene Bambu-RFID-Tags | Fragil gegenüber Firmware-Updates, rechtliche Grauzone |

Das Datenmodell (Hersteller → Filamenttyp → Rolle) ist gängige
Datenbankmodellierung. Fremde Stammdatenbestände werden nicht übernommen; die
mitgelieferte Datenbank entstand aus den veröffentlichten Herstellerangaben.

---

## Installation

### HACS (empfohlen)

1. HACS → Drei-Punkte-Menü → **Benutzerdefinierte Repositories**
2. `https://github.com/niclasreutter/FilamentMaster` als **Integration** hinzufügen
3. **Filament Manager** installieren und Home Assistant neu starten
4. **Einstellungen → Geräte & Dienste → Integration hinzufügen → Filament Manager**

### Manuell

`custom_components/filament_manager` nach `config/custom_components` kopieren
und Home Assistant neu starten.

### Erste Schritte

Die Einrichtung fragt nur, was die Entities prägt: wie viele AMS-Slots verfolgt
werden, die Schwelle für niedrigen Bestand und die Währung. AMS-Tray-Sensoren
von [`ha-bambulab`](https://github.com/greghesp/ha-bambulab) werden erkannt und
vorausgefüllt.

Alles Weitere liegt unter **Konfigurieren**:

- **Rolle anlegen** — das häufige Formular: Typ wählen, Preis, Datum, Lagerort
- **Rollen verwalten** — bearbeiten, Slot zuweisen, Tag verknüpfen, duplizieren, archivieren, löschen
- **Filamenttyp / Hersteller anlegen** — das seltene Formular mit allen Feldern
- **Drucker-Entities** — Tray-Sensoren, Druckstatus, Druckgewicht, aktives Tray
- **Einstellungen** — Schwellen, Farbtoleranz, Benachrichtigungsdienst, Automatik-Schalter
- **Import, Export, Beitragen**

---

## Das Datenmodell

Drei Ebenen:

**Vendor** — `id`, `name`, `website`.

**FilamentType** — das nachkaufbare Produkt: Material, Farbname und Hex-Wert,
Durchmesser, Dichte, Nenngewicht, **Leergewicht der Spule**, Spulentyp,
Temperaturen und eine Liste von Barcodes.

**Spool** — die physische Rolle: Rest- und Startgewicht, Kaufdatum, Preis,
Lagerort, Archiv-Flag, NFC-Tag, eine *Liste* von Bambu-RFID-UIDs und der
aktuelle Slot samt der Information, wie er zustande kam.

Das Leergewicht der Spule ist das Feld, das sich zu messen lohnt. Erst damit
wird aus einem Waagenwert ein Restfilament-Wert; die mitgelieferten Werte sind
Herstellerangaben, keine Messungen.

Ein Refill hat keine eigene Spule und landet auf einer, die du schon hattest —
deshalb kann eine Rolle ein eigenes Leergewicht tragen (*Rollen verwalten →
Bearbeiten*), das den Wert des Typs schlägt.

### Die Filamentdatenbank

Zwei Ebenen werden beim Start gemergt, **der User gewinnt**:

1. `custom_components/filament_manager/data/filaments.json` — mitgeliefert,
   kommt per HACS-Update (12 Hersteller, 45 Typen).
2. `/config/filament_db.json` — eigene Ergänzungen und Überschreibungen. Liegt
   im Konfigurationsverzeichnis und ist damit vom Backup abgedeckt.

Beide tragen ein `spec_version`-Feld für spätere Formatänderungen.

**Beitragen:** *Konfigurieren → Import, Export, Beitragen → Beitragen-Snippet*
(oder der Dienst `filament_manager.export_contribution`) macht aus den eigenen
Einträgen ein PR-fertiges JSON-Snippet. Die Basis-Datenbank wächst durch ihre
Nutzer.

---

## Bestand einpflegen

### Händisch (der Primärweg)

Über den Options-Flow, mit Schemas, die Home Assistant selbst rendert. Das
funktioniert in jedem Browser, in der iOS-App, ohne HTTPS-Kamerarechte, und ein
Frontend-Update kann die Eingabefelder nicht zerschießen.

Zwei unterschiedlich häufige Formulare:

- **Filamenttyp anlegen** (selten): alle Felder. Landet in `/config/filament_db.json`.
- **Rolle anlegen** (oft): Dropdown der bekannten Typen, sortiert nach zuletzt
  benutzt, dann nur noch Datum, Preis, Lagerort.

Dazu ein **Duplizieren**-Button am Gerät jeder Rolle — eine nachgekaufte Rolle
ist ein Klick.

### Per Barcode

Die Karte scannt EAN/UPC-Codes mit der Kamera.

> Ein Barcode identifiziert das **Produkt**, nicht die Rolle. Jede schwarze
> PLA-Rolle desselben Herstellers hat denselben Code — ein Treffer heißt „lege
> eine weitere Rolle dieses Typs an“, nie „genau diese Spule“.

```
Scan → Code nachschlagen
  ├─ bekannt   → neue Rolle dieses Typs anlegen
  └─ unbekannt → Typformular vorausgefüllt öffnen
                 → beim Speichern Typ in /config/filament_db.json schreiben
```

`BarcodeDetector` wird genutzt, wo es existiert (Chrome, Edge, Android-
Companion-App), weil es schneller ist. Auf iOS existiert es gar nicht, deshalb
fällt die Karte auf einen **mitgelieferten ZXing-WebAssembly-Build** zurück,
der erst beim ersten Scan geladen wird. Beide Wege brauchen HTTPS; ohne sagt
die Karte das und bietet ein Textfeld an.

**Fallback ohne Karte:** mit der iOS-Kamera scannen, Teilen-Sheet → Home
Assistant → das Event `mobile_app.share` trägt den Code im Feld `text`.

---

## Rolle → Slot zuordnen

Der Resolver arbeitet in absteigender Konfidenz.

### 1. RFID — Bambu-Filament (sicher, vollautomatisch)

Beim AMS Lite sitzt an jedem Spulenhalter ein RFID-Leser, und `ha-bambulab`
hängt die Tag-UID an die Tray-Entity.

```
Tray-Attribut ändert sich
  ├─ UID bekannt   → Rolle zuweisen, fertig, keine Interaktion
  └─ UID unbekannt → „Neue Rolle in Slot 2, welche ist das?“
                     → einmal zuordnen → ab dann automatisch
```

Zwei Stolpersteine:

- Das Attribut liefert die **Tag-UID**, nicht die Spulen-Seriennummer aus Bambu
  Studio. Die Integration mappt konsequent auf die UID.
- Bambu-Spulen tragen **zwei Tags** — eins pro Seite, mit unterschiedlichen
  UIDs —, damit der Leser unabhängig von der Einlegerichtung greift. Beide auf
  dieselbe Rolle anlernen, dann fragt nach dem Umdrehen nichts mehr nach.
  Deshalb ist `rfid_uids` eine Liste.

### 2. Heuristik — Fremdfilament (wahrscheinlich)

Fremdrollen haben nichts zu lesen. Aber sobald im Slicer Material und Farbe
gesetzt sind, meldet der Drucker `tray_type` und `tray_color` über MQTT.

```
tray_type/tray_color ändert sich
  → Abgleich gegen den nicht-archivierten Bestand
     (Material + Durchmesser + Farbwert mit Toleranz)
  ├─ genau 1 Treffer → automatisch zuweisen
  ├─ mehrere Treffer → nachfragen, mit Buttons
  └─ kein Treffer    → auf NFC warten
```

Farben werden im **CIE-L\*a\*b\***-Raum verglichen, nicht in RGB: ein
euklidischer Abstand in RGB macht Dunkelblau und Dunkelgrün zu Nachbarn — genau
die Art stiller Fehlzuordnung, die später Gramm vom falschen Bestand abzieht.
Die Toleranz liegt standardmäßig bei 25 und ist einstellbar.

Zwei angebrochene Rollen desselben Materials *und* derselben Farbe sind selten,
das deckt den Großteil der Fälle ohne jede Interaktion ab.

### 3. NFC — manuelle Auswahl (Rückfallebene)

Ein NTAG213 auf der Rolle. HA-Tags speichern nur eine UUID; das Mapping Tag →
Rolle liegt im Store dieser Integration. Die Sticker sind damit
wiederverwendbar und von keinem Herstellerformat abhängig.

Zwei Varianten für die Slot-Zuweisung:

- **Actionable Notification** nach dem Scan, ein Button pro Slot
- **Zwei Sticker**: einer pro Rolle, vier am AMS. Rolle scannen, Slot scannen,
  innerhalb von 30 Sekunden verknüpft — komplett ohne Bildschirm

> **iOS-Eigenheit:** Android feuert `tag_scanned` direkt beim Dranhalten. Auf
> dem iPhone kommt erst eine Banner-Notification, die angetippt werden muss.
> Funktioniert, ist nur ein Tap mehr — das ist kein Bug.

### Nachvollziehbarkeit

Jede Zuweisung speichert ihre `assignment_source` (`rfid`, `heuristic`, `nfc`
oder `manual`), und die UI unterscheidet „automatisch erkannt“ von „selbst
gewählt“. Ohne diese Unterscheidung ziehen Fehlzuordnungen still Gramm vom
falschen Bestand ab.

---

## Verbrauch

Ein abgeschlossener Druck löst den Abzug aus. Das gemeldete Auftragsgewicht
wird auf das verbucht, was eingelegt war.

- **Ein Slot** → der ganze Auftrag geht auf diese Rolle.
- **Mehrere Slots** (der Filament-Backup-Fall des AMS, bei dem eine Rolle leer
  läuft und das AMS still weiterzieht) → der Auftrag wird nach der aktiven Zeit
  je Slot aufgeteilt. `last_slot` verbucht stattdessen alles auf den Slot, der
  am längsten lief; Pausenzeit zählt nie mit.
- **Ein fehlgeschlagener oder abgebrochener Druck verbucht nichts**, weil das
  gemeldete Gewicht den ganzen Auftrag beschreibt, nicht den gedruckten Teil.
  Bei Bedarf mit `filament_manager.consume` von Hand nachtragen.

Absicherung:

- Eine `number`-Entity pro Rolle zur Korrektur nach dem Wiegen
  (Bruttogewicht − Leergewicht der Spule = Restfilament)
- Der Dienst `filament_manager.consume` für eigene Automationen
- Plausibilitätsprüfung: ein Abzug größer als der Rest erzeugt ein
  Repair-Issue statt eines negativen Werts und verschwindet nach der Korrektur

---

## Entities

Ein Gerät pro nicht-archivierter Rolle:

| Entity | Typ | Hinweis |
|---|---|---|
| Restfilament | `sensor` | Gramm, `device_class: weight` |
| Verbraucht gesamt | `sensor` | `state_class: total_increasing` → Langzeitstatistik gratis |
| Rest | `sensor` | Prozent |
| Restlänge | `sensor` | Meter, über die Dichte des Typs umgerechnet |
| Niedriger Bestand | `binary_sensor` | Schwelle global oder je Rolle konfigurierbar |
| Eingelegt | `binary_sensor` | ob die Rolle in einem Slot steckt |
| Gewogenes Bruttogewicht | `number` | zum Nachwiegen |
| Restfilament setzen | `number` | standardmäßig deaktiviert |
| Schwelle für niedrigen Bestand | `number` | standardmäßig deaktiviert |
| Duplizieren / Archivieren | `button` | |

Dazu am Gerät der Integration: ein `sensor` pro AMS-Slot (welche Rolle steckt
wo und wie kam das zustande), das Gesamtfilament im Bestand, der Bestandswert
in der eingestellten Währung, die Anzahl der Rollen und wie viele zur Neige
gehen.

**Archivierte Rollen** verlieren ihre Entities und behalten ihre Daten.

> **Recorder:** bei ~20 Rollen × mehreren Entities die Verbrauchssensoren in der
> `recorder`-Konfiguration ausschließen, sonst wächst die Datenbank unnötig:
>
> ```yaml
> recorder:
>   exclude:
>     entity_globs:
>       - sensor.*_verbraucht_gesamt
>       - sensor.*_restlange
> ```

### Einkaufsliste

Unter *Konfigurieren → Einstellungen → Einkaufsliste* eine To-do-Liste
auswählen, und ein Filamenttyp landet darauf, sobald **jede** Rolle dieses
Typs unter ihrer Schwelle liegt — eine volle Reserve im Regal hält ihn von
der Liste fern. Der Typ wird gemerkt, also einmal eingetragen statt bei jedem
Neustart, und ein Nachkauf macht ihn wieder eintragsfähig.

---

## Dienste

| Dienst | Wirkung |
|---|---|
| `add_spool` | Eine oder mehrere Rollen eines bekannten Typs anlegen |
| `update_spool` | Stammfelder ändern |
| `duplicate_spool` | Weitere Rolle desselben Typs |
| `consume` | Filament abziehen, nach Gewicht oder Länge |
| `assign_slot` / `clear_slot` | Festhalten, was wo liegt |
| `archive_spool` / `restore_spool` / `delete_spool` | Lebenszyklus |
| `correct_weight` | Korrektur über Brutto- oder Nettogewicht |
| `add_vendor` / `add_type` | Filamentdatenbank erweitern |
| `link_tag` / `unlink_tag` | NFC-Sticker an Rolle oder Slot binden |
| `learn_rfid` | Bambu-Tag-UID auf eine Rolle anlernen |
| `import_db` / `export_db` / `export_contribution` / `reload_db` | Stammdaten |

Beispiel — einen von SD-Karte gestarteten Druck nachtragen:

```yaml
action: filament_manager.consume
data:
  slot: 1
  amount: 42.5
```

### Events

`filament_manager_spool_assigned`, `filament_manager_spool_consumed`,
`filament_manager_spool_empty`, `filament_manager_unknown_tag` und
`filament_manager_ambiguous_match` — genug für eigene Actionable Notifications
oder eine Einkaufslisten-Automation.

### WebSocket-Commands

`filament_manager/list`, `/types`, `/slots`, `/scan`, `/subscribe`,
`/add_spool`, `/add_type`, `/assign_slot`, `/consume`, `/correct_weight`,
`/archive`, `/link_tag`, `/contribution`.

Der Scan läuft über WebSocket statt über einen Dienst, damit die Karte direkt
reagieren kann, statt auf einen Sensor zu pollen.

---

## Die Karte

Die Integration registriert die Karte selbst, unter Einstellungen → Dashboards
→ Ressourcen ist nichts nachzutragen. Über die Kartenauswahl hinzufügen oder in
YAML:

```yaml
type: custom:filament-manager-card
title: Filament
show_stats: true
show_slots: true
show_archived: false
show_scanner: true
columns: 0     # 0 = eine pro Zeile
```

Sie ist gegen reine DOM-APIs geschrieben — nichts wird aus dem
Home-Assistant-Frontend-Bundle importiert —, damit ein Rename im Frontend nicht
zu unsichtbaren Eingabefeldern führt.

---

## Fehlersuche

**Es werden keine Slots erkannt.** Die Tray-Sensoren unter *Konfigurieren →
Drucker-Entities* von Hand zuordnen. Die Erkennung sucht Sensoren der Plattform
`bambu_lab`, deren Id `tray` enthält.

**Nach dem Druck wird nichts abgezogen.** Druckstatus- und
Druckgewicht-Entity müssen unter *Konfigurieren → Drucker-Entities* gesetzt
sein, und dem Slot muss eine Rolle zugewiesen sein. Das Log sagt, was fehlte.

**Ein unbekanntes RFID-Tag fragt immer wieder.** Einmal mit
`filament_manager.learn_rfid` anlernen oder den Button in der Benachrichtigung
tippen. Denk daran, dass Bambu-Rollen pro Seite ein Tag tragen.

**Debug-Logging:**

```yaml
logger:
  logs:
    custom_components.filament_manager: debug
```

---

## Veröffentlichungs-Checkliste

- [x] `custom_components/filament_manager/` als Repo-Struktur
- [x] `manifest.json` mit `version`, `documentation`, `issue_tracker`, `codeowners`
- [x] `hacs.json` im Repo-Root
- [x] `config_flow: true`
- [x] README mit Installation und Recorder-Hinweis
- [x] Übersetzungen `de` und `en`
- [x] hassfest- und HACS-Validierung in der CI
- [x] Brand-Assets unter `custom_components/filament_manager/brand/`
- [ ] **Repo-Description und Topics** — ohne sie schlägt die HACS-Validierung
      fehl, und sie lassen sich nur in der GitHub-Oberfläche setzen
      (Repository → About → Zahnrad). Vorschlag für Topics: `home-assistant`,
      `hacs`, `custom-component`, `3d-printing`, `filament`, `bambulab`, `ams`
- [ ] GitHub-Release mit Tag
- [ ] PR an [`home-assistant/brands`](https://github.com/home-assistant/brands) für das Icon, das danach die mitgelieferten Assets ablöst
- [ ] Aufnahme in den HACS-Default-Store beantragen

---

## Entwicklung

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements_test.txt
pytest
ruff check . && ruff format --check .
```

---

## Credits

Das Barcode-Lesen fällt auf [zxing-wasm](https://github.com/Sec-ant/zxing-wasm)
(MIT) zurück, mitgeliefert in `custom_components/filament_manager/www/`. Dieses
Projekt steht unter der MIT-Lizenz, siehe [LICENSE](LICENSE).
