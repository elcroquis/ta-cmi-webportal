"""Coordinate reading of CMI values."""
from __future__ import annotations

import logging
from time import monotonic

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CMIClient, CMIError
from .const import DOMAIN, PROFILE_UVR, scan_interval_for_entry
from .generic import get_points

_LOGGER = logging.getLogger(__name__)


class CMIDataCoordinator(DataUpdateCoordinator[dict[str, str]]):
    def __init__(self, hass: HomeAssistant, client: CMIClient, entry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=scan_interval_for_entry(entry))
        self.client = client
        self.points = get_points(entry)
        self.profile = client.profile
        self._probed_temperature_options = False
        # When a CMI dialog is unavailable, don't re-fetch it on every poll.
        self._next_probe_at = 0.0

    async def _async_update_data(self) -> dict[str, str]:
        try:
            values = await self.client.read_values()
            if not self._probed_temperature_options and monotonic() >= self._next_probe_at:
                # Back off failed optional probes for 30 minutes. Sensor updates
                # continue at the user's normal polling interval.
                self._next_probe_at = monotonic() + 1800
                # Probe optional dropdowns; missing dialogs are retried on the
                # next refresh without ever making sensor reading fail.
                if self.profile == PROFILE_UVR:
                    await self.client.probe_temperature_options()
                if self.points:
                    await self.client.probe_generic_options(self.points)
                wanted = {p["id"] for p in self.points if p["kind"] in ("select", "number")}
                generic_ok = wanted.issubset(self.client.generic_options)
                uvr_ok = (self.profile != PROFILE_UVR
                          or {"pos16", "pos17"}.issubset(self.client.temperature_options))
                self._probed_temperature_options = generic_ok and uvr_ok
            return values
        except CMIError as err:
            raise UpdateFailed(str(err)) from err
