"""TA CMI Webportal integration for reading and controlled writes."""
from __future__ import annotations

import logging

from aiohttp import CookieJar
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import CMIClient
from .const import CONF_CMI_ID, CONF_PASS_FIELD, CONF_USER_FIELD, CONF_SCHEME, CONF_PROFILE, PROFILE_UVR
from .coordinator import CMIDataCoordinator

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SELECT, Platform.NUMBER, Platform.TIME, Platform.DATE, Platform.CLIMATE]
_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Dedicated, HA-managed session; critical to isolate the PHP session cookie.
    session = async_create_clientsession(hass, cookie_jar=CookieJar())
    client = CMIClient(
        session,
        entry.data[CONF_CMI_ID],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        entry.data.get(CONF_USER_FIELD) or "username",
        entry.data.get(CONF_PASS_FIELD) or "passwort",
        scheme_number=entry.data.get(CONF_SCHEME, 2),
        profile=entry.data.get(CONF_PROFILE, PROFILE_UVR),
    )
    coordinator = CMIDataCoordinator(hass, client, entry)
    # The remote TA portal may temporarily return incomplete HTML during
    # start-up or after switching a heating mode.  Unlike the "first refresh"
    # helper, async_refresh() lets the integration load with entities marked
    # unavailable. Normal coordinator polling recovers them automatically.
    try:
        await coordinator.async_refresh()
        # Provide a safe, actionable log message for failed initial reads.
        if not coordinator.last_update_success:
            _LOGGER.warning(
                "TA CMI: Startabfrage fehlgeschlagen; Entitäten vorerst nicht verfügbar: %s",
                coordinator.last_exception,
            )
        entry.runtime_data = coordinator
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        # A failed or cancelled setup must not keep an isolated portal session.
        await session.close()
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    success = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if success:
        await entry.runtime_data.client.session.close()
    return success


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Keep existing UVR1611 entries and stable entity unique IDs on upgrade."""
    if entry.version in (1, 2):
        # Prior releases treated a missing profile as the UVR1611 layout.
        # Preserve old entries; only NEW installations default to custom.
        data = dict(entry.data)
        data.setdefault(CONF_PROFILE, PROFILE_UVR)
        hass.config_entries.async_update_entry(entry, data=data, version=3)
        return True
    return entry.version == 3
