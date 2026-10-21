"""Briefankündigung – liest die Briefankündigungen der Deutschen Post aus dem Postfach (IMAP).

Zu festen Zeiten (Standard 02:00 und 05:00) werden die Mails von ankuendigung@brief.deutschepost.de
gelesen, Absender und Umschlagbild herausgezogen und im Ordner <config>/briefankuendigung abgelegt.
Für das iPhone-Widget gibt es zwei Adressen, die nur mit gültigem Home-Assistant-Token antworten:
  GET /api/briefankuendigung/briefe        – Liste (JSON)
  GET /api/briefankuendigung/bild/<id>     – Umschlagbild
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

from . import abruf
from .const import CONF_BENUTZER_HA, CONF_NAME, CONF_TAGE, CONF_ZEITEN, DOMAIN, SIGNAL_NEU, STANDARD

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR, Platform.BUTTON]
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
        self.index: dict = {"stand": 0, "fehler": "", "briefe": [], "uids": []}
        self.laeuft = False

    @property
    def cfg(self) -> dict:
        return {**STANDARD, **self.entry.data, **self.entry.options}

    @property
    def name(self) -> str:
        return self.entry.data.get(CONF_NAME) or self.entry.title

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
        for b in neue:
            eintrag = {k: b[k] for k in ("id", "datum", "absender", "betreff")}
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
        tmp = os.path.join(self.ordner, "index.json.neu")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.index, f, ensure_ascii=False)
        os.replace(tmp, os.path.join(self.ordner, "index.json"))

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


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Postfach einrichten."""
    daten = hass.data.setdefault(DOMAIN, {})
    abrufer = BriefAbruf(hass, entry)
    await abrufer.async_laden()
    daten[entry.entry_id] = abrufer

    if not daten.get("_ansichten"):
        hass.http.register_view(BriefeAnsicht(hass))
        hass.http.register_view(BildAnsicht(hass))
        daten["_ansichten"] = True

    for stunde, minute in zeiten_lesen(abrufer.cfg.get(CONF_ZEITEN, "")):
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
            konto = {"id": a.entry.entry_id, "name": a.name, "stand": a.index.get("stand", 0), "fehler": a.index.get("fehler", "")}
            konten.append(konto)
            briefe += [{**b, "bild": bool(b.get("bild")), "konto": a.entry.entry_id, "konto_name": a.name} for b in a.index["briefe"]]
            stand = max(stand, konto["stand"])
            fehler = fehler or (f"{a.name}: {konto['fehler']}" if konto["fehler"] else "")
        briefe.sort(key=lambda b: -b["datum"])
        konten.sort(key=lambda k: k["name"].lower())
        return self.json({"stand": stand, "fehler": fehler, "konten": konten, "briefe": briefe})


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
