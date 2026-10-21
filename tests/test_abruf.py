"""Abruf gegen den nachgebauten IMAP-Server – ohne Home Assistant."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from .nachbau import BILD_GROSS, MAIL_EINGEBETTET, MAIL_LATIN1, MAIL_VERLINKT, ImapNachbau, oeffnen_nachbau

# abruf.py direkt laden (ohne das Paket – das braucht Home Assistant)
_spec = importlib.util.spec_from_file_location("abruf", Path(__file__).parent.parent / "custom_components" / "briefankuendigung" / "abruf.py")
abruf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(abruf)


@pytest.fixture(autouse=True)
def netz_erlauben(request):
    """Die HA-Testumgebung sperrt Netzverbindungen – hier braucht es eine zum lokalen Nachbau."""
    try:
        request.getfixturevalue("socket_enabled")
    except pytest.FixtureLookupError:
        pass


@pytest.fixture
def imap():
    n = ImapNachbau({101: MAIL_EINGEBETTET, 102: MAIL_VERLINKT, 103: MAIL_LATIN1})
    yield n
    n.stop()


def cfg(port: int, passwort: str = "richtig") -> dict:
    return {"server": "127.0.0.1", "port": port, "benutzer": "kim@gmx.de", "passwort": passwort,
            "absender": "ankuendigung@brief.deutschepost.de", "ordner": "INBOX", "tage": 400}


def test_abruf_drei_mails(imap) -> None:
    geladen: list[str] = []
    neu, erledigt = abruf.abrufen(cfg(imap.port), set(), set(), sicher=False, oeffnen=oeffnen_nachbau(geladen))
    nach_absender = {b["absender"]: b for b in neu}
    assert set(nach_absender) == {"Hansemerkur KV AG Hauptverwaltung", "Finanzamt Hamburg-Nord", "Bezirksamt Wandsbek - Bürgeramt"}
    assert nach_absender["Hansemerkur KV AG Hauptverwaltung"]["bild"] == BILD_GROSS
    assert nach_absender["Finanzamt Hamburg-Nord"]["bild"] is not None  # von der Post nachgeladen
    assert nach_absender["Bezirksamt Wandsbek - Bürgeramt"]["bild"] is None
    assert nach_absender["Bezirksamt Wandsbek - Bürgeramt"]["betreff"] == "Ein Brief für Sie"
    assert geladen == ["https://briefankuendigung.deutschepost.de/scan/xyz.jpg"]  # kein Zählpixel
    assert not imap.nicht_gepeekt  # nichts als gelesen markiert
    assert "EXAMINE" in imap.befehle  # schreibgeschützt geöffnet
    assert erledigt == {"INBOX:101", "INBOX:102", "INBOX:103"}

    # zweiter Abruf: nichts erneut holen
    imap.befehle.clear()
    neu2, _ = abruf.abrufen(cfg(imap.port), erledigt, {b["id"] for b in neu}, sicher=False, oeffnen=oeffnen_nachbau([]))
    assert neu2 == []
    assert "UID FETCH" not in imap.befehle


def test_falsches_passwort(imap) -> None:
    with pytest.raises(abruf.AnmeldeFehler):
        abruf.abrufen(cfg(imap.port, 'alt"pw\\x'), set(), set(), sicher=False)


def test_server_nicht_erreichbar() -> None:
    with pytest.raises(abruf.VerbindungsFehler):
        abruf.verbinde("127.0.0.1", 1, "a", "b", sicher=False)


@pytest.mark.parametrize(("text", "erwartet"), [
    ("Ein Brief von Sparkasse Holstein wird Ihnen in Kürze zugestellt", "Sparkasse Holstein"),
    ("Ein Brief ist unterwegs zu Ihnen.", ""),
    ("eine Briefsendung von AOK Nordwest ist auf dem Weg zu Ihnen", "AOK Nordwest"),
])
def test_absender(text: str, erwartet: str) -> None:
    assert abruf.brief_absender(text) == erwartet
