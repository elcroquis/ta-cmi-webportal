"""UVR1611 heating operating mode (real device write, verified by readback)."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import CMIError
from .const import CONF_CMI_ID, DOMAIN
from .coordinator import CMIDataCoordinator
from .write_support import MODES, read_mode
from .const import PROFILE_UVR
from .dynamic_entities import GenericSelect


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities = [CMIHeatingMode(coordinator, entry)] if coordinator.profile == PROFILE_UVR else []
    entities.extend(GenericSelect(coordinator, entry, point) for point in coordinator.points
                    if point["kind"] == "select")
    async_add_entities(entities)


class CMIHeatingMode(CoordinatorEntity[CMIDataCoordinator], SelectEntity):
    """Mode choices are taken from the user's real UVR1611 changer dialog."""

    _attr_has_entity_name = True
    _attr_name = "Heizkreis Betriebsart einstellen"
    _attr_icon = "mdi:heating-coil"
    _attr_options = list(MODES)

    def __init__(self, coordinator: CMIDataCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        cmi = entry.data[CONF_CMI_ID]
        self._attr_unique_id = f"{cmi}_pos18_control"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, cmi)}, name=f"TA {cmi}",
            manufacturer="Technische Alternative", model="CMI / UVR1611 (Webportal)",
        )

    @property
    def available(self) -> bool:
        return super().available and "pos18" in (self.coordinator.data or {})

    @property
    def current_option(self) -> str | None:
        # pos18 contains e.g. "PARTY 00:00" when an end time is visible.
        # Derive the selected item from the *live CMI state*, not a default.
        return read_mode((self.coordinator.data or {}).get("pos18"))

    async def async_select_option(self, option: str) -> None:
        try:
            new_data = await self.coordinator.client.set_mode(option)
        except CMIError as err:
            # A write may have happened even when its HTTP response was lost.
            # Never claim success until the device reports the target state.
            try:
                await self.coordinator.async_request_refresh()
            except Exception:  # Best-effort refresh; preserve write error.
                pass
            raise HomeAssistantError(str(err)) from err
        self.coordinator.async_set_updated_data(new_data)
