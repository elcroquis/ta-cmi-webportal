"""Home Assistant entities configured entirely through the options flow."""
from __future__ import annotations

from decimal import Decimal

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.number import NumberEntity
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import CMIError
from .const import DOMAIN, CONF_CMI_ID
from .generic import parse_numeric, point_value


class _GenericBase:
    """Mixin for user-configured entities; names and unique IDs stay stable."""

    _attr_has_entity_name = True

    def _init_point(self, entry, point):
        self.point = point
        self._attr_name = point["name"]
        self._attr_unique_id = f'{entry.data[CONF_CMI_ID]}_custom_{point["id"]}'
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data[CONF_CMI_ID])},
            name=entry.title, manufacturer="Technische Alternative",
            model="CMI (Webportal)",
            configuration_url=self.coordinator.client.base + "schema.html#1",
        )

    def _read(self):
        return point_value(self.coordinator.data or {}, self.coordinator.client.anchors, self.point)

    @property
    def available(self):
        return super().available and self._read() is not None


class GenericSensor(_GenericBase, CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, entry, point):
        super().__init__(coordinator)
        self._init_point(entry, point)
        unit = point.get("unit") or None
        self._attr_native_unit_of_measurement = unit
        # Apply well-understood device classes only where the unit matches.
        classes = {"°C": SensorDeviceClass.TEMPERATURE,
                   "kW": SensorDeviceClass.POWER, "W": SensorDeviceClass.POWER,
                   "kWh": SensorDeviceClass.ENERGY, "Wh": SensorDeviceClass.ENERGY,
                   "%": SensorDeviceClass.HUMIDITY}
        self._attr_device_class = classes.get(unit)

    @property
    def native_value(self):
        text = self._read()
        if text is None:
            return None
        num = parse_numeric(text)
        # No unit means don't invent numeric state for arbitrary labels.
        return float(num) if num is not None else (text if not self.point.get("unit") else None)

    @property
    def state_class(self):
        return SensorStateClass.MEASUREMENT if isinstance(self.native_value, (float, int)) else None


class GenericBinary(_GenericBase, CoordinatorEntity, BinarySensorEntity):
    def __init__(self, coordinator, entry, point):
        super().__init__(coordinator)
        self._init_point(entry, point)

    @property
    def available(self):
        return super().available and self._read() in (
            self.point.get("on_value", "EIN"), self.point.get("off_value", "AUS"))

    @property
    def is_on(self):
        return self._read() == self.point.get("on_value", "EIN")


class _WritableBase(_GenericBase):
    def _choices(self):
        return self.coordinator.client.generic_options.get(self.point["id"], [])

    @property
    def available(self):
        return super().available and bool(self._choices())

    async def _write(self, value):
        try:
            data = await self.coordinator.client.set_generic(self.point, value)
        except CMIError as error:
            try:
                await self.coordinator.async_request_refresh()
            except Exception:
                pass
            raise HomeAssistantError(str(error)) from error
        self.coordinator.async_set_updated_data(data)


class GenericSelect(_WritableBase, CoordinatorEntity, SelectEntity):
    def __init__(self, coordinator, entry, point):
        super().__init__(coordinator)
        self._init_point(entry, point)

    @property
    def options(self):
        return [label for _, label in self._choices()]

    @property
    def current_option(self):
        current = self._read()
        return current if current in self.options else None

    async def async_select_option(self, option: str):
        await self._write(option)


class GenericNumber(_WritableBase, CoordinatorEntity, NumberEntity):
    _attr_mode = "box"

    def __init__(self, coordinator, entry, point):
        super().__init__(coordinator)
        self._init_point(entry, point)
        self._attr_native_unit_of_measurement = point.get("unit") or None

    def _numbers(self):
        return sorted(set(value for _, label in self._choices()
                          if (value := parse_numeric(label)) is not None))

    @property
    def available(self):
        return super().available and bool(self._numbers())

    @property
    def native_value(self):
        result = parse_numeric(self._read() or "")
        return float(result) if result is not None else None

    @property
    def native_min_value(self):
        choices = self._numbers()
        return float(choices[0]) if choices else 0.0

    @property
    def native_max_value(self):
        choices = self._numbers()
        return float(choices[-1]) if choices else 100.0

    @property
    def native_step(self):
        from functools import reduce
        from math import gcd
        numbers = self._numbers()
        if len(numbers) < 2:
            return 1.0
        decimals = max(0, *(-n.as_tuple().exponent for n in numbers))
        factor = 10 ** min(decimals, 4)
        ints = [int(n * factor) for n in numbers]
        gaps = [b - a for a, b in zip(ints, ints[1:]) if b > a]
        return float(Decimal(reduce(gcd, gaps)) / factor) if gaps else 1.0

    async def async_set_native_value(self, value: float):
        await self._write(value)
