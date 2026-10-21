# Briefankündigung für Home Assistant (HACS)

Integration, die die Briefankündigungen der Deutschen Post („Ein Brief ist unterwegs zu Ihnen“) aus deinem
Postfach liest (IMAP, z. B. GMX) – mit **Absender** und **Bild des Umschlags**. Funktioniert mit jeder
Home-Assistant-Installation (auch Container/Core, ohne Add-ons). Dazu gibt es ein iPhone-Widget (Scriptable),
das die angekündigten Briefe als Vorschau zeigt.

- Abruf zu festen Zeiten, Standard **02:00 und 05:00 Uhr** (einstellbar), nur lesend – nichts wird als gelesen
  markiert oder gelöscht
- Sensor **Angekündigte Briefe** (Anzahl der letzten 3 Tage, ohne Namen) und Knopf **Jetzt abrufen**
- Für das Widget: `/api/briefankuendigung/briefe` und `/api/briefankuendigung/bild/<id>` – nur mit gültigem
  Home-Assistant-Token erreichbar
- Bilder und Liste liegen in `<config>/briefankuendigung/` (nicht öffentlich), alte Briefe werden gelöscht

## Installieren

1. HACS → ⋮ → **Benutzerdefinierte Repositories** → `https://github.com/Teyro/Briefankuendigung-HA`,
   Typ **Integration** → hinzufügen, dann „Briefankündigung“ herunterladen.
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

## iPhone-Widget

Skript „Briefankuendigung“ in Scriptable: beim ersten Start die Adresse deines Home Assistant und ein
langlebiges Zugriffstoken (Profil → Sicherheit) eingeben. Mit mehreren Postfächern zeigt das Widget alle
(mit Namen auf jeder Karte) oder nur eins – im Skript unter „Postfach“ oder pro Widget mit dem Namen als
Widget-Parameter, z. B. `johanna`.
