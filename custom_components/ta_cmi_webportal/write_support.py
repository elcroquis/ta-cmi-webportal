"""Parse the UVR1611's existing, user-editable HTML controls.

Read operations do not modify the heating system. The change.cgi request is
only issued in response to an explicit Home Assistant select/number action.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from functools import reduce
from math import gcd
from html.parser import HTMLParser
import re


# Observed anchors in a UVR1611 reference online schematic.
KNOWN_LINKS = {
    "pos16": "101300C0F80",  # Normal temperature
    "pos17": "101300D0F80",  # Reduced temperature
    "pos18": "101301D0F80",  # Heating mode
    "party_end": "001301F0F",  # Party ends at this clock time (only in PARTY mode)
    "special_end": "00130200F",  # Shared holiday/vacation end, day and month only
}
# Observed in the user's actual holiday/vacation change dialog (UVR1611).
SPECIAL_END_CHANGEADR = "0FB9013020"

# Values and labels observed in the real CMI operating-mode dialog.
MODES = {
    "STANDBY": "1",
    "ZEIT/AUTO": "2",
    "NORMAL": "3",
    "ABGESENKT": "4",
    "PARTY": "5",
    "URLAUB": "6",
    "FEIERTAG": "7",
}

_NUMBER = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_DATE = re.compile(r"^(0?[1-9]|[12]\d|3[01])\.(0?[1-9]|1[0-2])\.$")
_ANCHOR = re.compile(
    r"^\s*javascript:\s*loadChanger\s*\(\s*['\"]([0-9A-Fa-f]+)['\"]\s*\)\s*;?\s*$",
    re.IGNORECASE,
)
_HEX = re.compile(r"^[0-9A-Fa-f]{8,32}$")
_VOID_ELEMENTS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class CMIDialogError(ValueError):
    """An editable portal control was missing or unexpectedly formatted."""


def parse_number(text: str) -> Decimal:
    """Extract a displayed number, accepting comma or dot decimals."""
    match = _NUMBER.search(text.strip())
    if match is None:
        raise CMIDialogError("Keinen Zahlenwert erkannt")
    try:
        return Decimal(match.group().replace(",", "."))
    except InvalidOperation as err:
        raise CMIDialogError("Ungültiger Zahlenwert") from err


def read_mode(value: str | None) -> str | None:
    """Return a CMI operating mode even if the position includes extra data.

    Example: <div id="pos18">PARTY<br>00:00</div> becomes
    "PARTY 00:00" in the position parser.  The first anchor is the mode;
    the second anchor contains the party end time, not part of the mode.
    """
    content = " ".join((value or "").upper().split())
    return next((mode for mode in MODES if content == mode or content.startswith(mode + " ")), None)


def read_auto_phase(value: str | None) -> str | None:
    """Read the current schedule phase without confusing it with the mode."""
    content = " ".join((value or "").upper().split())
    if not content.startswith("ZEIT/AUTO "):
        return None
    phase = content[len("ZEIT/AUTO "):]
    return phase if phase in ("NORMAL", "ABGESENKT") else None


def normalize_day_month(value: str) -> str:
    """Validate a *yearless* CMI date and return DD.MM. exactly."""
    match = _DATE.fullmatch(value.strip())
    if not match:
        raise CMIDialogError("Datum muss im Format TT.MM. eingegeben werden")
    day, month = map(int, match.groups())
    # Leap-year validation allows 29 February without inventing a device year.
    try:
        date(2024, month, day)
    except ValueError as err:
        raise CMIDialogError("Ungültiger Kalendertag") from err
    return f"{day:02d}.{month:02d}."


def next_occurrence(day_month: str, today: date) -> date:
    """Provide a calendar year for the HA date UI, never for CMI encoding.

    The UVR1611 returns only DD.MM., without a year. The earliest occurrence
    on/after today is used for display; the actual year is *not* known.
    """
    text = normalize_day_month(day_month)
    day, month = int(text[:2]), int(text[3:5])
    for year in range(today.year, today.year + 9):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue  # e.g. 29 February in a non-leap year
        if candidate >= today:
            return candidate
    raise ValueError("Enddatum konnte keinem Kalenderjahr zugeordnet werden")


class _EditLinkParser(HTMLParser):
    """Parse approved change links and special values inside pos16..pos18."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str | None] = []
        self.links: dict[str, str] = {}
        self.party_end: str | None = None
        self.special_end: str | None = None
        self._aux_kind: str | None = None
        self._aux_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        parent = self.stack[-1] if self.stack else None
        position = values.get("id") if tag == "div" else None
        current = position if position in ("pos16", "pos17", "pos18") else parent
        if tag == "a" and current:
            match = _ANCHOR.fullmatch(values.get("href") or "")
            if match:
                address = match.group(1).upper()
                if address == KNOWN_LINKS.get(current):
                    self.links[current] = address
                elif current == "pos18":
                    for kind in ("party_end", "special_end"):
                        if address == KNOWN_LINKS[kind]:
                            self.links[kind] = address
                            self._aux_kind = kind
                            self._aux_text = []
                            break
        if tag not in _VOID_ELEMENTS:
            self.stack.append(current)

    def handle_data(self, data: str) -> None:
        if self._aux_kind:
            self._aux_text.append(data)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _VOID_ELEMENTS:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._aux_kind:
            value = " ".join(" ".join(self._aux_text).split())
            if self._aux_kind == "party_end" and _TIME.fullmatch(value):
                self.party_end = value
            elif self._aux_kind == "special_end":
                try:
                    self.special_end = normalize_day_month(value)
                except CMIDialogError:
                    pass
            self._aux_kind = None
            self._aux_text = []
        if tag not in _VOID_ELEMENTS and self.stack:
            self.stack.pop()


def parse_edit_links(html: str) -> dict[str, str]:
    parser = _EditLinkParser()
    parser.feed(html)
    return parser.links


def parse_party_end(html: str) -> str | None:
    """Read the PARTY clock time only from its separate, fixed CMI link."""
    parser = _EditLinkParser()
    parser.feed(html)
    return parser.party_end


def parse_special_end(html: str) -> str | None:
    """Read URLAUB/FEIERTAG final day, without fabricating a year."""
    parser = _EditLinkParser()
    parser.feed(html)
    return parser.special_end


class ChangeDialog(HTMLParser):
    """HTML popup returned by /webi/INCLUDE/changer.cgi."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.inputs: dict[str, dict[str, str]] = {}
        self.selects: dict[str, list[tuple[str, str]]] = {}
        # Preserve the HTML selected= attribute, used to cross-check the
        # device's currently selected day before sending a write.
        self.selected_options: dict[str, list[tuple[str, str]]] = {}
        self._select: str | None = None
        self._option_value: str | None = None
        self._option_selected = False
        self._option_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        name = values.get("name") or values.get("id")
        if tag == "input" and name:
            self.inputs[name] = values
        elif tag == "select" and name:
            self._select = name
            self.selects[name] = []
            self.selected_options[name] = []
        elif tag == "option" and self._select is not None:
            self._option_value = values.get("value")
            self._option_selected = "selected" in values
            self._option_text = []

    def handle_data(self, data: str) -> None:
        if self._option_value is not None:
            self._option_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "option" and self._select and self._option_value is not None:
            item = (self._option_value, " ".join(" ".join(self._option_text).split()))
            self.selects[self._select].append(item)
            if self._option_selected:
                self.selected_options[self._select].append(item)
            self._option_value = None
            self._option_selected = False
            self._option_text = []
        elif tag == "select":
            self._select = None

    def change_address(self) -> str:
        address = self.inputs.get("changeadr", {}).get("value", "")
        if not _HEX.fullmatch(address):
            raise CMIDialogError("Der CMI-Dialog enthält keine gültige Änderungsadresse")
        return address

    def encode_mode(self, name: str) -> str:
        if name not in MODES:
            raise CMIDialogError("Unbekannte Betriebsart")
        items = self.selects.get("changeto", [])
        expected = MODES[name]
        # Validate BOTH the actual numerical option AND the text next to it.
        for value, label in items:
            if value == expected and name in label.upper():
                return value
        raise CMIDialogError(f"CMI bietet die Betriebsart {name} nicht wie erwartet an")

    def temperature_options(self) -> tuple[Decimal, ...] | None:
        """Read degrees actually offered by the CMI temperature selector.

        None means the CMI uses a free-text field. The empty tuple means
        there is a selector, but its numeric options could not be understood.
        """
        items = self.selects.get("changeto")
        if items is None:
            return None
        offered: set[Decimal] = set()
        for raw, label in items:
            if not raw:
                continue
            try:
                offered.add(parse_number(label))
            except CMIDialogError:
                continue
        return tuple(sorted(offered))

    def temperature_step(self) -> Decimal | None:
        """Find the smallest step representing all offered decimal values."""
        values = self.temperature_options()
        if not values or len(values) < 2:
            return None
        decimals = max(0, *(-x.as_tuple().exponent for x in values))
        factor = 10 ** decimals
        integers = [int(value * factor) for value in values]
        diffs = [b - a for a, b in zip(integers, integers[1:]) if b > a]
        if not diffs:
            return None
        return Decimal(reduce(gcd, diffs)) / Decimal(factor)

    def encode_temperature(self, value: float, current_display: str) -> str:
        requested = Decimal(str(value))
        if not requested.is_finite():
            raise CMIDialogError("Ungültige Solltemperatur")
        # Some CMI variants implement temperatures as a select, with
        # visible unit-based labels and possibly opaque internal values.
        if "changeto" in self.selects:
            for raw, label in self.selects["changeto"]:
                try:
                    if parse_number(label) == requested:
                        return raw
                except CMIDialogError:
                    continue
            options = self.temperature_options() or ()
            description = (
                f"Erlaubte Werte: {min(options)} bis {max(options)} °C, "
                f"Schrittweite {self.temperature_step()} °C"
                if len(options) >= 2 and self.temperature_step() is not None
                else "CMI bietet diesen Wert nicht an"
            )
            raise CMIDialogError(
                f"{requested} °C ist im CMI-Auswahlfeld nicht verfügbar. {description}."
            )
        field = self.inputs.get("changeto")
        if not field or field.get("type", "text") not in ("text", "number", "hidden"):
            raise CMIDialogError("CMI hat kein unterstütztes Temperatur-Eingabefeld")
        current_raw = field.get("value", "").strip()
        if not _NUMBER.fullmatch(current_raw):
            raise CMIDialogError("CMI-Temperaturformat unbekannt; Schreiben aus Sicherheitsgründen blockiert")
        actual = parse_number(current_display)
        raw = parse_number(current_raw)
        # Determine the scale from the live dialog and the actual displayed
        # temperature: CMI firmware may expect degrees or tenths of a degree.
        scales = (Decimal("1"), Decimal("10"), Decimal("100"))
        matching = [scale for scale in scales if raw == actual * scale]
        if len(matching) != 1:
            raise CMIDialogError("Temperaturskalierung nicht eindeutig; Schreiben blockiert")
        scaled = requested * matching[0]
        if matching[0] != 1 and scaled != scaled.to_integral_value():
            raise CMIDialogError("Temperatur lässt sich nicht exakt in der CMI-Auflösung darstellen")
        encoded = (
            str(int(scaled)) if scaled == scaled.to_integral_value()
            else format(scaled.normalize(), "f")
        )
        # Preserve comma separators for firmware that displays comma decimals.
        if "," in current_raw:
            encoded = encoded.replace(".", ",")
        return encoded

    def encode_party_end(self, requested: str, current_display: str) -> str:
        """Encode a party-end time only if the popup unambiguously supports it.

        Never guess numeric minutes, epoch seconds, or a firmware-specific
        packed integer.  Only reuse an explicit HH:MM selection or an input
        field whose current value matches the displayed HH:MM time.
        """
        if not _TIME.fullmatch(requested):
            raise CMIDialogError("Party-Ende muss als HH:MM eingegeben werden")
        if not _TIME.fullmatch(current_display):
            raise CMIDialogError("Die aktuelle Party-Endzeit ist nicht eindeutig")
        if "changeto" in self.selects:
            for raw, label in self.selects["changeto"]:
                if label.strip() == requested and raw:
                    return raw
            raise CMIDialogError(
                f"Die Party-Endzeit {requested} ist im CMI-Auswahlfeld nicht enthalten. "
                "Bitte die bereinigte HTML-Antwort von changer.cgi für die Party-Endzeit prüfen."
            )
        field = self.inputs.get("changeto")
        if not field or field.get("type", "text") not in ("text", "time"):
            raise CMIDialogError(
                "CMI verwendet ein noch unbekanntes Eingabeformat für Party-Ende. "
                "Bitte die bereinigte HTML-Antwort von changer.cgi für die Party-Endzeit prüfen."
            )
        current_raw = field.get("value", "").strip()
        if current_raw != current_display:
            raise CMIDialogError(
                "CMI codiert Party-Ende anders als HH:MM; "
                "Schreiben bis zur Prüfung des Dialogformats gesperrt."
            )
        return requested

    def encode_special_end(self, requested: str, current_display: str) -> str:
        """Find the CMI's actual dropdown code for a yearless date.

        Example observed from the UVR1611 on 27 October:
        <option value="0" selected>27.10.</option>
        <option value="5">01.11.</option>
        CMI expects `changeto=5`, *not* an encoded DDMM or day offset.
        
        Validate the hidden change address and selected option against the
        current value read from 2.cgi. An out-of-range or stale dropdown is
        rejected before requesting change.cgi; no writes are retried.
        """
        requested = normalize_day_month(requested)
        current_display = normalize_day_month(current_display)
        if self.change_address().upper() != SPECIAL_END_CHANGEADR:
            raise CMIDialogError(
                "Unerwartete Änderungsadresse für Urlaub/Feiertag; "
                "Schreiben vorsichtshalber blockiert"
            )

        items = self.selects.get("changeto")
        if items is not None:
            choices: list[tuple[str, str]] = []
            for raw, label in items:
                try:
                    day = normalize_day_month(label)
                except CMIDialogError:
                    continue  # e.g. non-date placeholder, never a valid target
                if not re.fullmatch(r"\d+", raw):
                    raise CMIDialogError("Unbekannte CMI-Datumscodierung; Schreiben blockiert")
                choices.append((raw, day))
            if not choices:
                raise CMIDialogError("Der CMI-Datumsdialog enthält keine gültigen Tage")
            # Avoid ambiguity if firmware or a proxy returns malformed options.
            if len({raw for raw, _ in choices}) != len(choices) or len({day for _, day in choices}) != len(choices):
                raise CMIDialogError("Mehrdeutige Datumsoptionen im CMI-Dialog; Schreiben blockiert")

            selected = self.selected_options.get("changeto", [])
            if len(selected) > 1:
                raise CMIDialogError("Mehrere aktuelle Datumswerte im CMI-Dialog; Schreiben blockiert")
            if selected:
                try:
                    selected_date = normalize_day_month(selected[0][1])
                except CMIDialogError as err:
                    raise CMIDialogError("Aktuelles CMI-Datum im Dialog ungültig") from err
                if selected_date != current_display:
                    raise CMIDialogError(
                        "Der CMI-Datumsdialog ist nicht mehr aktuell. "
                        "Bitte die Werte neu laden und erneut versuchen."
                    )
            elif current_display not in {day for _, day in choices}:
                raise CMIDialogError("Das aktuelle Enddatum fehlt im CMI-Dialog")

            for raw, day in choices:
                if day == requested:
                    return raw  # including value="0", which is a valid code
            raise CMIDialogError(
                f"{requested} liegt außerhalb der vom CMI angebotenen Datumswerte "
                f"({choices[0][1]} bis {choices[-1][1]}). "
                "Es wurde nichts geändert."
            )

        # Fallback for a different firmware presenting a human-readable field:
        # allow only exactly the current displayed DD.MM. format, no guessing.
        field = self.inputs.get("changeto")
        if not field or field.get("type", "text") not in ("text", "date"):
            raise CMIDialogError(
                "CMI-Datumsformat unbekannt. Kein Schreibbefehl gesendet."
            )
        raw = field.get("value", "").strip()
        try:
            if normalize_day_month(raw) != current_display:
                raise CMIDialogError("Aktuelles CMI-Datum stimmt nicht mit dem Dialog überein")
        except CMIDialogError as err:
            raise CMIDialogError(
                "CMI codiert das Datum anders als TT.MM.; Schreiben aus "
                "Sicherheitsgründen gesperrt"
            ) from err
        return requested


def parse_change_dialog(html: str) -> ChangeDialog:
    dialog = ChangeDialog()
    dialog.feed(html)
    dialog.change_address()
    return dialog
