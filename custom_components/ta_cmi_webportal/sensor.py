"""Sensor entities from UVR1611 HTML scheme."""
from __future__ import annotations

import re

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONF_CMI_ID
from .coordinator import CMIDataCoordinator
from .write_support import read_mode
from .const import PROFILE_UVR
from .dynamic_entities import GenericSensor

TEMP = SensorDeviceClass.TEMPERATURE
MEAS = SensorStateClass.MEASUREMENT
DESCRIPTIONS = (
    SensorEntityDescription(key="pos1", name="Messwert pos1", native_unit_of_measurement="l/h", state_class=MEAS),
    SensorEntityDescription(key="pos2", name="Temperatur pos2", device_class=TEMP, native_unit_of_measurement="°C", state_class=MEAS),
    SensorEntityDescription(key="pos3", name="Temperatur pos3", device_class=TEMP, native_unit_of_measurement="°C", state_class=MEAS),
    SensorEntityDescription(key="pos4", name="Energie pos4", device_class=SensorDeviceClass.ENERGY, native_unit_of_measurement="kWh", state_class=MEAS),
    SensorEntityDescription(key="pos5", name="Leistung pos5", device_class=SensorDeviceClass.POWER, native_unit_of_measurement="kW", state_class=MEAS),
    SensorEntityDescription(key="pos6", name="Temperatur pos6", device_class=TEMP, native_unit_of_measurement="°C", state_class=MEAS),
    SensorEntityDescription(key="pos8", name="Temperatur pos8", device_class=TEMP, native_unit_of_measurement="°C", state_class=MEAS),
    SensorEntityDescription(key="pos9", name="Temperatur pos9", device_class=TEMP, native_unit_of_measurement="°C", state_class=MEAS),
    SensorEntityDescription(key="pos10", name="Temperatur pos10", device_class=TEMP, native_unit_of_measurement="°C", state_class=MEAS),
    SensorEntityDescription(key="pos13", name="Raumtemperatur", device_class=TEMP, native_unit_of_measurement="°C", state_class=MEAS),
    SensorEntityDescription(key="pos14", name="Außentemperatur", device_class=TEMP, native_unit_of_measurement="°C", state_class=MEAS),
    SensorEntityDescription(key="pos16", name="Soll-Normal", device_class=TEMP, native_unit_of_measurement="°C", state_class=MEAS),
    SensorEntityDescription(key="pos17", name="Soll-Absenken", device_class=TEMP, native_unit_of_measurement="°C", state_class=MEAS),
    SensorEntityDescription(key="pos18", name="Heizkreis Betriebsart"),
    SensorEntityDescription(key="auto_phase", name="Heizkreis Automatikphase"),
    SensorEntityDescription(key="special_end", name="Urlaub/Feiertag Enddatum (TT.MM.)"),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    entities = []
    if coordinator.profile == PROFILE_UVR:
        entities.extend(CMISensor(coordinator, entry, desc) for desc in DESCRIPTIONS)
    entities.extend(GenericSensor(coordinator, entry, point) for point in coordinator.points
                    if point["kind"] == "sensor")
    async_add_entities(entities)


class CMISensor(CoordinatorEntity[CMIDataCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: CMIDataCoordinator, entry: ConfigEntry, description: SensorEntityDescription):
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.data[CONF_CMI_ID]}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data[CONF_CMI_ID])},
            name=f"TA {entry.data[CONF_CMI_ID]}",
            manufacturer="Technische Alternative",
            model="CMI / UVR1611 (Webportal)",
            configuration_url=coordinator.client.base + "schema.html#1",
        )

    @property
    def available(self) -> bool:
        return super().available and self.entity_description.key in (self.coordinator.data or {})

    @property
    def native_value(self) -> float | str | None:
        text = (self.coordinator.data or {}).get(self.entity_description.key)
        if text is None:
            return None
        if self.entity_description.key == "pos18":
            return read_mode(text) or text
        if self.entity_description.key in ("auto_phase", "special_end"):
            return text
        match = re.search(r"[-+]?\d+(?:[,.]\d+)?", text)
        return float(match.group(0).replace(",", ".")) if match else None
