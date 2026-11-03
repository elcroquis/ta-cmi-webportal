"""Generic, user-defined points for CMI schematic HTML.

Only exact `javascript:loadChanger('<hex>')` links are accepted. No URLs,
JavaScript or user-provided change addresses are executed or trusted.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import re
from typing import Any

_LINK = re.compile(r"^\s*javascript:\s*loadChanger\s*\(\s*['\"]([a-fA-F0-9]{6,32})['\"]\s*\)\s*;?\s*$", re.I)
_NUMBER = re.compile(r"^\s*([-+]?\d+(?:[.,]\d+)?)\s*(?:[A-Za-z°%/³²]+)?\s*$")
_POS = re.compile(r"^pos\d{1,4}$")
KINDS = ("sensor", "binary_sensor", "select", "number")


def valid_position(value: str) -> bool:
    return bool(_POS.fullmatch(value))


def parse_numeric(value: str) -> Decimal | None:
    """Parse a numeric label with optional units; never guess from arbitrary text."""
    match = _NUMBER.fullmatch(value)
    if not match:
        return None
    try:
        result = Decimal(match.group(1).replace(",", "."))
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


class _Anchors(HTMLParser):
    """Collect the text and edit address of each anchor inside a pos div."""
    _VOID = {"br", "input", "img", "meta", "link", "hr", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.position: str | None = None
        self.current: dict[str, Any] | None = None
        self.items: dict[str, list[dict[str, str]]] = {}

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if self.position is None and tag == "div" and valid_position(data.get("id", "")):
            self.position = data["id"]
            self.depth = 1
            self.items[self.position] = []
            return
        if self.position is not None:
            if tag == "a" and self.current is None:
                match = _LINK.fullmatch(data.get("href") or "")
                self.current = {"address": match.group(1).upper() if match else "", "parts": []}
            if tag == "div":
                self.depth += 1

    def handle_data(self, data):
        if self.current is not None:
            self.current["parts"].append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None and self.position:
            self.items[self.position].append({
                "address": self.current["address"],
                "text": " ".join(" ".join(self.current["parts"]).split()),
            })
            self.current = None
        if tag == "div" and self.position is not None:
            self.depth -= 1
            if self.depth == 0:
                self.position = None


def parse_anchors(html: str) -> dict[str, list[dict[str, str]]]:
    parser = _Anchors()
    parser.feed(html)
    return parser.items


def anchor_for(anchors: dict, position: str, link_index: int) -> dict[str, str] | None:
    links = anchors.get(position, [])
    return links[link_index] if 0 <= link_index < len(links) else None


def point_id(point: dict) -> str:
    return point["id"]


def get_points(entry) -> list[dict]:
    return list(entry.options.get("points", []))


def point_value(data: dict[str, str], anchors: dict, point: dict) -> str | None:
    if point["kind"] in ("select", "number"):
        link = anchor_for(anchors, point["position"], int(point.get("link_index", 0)))
        return link["text"] if link and link["address"] else None
    return data.get(point["position"])
