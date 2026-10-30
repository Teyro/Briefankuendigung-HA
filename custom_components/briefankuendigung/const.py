"""Konstanten der Integration Briefankündigung."""

DOMAIN = "briefankuendigung"
SIGNAL_NEU = "briefankuendigung_neu"

CONF_SERVER = "server"
CONF_PORT = "port"
CONF_BENUTZER = "benutzer"
CONF_PASSWORT = "passwort"
CONF_ABSENDER = "absender"
CONF_ORDNER = "ordner"
CONF_ZEITEN = "abrufzeiten"
CONF_TAGE = "tage"
CONF_NAME = "name"          # Anzeigename des Kontos, z. B. "Christian" oder "Johanna"
CONF_BENUTZER_HA = "ha_benutzer"  # Home-Assistant-Benutzer, die dieses Konto sehen dürfen (leer = alle)
CONF_PAKET = "paket_absender"     # Absender der DHL-Paketankündigungen (leer = keine Pakete)
CONF_WICHTIG = "wichtig"          # Stichwörter für wichtige Absender, mit Komma getrennt
CONF_EREIGNIS_ABSENDER = "ereignis_absender"  # Absender im Ereignis mitsenden (landet in der HA-Datenbank)
CONF_DIENSTE = "paketdienste"     # Paketdienste, deren Mails gelesen werden (dhl, hermes, dpd, amazon)
CONF_KUNDENKONTEN = "kundenkonten"  # „adresse = Name“ je Zeile – z. B. zwei Amazon-Konten in einem Postfach
CONF_DHL_SCHLUESSEL = "dhl_api_schluessel"  # optional: API-Schlüssel der DHL-Sendungsverfolgung (developer.dhl.com)
CONF_SIEBZEHN = "siebzehn_schluessel"  # optional: Schlüssel der 17TRACK-API (Hermes, DPD, GLS, UPS … live)
CONF_MITTEILEN = "mitteilen"      # Benachrichtigungsdienste (z. B. notify.mobile_app_iphone) für „kommt heute“ usw.

EREIGNIS = f"{DOMAIN}_neu"        # je neuem Brief/Paket: konto, art, wichtig, datum (+ absender, falls erlaubt)
ARCHIV_TAGE = 730                 # Archiv (nur Datum, Absender, Art) zwei Jahre aufheben

STANDARD = {
    CONF_SERVER: "imap.gmx.net",
    CONF_PORT: 993,
    CONF_ABSENDER: "ankuendigung@brief.deutschepost.de",
    CONF_ORDNER: "INBOX",
    CONF_ZEITEN: "02:00, 05:00",
    CONF_TAGE: 14,
    CONF_PAKET: "dhl.de",
    CONF_WICHTIG: "Finanzamt, Gericht, Staatsanwaltschaft, Polizei, Bußgeld, Inkasso, Rentenversicherung, Krankenkasse, Jobcenter, Agentur für Arbeit, Bezirksamt, Behörde, Bank, Sparkasse, Versicherung",
    CONF_EREIGNIS_ABSENDER: False,
}
CONF_UPS_ID = "ups_client_id"     # optional: UPS-Entwicklerzugang (developer.ups.com)
CONF_UPS_GEHEIM = "ups_client_secret"
CONF_SHIP24 = "ship24_schluessel"  # optional: Schlüssel der Ship24-API (Ersatz für 17TRACK)
CONF_OEFFENTLICH = "oeffentliche_seiten"  # öffentliche Sendungsverfolgung von GLS und DPD als Notnagel
EREIGNIS_PAKET = f"{DOMAIN}_paket"  # je neuem Paket-Stand: id, name, dienst, stand, vorher
CONF_ABWESEND = "niemand_da_melden"  # Hinweis, wenn heute ein Paket kommt und niemand zu Hause ist (Personen in HA)
