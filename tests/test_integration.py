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
    plattform = "button" if art == "abrufen" else "sensor"
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
