# Briefankündigung für Home Assistant (HACS)

<img src="custom_components/briefankuendigung/brand/icon.png" alt="" width="96" align="right">

[![In HACS öffnen](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Teyro&repository=Briefankuendigung-HA&category=integration)

Integration, die die Briefankündigungen der Deutschen Post („Ein Brief ist unterwegs zu Ihnen“) aus deinem
Postfach liest (IMAP, z. B. GMX) – mit **Absender** und **Bild des Umschlags**. Funktioniert mit jeder
Home-Assistant-Installation (auch Container/Core, ohne Add-ons). Dazu gibt es ein iPhone-Widget (Scriptable),
das die angekündigten Briefe als Vorschau zeigt.

- Abruf zu festen Zeiten, Standard **02:00 und 05:00 Uhr** (einstellbar), nur lesend – nichts wird als gelesen
  markiert oder gelöscht
- Sensoren **Angekündigte Briefe** / **Angekündigte Pakete** (Anzahl der letzten 3 Tage, ohne Namen), **Post heute**
  (an, wenn heute etwas angekündigt ist; Anzahl Briefe, Pakete, wichtige) und Knopf **Jetzt abrufen**
- **Pakete von DHL, Hermes, DPD und Amazon:** Die Versandmails der Dienste werden mitgelesen (in den Optionen
  wählbar) – mit Absender bzw. Artikel, Sendungsnummer, Stand (unterwegs / heute / zur Abholung bereit /
  zugestellt), voraussichtlicher Zustellung samt Zeitfenster und Link zur Sendungsverfolgung des Dienstes.
  Siehe [Pakete](#pakete)
- **Wichtige Absender:** Stichwörter wie „Finanzamt, Gericht, Bank“ (in den Optionen) – solche Briefe sind
  im Widget hervorgehoben und werden extra gemeldet
- **Ereignis `briefankuendigung_neu`** je neuem Brief/Paket (für Automationen, siehe unten)
- **Archiv:** Datum, Absender und Art der letzten zwei Jahre, durchsuchbar im Widget
- **Pakete mit Verlauf:** Mehrere DHL-Mails zur selben Sendungsnummer (unterwegs → heute → zugestellt) ergeben
  einen Eintrag mit Verlauf statt mehrerer
- **„Angekommen“ für alle Geräte:** Im Widget abgehakte Briefe gelten überall (Attribut `offen` an den Sensoren)
- Für das Widget: `/api/briefankuendigung/briefe`, `/api/briefankuendigung/bild/<id>` und `/api/briefankuendigung/archiv` – nur mit gültigem
  Home-Assistant-Token erreichbar
- Bilder und Liste liegen in `<config>/briefankuendigung/` (nicht öffentlich), alte Briefe werden gelöscht

## Installieren

1. Auf den Knopf **„In HACS öffnen“** oben tippen (oder HACS → ⋮ → **Benutzerdefinierte Repositories** →
   `https://github.com/Teyro/Briefankuendigung-HA`, Typ **Integration**), dann „Briefankündigung“ herunterladen.
2. Home Assistant neu starten.
3. Einstellungen → Geräte & Dienste → **Integration hinzufügen** → „Briefankündigung“:
   E-Mail-Adresse und Passwort eingeben. Der Zugang wird beim Speichern geprüft.

**GMX:** Im Browser unter *E-Mail → Einstellungen → POP3/IMAP Abruf* den Zugriff per IMAP erlauben. Mit
Zwei-Faktor-Anmeldung ein **App-Passwort** anlegen und das verwenden. Server `imap.gmx.net`, Port 993
(voreingestellt). Andere Anbieter: `imap.web.de`, `imap.gmail.com` …

## Mehrere Postfächer (z. B. Christian und Johanna)

Die Integration einfach **mehrmals hinzufügen** – je Postfach ein Eintrag mit eigenem Namen. Jedes Postfach
bekommt ein eigenes Gerät („Briefankündigung Christian“, „Briefankündigung Johanna“) mit Sensor und Knopf.

**Trennen:** Unter *Konfigurieren* bzw. beim Einrichten „Sichtbar für“ die Home-Assistant-Benutzer wählen,
die das Postfach sehen dürfen (nichts gewählt = alle). Johanna legt mit **ihrem eigenen** Home-Assistant-Benutzer
ein Zugriffstoken an und trägt es in ihrem Widget ein – dann sieht sie nur ihre Briefe (und Postfächer ohne
Einschränkung). Die Adresse `/api/briefankuendigung/briefe?konto=johanna` liefert nur ein Postfach.

## Pakete

Unter *Konfigurieren* → **Paketdienste** die Dienste wählen, deren Mails gelesen werden (DHL, Hermes, DPD,
Amazon). Weitere Absender (z. B. ein Shop, der selbst verschickt) gehen unter *Weitere Absender von Paket-Mails*.
Gelesen werden nur Versand- und Zustellmails – keine Werbung und keine Bestellbestätigungen. Mehrere Mails zur
selben Sendung ergeben einen Eintrag mit Verlauf.

**Mehrere Amazon-Konten:**

- **Landen alle Mails in einem Postfach** (z. B. über Weiterleitung oder Aliasse), unter **Kundenkonten** je
  Zeile Adresse und Namen eintragen, z. B. `johanna.k@gmx.de = Johanna`. Dann steht an jedem Paket, für wen es
  ist (Feld `kundenkonto`).
- **Hat jedes Konto ein eigenes Postfach,** die Integration einfach je Postfach hinzufügen (siehe unten).

**Sendung von Hand:** Für Pakete ohne Mail (z. B. Kleinanzeigen) die Nummer selbst eintragen – in den
Desktop-Apps und im Widget über „Sendung hinzufügen“ oder per Aktion:

```yaml
action: briefankuendigung.sendung_hinzufuegen
data: { nummer: "H1234567890123456789", name: "Laufschuhe" }   # dienst: dhl|hermes|dpd|amazon (sonst erkannt)
```

**DHL live (optional):** Mit einem eigenen kostenlosen Schlüssel für die *Shipment Tracking – Unified* API
(developer.dhl.com) unter **DHL-API-Schlüssel** fragt die Integration offene DHL-Sendungen stündlich direkt bei DHL
ab – mit genauerem Stand und Termin. Für Hermes, DPD und Amazon gibt es keine offene Schnittstelle; dort zählen
die Mails. Anmeldungen bei Amazon & Co. werden bewusst nicht nachgebaut (Zwei-Faktor, Nutzungsbedingungen).

Der Sensor **Angekündigte Pakete** hat das Attribut `nach_dienst` (z. B. `{"dhl": 1, "amazon": 2}`).

## Kalender, Aufgabenliste, Dashboard-Karte, Sprachassistent (ab 1.6)

- **Kalender „Lieferungen“**: erwartete Pakete als ganztägige Termine (mit Zeitfenster)
- **Aufgabenliste „Pakete“**: abhaken = angekommen; neuer Eintrag „Sendungsnummer Name“ = Sendung verfolgen
- **Sensor „Paket in Zustellung“**: an, solange ein Paket heute kommt – z. B. für ein Licht an der Haustür
- **Dashboard-Karte** ohne Zusatz-Installation: `type: custom:paketbote-card` (Optionen `titel`, `konto`, `zugestellt`, `hinzufuegen`)
- **Aktion `briefankuendigung.heute`** (mit Antwort): Satz und Zahlen für den Sprachassistenten
- **Ereignis `briefankuendigung_paket`** bei jedem neuen Stand (`stand`, `vorher`, `dienst`, `erwartet`)
- **Vorlagen** (Blueprints) in [`blueprints/automation/briefankuendigung`](blueprints/automation/briefankuendigung):
  Sprachassistent („Welche Pakete kommen heute?“), Licht bei Zustellung, Ansage bzw. Mitteilung bei neuem Stand

**Live-Stand ohne Mail:** DHL und Deutsche Post fragt die Integration ohne Schlüssel über die öffentliche
Sendungsverfolgung (dhl.de, ersatzweise deutschepost.de). Weitere Quellen der Reihe nach, wenn eingetragen:
DHL-Schlüssel, 17TRACK, UPS-Zugang, Ship24, öffentliche Seiten von GLS und DPD. Fällt eine aus, springt die nächste ein.

## Automationen

Je neuem Brief oder Paket feuert das Ereignis `briefankuendigung_neu` mit `konto`, `art` (`brief`/`paket`),
`wichtig` und `datum`. Den **Absender** enthält es nur, wenn du das in den Optionen erlaubst – Ereignisse landen
in der Home-Assistant-Datenbank. Beispiel: ein Lautsprecher sagt Bescheid, wenn wichtige Post kommt.

```yaml
automation:
  - alias: Wichtige Post ansagen
    triggers:
      - trigger: event
        event_type: briefankuendigung_neu
        event_data: { wichtig: true }
    actions:
      - action: notify.alexa_media_kueche
        data:
          message: "Heute kommt wichtige Post{{ ' von ' ~ trigger.event.data.absender if trigger.event.data.absender }}."
          data: { type: announce }
```

Oder einfach mit dem Sensor **Post heute** (z. B. eine Lampe leuchtet, solange er `on` ist).

## iPhone-Widget

Skript „Briefankuendigung“ in Scriptable: beim ersten Start die Adresse deines Home Assistant und ein
langlebiges Zugriffstoken (Profil → Sicherheit) eingeben. Mit mehreren Postfächern zeigt das Widget alle
(mit Namen auf jeder Karte) oder nur eins – im Skript unter „Postfach“ oder pro Widget mit dem Namen als
Widget-Parameter, z. B. `johanna`.

## Schnittstelle (für das Widget)

Alle Adressen brauchen ein gültiges Home-Assistant-Token und zeigen nur Postfächer, die der Benutzer sehen darf.

| Adresse | Zweck |
|---|---|
| `GET /api/briefankuendigung/briefe[?konto=]` | Liste mit Konten (inkl. `zeiten`, `naechster_abruf`), Briefen und Paketen (`erledigt`, `verlauf`) |
| `GET /api/briefankuendigung/bild/<id>` | Umschlagbild |
| `GET /api/briefankuendigung/archiv[?q=&konto=]` | Archiv der letzten zwei Jahre |
| `POST /api/briefankuendigung/erledigt` | `{"id": "…", "erledigt": true}` – als angekommen markieren |
| `POST /api/briefankuendigung/abrufen[?konto=]` | Postfach jetzt lesen (höchstens einmal pro Minute) |
| `POST /api/briefankuendigung/sendung` | `{"nummer": "…", "dienst": "dpd", "name": "…", "konto": "…"}` – Sendung von Hand verfolgen (ab 1.4) |
| `POST /api/briefankuendigung/sendung/entfernen` | `{"id": "…"}` – eigene Sendung entfernen (aus Mails: als angekommen markieren) |

Pakete haben ab 1.4 zusätzlich `dienst`, `dienst_name`, `erwartet` (`2026-10-23`), `zeitfenster`, `kundenkonto`,
`verfolgen` (Link), `manuell` und `status_text` (DHL live).

Das Symbol liegt in `custom_components/briefankuendigung/brand/` (ab Home Assistant 2026.3 wird es direkt angezeigt).
