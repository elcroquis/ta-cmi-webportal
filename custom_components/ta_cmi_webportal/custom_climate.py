"""Optional native thermostat for explicitly mapped CMI schematic controls.

The CMI/UVR remains the actual controller. No independent regulation is added.
"""
from __future__ import annotations
from decimal import Decimal
from functools import reduce
from math import gcd
from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACMode
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .api import CMIError
from .climate_mapping import active_mode, mapping_points, supported_modes
from .const import DOMAIN, CONF_CMI_ID
from .generic import parse_numeric, point_value

class ConfiguredCMIClimate(CoordinatorEntity, ClimateEntity):
    """A native thermostat backed by explicitly mapped CMI read/write entities."""
    _attr_has_entity_name = True
    _attr_name = "CMI Thermostat"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_icon = "mdi:radiator"
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE

    def __init__(self, coordinator, entry, mapping: dict) -> None:
        super().__init__(coordinator)
        self.mapping = mapping
        mapped = mapping_points(coordinator.points, mapping)
        if mapped is None:
            raise ValueError("Incomplete climate mapping")
        self.room, self.target, self.mode = mapped
        self._attr_unique_id = f"{entry.data[CONF_CMI_ID]}_configured_climate"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data[CONF_CMI_ID])},
            name=entry.title, manufacturer="Technische Alternative", model="CMI (Webportal)",
        )
        self._attr_hvac_modes = [HVACMode(mode) for mode in supported_modes(mapping)]

    def _value(self, point: dict) -> str | None:
        return point_value(self.coordinator.data or {}, self.coordinator.client.anchors, point)

    def _decimal(self, point: dict) -> Decimal | None:
        raw = self._value(point)
        return parse_numeric(raw) if raw is not None else None

    @property
    def available(self) -> bool:
        if not super().available or self._decimal(self.room) is None or self._decimal(self.target) is None:
            return False
        return not self.mode or self.hvac_mode is not None

    @property
    def current_temperature(self) -> float | None:
        result = self._decimal(self.room)
        return float(result) if result is not None else None

    @property
    def target_temperature(self) -> float | None:
        result = self._decimal(self.target)
        return float(result) if result is not None else None

    @property
    def hvac_mode(self) -> HVACMode | None:
        label = self._value(self.mode) if self.mode else None
        mode = active_mode(self.mapping, label)
        return HVACMode(mode) if mode is not None else None

    def _options(self) -> list[Decimal]:
        return sorted(set(value for _, label in self.coordinator.client.generic_options.get(self.target["id"], [])
                          if (value := parse_numeric(label)) is not None))

    @property
    def min_temp(self) -> float:
        items = self._options()
        return float(items[0]) if items else 5.0

    @property
    def max_temp(self) -> float:
        items = self._options()
        return float(items[-1]) if items else 30.0

    @property
    def target_temperature_step(self) -> float:
        items = self._options()
        if len(items) < 2:
            return 1.0
        decimals = max(0, *(-v.as_tuple().exponent for v in items))
        factor = 10 ** min(decimals, 4)
        integers = [int(v * factor) for v in items]
        gaps = [b - a for a, b in zip(integers, integers[1:]) if b > a]
        return float(Decimal(reduce(gcd, gaps)) / factor) if gaps else 1.0

    async def _write(self, point: dict, value: str | float) -> None:
        try:
            updated = await self.coordinator.client.set_generic(point, value)
        except CMIError as err:
            raise HomeAssistantError(str(err)) from err
        self.coordinator.async_set_updated_data(updated)

    async def async_set_temperature(self, **kwargs) -> None:
        if ATTR_TEMPERATURE not in kwargs:
            return
        if self.hvac_mode == HVACMode.OFF:
            raise HomeAssistantError("Temperature changes are unavailable while the mode is off")
        await self._write(self.target, float(kwargs[ATTR_TEMPERATURE]))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode not in self.hvac_modes:
            raise HomeAssistantError("The requested HVAC mode has not been mapped")
        if self.mode is None:
            return
        label = {
            HVACMode.HEAT: self.mapping.get("mode_heat"),
            HVACMode.OFF: self.mapping.get("mode_off"),
            HVACMode.AUTO: self.mapping.get("mode_auto"),
        }.get(hvac_mode)
        if not label:
            raise HomeAssistantError("No CMI selection is mapped to this mode")
        await self._write(self.mode, label)
