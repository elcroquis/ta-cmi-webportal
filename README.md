# TA CMI Webportal for Home Assistant (development beta v0.5.0b1)

**Unofficial community project; not affiliated with Technische Alternative.**

Manage CMI devices remotely through the TA cloud portal from Home Assistant.
No local network access to the CMI is required. All setup and entity mapping
is done in the Home Assistant UI; **no YAML required**.

## Features

- Multiple independent CMI entries (different serial numbers and credentials).
- Choose the CMI online schematic number (`schematic_files/<number>.cgi`).
- **Optional layout-specific UVR1611 compatibility profile**: existing temperature, flow, pump, mode,
  target temperatures, special-mode time/date and native climate entities.
- **Custom profile**: add/edit/remove sensors, binary sensors, selection fields
  and number controls, mapping each to a `posN` in the online schematic.
- Read-only sensors are the default. Write controls only operate through a
  live, validated CMI `loadChanger` link and an actual list of allowed options.
- Change requests are sent **once**, then verified by safe read-only requests.
- Session renewal and recovery from temporary empty schematic responses.
- Dedicated session per CMI to prevent portal cookie crossover.
- New installations default to **Custom**: no preset sensor positions.
- Optional native climate entity from explicitly mapped °C room sensor and writable °C number; exact mode labels can also be mapped.
- All legacy edit addresses are read from current CMI HTML, not fixed constants.
- English and German user interface.

## Important limitations

This integration uses **undocumented TA Webportal HTML endpoints**. Compatibility
with other CMI firmwares and non-UVR1611 schematics is **not guaranteed**.
The custom profile can map only HTML `div#posN` values present in the CMI online
schematic. It cannot expose arbitrary CAN bus menus or controller parameters
that are not part of that schematic. Generic write controls currently require
an HTML **select** list; other popup formats are read-only. Modes, party time,
holiday date and the `climate` entity are available through the **UVR1611
profile**, not automatically discovered for generic CMIs.

**Caution:** Writing setpoints or modes changes the real heating controller.
Only configure a writable entity after verifying its edit link, and test on
noncritical settings. No automatic write retries occur.

## Setup (no YAML)

1. Install as a **custom HACS repository** (see below) or manually copy
   `custom_components/ta_cmi_webportal` to Home Assistant `/config/custom_components`.
2. Restart Home Assistant.
3. **Settings → Devices & services → Add integration → TA CMI Webportal.**
4. Enter CMI serial, TA portal username and password, online schematic number,
   and either **Custom schematic** (recommended) or **UVR1611** (legacy layout, only after verifying point positions).
5. For custom mappings: **Settings → Devices & services → TA CMI Webportal → Configure**.
   Use Add / Edit / Remove to manage point entities entirely in the UI.
   - `position`: `pos1`, `pos2`, ... exactly as the online schematic HTML.
   - `kind`: `sensor`, `binary_sensor`, `select`, `number`.
   - `unit`: optional (e.g. `°C`, `kW`, `kWh`, `l/h`).
   - `link_index`: zero-based index of a `loadChanger(...)` link inside that
     `posN` HTML element, for controls. For example `pos18` may have 2 links.
   - `on_value`, `off_value`: exact state labels for binary sensors.
6. For a native thermostat, map a °C sensor and writable °C number, then use **Configure → Configure thermostat**. Optional exact mode labels map Heat/Off/Auto.
7. After saving an entity mapping, Home Assistant automatically reloads the
   integration. You don't need to restart for each new point.

### Custom HACS repository

In HACS, open **Custom repositories**, supply the URL of **your published GitHub
repository** and select the integration category. Install and restart.

**This archive is repository source, not an already published HACS listing.**
You must publish it on GitHub first. See [PUBLISHING.md](PUBLISHING.md).

## Upgrading from v0.3.0 or v0.4.0

The default profile for existing entries with no stored profile remains
`uvr1611`, and the schematic number remains `2`. The existing entity unique
IDs for the original UVR1611 entities are preserved. **Back up** your current
`custom_components/ta_cmi_webportal` directory before replacing it.

Do not delete the existing integration entry or recreate its entities.
The first load of the beta may temporarily show entities unavailable if the
cloud returns incomplete HTML; polling will recover on a valid response.

## Privacy and safety

Login credentials stay in Home Assistant's config-entry storage and are only
sent to the fixed HTTPS domain `cmi.ta.co.at`. Do not post raw cookies or
passwords in GitHub issues. The integration is cloud-dependent: internet
connectivity and a valid TA portal login are required. It is not an official
TA API and may break when the portal HTML changes.

## Development

Run `python -m compileall custom_components/ta_cmi_webportal` and
`python -m unittest discover -s tests -v` for local non-HA parsing tests.
Validate with Home Assistant **hassfest** and the **HACS action** before a public
HACS submission. Real device testing is still required for the beta.

Licensed under MIT.

## v0.5.0b1 development disclaimer

The **Custom** profile is now the default for *new* installations. Existing entries are migrated to the original UVR1611 profile and retain entity unique IDs. The legacy profile remains schematic-specific and must not be used blindly with another device. This branch is **not released**; verify migration, UI configuration and write actions in a Home Assistant test environment before merging.
