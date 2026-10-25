"""Integration im echten Home-Assistant-Kern (pytest-homeassistant-custom-component)."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.briefankuendigung import abruf, zeiten_lesen
from custom_components.briefankuendigung.const import DOMAIN

DATEN = {"name": "Kim", "benutzer": "kim@gmx.de", "passwort": "geheim", "server": "imap.gmx.net", "port": 993,
         "abrufzeiten": "02:00, 05:00", "tage": 14, "absender": "ankuendigung@brief.deutschepost.de", "ordner": "INBOX"}


def eid(hass: HomeAssistant, entry, art: str) -> str:
    """Entitäts-ID über die feste unique_id (der Name hängt von der Sprache ab)."""
    plattform = "button" if art == "abrufen" else "binary_sensor" if art == "post_heute" else "sensor"
    e = er.async_get(hass).async_get_entity_id(plattform, DOMAIN, f"{entry.entry_id}_{art}")
    assert e, art
    return e


@pytest.fixture(autouse=True)
def eigene_integrationen(enable_custom_integrations):
    yield


async def test_einrichtung_prueft_zugang(hass: HomeAssistant) -> None:
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert r["type"] is FlowResultType.FORM
    with patch.object(abruf, "verbinde", side_effect=abruf.AnmeldeFehler("nein")):
        r2 = await hass.config_entries.flow.async_configure(r["flow_id"], DATEN)
    assert r2["errors"] == {"base": "anmeldung"}
    class Imap:
        def logout(self): pass
    with patch.object(abruf, "verbinde", return_value=Imap()), \
         patch("custom_components.briefankuendigung.async_setup_entry", return_value=True):
        r3 = await hass.config_entries.flow.async_configure(r["flow_id"], DATEN)
    assert r3["type"] is FlowResultType.CREATE_ENTRY
    assert r3["title"] == "Kim"


async def test_abruf_sensor_und_api(hass: HomeAssistant, hass_client, hass_client_no_auth, tmp_path) -> None:
    hass.config.config_dir = str(tmp_path)
    entry = MockConfigEntry(domain=DOMAIN, data=DATEN, unique_id="kim@gmx.de")
    entry.add_to_hass(hass)
    jetzt = int(time.time() * 1000)
    neue = [
        {"id": "a" * 20, "datum": jetzt - 3600_000, "absender": "Hansemerkur KV AG", "betreff": "Ein Brief", "bild": b"\xff\xd8JPEGDATEN" * 500, "bild_typ": "image/jpeg"},
        {"id": "b" * 20, "datum": jetzt - 5 * 86400_000, "absender": "Finanzamt", "betreff": "Ein Brief", "bild": None, "bild_typ": None},
    ]
    with patch.object(abruf, "abrufen", return_value=(neue, {"INBOX:1", "INBOX:2"})) as aufruf:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await hass.services.async_call("button", "press", {"entity_id": eid(hass, entry, "abrufen")}, blocking=True)
        await hass.async_block_till_done()
        assert aufruf.called

    zustand = hass.states.get(eid(hass, entry, "briefe"))
    assert zustand is not None, [s.entity_id for s in hass.states.async_all()]
    assert zustand.state == "1"  # nur der Brief der letzten 3 Tage
    assert zustand.attributes["gesamt"] == 2
    assert "Hansemerkur" not in str(zustand.attributes)  # keine Absender im Recorder

    client = await hass_client()
    r = await client.get("/api/briefankuendigung/briefe")
    assert r.status == 200
    daten = await r.json()
    assert [b["absender"] for b in daten["briefe"]] == ["Hansemerkur KV AG", "Finanzamt"]
    assert daten["briefe"][0]["bild"] is True and daten["fehler"] == ""
    r = await client.get("/api/briefankuendigung/bild/" + "a" * 20)
    assert r.status == 200 and r.content_type == "image/jpeg" and (await r.read()).startswith(b"\xff\xd8")
    assert (await client.get("/api/briefankuendigung/bild/" + "b" * 20)).status == 404
    assert (await client.get("/api/briefankuendigung/bild/..%2F..%2Fsecrets.yaml")).status in (400, 404)

    ohne = await hass_client_no_auth()
    assert (await ohne.get("/api/briefankuendigung/briefe")).status == 401
    assert (await ohne.get("/api/briefankuendigung/bild/" + "a" * 20)).status == 401

    # Anmeldefehler wird angezeigt, Briefe bleiben
    with patch.object(abruf, "abrufen", side_effect=abruf.AnmeldeFehler("nein")):
        await hass.services.async_call("button", "press", {"entity_id": eid(hass, entry, "abrufen")}, blocking=True)
        await hass.async_block_till_done()
    daten = await (await client.get("/api/briefankuendigung/briefe")).json()
    assert "Anmeldung" in daten["fehler"] and len(daten["briefe"]) == 2

    assert await hass.config_entries.async_unload(entry.entry_id)


def test_zeiten_lesen() -> None:
    assert zeiten_lesen("02:00, 05:00") == [(2, 0), (5, 0)]
    assert zeiten_lesen("7:30;22:15") == [(7, 30), (22, 15)]
    assert zeiten_lesen("quatsch") == [(2, 0), (5, 0)]


async def test_zwei_konten_getrennt(hass: HomeAssistant, hass_client, hass_admin_user, tmp_path) -> None:
    """Christian und Johanna: zwei Postfächer; Johannas ist nur für ihren HA-Benutzer sichtbar."""
    hass.config.config_dir = str(tmp_path)
    jetzt = int(time.time() * 1000)
    johanna_user = await hass.auth.async_create_user("Johanna")
    e1 = MockConfigEntry(domain=DOMAIN, title="Christian", data={**DATEN, "name": "Christian", "benutzer": "c@gmx.de"}, unique_id="c@gmx.de")
    e2 = MockConfigEntry(domain=DOMAIN, title="Johanna", data={**DATEN, "name": "Johanna", "benutzer": "j@gmx.de"},
                         options={"ha_benutzer": [johanna_user.id]}, unique_id="j@gmx.de")
    e1.add_to_hass(hass); e2.add_to_hass(hass)

    def antwort(cfg, *_a, **_k):
        wer = cfg["name"]
        return ([{"id": (wer[0].lower() * 20), "datum": jetzt, "absender": "Brief an " + wer, "betreff": "", "bild": None, "bild_typ": None}], set())
    with patch.object(abruf, "abrufen", side_effect=antwort):
        assert await hass.config_entries.async_setup(e1.entry_id)
        await hass.async_block_till_done()
        for e in (e1, e2):
            await hass.services.async_call("button", "press", {"entity_id": eid(hass, e, "abrufen")}, blocking=True)
        await hass.async_block_till_done()
    assert hass.states.get(eid(hass, e1, "briefe")).state == "1"
    assert hass.states.get(eid(hass, e2, "briefe")).state == "1"
    assert "christian" in eid(hass, e1, "briefe") and "johanna" in eid(hass, e2, "briefe")  # Gerätename je Konto

    # Admin (nicht Johanna) sieht nur Christians Konto
    client = await hass_client()
    daten = await (await client.get("/api/briefankuendigung/briefe")).json()
    assert [k["name"] for k in daten["konten"]] == ["Christian"]
    assert [b["absender"] for b in daten["briefe"]] == ["Brief an Christian"]
    assert daten["briefe"][0]["konto_name"] == "Christian"

    # Johanna mit eigenem Token: nur ihr Konto
    from datetime import timedelta
    from homeassistant.auth import models
    rt = await hass.auth.async_create_refresh_token(johanna_user, client_name="iPhone Johanna",
                                                    token_type=models.TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN, access_token_expiration=timedelta(days=30))
    token = hass.auth.async_create_access_token(rt)
    jc = await hass_client(token)
    daten = await (await jc.get("/api/briefankuendigung/briefe")).json()
    assert [k["name"] for k in daten["konten"]] == ["Christian", "Johanna"]  # Christians Konto ist für alle sichtbar
    daten = await (await jc.get("/api/briefankuendigung/briefe?konto=johanna")).json()
    assert [b["absender"] for b in daten["briefe"]] == ["Brief an Johanna"]


async def test_pakete_wichtig_ereignis_archiv(hass: HomeAssistant, hass_client, tmp_path) -> None:
    """1.2: Pakete, wichtige Absender, Ereignis je neuem Brief, „Post heute“ und Archiv mit Suche."""
    hass.config.config_dir = str(tmp_path)
    entry = MockConfigEntry(domain=DOMAIN, data=DATEN, unique_id="kim@gmx.de")
    entry.add_to_hass(hass)
    jetzt = int(time.time() * 1000)
    neue = [
        {"id": "c" * 20, "datum": jetzt - 60_000, "art": "brief", "absender": "Finanzamt Hamburg-Nord", "betreff": "", "bild": None, "bild_typ": None},
        {"id": "d" * 20, "datum": jetzt - 120_000, "art": "paket", "absender": "Thalia", "betreff": "", "bild": None, "bild_typ": None,
         "sendung": "00340434161094015902", "stand": "heute"},
        {"id": "e" * 20, "datum": jetzt - 3 * 86400_000, "art": "brief", "absender": "AOK", "betreff": "", "bild": None, "bild_typ": None},
    ]
    ereignisse = []
    hass.bus.async_listen("briefankuendigung_neu", lambda e: ereignisse.append(e.data))
    with patch.object(abruf, "abrufen", return_value=(neue, set())):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await hass.services.async_call("button", "press", {"entity_id": eid(hass, entry, "abrufen")}, blocking=True)
        await hass.async_block_till_done()

    heute = hass.states.get(eid(hass, entry, "post_heute"))
    assert heute.state == "on"
    assert heute.attributes["briefe"] == 1 and heute.attributes["pakete"] == 1 and heute.attributes["wichtig"] == 1
    assert hass.states.get(eid(hass, entry, "pakete")).state == "1"
    assert hass.states.get(eid(hass, entry, "briefe")).state == "1"  # AOK ist älter als 3 Tage
    assert "Finanzamt" not in str(heute.attributes)

    assert len(ereignisse) == 3
    fa = next(e for e in ereignisse if e["wichtig"])
    assert fa["konto"] == "Kim" and fa["art"] == "brief" and "absender" not in fa  # Absender nur auf Wunsch
    assert [e["art"] for e in ereignisse].count("paket") == 1

    client = await hass_client()
    daten = await (await client.get("/api/briefankuendigung/briefe")).json()
    nach = {b["absender"]: b for b in daten["briefe"]}
    assert nach["Finanzamt Hamburg-Nord"]["wichtig"] is True and nach["Thalia"]["art"] == "paket"
    assert nach["Thalia"]["sendung"] == "00340434161094015902" and nach["AOK"]["wichtig"] is False

    archiv = await (await client.get("/api/briefankuendigung/archiv")).json()
    assert [e["absender"] for e in archiv["eintraege"]] == ["Finanzamt Hamburg-Nord", "Thalia", "AOK"]
    gesucht = await (await client.get("/api/briefankuendigung/archiv", params={"q": "finanz"})).json()
    assert [e["absender"] for e in gesucht["eintraege"]] == ["Finanzamt Hamburg-Nord"]

    # Absender im Ereignis, wenn erlaubt
    hass.config_entries.async_update_entry(entry, options={**entry.options, "ereignis_absender": True})
    await hass.async_block_till_done()
    ereignisse.clear()
    neu2 = [{"id": "f" * 20, "datum": jetzt, "art": "brief", "absender": "Sparkasse Holstein", "betreff": "", "bild": None, "bild_typ": None}]
    with patch.object(abruf, "abrufen", return_value=(neu2, set())):
        await hass.services.async_call("button", "press", {"entity_id": eid(hass, entry, "abrufen")}, blocking=True)
        await hass.async_block_till_done()
    assert ereignisse and ereignisse[-1]["absender"] == "Sparkasse Holstein" and ereignisse[-1]["wichtig"] is True
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_sendung_zusammen_erledigt_abrufen(hass: HomeAssistant, hass_client, tmp_path) -> None:
    """1.3: mehrere Mails zu einer Sendung = ein Eintrag; „Angekommen“ gilt für alle Geräte; Abruf aus dem Widget."""
    hass.config.config_dir = str(tmp_path)
    entry = MockConfigEntry(domain=DOMAIN, data=DATEN, unique_id="kim@gmx.de")
    entry.add_to_hass(hass)
    jetzt = int(time.time() * 1000)
    paket = {"art": "paket", "absender": "Thalia", "betreff": "", "bild": None, "bild_typ": None, "sendung": "00340434161094015902"}
    erste = [{**paket, "id": "1" * 20, "datum": jetzt - 7200_000, "stand": "unterwegs"},
             {"id": "2" * 20, "datum": jetzt - 3600_000, "art": "brief", "absender": "AOK", "betreff": "", "bild": None, "bild_typ": None}]
    with patch.object(abruf, "abrufen", return_value=(erste, set())):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await hass.services.async_call("button", "press", {"entity_id": eid(hass, entry, "abrufen")}, blocking=True)
        await hass.async_block_till_done()
    zweite = [{**paket, "id": "3" * 20, "datum": jetzt - 600_000, "stand": "zugestellt", "absender": ""}]
    with patch.object(abruf, "abrufen", return_value=(zweite, set())):
        await hass.services.async_call("button", "press", {"entity_id": eid(hass, entry, "abrufen")}, blocking=True)
        await hass.async_block_till_done()

    client = await hass_client()
    daten = await (await client.get("/api/briefankuendigung/briefe")).json()
    pakete = [b for b in daten["briefe"] if b["art"] == "paket"]
    assert len(pakete) == 1 and pakete[0]["stand"] == "zugestellt" and pakete[0]["absender"] == "Thalia"
    assert [v["stand"] for v in pakete[0]["verlauf"]] == ["unterwegs", "zugestellt"] and pakete[0]["erledigt"] is True
    assert daten["konten"][0]["zeiten"] == ["02:00", "05:00"] and daten["konten"][0]["naechster_abruf"] > jetzt
    archiv = await (await client.get("/api/briefankuendigung/archiv")).json()
    assert [e["absender"] for e in archiv["eintraege"]].count("Thalia") == 1

    # Angekommen markieren: Liste und Sensor zeigen es
    r = await client.post("/api/briefankuendigung/erledigt", json={"id": "2" * 20, "erledigt": True})
    assert r.status == 200
    await hass.async_block_till_done()
    daten = await (await client.get("/api/briefankuendigung/briefe")).json()
    assert next(b for b in daten["briefe"] if b["id"] == "2" * 20)["erledigt"] is True
    assert hass.states.get(eid(hass, entry, "briefe")).attributes["offen"] == 0
    assert (await client.post("/api/briefankuendigung/erledigt", json={"id": "f" * 20})).status == 404
    assert (await client.post("/api/briefankuendigung/erledigt", json={"id": "../x"})).status == 400
    await client.post("/api/briefankuendigung/erledigt", json={"id": "2" * 20, "erledigt": False})
    assert hass.states.get(eid(hass, entry, "briefe")).attributes["offen"] == 1

    # Jetzt abrufen: direkt nach einem Abruf nicht noch einmal, sonst ja
    with patch.object(abruf, "abrufen", return_value=([], set())) as aufruf:
        r = await client.post("/api/briefankuendigung/abrufen")
        assert (await r.json())["gestartet"] == 0
        hass.data[DOMAIN][entry.entry_id].index["stand"] = 0
        r = await client.post("/api/briefankuendigung/abrufen")
        assert r.status == 202 and (await r.json())["gestartet"] == 1
        await hass.async_block_till_done()
        assert aufruf.called
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_paketdienste_kundenkonten_von_hand_dhl(hass: HomeAssistant, hass_client, aioclient_mock, tmp_path) -> None:
    """1.4: DHL, Hermes, DPD, Amazon; Kundenkonten; Sendung von Hand; DHL-Sendungsverfolgung."""
    hass.config.config_dir = str(tmp_path)
    entry = MockConfigEntry(domain=DOMAIN, data=DATEN, unique_id="kim@gmx.de",
                            options={"paketdienste": ["dhl", "hermes", "dpd", "amazon"], "kundenkonten": "johanna.k@gmx.de = Johanna",
                                     "dhl_api_schluessel": "geheim"})
    entry.add_to_hass(hass)
    jetzt = int(time.time() * 1000)
    neue = [
        {"id": "1" * 20, "datum": jetzt - 3600_000, "art": "paket", "absender": "OTTO", "betreff": "", "bild": None, "bild_typ": None,
         "sendung": "H1002345678901234567", "stand": "unterwegs", "dienst": "hermes", "erwartet": "2026-10-23", "zeitfenster": "10:00–13:00", "empfaenger": "kim@gmx.de"},
        {"id": "2" * 20, "datum": jetzt - 1800_000, "art": "paket", "absender": "Kaffeebohnen 1 kg", "betreff": "", "bild": None, "bild_typ": None,
         "sendung": "TBA309999999999", "stand": "unterwegs", "dienst": "amazon", "erwartet": "", "zeitfenster": "", "empfaenger": "johanna.k@gmx.de"},
    ]
    aioclient_mock.get("https://api-eu.dhl.com/track/shipments", json={"shipments": [
        {"status": {"statusCode": "transit", "description": "Die Sendung wurde in das Zustellfahrzeug geladen."}, "estimatedTimeOfDelivery": "2026-10-21T12:00:00Z"}]})
    with patch.object(abruf, "abrufen", return_value=(neue, set())):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await hass.services.async_call("button", "press", {"entity_id": eid(hass, entry, "abrufen")}, blocking=True)
        await hass.async_block_till_done()

    client = await hass_client()
    daten = await (await client.get("/api/briefankuendigung/briefe")).json()
    assert daten["version"] == 3 and daten["dienste"]["dpd"] == "DPD" and daten["konten"][0]["dhl_live"] is True
    nach = {b["id"]: b for b in daten["briefe"]}
    assert nach["1" * 20]["dienst_name"] == "Hermes" and nach["1" * 20]["verfolgen"].startswith("https://www.myhermes.de/")
    assert nach["2" * 20]["kundenkonto"] == "Johanna" and nach["1" * 20]["kundenkonto"] == ""

    # Von Hand: Amazon an der Nummer erkannt; 14 Ziffern nur mit Dienst
    r = await client.post("/api/briefankuendigung/sendung", json={"nummer": "TBA301234567890", "name": "Kindle"})
    assert r.status == 200 and (await r.json())["dienst"] == "amazon"
    assert (await client.post("/api/briefankuendigung/sendung", json={"nummer": "01234567890123"})).status == 400
    r = await client.post("/api/briefankuendigung/sendung", json={"nummer": "00340434161094015902", "dienst": "dhl", "name": "Thalia"})
    dhl = await r.json()
    assert dhl["dienst"] == "dhl" and dhl["manuell"] is True
    await hass.services.async_call(DOMAIN, "sendung_hinzufuegen", {"nummer": "01234567890123", "dienst": "dpd"}, blocking=True)
    pakete = hass.states.get(eid(hass, entry, "pakete"))
    assert pakete.attributes["nach_dienst"] == {"amazon": 2, "dhl": 1, "dpd": 1, "hermes": 1}, pakete.attributes

    # DHL-Sendungsverfolgung: Stand und Termin übernommen
    await hass.data[DOMAIN][entry.entry_id].async_dhl_aktualisieren()
    daten = await (await client.get("/api/briefankuendigung/briefe")).json()
    d = next(b for b in daten["briefe"] if b["id"] == dhl["id"])
    assert d["stand"] == "heute" and d["erwartet"] == "2026-10-21" and "Zustellfahrzeug" in d["status_text"]
    assert aioclient_mock.mock_calls[0][3]["DHL-API-Key"] == "geheim"

    # Entfernen: von Hand → weg; aus einer Mail → nur angekommen
    assert (await client.post("/api/briefankuendigung/sendung/entfernen", json={"id": dhl["id"]})).status == 200
    assert (await client.post("/api/briefankuendigung/sendung/entfernen", json={"id": "1" * 20})).status == 200
    daten = await (await client.get("/api/briefankuendigung/briefe")).json()
    nach = {b["id"]: b for b in daten["briefe"]}
    assert dhl["id"] not in nach and nach["1" * 20]["erledigt"] is True
    assert await hass.config_entries.async_unload(entry.entry_id)
