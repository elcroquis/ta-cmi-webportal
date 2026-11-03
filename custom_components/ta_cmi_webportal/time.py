"""PARTY end time exposed by the TA CMI online schema.

This entity is only available when the UVR1611 is currently in PARTY mode.
Writes are verified using the live changer.cgi dialog and a readback; no
unverified time encoding is ever sent to the heating controller.
"""
from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import CMIError
from .const import CONF_CMI_ID, DOMAIN
from .coordinator import CMIDataCoordinator
from .write_support import read_mode


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    if entry.runtime_data.profile != "uvr1611":
        return
    async_add_entities([CMIPartyEnd(entry.runtime_data, entry)])


class CMIPartyEnd(CoordinatorEntity[CMIDataCoordinator], TimeEntity):
    """Party operation ends at the UVR1611's selected local time."""

    _attr_has_entity_name = True
    _attr_name = "Party-Ende einstellen"
    _attr_icon = "mdi:clock-end"

    def __init__(self, coordinator: CMIDataCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        cmi = entry.data[CONF_CMI_ID]
        self._attr_unique_id = f"{cmi}_party_end_control"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, cmi)}, name=f"TA {cmi}",
            manufacturer="Technische Alternative",
            model="CMI / UVR1611 (Webportal)",
        )

    @property
    def available(self) -> bool:
        values = self.coordinator.data or {}
        return (
            super().available
            and read_mode(values.get("pos18")) == "PARTY"
            and "party_end" in values
        )

    @property
    def native_value(self) -> time | None:
        value = (self.coordinator.data or {}).get("party_end")
        if not value:
            return None
        try:
            return time.fromisoformat(value)
        except ValueError:
            return None

    async def async_set_value(self, value: time) -> None:
        if value.second != 0 or value.microsecond != 0:
            raise HomeAssistantError("Party-Ende unterstützt nur Stunden und Minuten")
        try:
            updated = await self.coordinator.client.set_party_end(value.strftime("%H:%M"))
        except CMIError as err:
            try:
                await self.coordinator.async_request_refresh()
            except Exception:
                pass
            raise HomeAssistantError(str(err)) from err
        self.coordinator.async_set_updated_data(updated)
