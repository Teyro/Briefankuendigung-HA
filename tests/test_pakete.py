"""Paketdienste (DHL, Hermes, DPD, Amazon) – ohne Home Assistant."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest

from .nachbau import MAIL_AMAZON_1, MAIL_AMAZON_2, MAIL_AMAZON_WERBUNG, MAIL_DHL, MAIL_DPD, MAIL_HERMES, ImapNachbau

_wurzel = Path(__file__).parent.parent / "custom_components" / "briefankuendigung"
_spec = importlib.util.spec_from_file_location("abruf_p", _wurzel / "abruf.py")
abruf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(abruf)
pakete = abruf.pakete


@pytest.fixture(autouse=True)
def netz_erlauben(request):
    """Die HA-Testumgebung sperrt Netzverbindungen – hier braucht es eine zum lokalen Nachbau."""
    try:
        request.getfixturevalue("socket_enabled")
    except pytest.FixtureLookupError:
        pass


@pytest.mark.parametrize(("von", "dienst"), [
    ("noreply@paketankuendigung.myhermes.de", "hermes"), ("DPD <noreply@dpd.de>", "dpd"), ("versandbestaetigung@amazon.de", "amazon"),
    ("noreply@dhl.de", "dhl"), ("info@otto.de", ""),
])
def test_dienst_von_absender(von: str, dienst: str) -> None:
    assert pakete.dienst_von_absender(von) == dienst


@pytest.mark.parametrize(("nummer", "dienst"), [
    ("TBA301234567890", "amazon"), ("302-1234567-7654321", "amazon"), ("00340434161094015902", "dhl"), ("JJD000390012345678", "dhl"),
    ("H1002345678901234567", "hermes"), ("01234567890123", ""), ("1Z999AA10123456784", "ups"),
])
def test_dienst_von_nummer(nummer: str, dienst: str) -> None:
    assert pakete.dienst_von_nummer(nummer) == dienst


def test_erwartet_und_stand() -> None:
    b = date(2026, 10, 21)
    assert pakete.erwartet("Voraussichtliche Zustellung: 23.10.2026 zwischen 10:00 und 13:00 Uhr", b) == ("2026-10-23", "10:00–13:00")
    assert pakete.erwartet("Lieferung: Donnerstag, 22. Oktober", b) == ("2026-10-22", "")
    assert pakete.erwartet("Ihr Paket kommt heute zwischen 9:30 und 10:30", b) == ("2026-10-21", "09:30–10:30")
    assert pakete.erwartet("Zustellung voraussichtlich am 2.1.", date(2026, 12, 30)) == ("2027-01-02", "")
    assert pakete.stand("Ihr Paket liegt in der Filiale zur Abholung bereit") == "abholbereit"
    assert pakete.stand("Zugestellt: „Kaffee“") == "zugestellt"
    assert not pakete.ist_sendung("Entdecken Sie unsere Angebote und Gutscheine.")
    assert pakete.ist_sendung("Ihr Paket wurde versandt")


def test_verfolgen_und_konten() -> None:
    assert pakete.verfolgen("dpd", "01234567890123") == "https://tracking.dpd.de/status/de_DE/parcel/01234567890123"
    assert pakete.verfolgen("amazon", "302-1234567-7654321").endswith("orderID=302-1234567-7654321")
    assert pakete.verfolgen("amazon", "TBA301234567890") == "https://track.amazon.de/tracking/TBA301234567890"
    assert pakete.verfolgen("hermes", "H1").startswith("https://www.myhermes.de/")
    assert pakete.konten_lesen("kim@gmx.de = Kim\njohanna.k@gmx.de: Johanna\nquatsch") == {"kim@gmx.de": "Kim", "johanna.k@gmx.de": "Johanna"}


def test_dhl_status() -> None:
    a = {"shipments": [{"status": {"statusCode": "transit", "description": "Die Sendung wurde in das Zustellfahrzeug geladen."},
                        "estimatedTimeOfDelivery": "2026-10-21T12:00:00Z"}]}
    assert pakete.dhl_status(a) == {"stand": "heute", "erwartet": "2026-10-21", "text": "Die Sendung wurde in das Zustellfahrzeug geladen.",
                                    "ereignisse": []}
    a["shipments"][0]["events"] = [{"timestamp": "2026-10-21T07:00:00", "description": "In Zustellung",
                                    "location": {"address": {"addressLocality": "Hamburg"}}}, {"timestamp": None}]
    assert pakete.dhl_status(a)["ereignisse"] == [{"zeit": "2026-10-21T07:00:00", "text": "In Zustellung", "ort": "Hamburg"}]
    assert pakete.dhl_status({"shipments": [{"status": {"statusCode": "delivered"}}]})["stand"] == "zugestellt"
    assert pakete.dhl_status({}) is None


def cfg(port: int, **mehr) -> dict:
    return {"server": "127.0.0.1", "port": port, "benutzer": "kim@gmx.de", "passwort": "richtig", "ordner": "INBOX", "tage": 400,
            "absender": "ankuendigung@brief.deutschepost.de", **mehr}


def test_alle_dienste_aus_dem_postfach() -> None:
    n = ImapNachbau({1: MAIL_DHL, 2: MAIL_HERMES, 3: MAIL_DPD, 4: MAIL_AMAZON_1, 5: MAIL_AMAZON_2, 6: MAIL_AMAZON_WERBUNG})
    try:
        neu, erledigt = abruf.abrufen(cfg(n.port, paketdienste=["dhl", "hermes", "dpd", "amazon"]), set(), set(), sicher=False)
    finally:
        n.stop()
    nach = {p["dienst"] + ":" + p["sendung"]: p for p in neu}
    assert len(neu) == 5, [p["betreff"] for p in neu]  # Werbung ist keine Sendung
    h = nach["hermes:H1002345678901234567"]
    assert h["absender"] == "OTTO GmbH & Co KG" and h["erwartet"] == "2026-10-23" and h["zeitfenster"] == "10:00–13:00" and h["stand"] == "unterwegs"
    d = nach["dpd:01234567890123"]
    assert d["absender"] == "Zalando SE" and d["stand"] == "heute" and d["erwartet"] == "2026-10-21"
    a1 = nach["amazon:TBA301234567890"]
    assert a1["absender"] == "Kindle Paperwhite (16 GB)" and a1["erwartet"] == "2026-10-22" and a1["empfaenger"] == "kim@gmx.de"
    a2 = nach["amazon:TBA309999999999"]
    assert a2["stand"] == "zugestellt" and a2["empfaenger"] == "johanna.k@gmx.de"
    assert len(erledigt) == 6


def test_nur_gewaehlte_dienste() -> None:
    n = ImapNachbau({2: MAIL_HERMES, 3: MAIL_DPD})
    try:
        neu, _ = abruf.abrufen(cfg(n.port, paketdienste=["dpd"]), set(), set(), sicher=False)
    finally:
        n.stop()
    assert [p["dienst"] for p in neu] == ["dpd"]


def test_siebzehn_status_und_verlauf() -> None:
    e = {"number": "H1", "track_info": {
        "latest_status": {"status": "AvailableForPickup"},
        "latest_event": {"description": "Liegt im Paketshop", "description_translation": {"description": "Liegt im Paketshop bereit"}},
        "time_metrics": {"estimated_delivery_date": {"from": "2026-10-22T00:00:00+02:00"}},
        "tracking": {"providers": [{"provider": {"name": "Hermes"}, "events": [
            {"time_iso": "2026-10-20T10:00:00+02:00", "description": "Im Verteilzentrum", "location": "Ketzin"},
            {"time_iso": "2026-10-21T09:00:00+02:00", "description": "Liegt im Paketshop"}]}]}}}
    st = pakete.siebzehn_status(e)
    assert st["stand"] == "abholbereit" and st["erwartet"] == "2026-10-22" and st["text"] == "Liegt im Paketshop bereit"
    assert [x["text"] for x in st["ereignisse"]] == ["Liegt im Paketshop", "Im Verteilzentrum"] and not st["ende"]
    assert pakete.siebzehn_status({"number": "x", "track_info": {"latest_status": {"status": "Delivered"}}})["ende"] is True
    assert pakete.siebzehn_status({"track_info": {"latest_status": {"status": "Exception"}}})["problem"] is True
    assert pakete.siebzehn_status("quatsch") is None
    # zusammenführen: ohne Doppelte, neuestes zuerst
    alt = [{"zeit": "2026-10-20T10:00:00+02:00", "text": "Im Verteilzentrum"}]
    assert len(pakete.ereignisse_zusammen(alt, st["ereignisse"])) == 2
    assert pakete.verfolgen("gls", "12345678901").startswith("https://gls-group.com/")


def test_ersatz_anbieter() -> None:
    ups = pakete.ups_status({"currentStatus": {"type": "I", "description": "Out For Delivery Today"},
                             "deliveryDate": [{"type": "SDD", "date": "20261023"}], "deliveryTime": {"startTime": "100000", "endTime": "140000"},
                             "activity": [{"date": "20261023", "time": "071500", "status": {"type": "I", "description": "OUT FOR DELIVERY TODAY"},
                                           "location": {"address": {"city": "HAMBURG", "countryCode": "DE"}}}]})
    assert ups["stand"] == "heute" and ups["erwartet"] == "2026-10-23" and ups["zeitfenster"] == "10:00–14:00"
    assert ups["ereignisse"][0] == {"zeit": "2026-10-23T07:15:00", "text": "Out for delivery today", "ort": "Hamburg, DE"}
    s24 = pakete.ship24_status({"shipment": {"statusMilestone": "available_for_pickup", "delivery": {"estimatedDeliveryDate": "2026-10-22T00:00:00Z"}},
                                "events": [{"occurrenceDatetime": "2026-10-22T09:00:00", "status": "Im Paketshop", "location": "Hamburg"}]})
    assert s24["stand"] == "abholbereit" and s24["erwartet"] == "2026-10-22" and s24["text"] == "Im Paketshop"
    gls = pakete.gls_status({"tuStatus": [{"progressBar": {"statusInfo": "DELIVERED"},
                                           "history": [{"date": "2026-10-21", "time": "11:00:00", "evtDscr": "Zugestellt", "address": {"city": "Hamburg"}}]}]})
    assert gls["stand"] == "zugestellt" and gls["ende"] and gls["ereignisse"][0]["ort"] == "Hamburg"
    dpd = pakete.dpd_status({"parcellifecycleResponse": {"parcelLifeCycleData": {
        "statusInfo": [{"status": "COURIER", "label": "In Zustellung", "statusHasBeenReached": True, "isCurrentStatus": True}],
        "scanInfo": {"scan": [{"date": "2026-10-21T07:00:00", "scanData": {"location": "Hamburg"}, "scanDescription": {"content": ["Paket ist in Zustellung."]}}]}}}})
    assert dpd["stand"] == "heute" and dpd["ereignisse"][0]["text"] == "Paket ist in Zustellung."
    assert pakete.gls_status({}) is None and pakete.dpd_status({}) is None and pakete.ups_status(None) is None
