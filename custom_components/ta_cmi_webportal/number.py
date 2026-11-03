"""UVR1611 normal and reduced room temperature setpoints."""
from __future__ import annotations

import re

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import CMIError
from .const import PROFILE_UVR
from .dynamic_entities import GenericNumber
from .const import CONF_CMI_ID, DOMAIN
from .coordinator import CMIDataCoordinator

_NUMBER = re.compile(r"[-+]?\d+(?:[,.]\d+)?")
_SETPOINTS = {"pos16": "Soll-Normal einstellen", "pos17": "Soll-Absenken einstellen"}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities = ([CMISetpoint(coordinator, entry, key, label) for key, label in _SETPOINTS.items()]
                if coordinator.profile == PROFILE_UVR else [])
    entities.extend(GenericNumber(coordinator, entry, point) for point in coordinator.points
                    if point["kind"] == "number")
    async_add_entities(entities)


class CMISetpoint(CoordinatorEntity[CMIDataCoordinator], NumberEntity):
    """Adjust one setpoint; only expose a conservative local temperature range."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:thermometer-auto"
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_mode = NumberMode.BOX
    # Fallback until the live CMI popup exposes the supported range/step.
    _attr_native_min_value = 5.0
    _attr_native_max_value = 30.0
    _attr_native_step = 1.0

    def __init__(
        self, coordinator: CMIDataCoordinator, entry: ConfigEntry, key: str, label: str
    ) -> None:
        super().__init__(coordinator)
        self.position = key
        self._attr_name = label
        cmi = entry.data[CONF_CMI_ID]
        self._attr_unique_id = f"{cmi}_{key}_control"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, cmi)}, name=f"TA {cmi}",
            manufacturer="Technische Alternative", model="CMI / UVR1611 (Webportal)",
        )

    @property
    def native_min_value(self) -> float:
        choices = self.coordinator.client.temperature_options.get(self.position)
        return float(min(choices)) if choices else self._attr_native_min_value

    @property
    def native_max_value(self) -> float:
        choices = self.coordinator.client.temperature_options.get(self.position)
        return float(max(choices)) if choices else self._attr_native_max_value

    @property
    def native_step(self) -> float:
        step = self.coordinator.client.temperature_steps.get(self.position)
        return float(step) if step is not None else self._attr_native_step

    @property
    def available(self) -> bool:
        return super().available and self.position in (self.coordinator.data or {})

    @property
    def native_value(self) -> float | None:
        text = (self.coordinator.data or {}).get(self.position, "")
        match = _NUMBER.search(text)
        return float(match.group().replace(",", ".")) if match else None

    async def async_set_native_value(self, value: float) -> None:
        try:
            new_data = await self.coordinator.client.set_temperature(self.position, value)
        except CMIError as err:
            try:
                await self.coordinator.async_request_refresh()
            except Exception:  # Best-effort refresh; preserve write error.
                pass
            raise HomeAssistantError(str(err)) from err
        self.coordinator.async_set_updated_data(new_data)
