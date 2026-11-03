"""Shared configuration constants for TA CMI Webportal."""
from datetime import timedelta

DOMAIN = "ta_cmi_webportal"
LOGIN_FORM_URL = "https://cmi.ta.co.at/portal/ta/loginformular/"
LOGIN_POST_URL = "https://cmi.ta.co.at/portal/checkLogin.inc.php?mode=ta"
# Polling the TA cloud too often can exhaust sessions / cause throttling.
# Each CMI entry has its own configurable interval in entry.options.
DEFAULT_SCAN_INTERVAL_SECONDS = 120
MIN_SCAN_INTERVAL_SECONDS = 60
MAX_SCAN_INTERVAL_SECONDS = 3600
OPT_SCAN_INTERVAL = "scan_interval"
SCAN_INTERVAL = timedelta(seconds=DEFAULT_SCAN_INTERVAL_SECONDS)  # Legacy alias


def scan_interval_for_entry(entry) -> timedelta:
    """Read the configured polling interval (old entries default to 120 s)."""
    seconds = int(entry.options.get(OPT_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS))
    return timedelta(
        seconds=max(MIN_SCAN_INTERVAL_SECONDS, min(MAX_SCAN_INTERVAL_SECONDS, seconds))
    )

CONF_CMI_ID = "cmi_id"
CONF_USER_FIELD = "user_field"
CONF_PASS_FIELD = "pass_field"
CONF_SCHEME = "scheme_number"
CONF_PROFILE = "profile"
CONF_NAME = "display_name"
PROFILE_UVR = "uvr1611"
PROFILE_GENERIC = "custom"
OPT_POINTS = "points"
