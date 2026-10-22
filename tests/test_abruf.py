"""Abruf gegen den nachgebauten IMAP-Server – ohne Home Assistant."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from .nachbau import BILD_GROSS, MAIL_DHL, MAIL_DHL_NEWSLETTER, MAIL_EINGEBETTET, MAIL_LATIN1, MAIL_VERLINKT, ImapNachbau, oeffnen_nachbau

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


def test_pakete_und_briefe_getrennt() -> None:
    n = ImapNachbau({101: MAIL_EINGEBETTET, 201: MAIL_DHL, 202: MAIL_DHL_NEWSLETTER})
    try:
        geladen: list[str] = []
        neu, erledigt = abruf.abrufen({**cfg(n.port), "paket_absender": "dhl.de"}, set(), set(), sicher=False, oeffnen=oeffnen_nachbau(geladen))
    finally:
        n.stop()
    arten = {b["art"]: b for b in neu}
    assert set(arten) == {"brief", "paket"} and len(neu) == 2  # Newsletter ist kein Paket
    p = arten["paket"]
    assert p["absender"] == "Thalia Bücher GmbH" and p["sendung"] == "00340434161094015902" and p["stand"] == "heute"
    assert p["bild"] is None and geladen == []  # keine Werbebilder von DHL
    assert erledigt == {"INBOX:101", "INBOX:201", "INBOX:202"}  # Newsletter nicht noch einmal ansehen


def test_ohne_paket_absender_keine_pakete() -> None:
    n = ImapNachbau({201: MAIL_DHL})
    try:
        neu, _ = abruf.abrufen({**cfg(n.port), "paket_absender": ""}, set(), set(), sicher=False)
    finally:
        n.stop()
    assert neu == []


@pytest.mark.parametrize(("text", "erwartet"), [
    ("Ihr Paket von Amazon EU S.a.r.l. kommt heute zwischen 10 und 13 Uhr. Sendungsnummer JJD000390012345678", ("Amazon EU S.a.r.l", "JJD000390012345678", "heute")),
    ("Ihre Sendung 00340434161094015902 wurde erfolgreich zugestellt.", ("", "00340434161094015902", "zugestellt")),
    ("Ihre Sendung von Zalando SE ist unterwegs.", ("Zalando SE", "", "unterwegs")),
])
def test_paket_infos(text: str, erwartet: tuple) -> None:
    i = abruf.paket_infos(text)
    assert (i["absender"], i["sendung"], i["stand"]) == erwartet


def test_wichtig() -> None:
    liste = "Finanzamt, Gericht , Sparkasse"
    assert abruf.wichtig("Finanzamt Hamburg-Nord", liste)
    assert abruf.wichtig("Amtsgericht Wandsbek", liste)
    assert not abruf.wichtig("Hansemerkur KV AG", liste)
    assert not abruf.wichtig("", liste) and not abruf.wichtig("Finanzamt", "")
