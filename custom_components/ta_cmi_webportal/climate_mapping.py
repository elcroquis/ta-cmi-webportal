"""Pure helpers for user-configured climate mappings (no Home Assistant dependency)."""
from __future__ import annotations

def mapping_points(points: list[dict], mapping: dict) -> tuple[dict, dict, dict | None] | None:
    """Only map explicit °C sensor/number points and optional select modes."""
    by_id = {point["id"]: point for point in points}
    room = by_id.get(mapping.get("room_point", ""))
    target = by_id.get(mapping.get("setpoint_point", ""))
    mode_id = mapping.get("mode_point", "")
    mode = by_id.get(mode_id) if mode_id else None
    if (not room or room["kind"] != "sensor" or room.get("unit") != "°C"
            or not target or target["kind"] != "number" or target.get("unit") != "°C"):
        return None
    if mode_id and (not mode or mode["kind"] != "select"):
        return None
    if mode_id and not mapping.get("mode_heat"):
        return None
    labels = [mapping.get(key) for key in ("mode_heat", "mode_off", "mode_auto")]
    present = [label for label in labels if label]
    if len(present) != len(set(present)):
        return None
    if not mode_id and present:
        return None
    return room, target, mode

def active_mode(mapping: dict, label: str | None) -> str | None:
    """Never claim a specific HVAC state for a CMI label not configured by user."""
    if not mapping.get("mode_point"):
        return "heat"
    if label is None:
        return None
    choices = {mapping.get("mode_heat"): "heat"}
    for key, mode in (("mode_off", "off"), ("mode_auto", "auto")):
        if mapping.get(key):
            choices[mapping[key]] = mode
    return choices.get(label)

def supported_modes(mapping: dict) -> list[str]:
    result = ["heat"]
    if mapping.get("mode_off"):
        result.append("off")
    if mapping.get("mode_auto"):
        result.append("auto")
    return result
