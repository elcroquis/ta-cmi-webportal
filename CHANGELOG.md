# Changelog

## 0.5.0b1 (development preview, not released)
- New CMI entries default to fully configurable custom schematics.
- Existing installations are migrated to the original UVR1611 profile, retaining entity unique IDs.
- Device edit addresses are discovered in live CMI HTML; no installation-specific constants.
- A native thermostat is available after explicitly mapping °C sensor/number points and optional mode labels.
- Neutral names for legacy schematic positions; parser and mapping regression tests.


## 0.4.0b2 (Beta)

- Add a **polling interval** setting in the initial UI setup and in
  **Configure → Settings / polling interval** for each CMI separately.
- Allow 60–3600 seconds; default remains 120 seconds for older installations.
- Apply updated settings by automatically reloading the selected config entry.
- Preserve previously defined points, authentication, and entity unique IDs.

## 0.4.0b1 (Beta)

- Add custom CMI schematic profiles and UI-based entity mapping.
- Preserve the UVR1611 compatibility profile.
