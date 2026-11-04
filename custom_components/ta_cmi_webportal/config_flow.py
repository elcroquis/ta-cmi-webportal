"""No-YAML configuration and per-device entity mappings for TA CMI."""
from __future__ import annotations

import re
from typing import Any

from aiohttp import CookieJar
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, OptionsFlowWithReload
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import CMIClient, CMIError
from .const import (
    DOMAIN, CONF_CMI_ID, CONF_USER_FIELD, CONF_PASS_FIELD,
    CONF_SCHEME, CONF_PROFILE, CONF_NAME, PROFILE_UVR, PROFILE_GENERIC, OPT_POINTS, OPT_CLIMATE,
    OPT_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS,
    MIN_SCAN_INTERVAL_SECONDS, MAX_SCAN_INTERVAL_SECONDS,
)
from .generic import KINDS, valid_position

_CMI_PATTERN = re.compile(r"^CMI[0-9]{6,10}$")


def _entry_client(hass, data: dict):
    """Use a disposable isolated session, never HA's shared cookie jar."""
    session = async_create_clientsession(hass, cookie_jar=CookieJar())
    return CMIClient(
        session, data[CONF_CMI_ID], data[CONF_USERNAME], data[CONF_PASSWORD],
        data.get(CONF_USER_FIELD) or "username", data.get(CONF_PASS_FIELD) or "passwort",
        scheme_number=int(data.get(CONF_SCHEME, 2)), profile=data.get(CONF_PROFILE, PROFILE_UVR),
    )


class CMIConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """One entry per CMI, with an optional UVR1611 compatibility profile."""

    VERSION = 3

    async def async_step_user(self, user_input: dict | None = None):
        errors = {}
        if user_input is not None:
            new = dict(user_input)
            new[CONF_CMI_ID] = new[CONF_CMI_ID].strip().upper()
            if not _CMI_PATTERN.fullmatch(new[CONF_CMI_ID]):
                errors[CONF_CMI_ID] = "invalid_cmi"
            else:
                try:
                    client = _entry_client(self.hass, new)
                    await client.read_values()
                except CMIError:
                    errors["base"] = "cannot_connect"
                else:
                    await self.async_set_unique_id(new[CONF_CMI_ID])
                    self._abort_if_unique_id_configured()
                    scan_interval = new.pop(OPT_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS)
                    return self.async_create_entry(
                        title=new.get(CONF_NAME) or f"TA {new[CONF_CMI_ID]}",
                        data=new,
                        options={OPT_SCAN_INTERVAL: scan_interval},
                    )
        schema = vol.Schema({
            vol.Required(CONF_CMI_ID): str,
            vol.Required(CONF_USERNAME): str,
            vol.Required(CONF_PASSWORD): str,
            vol.Required(CONF_SCHEME, default=2): vol.All(vol.Coerce(int), vol.Range(min=1, max=99)),
            vol.Required(OPT_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL_SECONDS):
                vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL_SECONDS,
                                                   max=MAX_SCAN_INTERVAL_SECONDS)),
            vol.Required(CONF_PROFILE, default=PROFILE_GENERIC): vol.In([PROFILE_UVR, PROFILE_GENERIC]),
            vol.Optional(CONF_NAME, default=""): str,
            vol.Optional(CONF_USER_FIELD, default="username"): str,
            vol.Optional(CONF_PASS_FIELD, default="passwort"): str,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry):
        return CMIOptionsFlow()

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        errors = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            details = {**entry.data, **user_input}
            try:
                await _entry_client(self.hass, details).read_values()
            except CMIError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(entry, data_updates=user_input)
        return self.async_show_form(step_id="reauth_confirm", errors=errors,
                                    data_schema=vol.Schema({
                                        vol.Required(CONF_USERNAME, default=entry.data[CONF_USERNAME]): str,
                                        vol.Required(CONF_PASSWORD): str,
                                    }))

    async def async_step_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            details = {**entry.data, **user_input}
            try:
                await _entry_client(self.hass, details).read_values()
            except CMIError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(entry, data_updates=user_input)
        return self.async_show_form(step_id="reconfigure", errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_SCHEME, default=entry.data.get(CONF_SCHEME, 2)):
                vol.All(vol.Coerce(int), vol.Range(min=1, max=99)),
            vol.Required(CONF_PROFILE, default=entry.data.get(CONF_PROFILE, PROFILE_UVR)):
                vol.In([PROFILE_UVR, PROFILE_GENERIC]),
            vol.Optional(CONF_NAME, default=entry.data.get(CONF_NAME, "")): str,
        }))


class CMIOptionsFlow(OptionsFlowWithReload):
    """Simple forms to add/edit/remove any number of schematic entities."""

    def _points(self) -> list[dict]:
        return list(self.config_entry.options.get(OPT_POINTS, []))

    def _save(self, points: list[dict]):
        return self.async_create_entry(data={**self.config_entry.options, OPT_POINTS: points})

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init", menu_options=["settings", "discover", "add", "edit", "remove", "climate", "climate_remove"]
        )

    async def async_step_settings(self, user_input=None):
        """Change how frequently this CMI is polled, without YAML or a HA restart."""
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={**self.config_entry.options, OPT_SCAN_INTERVAL: user_input[OPT_SCAN_INTERVAL]},
            )
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema({
                vol.Required(
                    OPT_SCAN_INTERVAL,
                    default=self.config_entry.options.get(
                        OPT_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS
                    ),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_SCAN_INTERVAL_SECONDS, max=MAX_SCAN_INTERVAL_SECONDS),
                ),
            }),
        )

    async def async_step_discover(self, user_input=None):
        """List actual cached schematic positions and current text in the UI."""
        if user_input is not None:
            self._suggested_position = user_input["position"]
            return await self.async_step_add()
        coordinator = getattr(self.config_entry, "runtime_data", None)
        data = (coordinator.data or {}) if coordinator else {}
        items = {key: f"{key}: {str(text)[:55]}" for key, text in data.items()
                 if valid_position(key)}
        if not items:
            return self.async_abort(reason="no_schema")
        return self.async_show_form(step_id="discover", data_schema=vol.Schema({
            vol.Required("position"): vol.In(items)
        }))

    @staticmethod
    def _schema(defaults: dict | None = None):
        d = defaults or {}
        return vol.Schema({
            vol.Required("name", default=d.get("name", "")): str,
            vol.Required("position", default=d.get("position", "pos1")): str,
            vol.Required("kind", default=d.get("kind", "sensor")): vol.In(KINDS),
            vol.Optional("unit", default=d.get("unit", "")): str,
            vol.Optional("link_index", default=int(d.get("link_index", 0))):
                vol.All(vol.Coerce(int), vol.Range(min=0, max=10)),
            vol.Optional("on_value", default=d.get("on_value", "EIN")): str,
            vol.Optional("off_value", default=d.get("off_value", "AUS")): str,
        })

    @staticmethod
    def _clean(data: dict, ident: str) -> dict:
        # Only well-formed position identifiers and a known entity type.
        return {
            "id": ident, "name": data["name"].strip()[:100],
            "position": data["position"].strip().lower(),
            "kind": data["kind"], "unit": data.get("unit", "").strip()[:20],
            "link_index": int(data.get("link_index", 0)),
            "on_value": data.get("on_value", "EIN").strip(),
            "off_value": data.get("off_value", "AUS").strip(),
        }

    @staticmethod
    def _error(point: dict, items: list[dict], editing_id: str | None = None):
        if not point["name"] or not valid_position(point["position"]):
            return "invalid_point"
        if point["kind"] == "binary_sensor" and point["on_value"] == point["off_value"]:
            return "invalid_point"
        # Same source and type can have distinct link indices, but not duplicates.
        if any(p["id"] != editing_id and p["position"] == point["position"]
               and p["kind"] == point["kind"]
               and (point["kind"] not in ("select", "number")
                    or int(p.get("link_index", 0)) == point["link_index"])
               for p in items):
            return "duplicate_point"
        return None

    async def async_step_add(self, user_input=None):
        errors = {}
        if user_input is not None:
            entries = self._points()
            # Stable key independent from name, so renames preserve unique_id.
            import uuid
            point = self._clean(user_input, uuid.uuid4().hex[:16])
            error = self._error(point, entries)
            if error:
                errors["base"] = error
            else:
                entries.append(point)
                return self._save(entries)
        return self.async_show_form(step_id="add", errors=errors,
                                    data_schema=self._schema({"position": getattr(self, "_suggested_position", "pos1")}))

    async def async_step_edit(self, user_input=None):
        points = self._points()
        if not points:
            return self.async_abort(reason="no_points")
        if user_input is not None:
            self._edit_id = user_input["point_id"]
            return await self.async_step_edit_details()
        return self.async_show_form(step_id="edit", data_schema=vol.Schema({
            vol.Required("point_id"): vol.In({p["id"]: f'{p["name"]} ({p["position"]})' for p in points})
        }))

    async def async_step_edit_details(self, user_input=None):
        entries = self._points()
        old = next((p for p in entries if p["id"] == self._edit_id), None)
        if old is None:
            return self.async_abort(reason="no_points")
        errors = {}
        if user_input is not None:
            point = self._clean(user_input, self._edit_id)
            error = self._error(point, entries, self._edit_id)
            if error:
                errors["base"] = error
            else:
                return self._save([point if p["id"] == self._edit_id else p for p in entries])
        return self.async_show_form(step_id="edit_details", errors=errors, data_schema=self._schema(old))

    async def async_step_remove(self, user_input=None):
        points = self._points()
        if not points:
            return self.async_abort(reason="no_points")
        if user_input is not None:
            return self._save([p for p in points if p["id"] != user_input["point_id"]])
        return self.async_show_form(step_id="remove", data_schema=vol.Schema({
            vol.Required("point_id"): vol.In({p["id"]: f'{p["name"]} ({p["position"]})' for p in points})
        }))


    async def async_step_climate(self, user_input=None):
        """Bind climate to explicit user-created room, setpoint and mode points."""
        points = self._points()
        temperatures = {item["id"]: f'{item["name"]} ({item["position"]})'
                        for item in points if item["kind"] == "sensor" and item.get("unit") == "°C"}
        targets = {item["id"]: f'{item["name"]} ({item["position"]})'
                   for item in points if item["kind"] == "number" and item.get("unit") == "°C"}
        modes = {"": "No mode selection"}
        modes.update({item["id"]: f'{item["name"]} ({item["position"]})'
                      for item in points if item["kind"] == "select"})
        if not temperatures or not targets:
            return self.async_abort(reason="no_climate_points")
        saved = self.config_entry.options.get(OPT_CLIMATE, {})
        errors = {}
        if user_input is not None:
            new = {k: str(user_input.get(k, "")).strip() for k in
                   ("room_point", "setpoint_point", "mode_point", "mode_heat", "mode_off", "mode_auto")}
            labels = [new[k] for k in ("mode_heat", "mode_off", "mode_auto") if new[k]]
            if (new["room_point"] not in temperatures
                    or new["setpoint_point"] not in targets
                    or new["mode_point"] not in modes
                    or (new["mode_point"] and not new["mode_heat"])
                    or (not new["mode_point"] and labels)
                    or len(labels) != len(set(labels))):
                errors["base"] = "invalid_climate"
            else:
                return self.async_create_entry(data={**self.config_entry.options, OPT_CLIMATE: new})
        return self.async_show_form(step_id="climate", errors=errors, data_schema=vol.Schema({
            vol.Required("room_point", default=saved.get("room_point", next(iter(temperatures)))): vol.In(temperatures),
            vol.Required("setpoint_point", default=saved.get("setpoint_point", next(iter(targets)))): vol.In(targets),
            vol.Required("mode_point", default=saved.get("mode_point", "")): vol.In(modes),
            vol.Optional("mode_heat", default=saved.get("mode_heat", "")): str,
            vol.Optional("mode_off", default=saved.get("mode_off", "")): str,
            vol.Optional("mode_auto", default=saved.get("mode_auto", "")): str,
        }))

    async def async_step_climate_remove(self, user_input=None):
        """Delete the configured climate while preserving all other entities."""
        if OPT_CLIMATE not in self.config_entry.options:
            return self.async_abort(reason="no_climate")
        if user_input is not None:
            if user_input["confirm"]:
                options = dict(self.config_entry.options)
                options.pop(OPT_CLIMATE, None)
                return self.async_create_entry(data=options)
            return self.async_abort(reason="cancelled")
        return self.async_show_form(step_id="climate_remove", data_schema=vol.Schema({
            vol.Required("confirm", default=False): bool
        }))
