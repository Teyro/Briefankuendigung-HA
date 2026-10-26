"""Einrichtung über die Oberfläche: Postfach-Zugang (wird beim Speichern geprüft) und Optionen."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector, SelectOptionDict, SelectSelector, SelectSelectorConfig, SelectSelectorMode, TextSelector, TextSelectorConfig,
    TextSelectorType,
)

from . import abruf
from .const import (
    CONF_ABSENDER, CONF_BENUTZER, CONF_BENUTZER_HA, CONF_DHL_SCHLUESSEL, CONF_DIENSTE, CONF_EREIGNIS_ABSENDER, CONF_KUNDENKONTEN, CONF_NAME,
    CONF_ORDNER, CONF_PAKET, CONF_PASSWORT, CONF_PORT, CONF_SERVER, CONF_TAGE, CONF_WICHTIG, CONF_ZEITEN, DOMAIN, STANDARD,
)
from .pakete import ALLE as ALLE_DIENSTE, DIENSTE

ZEITEN_MUSTER = r"^\s*\d{1,2}:\d{2}([ ,;]+\d{1,2}:\d{2})*\s*$"


async def _ha_benutzer(hass) -> list[SelectOptionDict]:
    """Echte Home-Assistant-Benutzer (ohne System-Konten) zur Auswahl."""
    return [SelectOptionDict(value=u.id, label=u.name or u.id) for u in await hass.auth.async_get_users() if not u.system_generated and u.is_active]


def _optionen_schema(v: dict, benutzer: list[SelectOptionDict]) -> dict:
    return {
        vol.Optional(CONF_BENUTZER_HA, default=v.get(CONF_BENUTZER_HA, [])): SelectSelector(
            SelectSelectorConfig(options=benutzer, multiple=True, mode=SelectSelectorMode.LIST)),
        vol.Required(CONF_ZEITEN, default=v.get(CONF_ZEITEN, STANDARD[CONF_ZEITEN])): vol.All(str, vol.Match(ZEITEN_MUSTER)),
        vol.Required(CONF_TAGE, default=v.get(CONF_TAGE, STANDARD[CONF_TAGE])): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
        vol.Required(CONF_ABSENDER, default=v.get(CONF_ABSENDER, STANDARD[CONF_ABSENDER])): str,
        vol.Required(CONF_ORDNER, default=v.get(CONF_ORDNER, STANDARD[CONF_ORDNER])): str,
        # bis 1.3 gab es nur „paket_absender“ (DHL; leer = keine Pakete)
        vol.Optional(CONF_DIENSTE, default=v.get(CONF_DIENSTE, ALLE_DIENSTE if v.get(CONF_PAKET, STANDARD[CONF_PAKET]) else [])): SelectSelector(
            SelectSelectorConfig(options=[SelectOptionDict(value=k, label=d["name"]) for k, d in DIENSTE.items()], multiple=True,
                                 mode=SelectSelectorMode.LIST)),
        vol.Optional(CONF_KUNDENKONTEN, default=v.get(CONF_KUNDENKONTEN, "")): TextSelector(TextSelectorConfig(multiline=True)),
        vol.Optional(CONF_DHL_SCHLUESSEL, default=v.get(CONF_DHL_SCHLUESSEL, "")): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
        vol.Optional(CONF_PAKET, default=v.get(CONF_PAKET, STANDARD[CONF_PAKET])): str,
        vol.Optional(CONF_WICHTIG, default=v.get(CONF_WICHTIG, STANDARD[CONF_WICHTIG])): TextSelector(TextSelectorConfig(multiline=True)),
        vol.Optional(CONF_EREIGNIS_ABSENDER, default=v.get(CONF_EREIGNIS_ABSENDER, STANDARD[CONF_EREIGNIS_ABSENDER])): BooleanSelector(),
    }


class BriefConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        fehler: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_BENUTZER].strip().lower())
            self._abort_if_unique_id_configured()
            fehler = await self._pruefen(user_input)
            if not fehler:
                if not user_input.get(CONF_NAME):
                    user_input[CONF_NAME] = user_input[CONF_BENUTZER].split("@")[0].replace(".", " ").title()
                return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)
        v = user_input or {}
        schema = vol.Schema({
            vol.Optional(CONF_NAME, default=v.get(CONF_NAME, "")): str,
            vol.Required(CONF_BENUTZER, default=v.get(CONF_BENUTZER, "")): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL)),
            vol.Required(CONF_PASSWORT): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
            vol.Required(CONF_SERVER, default=v.get(CONF_SERVER, STANDARD[CONF_SERVER])): str,
            vol.Required(CONF_PORT, default=v.get(CONF_PORT, STANDARD[CONF_PORT])): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            **_optionen_schema(v, await _ha_benutzer(self.hass)),
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=fehler)

    async def _pruefen(self, daten: dict) -> dict[str, str]:
        """Anmeldung testen (im Executor)."""
        def test() -> None:
            imap = abruf.verbinde(daten[CONF_SERVER], int(daten[CONF_PORT]), daten[CONF_BENUTZER], daten[CONF_PASSWORT])
            imap.logout()
        try:
            await self.hass.async_add_executor_job(test)
        except abruf.AnmeldeFehler:
            return {"base": "anmeldung"}
        except abruf.VerbindungsFehler:
            return {"base": "verbindung"}
        except Exception:  # noqa: BLE001
            return {"base": "unbekannt"}
        return {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return BriefOptionsFlow()


class BriefOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        aktuell = {**STANDARD, **self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=vol.Schema(_optionen_schema(aktuell, await _ha_benutzer(self.hass))))
