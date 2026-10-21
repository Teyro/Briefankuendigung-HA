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

STANDARD = {
    CONF_SERVER: "imap.gmx.net",
    CONF_PORT: 993,
    CONF_ABSENDER: "ankuendigung@brief.deutschepost.de",
    CONF_ORDNER: "INBOX",
    CONF_ZEITEN: "02:00, 05:00",
    CONF_TAGE: 14,
}
