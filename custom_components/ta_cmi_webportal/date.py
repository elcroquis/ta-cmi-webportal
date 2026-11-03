"""URLAUB/FEIERTAG end day from the UVR1611's yearless CMI calendar."""
from __future__ import annotations

from datetime import date

from homeassistant.components.date import DateEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import CMIError
from .const import CONF_CMI_ID, DOMAIN
from .coordinator import CMIDataCoordinator
from .write_support import CMIDialogError, next_occurrence, read_mode


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    if entry.runtime_data.profile != "uvr1611":
        return
    async_add_entities([CMISpecialEnd(entry.runtime_data, entry)])


class CMISpecialEnd(CoordinatorEntity[CMIDataCoordinator], DateEntity):
    """Last vacation/holiday day. Visible only in these two operating modes."""

    _attr_has_entity_name = True
    _attr_name = "Urlaub/Feiertag Enddatum einstellen"
    _attr_icon = "mdi:calendar-end"

    def __init__(self, coordinator: CMIDataCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        cmi = entry.data[CONF_CMI_ID]
        self._attr_unique_id = f"{cmi}_special_end_control"
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
            and read_mode(values.get("pos18")) in ("URLAUB", "FEIERTAG")
            and "special_end" in values
        )

    @property
    def native_value(self) -> date | None:
        raw = (self.coordinator.data or {}).get("special_end")
        if not raw:
            return None
        try:
            return next_occurrence(raw, dt_util.as_local(dt_util.utcnow()).date())
        except (CMIDialogError, ValueError):
            return None

    async def async_set_value(self, value: date) -> None:
        today = dt_util.as_local(dt_util.utcnow()).date()
        target = value.strftime("%d.%m.")
        if value != next_occurrence(target, today):
            raise HomeAssistantError(
                "Die UVR1611 überträgt nur Tag und Monat, kein Jahr. "
                "Bitte das nächste bevorstehende Datum wählen."
            )
        try:
            updated = await self.coordinator.client.set_special_end(target)
        except CMIError as err:
            try:
                await self.coordinator.async_request_refresh()
            except Exception:
                pass
            raise HomeAssistantError(str(err)) from err
        self.coordinator.async_set_updated_data(updated)
