# Briefankündigung

Liest zu festen Zeiten (Standard **02:00 und 05:00 Uhr**) dein Postfach per IMAP und sucht die Mails
der Deutschen Post „Ein Brief ist unterwegs zu Ihnen“ (Absender `ankuendigung@brief.deutschepost.de`).
Aus jeder Mail werden der **Absender des Briefs** („ein Brief von *…* ist unterwegs“) und das **Bild des
Umschlags** gelesen. Die Mails selbst bleiben unverändert (nichts wird als gelesen markiert oder gelöscht).

Ergebnis:
- Seite **„Briefe“** in der Seitenleiste mit allen angekündigten Briefen und „Jetzt abrufen“
- Sensor `sensor.briefankuendigungen` – Anzahl der Briefe der letzten 3 Tage (nur Zahlen, keine Absender)
- Ordner `/media/briefankuendigung` (Liste `index.json` + Bilder) – daraus liest das iPhone-Widget
  (Scriptable-Skript „Briefankuendigung“) mit deinem Home-Assistant-Zugang

## Einrichten (GMX)

1. Bei GMX im Browser: *E-Mail → Einstellungen → POP3/IMAP Abruf* → „Zugriff über POP3 und IMAP
   erlauben“ einschalten. Mit Zwei-Faktor-Anmeldung zusätzlich ein **App-Passwort** anlegen.
2. In der Konfiguration dieses Add-ons **E-Mail-Adresse** und **Passwort** (bzw. App-Passwort) eintragen.
   Server (`imap.gmx.net`, Port 993) ist voreingestellt. Andere Anbieter: `imap.web.de`, `imap.gmail.com` …
3. Add-on starten. Es ruft einmal sofort ab und danach zu den Abrufzeiten. Unter „Briefe“ siehst du das Ergebnis.

## iPhone-Widget

Skript „Briefankuendigung“ in Scriptable: beim ersten Start die Adresse deines Home Assistant und ein
langlebiges Zugriffstoken (Profil → Sicherheit) eingeben. Das Widget liest `index.json` und die Bilder über
`/media/local/briefankuendigung/…` – das geht nur mit gültigem Token.

## Datenschutz

Das Passwort steht nur in der Add-on-Konfiguration. Bilder werden nur aus der Mail selbst genommen oder,
wenn sie verlinkt sind, ausschließlich von Servern der Deutschen Post/DHL geladen (keine Zählpixel Dritter).
Alte Briefe (älter als „Tage aufheben“) werden samt Bild gelöscht.
