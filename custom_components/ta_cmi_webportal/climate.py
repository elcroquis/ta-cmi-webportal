"""Native Home Assistant thermostat facade for the UVR1611 heating circuit.

The UVR1611 remains the thermostat/controller. No local PID or relay control.
"""
from __future__ import annotations

import re

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import CMIError
from .const import CONF_CMI_ID, DOMAIN, OPT_CLIMATE, PROFILE_UVR
from .coordinator import CMIDataCoordinator
from .write_support import MODES, read_auto_phase, read_mode

_NUM = re.compile(r"[-+]?\d+(?:[,.]\d+)?")


def _temperature(data: dict, key: str) -> float | None:
    text = data.get(key, "")
    match = _NUM.search(text)
    return float(match.group().replace(",", ".")) if match else None


def effective_setpoint_position(mode: str | None, phase: str | None) -> str | None:
    """Use the actively displayed room setpoint; don't guess in special modes."""
    if mode in ("NORMAL", "PARTY", "FEIERTAG"):
        return "pos16"
    if mode in ("ABGESENKT", "URLAUB"):
        return "pos17"
    if mode == "ZEIT/AUTO":
        return {"NORMAL": "pos16", "ABGESENKT": "pos17"}.get(phase)
    return None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    if entry.runtime_data.profile == PROFILE_UVR:
        async_add_entities([CMIHeatingClimate(entry.runtime_data, entry)])
        return
    mapping = entry.options.get(OPT_CLIMATE, {})
    if mapping:
        from .climate_mapping import mapping_points
        from .custom_climate import ConfiguredCMIClimate
        if mapping_points(entry.runtime_data.points, mapping):
            async_add_entities([ConfiguredCMIClimate(entry.runtime_data, entry, mapping)])


class CMIHeatingClimate(CoordinatorEntity[CMIDataCoordinator], ClimateEntity):
    """Reflect real CMI mode and temperatures; delegate writes to proven API."""

    _attr_has_entity_name = True
    _attr_name = "Heizkreis Thermostat"
    _attr_icon = "mdi:radiator"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.AUTO]
    _attr_preset_modes = list(MODES)
    _attr_min_temp = 5.0
    _attr_max_temp = 30.0
    _attr_target_temperature_step = 1.0

    def __init__(self, coordinator: CMIDataCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        cmi = entry.data[CONF_CMI_ID]
        self._attr_unique_id = f"{cmi}_heizkreis_climate"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, cmi)},
            name=f"TA {cmi}",
            manufacturer="Technische Alternative",
            model="CMI / UVR1611 (Webportal)",
        )

    @property
    def available(self) -> bool:
        data = self.coordinator.data or {}
        return super().available and read_mode(data.get("pos18")) is not None

    @property
    def current_temperature(self) -> float | None:
        return _temperature(self.coordinator.data or {}, "pos13")

    @property
    def hvac_mode(self) -> HVACMode | None:
        mode = read_mode((self.coordinator.data or {}).get("pos18"))
        if mode == "STANDBY":
            return HVACMode.OFF
        if mode == "ZEIT/AUTO":
            return HVACMode.AUTO
        if mode in MODES:
            return HVACMode.HEAT
        return None

    @property
    def preset_mode(self) -> str | None:
        return read_mode((self.coordinator.data or {}).get("pos18"))

    @property
    def target_temperature(self) -> float | None:
        data = self.coordinator.data or {}
        position = effective_setpoint_position(read_mode(data.get("pos18")), read_auto_phase(data.get("pos18")))
        return _temperature(data, position) if position else None

    @property
    def target_temperature_step(self) -> float:
        data = self.coordinator.data or {}
        position = effective_setpoint_position(read_mode(data.get("pos18")), read_auto_phase(data.get("pos18")))
        if position:
            step = self.coordinator.client.temperature_steps.get(position)
            if step is not None:
                return float(step)
        return 1.0

    async def _write(self, operation) -> None:
        try:
            data = await operation
        except CMIError as err:
            try:
                await self.coordinator.async_request_refresh()
            except Exception:
                pass
            raise HomeAssistantError(str(err)) from err
        self.coordinator.async_set_updated_data(data)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        targets = {HVACMode.OFF: "STANDBY", HVACMode.HEAT: "NORMAL", HVACMode.AUTO: "ZEIT/AUTO"}
        if hvac_mode not in targets:
            raise HomeAssistantError(f"Nicht unterstützter Modus: {hvac_mode}")
        await self._write(self.coordinator.client.set_mode(targets[hvac_mode]))

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode not in MODES:
            raise HomeAssistantError(f"Unbekannte UVR1611-Betriebsart: {preset_mode}")
        await self._write(self.coordinator.client.set_mode(preset_mode))

    async def async_set_temperature(self, **kwargs) -> None:
        if ATTR_TEMPERATURE not in kwargs:
            return
        data = self.coordinator.data or {}
        mode = read_mode(data.get("pos18"))
        # Schedule/special modes are deliberately read-only on the thermostat:
        # use the existing explicit Normal/Reduced number entities instead.
        if mode not in ("NORMAL", "ABGESENKT"):
            raise HomeAssistantError(
                "Temperatur am Thermostat nur in NORMAL oder ABGESENKT ändern. "
                "Bei ZEIT/AUTO und Sondermodi bitte Soll-Normal/Soll-Absenken verwenden."
            )
        position = "pos16" if mode == "NORMAL" else "pos17"
        await self._write(self.coordinator.client.set_temperature(position, float(kwargs[ATTR_TEMPERATURE])))
