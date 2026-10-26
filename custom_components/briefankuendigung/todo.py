"""Aufgabenliste „Pakete“: offene Sendungen. Abhaken = angekommen; neuer Eintrag = Sendungsnummer verfolgen."""

from __future__ import annotations

from homeassistant.components.todo import TodoItem, TodoItemStatus, TodoListEntity, TodoListEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import pakete
from .const import DOMAIN, SIGNAL_NEU
from .sensor import geraet


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([PaketListe(hass.data[DOMAIN][entry.entry_id], entry)])


class PaketListe(TodoListEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "paketliste"
    _attr_icon = "mdi:package-variant"
    _attr_should_poll = False
    _attr_supported_features = TodoListEntityFeature.CREATE_TODO_ITEM | TodoListEntityFeature.UPDATE_TODO_ITEM | TodoListEntityFeature.DELETE_TODO_ITEM

    def __init__(self, abrufer, entry: ConfigEntry) -> None:
        self._abrufer = abrufer
        self._attr_unique_id = f"{entry.entry_id}_paketliste"
        self._attr_device_info = geraet(entry)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, f"{SIGNAL_NEU}_{self._abrufer.entry.entry_id}", self._neu))

    @callback
    def _neu(self) -> None:
        self.async_write_ha_state()

    @property
    def todo_items(self) -> list[TodoItem]:
        erg = []
        for b in self._abrufer.index["briefe"]:
            if b.get("art") != "paket":
                continue
            dienst = pakete.DIENSTE.get(b.get("dienst") or "dhl", {}).get("name", "")
            stand = {"heute": "kommt heute", "abholbereit": "zur Abholung bereit", "zugestellt": "zugestellt"}.get(b.get("stand") or "", "unterwegs")
            termin = f", voraussichtlich {b['erwartet']}" if b.get("erwartet") and b.get("stand") not in ("zugestellt", "heute") else ""
            erg.append(TodoItem(
                uid=b["id"], summary=f"{b.get('name') or b.get('absender') or b.get('sendung') or 'Paket'} ({dienst})",
                status=TodoItemStatus.COMPLETED if self._abrufer.ist_erledigt(b) else TodoItemStatus.NEEDS_ACTION,
                description=f"{stand}{termin}" + (f" · {b['sendung']}" if b.get("sendung") else ""),
            ))
        return erg

    async def async_create_todo_item(self, item: TodoItem) -> None:
        """„Sendungsnummer Name“ → verfolgen (Dienst wird an der Nummer erkannt)"""
        nummer, _, name = (item.summary or "").strip().partition(" ")
        try:
            await self._abrufer.async_sendung_hinzufuegen(nummer, "", name.strip())
        except ValueError as e:
            raise ServiceValidationError("Bitte „Sendungsnummer Name“ eintragen – der Paketdienst wird an der Nummer erkannt "
                                         "(bei 14 Ziffern bitte die Aktion „Sendung verfolgen“ mit Dienst benutzen).") from e

    async def async_update_todo_item(self, item: TodoItem) -> None:
        if item.status is not None:
            await self._abrufer.async_erledigt(item.uid, item.status == TodoItemStatus.COMPLETED)
        if item.summary:
            b = next((x for x in self._abrufer.index["briefe"] if x["id"] == item.uid), None)
            alt = f"{(b or {}).get('name') or (b or {}).get('absender') or ''}"
            neu = item.summary.split(" (")[0].strip()
            if b is not None and neu and neu != alt and neu != b.get("sendung"):
                await self._abrufer.async_umbenennen(item.uid, neu)

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        for uid in uids:
            await self._abrufer.async_sendung_entfernen(uid)
