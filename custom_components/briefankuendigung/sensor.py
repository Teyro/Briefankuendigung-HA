"""Sensor: Anzahl der angekündigten Briefe der letzten 3 Tage (keine Absender – der Recorder speichert dauerhaft)."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_BENUTZER, CONF_NAME, DOMAIN, SIGNAL_NEU


def geraet(entry: ConfigEntry) -> DeviceInfo:
    # ein Gerät je Konto: "Briefankündigung Christian", "Briefankündigung Johanna"
    name = entry.data.get(CONF_NAME) or entry.title
    return DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name=f"Briefankündigung {name}".strip(), manufacturer="Teyro",
                      model=entry.data.get(CONF_BENUTZER, "Postfach"))


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([BriefeSensor(hass.data[DOMAIN][entry.entry_id], entry)])


class BriefeSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "briefe"
    _attr_icon = "mdi:email-newsletter"
    _attr_native_unit_of_measurement = "Briefe"
    _attr_should_poll = False

    def __init__(self, abrufer, entry: ConfigEntry) -> None:
        self._abrufer = abrufer
        self._attr_unique_id = f"{entry.entry_id}_briefe"
        self._attr_device_info = geraet(entry)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, f"{SIGNAL_NEU}_{self._abrufer.entry.entry_id}", self._neu))

    @callback
    def _neu(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> int:
        grenze = (time.time() - 3 * 86400) * 1000
        return sum(1 for b in self._abrufer.index["briefe"] if b["datum"] >= grenze)

    @property
    def extra_state_attributes(self) -> dict:
        i = self._abrufer.index
        iso = lambda ms: datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat() if ms else None  # noqa: E731
        return {
            "gesamt": len(i["briefe"]),
            "letzte_ankuendigung": iso(i["briefe"][0]["datum"]) if i["briefe"] else None,
            "letzter_abruf": iso(i.get("stand", 0)),
            "fehler": i.get("fehler") or None,
        }
