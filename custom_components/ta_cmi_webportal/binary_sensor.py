"""UVR1611 pump running state."""
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONF_CMI_ID
from .coordinator import CMIDataCoordinator
from .const import PROFILE_UVR
from .dynamic_entities import GenericBinary


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    entities = [CMIPump(coordinator, entry)] if coordinator.profile == PROFILE_UVR else []
    entities.extend(GenericBinary(coordinator, entry, point) for point in coordinator.points
                    if point["kind"] == "binary_sensor")
    async_add_entities(entities)


class CMIPump(CoordinatorEntity[CMIDataCoordinator], BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Pumpe"

    def __init__(self, coordinator: CMIDataCoordinator, entry: ConfigEntry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.data[CONF_CMI_ID]}_pos7"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data[CONF_CMI_ID])},
            name=f"TA {entry.data[CONF_CMI_ID]}",
            manufacturer="Technische Alternative", model="CMI / UVR1611 (Webportal)",
        )

    @property
    def available(self) -> bool:
        return super().available and (self.coordinator.data or {}).get("pos7") in ("EIN", "AUS")

    @property
    def is_on(self) -> bool | None:
        return (self.coordinator.data or {}).get("pos7") == "EIN"
