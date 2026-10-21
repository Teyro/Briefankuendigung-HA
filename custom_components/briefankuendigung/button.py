"""Knopf „Jetzt abrufen“."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .sensor import geraet


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([AbrufKnopf(hass.data[DOMAIN][entry.entry_id], entry)])


class AbrufKnopf(ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "abrufen"
    _attr_icon = "mdi:email-sync"

    def __init__(self, abrufer, entry: ConfigEntry) -> None:
        self._abrufer = abrufer
        self._attr_unique_id = f"{entry.entry_id}_abrufen"
        self._attr_device_info = geraet(entry)

    async def async_press(self) -> None:
        await self._abrufer.async_abrufen()
