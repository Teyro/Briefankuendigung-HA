"""Kalender „Pakete“: erwartete Lieferungen als ganztägige Termine (mit Zeitfenster in der Beschreibung)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import pakete
from .const import DOMAIN, SIGNAL_NEU
from .sensor import geraet


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([PaketKalender(hass.data[DOMAIN][entry.entry_id], entry)])


class PaketKalender(CalendarEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "lieferungen"
    _attr_icon = "mdi:truck-delivery"
    _attr_should_poll = False

    def __init__(self, abrufer, entry: ConfigEntry) -> None:
        self._abrufer = abrufer
        self._attr_unique_id = f"{entry.entry_id}_lieferungen"
        self._attr_device_info = geraet(entry)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, f"{SIGNAL_NEU}_{self._abrufer.entry.entry_id}", self._neu))

    @callback
    def _neu(self) -> None:
        self.async_write_ha_state()

    def _termine(self) -> list[CalendarEvent]:
        erg = []
        for b in self._abrufer.index["briefe"]:
            if b.get("art") != "paket" or self._abrufer.ist_erledigt(b):
                continue
            tag = b.get("erwartet") or ""
            if not tag and b.get("stand") in ("heute", "abholbereit"):
                tag = dt_util.now().date().isoformat()
            try:
                d = date.fromisoformat(tag)
            except ValueError:
                continue
            dienst = pakete.DIENSTE.get(b.get("dienst") or "dhl", {}).get("name", "")
            name = b.get("name") or b.get("absender") or "Paket"
            teile = [x for x in (b.get("zeitfenster"), b.get("status_text"), f"Sendung {b['sendung']}" if b.get("sendung") else "") if x]
            erg.append(CalendarEvent(start=d, end=d + timedelta(days=1), summary=f"📦 {name} ({dienst})" if dienst else f"📦 {name}",
                                     description="\n".join(teile) or None, uid=b["id"]))
        return sorted(erg, key=lambda e: e.start)

    @property
    def event(self) -> CalendarEvent | None:
        heute = dt_util.now().date()
        return next((e for e in self._termine() if e.end > heute), None)

    async def async_get_events(self, hass: HomeAssistant, start_date: datetime, end_date: datetime) -> list[CalendarEvent]:
        von, bis = start_date.date(), end_date.date()
        return [e for e in self._termine() if e.end > von and e.start <= bis]
