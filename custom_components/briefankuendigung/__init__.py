"""Briefankündigung – liest die Briefankündigungen der Deutschen Post aus dem Postfach (IMAP).

Zu festen Zeiten (Standard 02:00 und 05:00) werden die Mails von ankuendigung@brief.deutschepost.de
gelesen, Absender und Umschlagbild herausgezogen und im Ordner <config>/briefankuendigung abgelegt.
Optional werden auch DHL-Paketankündigungen gelesen. Je neuem Brief/Paket gibt es das Ereignis
„briefankuendigung_neu“ (für Automationen), dazu den Sensor „Post heute“.
Für das iPhone-Widget gibt es Adressen, die nur mit gültigem Home-Assistant-Token antworten:
  GET /api/briefankuendigung/briefe        – Liste (JSON)
  GET /api/briefankuendigung/bild/<id>     – Umschlagbild
  GET /api/briefankuendigung/archiv        – Archiv der letzten zwei Jahre (Datum, Absender, Art)
  POST /api/briefankuendigung/erledigt     – {"id", "erledigt"}: Brief als angekommen markieren (gilt auf allen Geräten)
  POST /api/briefankuendigung/abrufen      – Postfach jetzt lesen (höchstens einmal pro Minute)
  POST /api/briefankuendigung/sendung      – {"nummer", "dienst"?, "name"?, "konto"?}: Sendung von Hand verfolgen
  POST /api/briefankuendigung/sendung/entfernen – {"id"}: von Hand hinzugefügte Sendung entfernen
Pakete kommen von DHL, Hermes, DPD und Amazon (aus deren Mails; DHL optional über die offizielle Sendungsverfolgung).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from datetime import timedelta
from functools import partial

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_time_change, async_track_time_interval
from homeassistant.util import dt as dt_util

from . import abruf, pakete
from .const import (
    ARCHIV_TAGE, CONF_BENUTZER_HA, CONF_DHL_SCHLUESSEL, CONF_EREIGNIS_ABSENDER, CONF_KUNDENKONTEN, CONF_NAME, CONF_TAGE, CONF_WICHTIG,
    CONF_ZEITEN, DOMAIN, EREIGNIS, SIGNAL_NEU, STANDARD,
)

PAKET_FELDER = ("sendung", "stand", "dienst", "erwartet", "zeitfenster", "empfaenger", "manuell", "status_text")
DHL_API = "https://api-eu.dhl.com/track/shipments"

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]
ENDUNG = {"image/png": "png", "image/gif": "gif", "image/webp": "webp"}


def zeiten_lesen(text: str) -> list[tuple[int, int]]:
    """'02:00, 05:00' -> [(2, 0), (5, 0)] (ungültige Angaben werden übergangen)."""
    ergebnis = []
    for teil in re.split(r"[,; ]+", text or ""):
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", teil.strip())
        if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
            ergebnis.append((int(m.group(1)), int(m.group(2))))
    return ergebnis or [(2, 0), (5, 0)]


class BriefAbruf:
    """Hält Liste und Bilder eines Postfachs und ruft zu den eingestellten Zeiten ab."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.ordner = hass.config.path(DOMAIN, entry.entry_id)
        self.index: dict = {"stand": 0, "fehler": "", "briefe": [], "uids": [], "archiv": [], "erledigt": {}}
        self.laeuft = False

    @property
    def cfg(self) -> dict:
        return {**STANDARD, **self.entry.data, **self.entry.options}

    @property
    def name(self) -> str:
        return self.entry.data.get(CONF_NAME) or self.entry.title

    def ist_wichtig(self, absender: str) -> bool:
        return abruf.wichtig(absender, self.cfg.get(CONF_WICHTIG, ""))

    def heute(self) -> list[dict]:
        """Heute angekündigte Briefe und Pakete (nach Ortszeit)."""
        tag = dt_util.now().date()
        return [b for b in self.index["briefe"] if dt_util.as_local(dt_util.utc_from_timestamp(b["datum"] / 1000)).date() == tag]

    def zeiten(self) -> list[tuple[int, int]]:
        return zeiten_lesen(self.cfg.get(CONF_ZEITEN, ""))

    def naechster_abruf(self) -> int:
        return int(abruf.naechster_abruf(self.zeiten(), dt_util.now()).timestamp() * 1000)

    def ist_erledigt(self, b: dict) -> bool:
        """Als angekommen markiert – oder ein zugestelltes Paket."""
        return b["id"] in self.index.get("erledigt", {}) or (b.get("art") == "paket" and b.get("stand") == "zugestellt")

    def offen(self) -> list[dict]:
        return [b for b in self.index["briefe"] if not self.ist_erledigt(b)]

    def sichtbar_fuer(self, benutzer) -> bool:
        """Darf dieser Home-Assistant-Benutzer das Konto sehen? Leere Liste = alle."""
        erlaubt = self.cfg.get(CONF_BENUTZER_HA) or []
        return not erlaubt or (benutzer is not None and benutzer.id in erlaubt)

    # --- Speicher (Dateien im Executor) ---
    def _laden(self) -> None:
        os.makedirs(self.ordner, exist_ok=True)
        try:
            with open(os.path.join(self.ordner, "index.json"), encoding="utf-8") as f:
                self.index = {**self.index, **json.load(f)}
        except (OSError, ValueError):
            pass

    def _speichern(self, neue: list[dict]) -> None:
        os.makedirs(self.ordner, exist_ok=True)
        archiv = self.index.setdefault("archiv", [])
        bekannt = {a["id"] for a in archiv}
        for b in neue:
            # Weitere Mail zu einer bekannten Sendung (unterwegs → heute → zugestellt): denselben Eintrag aktualisieren
            if b.get("art") == "paket" and b.get("sendung"):
                alt = next((x for x in self.index["briefe"] if x.get("art") == "paket" and x.get("sendung") == b["sendung"]), None)
                if alt:
                    abruf.paket_zusammenfuehren(alt, b)
                    for a in archiv:
                        if a.get("sendung") == b["sendung"]:
                            a["datum"] = alt["datum"]
                            a["absender"] = a["absender"] or alt["absender"]
                    continue
            eintrag = {k: b[k] for k in ("id", "datum", "absender", "betreff")}
            eintrag["art"] = b.get("art", "brief")
            for k in PAKET_FELDER:
                if b.get(k):
                    eintrag[k] = b[k]
            if b["id"] not in bekannt:
                archiv.append({"id": b["id"], "datum": b["datum"], "absender": b["absender"], "art": eintrag["art"],
                               **({"sendung": b["sendung"]} if b.get("sendung") else {}), **({"dienst": b["dienst"]} if b.get("dienst") else {})})
            eintrag["bild"] = None
            if b.get("bild"):
                datei = f"{b['id']}.{ENDUNG.get(b['bild_typ'], 'jpg')}"
                with open(os.path.join(self.ordner, datei), "wb") as f:
                    f.write(b["bild"])
                eintrag["bild"] = datei
            self.index["briefe"].append(eintrag)
        # Alte Briefe samt Bild entfernen
        grenze = (time.time() - int(self.cfg.get(CONF_TAGE, 14)) * 86400) * 1000
        for b in [x for x in self.index["briefe"] if x["datum"] < grenze]:
            if b.get("bild"):
                try:
                    os.remove(os.path.join(self.ordner, b["bild"]))
                except OSError:
                    pass
        self.index["briefe"] = sorted((x for x in self.index["briefe"] if x["datum"] >= grenze), key=lambda x: -x["datum"])
        ids = {x["id"] for x in self.index["briefe"]}
        self.index["erledigt"] = {k: v for k, v in self.index.get("erledigt", {}).items() if k in ids}
        archiv_grenze = (time.time() - ARCHIV_TAGE * 86400) * 1000
        self.index["archiv"] = sorted((a for a in archiv if a["datum"] >= archiv_grenze), key=lambda a: -a["datum"])[:3000]
        self._index_schreiben()

    def _index_schreiben(self) -> None:
        # eigener Name je Schreibvorgang: zwei gleichzeitige Speicherungen (z. B. Sendung + DHL-Abfrage) stören sich nicht
        os.makedirs(self.ordner, exist_ok=True)
        tmp = os.path.join(self.ordner, f"index.json.{uuid.uuid4().hex}.neu")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.index, f, ensure_ascii=False)
        os.replace(tmp, os.path.join(self.ordner, "index.json"))

    def kundenkonto(self, b: dict) -> str:
        """Name des Kundenkontos (z. B. „Johanna“ für ihr Amazon-Konto) aus der Empfängeradresse"""
        return pakete.konten_lesen(self.cfg.get(CONF_KUNDENKONTEN, "")).get((b.get("empfaenger") or "").lower(), "")

    async def _speichern_jetzt(self) -> None:
        await self.hass.async_add_executor_job(self._index_schreiben)
        async_dispatcher_send(self.hass, f"{SIGNAL_NEU}_{self.entry.entry_id}")

    async def async_sendung_hinzufuegen(self, nummer: str, dienst: str = "", name: str = "") -> dict:
        """Sendung von Hand verfolgen (z. B. die Nummer aus einer Bestellbestätigung). Spätere Mails dazu aktualisieren sie."""
        nummer = re.sub(r"\s+", "", nummer or "").upper()
        dienst = dienst or pakete.dienst_von_nummer(nummer)
        if not re.fullmatch(r"[A-Z0-9-]{6,35}", nummer) or dienst not in pakete.DIENSTE:
            raise ValueError("nummer" if dienst in pakete.DIENSTE or not nummer else "dienst")
        vorhanden = next((b for b in self.index["briefe"] if b.get("art") == "paket" and (b.get("sendung") or "").upper() == nummer), None)
        if vorhanden:
            if name and not vorhanden.get("absender"):
                vorhanden["absender"] = name
            await self._speichern_jetzt()
            return vorhanden
        b = {"id": pakete.sendung_id(dienst, nummer), "datum": int(time.time() * 1000), "art": "paket", "absender": (name or "")[:100],
             "betreff": "", "bild": None, "sendung": nummer, "stand": "unterwegs", "dienst": dienst, "manuell": True}
        self.index["briefe"].insert(0, b)
        await self._speichern_jetzt()
        if dienst == "dhl" and self.cfg.get(CONF_DHL_SCHLUESSEL):
            self.hass.async_create_task(self.async_dhl_aktualisieren())
        return b

    async def async_sendung_entfernen(self, brief_id: str) -> bool:
        """Von Hand hinzugefügte Sendung entfernen; Sendungen aus Mails werden nur als angekommen markiert."""
        b = next((x for x in self.index["briefe"] if x["id"] == brief_id), None)
        if not b:
            return False
        if b.get("manuell"):
            self.index["briefe"].remove(b)
            await self._speichern_jetzt()
            return True
        return await self.async_erledigt(brief_id, True)

    async def async_dhl_aktualisieren(self, *_args) -> None:
        """Offene DHL-Sendungen über die offizielle Sendungsverfolgung abfragen (eigener API-Schlüssel, höchstens 15 je Lauf)."""
        schluessel = (self.cfg.get(CONF_DHL_SCHLUESSEL) or "").strip()
        if not schluessel:
            return
        sitzung = async_get_clientsession(self.hass)
        geaendert = False
        offen = [b for b in self.index["briefe"] if b.get("dienst") == "dhl" and b.get("sendung") and b.get("stand") != "zugestellt"
                 and b["id"] not in self.index.get("erledigt", {})][:15]
        for b in offen:
            try:
                async with sitzung.get(DHL_API, params={"trackingNumber": b["sendung"], "language": "de"},
                                       headers={"DHL-API-Key": schluessel, "Accept": "application/json"}, timeout=20) as r:
                    if r.status == 401:
                        _LOGGER.warning("Briefankündigung: DHL lehnt den API-Schlüssel ab")
                        return
                    if r.status != 200:
                        continue
                    st = pakete.dhl_status(await r.json())
            except Exception as e:  # noqa: BLE001 – ohne Netz einfach beim nächsten Mal
                _LOGGER.debug("Briefankündigung: DHL-Abfrage %s: %s", b["sendung"], e)
                continue
            if not st:
                continue
            if st["stand"] != b.get("stand") or (st["erwartet"] and st["erwartet"] != b.get("erwartet")):
                abruf.paket_zusammenfuehren(b, {"datum": int(time.time() * 1000), "stand": st["stand"], "erwartet": st["erwartet"]})
                geaendert = True
            if st["text"] and st["text"] != b.get("status_text"):
                b["status_text"] = st["text"]
                geaendert = True
        if geaendert:
            await self._speichern_jetzt()

    async def async_erledigt(self, brief_id: str, erledigt: bool) -> bool:
        """Brief als angekommen markieren (oder zurücknehmen). False, wenn es ihn in diesem Konto nicht gibt."""
        if not any(b["id"] == brief_id for b in self.index["briefe"]):
            return False
        e = self.index.setdefault("erledigt", {})
        if erledigt:
            e[brief_id] = int(time.time() * 1000)
        else:
            e.pop(brief_id, None)
        await self.hass.async_add_executor_job(self._index_schreiben)
        async_dispatcher_send(self.hass, f"{SIGNAL_NEU}_{self.entry.entry_id}")
        return True

    def bild(self, bild_id: str) -> tuple[bytes, str] | None:
        b = next((x for x in self.index["briefe"] if x["id"] == bild_id and x.get("bild")), None)
        if not b:
            return None
        try:
            with open(os.path.join(self.ordner, b["bild"]), "rb") as f:
                daten = f.read()
        except OSError:
            return None
        typ = next((t for t, e in ENDUNG.items() if b["bild"].endswith("." + e)), "image/jpeg")
        return daten, typ

    async def async_laden(self) -> None:
        await self.hass.async_add_executor_job(self._laden)

    async def async_abrufen(self, *_args) -> None:
        if self.laeuft:
            return
        self.laeuft = True
        neue: list[dict] = []
        try:
            bekannt = {b["id"] for b in self.index["briefe"]}
            neue, erledigt = await self.hass.async_add_executor_job(
                partial(abruf.abrufen, self.cfg, set(self.index.get("uids", [])), bekannt)
            )
            self.index["uids"] = sorted(erledigt)[-400:]
            self.index["fehler"] = ""
            _LOGGER.debug("Briefankündigung: %s neu", len(neue))
        except abruf.AnmeldeFehler:
            self.index["fehler"] = "Anmeldung am Postfach abgelehnt – Passwort prüfen und bei GMX „POP3/IMAP-Abruf“ erlauben."
            _LOGGER.warning("Briefankündigung: %s", self.index["fehler"])
        except Exception as e:  # noqa: BLE001 – Fehler anzeigen statt abstürzen
            self.index["fehler"] = str(e)[:200] or e.__class__.__name__
            _LOGGER.warning("Briefankündigung: Abruf fehlgeschlagen: %s", e)
        finally:
            self.index["stand"] = int(time.time() * 1000)
            try:
                await self.hass.async_add_executor_job(self._speichern, neue)
            finally:
                self.laeuft = False
            async_dispatcher_send(self.hass, f"{SIGNAL_NEU}_{self.entry.entry_id}")
            self._melden(neue)

    @callback
    def _melden(self, neue: list[dict]) -> None:
        """Ereignis je neuem Brief/Paket – Absender nur, wenn in den Optionen erlaubt (die Datenbank speichert Ereignisse)."""
        mit_absender = bool(self.cfg.get(CONF_EREIGNIS_ABSENDER))
        for b in sorted(neue, key=lambda x: x["datum"]):
            daten = {
                "konto": self.name, "konto_id": self.entry.entry_id, "art": b.get("art", "brief"),
                **({"stand": b["stand"]} if b.get("stand") else {}),
                **({"dienst": b["dienst"]} if b.get("dienst") else {}),
                "wichtig": self.ist_wichtig(b["absender"]),
                "datum": dt_util.utc_from_timestamp(b["datum"] / 1000).isoformat(),
            }
            if mit_absender:
                daten["absender"] = b["absender"]
            self.hass.bus.async_fire(EREIGNIS, daten)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Postfach einrichten."""
    daten = hass.data.setdefault(DOMAIN, {})
    abrufer = BriefAbruf(hass, entry)
    await abrufer.async_laden()
    daten[entry.entry_id] = abrufer

    if not daten.get("_ansichten"):
        hass.http.register_view(BriefeAnsicht(hass))
        hass.http.register_view(BildAnsicht(hass))
        hass.http.register_view(ArchivAnsicht(hass))
        hass.http.register_view(ErledigtAnsicht(hass))
        hass.http.register_view(AbrufenAnsicht(hass))
        hass.http.register_view(SendungAnsicht(hass))
        hass.http.register_view(SendungEntfernenAnsicht(hass))
        daten["_ansichten"] = True
        _dienste_anmelden(hass)

    for stunde, minute in abrufer.zeiten():
        entry.async_on_unload(async_track_time_change(hass, abrufer.async_abrufen, hour=stunde, minute=minute, second=0))
    # gleich nach dem Start einmal abrufen, damit etwas da ist
    entry.async_on_unload(async_call_later(hass, timedelta(seconds=15), abrufer.async_abrufen))
    # DHL-Sendungsverfolgung (nur mit eigenem API-Schlüssel): stündlich
    if abrufer.cfg.get(CONF_DHL_SCHLUESSEL):
        entry.async_on_unload(async_track_time_interval(hass, abrufer.async_dhl_aktualisieren, timedelta(hours=1)))
        entry.async_on_unload(async_call_later(hass, timedelta(seconds=60), abrufer.async_dhl_aktualisieren))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_neu_laden))
    return True


async def _neu_laden(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return ok


@callback
def _abrufer(hass: HomeAssistant) -> list[BriefAbruf]:
    return [v for k, v in hass.data.get(DOMAIN, {}).items() if isinstance(v, BriefAbruf)]


class BriefeAnsicht(HomeAssistantView):
    """Liste der angekündigten Briefe (nur mit Home-Assistant-Token)."""

    url = "/api/briefankuendigung/briefe"
    name = "api:briefankuendigung:briefe"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Alle Konten, die der angemeldete Benutzer sehen darf; ?konto=<Name oder ID> filtert."""
        benutzer = request.get("hass_user")
        gewuenscht = (request.query.get("konto") or "").strip().lower()
        briefe, konten, stand, fehler = [], [], 0, ""
        for a in _abrufer(self.hass):
            if not a.sichtbar_fuer(benutzer):
                continue
            if gewuenscht and gewuenscht not in (a.entry.entry_id, a.name.lower()):
                continue
            konto = {"id": a.entry.entry_id, "name": a.name, "stand": a.index.get("stand", 0), "fehler": a.index.get("fehler", ""),
                     "zeiten": [f"{h:02d}:{m:02d}" for h, m in a.zeiten()], "naechster_abruf": a.naechster_abruf(), "laeuft": a.laeuft,
                     "dhl_live": bool(a.cfg.get(CONF_DHL_SCHLUESSEL))}
            konten.append(konto)
            briefe += [_fuer_api(a, b) for b in a.index["briefe"]]
            stand = max(stand, konto["stand"])
            fehler = fehler or (f"{a.name}: {konto['fehler']}" if konto["fehler"] else "")
        briefe.sort(key=lambda b: -b["datum"])
        konten.sort(key=lambda k: k["name"].lower())
        return self.json({"stand": stand, "fehler": fehler, "konten": konten, "briefe": briefe, "version": 3,
                          "dienste": {k: v["name"] for k, v in pakete.DIENSTE.items()}})


class BildAnsicht(HomeAssistantView):
    """Umschlagbild eines Briefs (nur mit Home-Assistant-Token)."""

    url = "/api/briefankuendigung/bild/{bild_id}"
    name = "api:briefankuendigung:bild"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request, bild_id: str) -> web.Response:
        if not re.fullmatch(r"[0-9a-f]{20}", bild_id):
            return web.Response(status=404)
        benutzer = request.get("hass_user")
        for a in _abrufer(self.hass):
            if not a.sichtbar_fuer(benutzer):
                continue
            ergebnis = await self.hass.async_add_executor_job(a.bild, bild_id)
            if ergebnis:
                return web.Response(body=ergebnis[0], content_type=ergebnis[1], headers={"Cache-Control": "private, max-age=86400"})
        return web.Response(status=404)


class ArchivAnsicht(HomeAssistantView):
    """Archiv: alle angekündigten Briefe/Pakete der letzten zwei Jahre (ohne Bilder). ?q= sucht im Absender, ?konto= filtert."""

    url = "/api/briefankuendigung/archiv"
    name = "api:briefankuendigung:archiv"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:
        benutzer = request.get("hass_user")
        gewuenscht = (request.query.get("konto") or "").strip().lower()
        suche = (request.query.get("q") or "").strip().lower()
        eintraege = []
        for a in _abrufer(self.hass):
            if not a.sichtbar_fuer(benutzer) or (gewuenscht and gewuenscht not in (a.entry.entry_id, a.name.lower())):
                continue
            eintraege += [{**e, "wichtig": a.ist_wichtig(e["absender"]), "konto_name": a.name}
                          for e in a.index.get("archiv", []) if not suche or suche in (e["absender"] or "").lower()]
        eintraege.sort(key=lambda e: -e["datum"])
        return self.json({"eintraege": eintraege[:3000]})


class ErledigtAnsicht(HomeAssistantView):
    """Brief als angekommen markieren: POST {"id": "<20 hex>", "erledigt": true|false} – gilt für alle Geräte."""

    url = "/api/briefankuendigung/erledigt"
    name = "api:briefankuendigung:erledigt"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def post(self, request: web.Request) -> web.Response:
        try:
            daten = await request.json()
        except ValueError:
            return self.json_message("Ungültige Anfrage", 400)
        if not isinstance(daten, dict):
            return self.json_message("Ungültige Anfrage", 400)
        brief_id = str(daten.get("id") or "")
        if not re.fullmatch(r"[0-9a-f]{20}", brief_id):
            return self.json_message("Ungültige ID", 400)
        benutzer = request.get("hass_user")
        for a in _abrufer(self.hass):
            if a.sichtbar_fuer(benutzer) and await a.async_erledigt(brief_id, bool(daten.get("erledigt", True))):
                return self.json({"id": brief_id, "erledigt": bool(daten.get("erledigt", True))})
        return self.json_message("Brief nicht gefunden", 404)


class AbrufenAnsicht(HomeAssistantView):
    """Postfach jetzt lesen (für „Jetzt nachsehen“ im Widget). ?konto= filtert; höchstens einmal pro Minute je Konto."""

    url = "/api/briefankuendigung/abrufen"
    name = "api:briefankuendigung:abrufen"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def post(self, request: web.Request) -> web.Response:
        benutzer = request.get("hass_user")
        gewuenscht = (request.query.get("konto") or "").strip().lower()
        gestartet = 0
        for a in _abrufer(self.hass):
            if not a.sichtbar_fuer(benutzer) or (gewuenscht and gewuenscht not in (a.entry.entry_id, a.name.lower())):
                continue
            if a.laeuft or time.time() * 1000 - a.index.get("stand", 0) < 60_000:
                continue
            self.hass.async_create_task(a.async_abrufen())
            gestartet += 1
        return self.json({"gestartet": gestartet}, status_code=202 if gestartet else 200)


def _fuer_api(a: BriefAbruf, b: dict) -> dict:
    """Ein Brief bzw. eine Sendung für Widget und Apps (mit Dienst, Kundenkonto und Link zur Sendungsverfolgung)."""
    erg = {**b, "art": b.get("art", "brief"), "bild": bool(b.get("bild")), "wichtig": a.ist_wichtig(b["absender"]),
           "erledigt": a.ist_erledigt(b), "konto": a.entry.entry_id, "konto_name": a.name}
    if erg["art"] == "paket":
        erg["dienst"] = b.get("dienst") or "dhl"   # bis 1.3 kamen Pakete nur von DHL
        erg["dienst_name"] = pakete.DIENSTE.get(erg["dienst"], {}).get("name", erg["dienst"])
        erg["verfolgen"] = pakete.verfolgen(erg["dienst"], b.get("sendung", ""))
        erg["kundenkonto"] = a.kundenkonto(b)
    return erg


def _abrufer_fuer(hass: HomeAssistant, benutzer, konto: str) -> BriefAbruf | None:
    """Erstes sichtbare Konto – oder das genannte (Name oder ID)"""
    k = (konto or "").strip().lower()
    for a in _abrufer(hass):
        if a.sichtbar_fuer(benutzer) and (not k or k in (a.entry.entry_id, a.name.lower())):
            return a
    return None


class SendungAnsicht(HomeAssistantView):
    """Sendung von Hand verfolgen: POST {"nummer", "dienst"?, "name"?, "konto"?}"""

    url = "/api/briefankuendigung/sendung"
    name = "api:briefankuendigung:sendung"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def post(self, request: web.Request) -> web.Response:
        try:
            daten = await request.json()
        except ValueError:
            return self.json_message("Ungültige Anfrage", 400)
        if not isinstance(daten, dict):
            return self.json_message("Ungültige Anfrage", 400)
        a = _abrufer_fuer(self.hass, request.get("hass_user"), str(daten.get("konto") or ""))
        if not a:
            return self.json_message("Postfach nicht gefunden", 404)
        try:
            b = await a.async_sendung_hinzufuegen(str(daten.get("nummer") or ""), str(daten.get("dienst") or ""), str(daten.get("name") or ""))
        except ValueError as e:
            return self.json_message("Paketdienst nicht erkannt – bitte angeben" if str(e) == "dienst" else "Ungültige Sendungsnummer", 400)
        return self.json(_fuer_api(a, b))


class SendungEntfernenAnsicht(HomeAssistantView):
    """Von Hand hinzugefügte Sendung entfernen: POST {"id"}"""

    url = "/api/briefankuendigung/sendung/entfernen"
    name = "api:briefankuendigung:sendung:entfernen"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def post(self, request: web.Request) -> web.Response:
        try:
            daten = await request.json()
        except ValueError:
            return self.json_message("Ungültige Anfrage", 400)
        brief_id = str(daten.get("id") or "") if isinstance(daten, dict) else ""
        if not re.fullmatch(r"[0-9a-f]{20}", brief_id):
            return self.json_message("Ungültige ID", 400)
        for a in _abrufer(self.hass):
            if a.sichtbar_fuer(request.get("hass_user")) and await a.async_sendung_entfernen(brief_id):
                return self.json({"id": brief_id})
        return self.json_message("Sendung nicht gefunden", 404)


@callback
def _dienste_anmelden(hass: HomeAssistant) -> None:
    """Aktion briefankuendigung.sendung_hinzufuegen (für Automationen und Skripte)"""
    import voluptuous as vol
    from homeassistant.core import ServiceCall
    from homeassistant.exceptions import ServiceValidationError
    from homeassistant.helpers import config_validation as cv

    async def hinzufuegen(aufruf: ServiceCall) -> None:
        k = aufruf.data.get("konto", "").strip().lower()
        a = next((x for x in _abrufer(hass) if not k or k in (x.entry.entry_id, x.name.lower())), None)
        if not a:
            raise ServiceValidationError("Kein Postfach eingerichtet")
        try:
            await a.async_sendung_hinzufuegen(aufruf.data["nummer"], aufruf.data.get("dienst", ""), aufruf.data.get("name", ""))
        except ValueError as e:
            raise ServiceValidationError("Paketdienst nicht erkannt – bitte angeben" if str(e) == "dienst" else "Ungültige Sendungsnummer") from e

    hass.services.async_register(DOMAIN, "sendung_hinzufuegen", hinzufuegen, schema=vol.Schema({
        vol.Required("nummer"): cv.string,
        vol.Optional("dienst", default=""): vol.In(["", *pakete.ALLE]),
        vol.Optional("name", default=""): cv.string,
        vol.Optional("konto", default=""): cv.string,
    }))
