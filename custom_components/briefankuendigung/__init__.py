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
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import timedelta
from functools import partial

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_change
from homeassistant.util import dt as dt_util

from . import abruf
from .const import (
    ARCHIV_TAGE, CONF_BENUTZER_HA, CONF_EREIGNIS_ABSENDER, CONF_NAME, CONF_TAGE, CONF_WICHTIG, CONF_ZEITEN, DOMAIN, EREIGNIS, SIGNAL_NEU,
    STANDARD,
)

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
            for k in ("sendung", "stand"):
                if b.get(k):
                    eintrag[k] = b[k]
            if b["id"] not in bekannt:
                archiv.append({"id": b["id"], "datum": b["datum"], "absender": b["absender"], "art": eintrag["art"],
                               **({"sendung": b["sendung"]} if b.get("sendung") else {})})
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
        tmp = os.path.join(self.ordner, "index.json.neu")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.index, f, ensure_ascii=False)
        os.replace(tmp, os.path.join(self.ordner, "index.json"))

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
        daten["_ansichten"] = True

    for stunde, minute in abrufer.zeiten():
        entry.async_on_unload(async_track_time_change(hass, abrufer.async_abrufen, hour=stunde, minute=minute, second=0))
    # gleich nach dem Start einmal abrufen, damit etwas da ist
    entry.async_on_unload(async_call_later(hass, timedelta(seconds=15), abrufer.async_abrufen))

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
                     "zeiten": [f"{h:02d}:{m:02d}" for h, m in a.zeiten()], "naechster_abruf": a.naechster_abruf(), "laeuft": a.laeuft}
            konten.append(konto)
            briefe += [{**b, "art": b.get("art", "brief"), "bild": bool(b.get("bild")), "wichtig": a.ist_wichtig(b["absender"]),
                        "erledigt": a.ist_erledigt(b), "konto": a.entry.entry_id, "konto_name": a.name} for b in a.index["briefe"]]
            stand = max(stand, konto["stand"])
            fehler = fehler or (f"{a.name}: {konto['fehler']}" if konto["fehler"] else "")
        briefe.sort(key=lambda b: -b["datum"])
        konten.sort(key=lambda k: k["name"].lower())
        return self.json({"stand": stand, "fehler": fehler, "konten": konten, "briefe": briefe, "version": 2})


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
