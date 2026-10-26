"""Sensor „Post heute“: an, wenn für heute ein Brief oder Paket angekündigt ist (nur Zahlen, keine Absender)."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change

from .const import DOMAIN, SIGNAL_NEU
from .sensor import geraet


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    abrufer = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PostHeute(abrufer, entry), InZustellung(abrufer, entry)])


class PostHeute(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "post_heute"
    _attr_icon = "mdi:mailbox-up"
    _attr_should_poll = False

    def __init__(self, abrufer, entry: ConfigEntry) -> None:
        self._abrufer = abrufer
        self._attr_unique_id = f"{entry.entry_id}_post_heute"
        self._attr_device_info = geraet(entry)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, f"{SIGNAL_NEU}_{self._abrufer.entry.entry_id}", self._neu))
        # um Mitternacht wieder aus
        self.async_on_remove(async_track_time_change(self.hass, self._neu, hour=0, minute=0, second=10))

    @callback
    def _neu(self, *_args) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return bool(self._abrufer.heute())

    @property
    def extra_state_attributes(self) -> dict:
        heute = self._abrufer.heute()
        return {
            "briefe": sum(1 for b in heute if b.get("art", "brief") == "brief"),
            "pakete": sum(1 for b in heute if b.get("art") == "paket"),
            "wichtig": sum(1 for b in heute if self._abrufer.ist_wichtig(b["absender"])),
            "offen": sum(1 for b in heute if not self._abrufer.ist_erledigt(b)),
        }


class InZustellung(PostHeute):
    """An, solange ein Paket in Zustellung ist (bzw. heute kommt) – z. B. für eine Lampe an der Haustür"""
    _attr_translation_key = "in_zustellung"
    _attr_icon = "mdi:truck-fast"

    def __init__(self, abrufer, entry: ConfigEntry) -> None:
        super().__init__(abrufer, entry)
        self._attr_unique_id = f"{entry.entry_id}_in_zustellung"

    def _heute(self) -> list[dict]:
        from homeassistant.util import dt as dt_util
        tag = dt_util.now().date().isoformat()
        return [b for b in self._abrufer.index["briefe"] if b.get("art") == "paket" and not self._abrufer.ist_erledigt(b)
                and b.get("stand") != "zugestellt" and (b.get("stand") == "heute" or b.get("erwartet") == tag)]

    @property
    def is_on(self) -> bool:
        return bool(self._heute())

    @property
    def extra_state_attributes(self) -> dict:
        h = self._heute()
        return {"anzahl": len(h), "dienste": sorted({b.get("dienst") or "dhl" for b in h}),
                "zeitfenster": [b["zeitfenster"] for b in h if b.get("zeitfenster")]}
