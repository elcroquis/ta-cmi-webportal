"""HTTPS client for TA CMI: authenticated reading and explicit controlled writing."""
from __future__ import annotations

import asyncio
from html.parser import HTMLParser
from decimal import Decimal
import re
from urllib.parse import urljoin, urlparse

from aiohttp import ClientError, ClientSession

from .const import LOGIN_FORM_URL, LOGIN_POST_URL, PROFILE_UVR, PROFILE_GENERIC
from .generic import parse_anchors, anchor_for, parse_numeric
from .write_support import (
    CMIDialogError, MODES, parse_change_dialog, parse_edit_links,
    parse_number, parse_party_end, parse_special_end, read_auto_phase, read_mode,
    normalize_day_month,
)


class CMIError(Exception):
    """CMI access or parsing error."""


class CMILoginError(CMIError):
    """Authentication is missing or expired."""


class CMISchemaError(CMIError):
    """The remote portal temporarily returned no usable schematic data."""


class _FormParser(HTMLParser):
    """Collect HTML forms/inputs, without executing any page scripts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict] = []
        self.current: dict | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        d = dict(attrs)
        if tag == "form":
            self.current = {"action": d.get("action", ""), "method": d.get("method", "get"), "inputs": []}
            self.forms.append(self.current)
        elif tag == "input" and self.current is not None and d.get("name"):
            self.current["inputs"].append({
                "name": d["name"],
                "type": (d.get("type") or "text").lower(),
                "value": d.get("value") or "",
            })


def parse_positions(html: str) -> dict[str, str]:
    """Extract human-readable values from <div id='posN'>...</div>."""
    class PositionParser(HTMLParser):
        # Void tags do not require a closing tag in HTML and must not
        # consume the closing </div> of a position.
        VOID_TAGS = {
            "area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr",
        }

        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.depth = 0
            self.target: str | None = None
            self.parts: list[str] = []
            self.results: dict[str, str] = {}

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            d = dict(attrs)
            if self.target is not None:
                if tag not in self.VOID_TAGS:
                    self.depth += 1
            elif tag == "div" and re.fullmatch(r"pos\d+", d.get("id", "")):
                self.target = d["id"]
                self.depth = 1
                self.parts = []

        def handle_data(self, data: str) -> None:
            if self.target is not None:
                self.parts.append(data)

        def handle_endtag(self, tag: str) -> None:
            if self.target is not None and tag not in self.VOID_TAGS:
                self.depth -= 1
                if self.depth == 0:
                    self.results[self.target] = " ".join(" ".join(self.parts).split())
                    self.target = None
                    self.parts = []

    parser = PositionParser()
    parser.feed(html)
    return parser.results


def discover_login_fields(html: str, custom_user: str, custom_password: str) -> tuple[str, str, dict[str, str]]:
    """Return selected form field names and hidden fields; never store credentials in HTML."""
    parser = _FormParser()
    parser.feed(html)
    forms = [f for f in parser.forms if any(i["type"] == "password" for i in f["inputs"])]
    if not forms:
        if custom_user and custom_password:
            return custom_user, custom_password, {}
        raise CMILoginError("Loginformular nicht erkannt und Feldnamen fehlen")
    form = next((f for f in forms if "checkLogin" in (f["action"] or "")), forms[0])
    fields = form["inputs"]
    pw = next((i["name"] for i in fields if i["type"] == "password"), None)
    candidates = [i for i in fields if i["type"] in ("text", "email")]
    def score(item: dict) -> int:
        n = item["name"].lower()
        return sum(x in n for x in ("user", "login", "mail", "name"))
    user = max(candidates, key=score)["name"] if candidates else None
    user = custom_user or user
    pw = custom_password or pw
    if not user or not pw:
        raise CMILoginError("Benutzername-/Passwort-Feldnamen nicht erkannt")
    hidden = {i["name"]: i["value"] for i in fields if i["type"] == "hidden"}
    return user, pw, hidden


class CMIClient:
    """One per-config-entry authenticated CMI connection."""

    def __init__(self, session: ClientSession, cmi_id: str, username: str, password: str,
                 user_field: str = "username", pass_field: str = "passwort",
                 scheme_number: int = 2, profile: str = PROFILE_GENERIC) -> None:
        self.session = session
        self.cmi_id = cmi_id.lower()
        if not re.fullmatch(r"cmi[0-9]{6,10}", self.cmi_id):
            raise CMIError("CMI-ID muss wie CMI123456 aussehen")
        self.username = username
        self.password = password
        self.user_field = user_field.strip() or "username"
        self.pass_field = pass_field.strip() or "passwort"
        if not 1 <= int(scheme_number) <= 99:
            raise CMIError("Ungültige Schemanummer")
        self.scheme_number = int(scheme_number)
        self.profile = profile
        self.base = f"https://{self.cmi_id}.cmi.ta.co.at/webi/"
        self.anchors: dict[str, list[dict[str, str]]] = {}
        self.generic_options: dict[str, list[tuple[str, str]]] = {}
        self._authenticated = False
        self._lock = asyncio.Lock()
        self._edit_links: dict[str, str] = {}
        self.temperature_options: dict[str, tuple[Decimal, ...]] = {}
        self.temperature_steps: dict[str, Decimal] = {}

    async def _login(self) -> None:
        """GET login form for cookies/hidden fields, POST credentials, then check read access."""
        try:
            async with self.session.get(LOGIN_FORM_URL, timeout=20) as resp:
                if resp.status != 200:
                    raise CMILoginError(f"Loginformular nicht erreichbar: HTTP {resp.status}")
                form_html = await resp.text()
            # Browser network trace confirmed POST names: username and passwort.
            # The form is parsed only for optional hidden fields (e.g. CSRF).
            # Explicit field names take precedence over any auto-detected names.
            uname, pwd, hidden = discover_login_fields(form_html, self.user_field, self.pass_field)
            payload = {**hidden, uname: self.username, pwd: self.password}
            # Fixed, known endpoint; never send credentials to any URL derived from HTML.
            async with self.session.post(
                LOGIN_POST_URL, data=payload, timeout=20, allow_redirects=False,
                headers={"Referer": LOGIN_FORM_URL, "Origin": "https://cmi.ta.co.at"},
            ) as resp:
                if resp.status not in (200, 302, 303):
                    raise CMILoginError(f"Login fehlgeschlagen: HTTP {resp.status}")
                location = resp.headers.get("Location", "")
                if location:
                    redirect = urlparse(urljoin(LOGIN_POST_URL, location))
                    if redirect.scheme != "https" or redirect.hostname != "cmi.ta.co.at":
                        raise CMILoginError("Unerwartete Login-Weiterleitung")
                    if "loginformular" in redirect.path.lower():
                        raise CMILoginError("Anmeldung vom Portal abgelehnt")
            self._authenticated = True
        except (TimeoutError, ClientError) as err:
            raise CMIError("Netzwerkfehler bei der Anmeldung") from err

    async def _fetch(self) -> dict[str, str]:
        """Read the schema once; do not mistake temporary empty HTML for bad credentials."""
        url = self.base + f"schematic_files/{self.scheme_number}.cgi"
        try:
            async with self.session.get(url, timeout=20, allow_redirects=False, headers={
                "Referer": self.base + "schema.html",
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "text/html, */*; q=0.01",
            }) as resp:
                if resp.status in (301, 302, 303, 307, 308, 401, 403):
                    raise CMILoginError(f"Schema-Zugriff nicht autorisiert: HTTP {resp.status}")
                if resp.status != 200:
                    raise CMIError(f"2.cgi antwortet mit HTTP {resp.status}")
                page = await resp.text()
                response_type = resp.content_type or "unbekannt"
        except (TimeoutError, ClientError) as err:
            raise CMIError("Netzwerkfehler beim Auslesen") from err
        fields = parse_positions(page)
        required = ("pos13", "pos14", "pos18") if self.profile == PROFILE_UVR else ()
        missing = [key for key in required if key not in fields]
        if missing or not fields:
            # The TA proxy sometimes returns an empty, partial, or login page
            # with HTTP 200 immediately after a successful mode change.
            if re.search(r'<input[^>]+type=[\'"]?password', page, re.IGNORECASE):
                raise CMILoginError("CMI antwortet mit einer Anmeldeseite")
            # No body, cookie value, URL query, or authentication data in logs.
            raise CMISchemaError(
                "2.cgi ohne vollständige Schemawerte "
                f"(Positionen: {len(fields)}, fehlend: {','.join(missing)}, "
                f"Antwortlänge: {len(page)}, Content-Type: {response_type})"
            )
        self._edit_links = parse_edit_links(page)
        self.anchors = parse_anchors(page)
        # The PARTY-mode position contains TWO linked values. Keep the mode
        # in pos18 and publish its end time separately for the time entity.
        mode = read_mode(fields.get("pos18")) if self.profile == PROFILE_UVR else None
        if mode == "PARTY":
            party_end = parse_party_end(page)
            if party_end is not None:
                fields["party_end"] = party_end
        elif mode in ("URLAUB", "FEIERTAG"):
            special_end = parse_special_end(page)
            if special_end is not None:
                fields["special_end"] = special_end
        elif mode == "ZEIT/AUTO":
            phase = read_auto_phase(fields.get("pos18"))
            if phase is not None:
                fields["auto_phase"] = phase
        return fields

    async def _read_with_recovery(self) -> dict[str, str]:
        """Retry harmless GETs, with at most one re-login per read sequence."""
        if not self._authenticated:
            await self._login()
        last_error: CMIError | None = None
        relogged = False
        for attempt in range(4):
            try:
                return await self._fetch()
            except CMILoginError as err:
                last_error = err
                if relogged:
                    break
                self._authenticated = False
                await self._login()
                relogged = True
            except CMISchemaError as err:
                last_error = err
                # Some sessions expire with HTTP 200 / empty body.
                if attempt == 2 and not relogged:
                    self._authenticated = False
                    await self._login()
                    relogged = True
            except CMIError as err:
                # HTTP 5xx / transient network failure: retry reads only.
                last_error = err
            if attempt < 3:
                await asyncio.sleep((1, 2, 3)[attempt])
        raise last_error or CMIError("CMI konnte nicht ausgelesen werden")

    async def read_values(self) -> dict[str, str]:
        async with self._lock:
            return await self._read_with_recovery()

    async def probe_temperature_options(self) -> None:
        """Discover the offered temperatures for HA UI limits (read-only)."""
        async with self._lock:
            for position in ("pos16", "pos17"):
                try:
                    popup = await self._popup(position)
                except CMIError:
                    continue  # Non-critical; writes still validate actual CMI popup.
                options = popup.temperature_options()
                if options:
                    self.temperature_options[position] = options
                    step = popup.temperature_step()
                    if step is not None:
                        self.temperature_steps[position] = step


    async def _popup(self, position: str):
        """Load the same edit popup the web interface uses, without writing."""
        # Only accept a link discovered from the current live CMI schema.
        # The change dialog still validates the selected value before writing.
        link = self._edit_links.get(position)
        if not link or not re.fullmatch(r"[0-9A-F]{6,32}", link):
            raise CMIError(f"{position} hat keinen gültigen Bearbeiten-Link")
        try:
            async with self.session.get(
                self.base + "INCLUDE/changer.cgi",
                params={"sadr": link},
                timeout=20,
                headers={"Referer": self.base + "schema.html", "X-Requested-With": "XMLHttpRequest"},
            ) as resp:
                if resp.status != 200:
                    raise CMIError(f"CMI-Bearbeitungsdialog: HTTP {resp.status}")
                html = await resp.text()
        except (TimeoutError, ClientError) as err:
            raise CMIError("CMI-Bearbeitungsdialog nicht erreichbar") from err
        try:
            return parse_change_dialog(html)
        except CMIDialogError as err:
            raise CMIError(str(err)) from err

    async def _change_and_confirm(
        self, position: str, value: float | str
    ) -> dict[str, str]:
        """Write a change exactly once, then confirm it by reading the scheme."""
        async with self._lock:
            if not self._authenticated:
                await self._login()
            # Recover the session BEFORE writing; never repeat a write.
            old_values = await self._read_with_recovery()
            if position not in old_values:
                raise CMIError(f"Messwert {position} nicht gefunden")
            current = old_values[position]
            if position == "pos18":
                if not isinstance(value, str) or value not in MODES:
                    raise CMIError("Unbekannte Betriebsart")
                unchanged = read_mode(current) == value
            elif position == "party_end":
                if read_mode(old_values.get("pos18")) != "PARTY":
                    raise CMIError("Party-Ende kann nur im Betriebsmodus PARTY geändert werden")
                if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
                    raise CMIError("Ungültiges Party-Ende, erwartet HH:MM")
                unchanged = current == value
            elif position == "special_end":
                if read_mode(old_values.get("pos18")) not in ("URLAUB", "FEIERTAG"):
                    raise CMIError("Enddatum ist nur bei URLAUB oder FEIERTAG verfügbar")
                if not isinstance(value, str):
                    raise CMIError("Ungültiges Datum, erwartet TT.MM.")
                try:
                    value = normalize_day_month(value)
                except CMIDialogError as err:
                    raise CMIError(str(err)) from err
                unchanged = current == value
            elif position in ("pos16", "pos17"):
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise CMIError("Ungültige Solltemperatur")
                try:
                    unchanged = abs(float(parse_number(current)) - value) < 0.05
                except CMIDialogError as err:
                    raise CMIError(str(err)) from err
            else:
                raise CMIError("Unbekannte Steuerungsposition")
            if unchanged:
                return old_values
            dialog = await self._popup(position)
            if position in ("pos16", "pos17"):
                choices = dialog.temperature_options()
                if choices:
                    self.temperature_options[position] = choices
                    step = dialog.temperature_step()
                    if step is not None:
                        self.temperature_steps[position] = step
            try:
                if position == "pos18":
                    selected = dialog.selected_options.get("changeto", [])
                    if len(selected) != 1 or read_mode(selected[0][1]) != read_mode(current):
                        raise CMIDialogError("Der Betriebsarten-Dialog ist nicht mehr aktuell")
                    encoded = dialog.encode_mode(value)
                elif position == "party_end":
                    encoded = dialog.encode_party_end(value, current)
                elif position == "special_end":
                    encoded = dialog.encode_special_end(value, current)
                else:
                    encoded = dialog.encode_temperature(value, current)
                address = dialog.change_address()
            except CMIDialogError as err:
                raise CMIError(str(err)) from err
            # GET is the device's original write API, as shown by its
            # scheme.js doChange(). This is the only side-effecting request.
            try:
                async with self.session.get(
                    self.base + "INCLUDE/change.cgi",
                    params={"changeadr": address, "changeto": encoded},
                    timeout=20,
                    allow_redirects=False,
                    headers={"Referer": self.base + "schema.html", "X-Requested-With": "XMLHttpRequest"},
                ) as resp:
                    if resp.status != 200:
                        raise CMIError(
                            f"CMI-Schreibaufruf: HTTP {resp.status}; Änderung nicht bestätigt"
                        )
                    response = (await resp.text()).strip()
                    if "error" in response.lower() or "fehler" in response.lower():
                        raise CMIError("CMI meldet einen Fehler beim Schreiben")
            except (TimeoutError, ClientError) as err:
                raise CMIError(
                    "Schreibaufruf nicht sicher bestätigt; keine automatische Wiederholung"
                ) from err
            # One write, multiple safe reads: the UVR1611/TA proxy may be
            # briefly unavailable after a change. Do NOT repeat change.cgi.
            relogged = False
            for attempt in range(5):
                if attempt:
                    await asyncio.sleep((2, 3, 4, 5)[attempt - 1])
                try:
                    updated = await self._fetch()
                except CMILoginError:
                    if not relogged:
                        self._authenticated = False
                        try:
                            await self._login()
                            relogged = True
                        except CMIError:
                            pass  # The write's outcome is still uncertain.
                    continue
                except CMISchemaError:
                    if attempt == 2 and not relogged:
                        self._authenticated = False
                        try:
                            await self._login()
                            relogged = True
                        except CMIError:
                            pass
                    continue
                except CMIError:
                    continue
                if position == "pos18":
                    confirmed = read_mode(updated.get(position)) == value
                elif position in ("party_end", "special_end"):
                    confirmed = updated.get(position) == value
                else:
                    try:
                        confirmed = abs(
                            float(parse_number(updated.get(position, ""))) - float(value)
                        ) < 0.05
                    except CMIDialogError:
                        confirmed = False
                if confirmed:
                    return updated
            raise CMIError(
                "CMI hat den gewünschten Wert nicht bestätigt. "
                "Prüfe Anwenderrechte und Regleranzeige."
            )

    async def _generic_popup(self, address: str):
        """GET the CMI's actual dialog for a discovered link, never an arbitrary URL."""
        try:
            async with self.session.get(
                self.base + "INCLUDE/changer.cgi", params={"sadr": address},
                timeout=20, allow_redirects=False,
                headers={"Referer": self.base + "schema.html", "X-Requested-With": "XMLHttpRequest"},
            ) as response:
                if response.status != 200:
                    raise CMIError(f"Änderungsdialog HTTP {response.status}")
                return parse_change_dialog(await response.text())
        except (TimeoutError, ClientError) as error:
            raise CMIError("CMI-Änderungsdialog nicht erreichbar") from error
        except CMIDialogError as error:
            raise CMIError(str(error)) from error

    async def probe_generic_options(self, points: list[dict]) -> None:
        """Fetch option labels for custom select/number entities without writing."""
        async with self._lock:
            for point in points:
                if point["kind"] not in ("select", "number"):
                    continue
                link = anchor_for(self.anchors, point["position"], int(point.get("link_index", 0)))
                if not link or not link["address"]:
                    continue
                try:
                    dialog = await self._generic_popup(link["address"])
                    options = dialog.selects.get("changeto", [])
                    if options and len({label for _, label in options}) == len(options):
                        self.generic_options[point["id"]] = options
                except (CMIError, CMIDialogError):
                    continue

    async def set_generic(self, point: dict, requested: str | float) -> dict[str, str]:
        """One safe write with validated live options and independent readback."""
        from decimal import Decimal

        async with self._lock:
            before = await self._read_with_recovery()
            current_link = anchor_for(self.anchors, point["position"], int(point.get("link_index", 0)))
            if not current_link or not current_link["address"]:
                raise CMIError("Kein gültiger Bearbeitungslink am gewählten Messpunkt")
            existing = current_link["text"]
            kind = point["kind"]
            if kind == "select":
                if existing == str(requested):
                    return before
            elif kind == "number":
                old_number = parse_numeric(existing)
                target_number = Decimal(str(requested))
                if not target_number.is_finite() or old_number is None:
                    raise CMIError("Zahlenformat nicht erkannt")
                if old_number == target_number:
                    return before
            else:
                raise CMIError("Diese Entität ist nicht schreibbar")

            dialog = await self._generic_popup(current_link["address"])
            options = dialog.selects.get("changeto")
            if not options or len({label for _, label in options}) != len(options):
                raise CMIError("Das CMI bietet keine eindeutige Auswahlliste")
            selected = dialog.selected_options.get("changeto", [])
            if len(selected) != 1:
                raise CMIError("Aktuelle Option im CMI-Dialog nicht eindeutig")
            selected_label = selected[0][1]
            if kind == "select":
                if selected_label != existing:
                    raise CMIError("CMI-Dialog ist veraltet; bitte neu laden")
                matching = [raw for raw, label in options if label == str(requested)]
            else:
                selected_number = parse_numeric(selected_label)
                if selected_number != old_number:
                    raise CMIError("CMI-Dialog ist veraltet; bitte neu laden")
                matching = [raw for raw, label in options if parse_numeric(label) == target_number]
            if len(matching) != 1:
                raise CMIError("Gewünschter Wert nicht eindeutig in der CMI-Auswahlliste")
            try:
                change_address = dialog.change_address()
            except CMIDialogError as error:
                raise CMIError(str(error)) from error

            # Exactly one write request: an uncertain outcome must never cause retry.
            try:
                async with self.session.get(
                    self.base + "INCLUDE/change.cgi",
                    params={"changeadr": change_address, "changeto": matching[0]},
                    timeout=20, allow_redirects=False,
                    headers={"Referer": self.base + "schema.html", "X-Requested-With": "XMLHttpRequest"},
                ) as response:
                    if response.status != 200:
                        raise CMIError(f"Schreibvorgang HTTP {response.status}; nicht bestätigt")
                    result = (await response.text()).lower()
                    if "error" in result or "fehler" in result:
                        raise CMIError("CMI meldet einen Schreibfehler")
            except (TimeoutError, ClientError) as error:
                raise CMIError("Schreibvorgang möglicherweise ausgeführt; keine Wiederholung") from error
            for attempt in range(5):
                if attempt:
                    await asyncio.sleep((2, 3, 4, 5)[attempt - 1])
                try:
                    updated = await self._read_with_recovery()
                except CMIError:
                    continue
                link = anchor_for(self.anchors, point["position"], int(point.get("link_index", 0)))
                if link:
                    if kind == "select" and link["text"] == str(requested):
                        return updated
                    if kind == "number" and parse_numeric(link["text"]) == target_number:
                        return updated
            raise CMIError("CMI hat den geschriebenen Wert nicht durch Rücklesen bestätigt")

    async def set_mode(self, mode: str) -> dict[str, str]:
        return await self._change_and_confirm("pos18", mode)

    async def set_temperature(self, position: str, degrees: float) -> dict[str, str]:
        return await self._change_and_confirm(position, degrees)

    async def set_party_end(self, value: str) -> dict[str, str]:
        return await self._change_and_confirm("party_end", value)

    async def set_special_end(self, day_month: str) -> dict[str, str]:
        """Set the date shared by URLAUB and FEIERTAG, verified by readback."""
        return await self._change_and_confirm("special_end", day_month)
