"""Pure parsing/regression tests independent of Home Assistant runtime."""
import asyncio
from decimal import Decimal
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest.mock import AsyncMock

ROOT = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "ta_cmi_webportal"
PKG = "ta_cmi_webportal"
sys.modules[PKG] = types.ModuleType(PKG)
sys.modules[PKG].__path__ = [str(ROOT)]


def load(module):
    spec = importlib.util.spec_from_file_location(f"{PKG}.{module}", ROOT / f"{module}.py")
    obj = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = obj
    spec.loader.exec_module(obj)
    return obj


const = load("const")
generic = load("generic")
write_support = load("write_support")
api = load("api")


HTML = """
<div id='pos1'>  48,1 °C </div>
<div id='pos7'>AUS</div>
<div id='pos16'><a href="javascript:loadChanger('101300C0F80');">21 °C</a></div>
<div id='pos18'><a href="javascript:loadChanger('101301D0F80');">PARTY</a><br>
<a href="javascript:loadChanger('001301F0F');">00:00</a></div>
"""
DIALOG = """<input name="changeadr" value="0F9001301D" type="hidden">
<select name="changeto"><option value="3" selected>NORMAL</option>
<option value="5">PARTY</option></select>"""


class ParsingTests(unittest.TestCase):
    def test_positions(self):
        self.assertEqual(api.parse_positions(HTML)["pos1"], "48,1 °C")
        self.assertEqual(api.parse_positions(HTML)["pos18"], "PARTY 00:00")

    def test_anchors_and_index(self):
        anchors = generic.parse_anchors(HTML)
        self.assertEqual(generic.anchor_for(anchors, "pos18", 0)["text"], "PARTY")
        self.assertEqual(generic.anchor_for(anchors, "pos18", 1)["text"], "00:00")
        self.assertEqual(generic.anchor_for(anchors, "pos18", 1)["address"], "001301F0F")
        self.assertEqual(generic.point_value({}, anchors, {"kind": "select", "position": "pos18", "link_index": 0}), "PARTY")

    def test_parsing_never_accepts_arbitrary_javascript(self):
        bad = "<div id='pos1'><a href=\"javascript:fetch('x')\">Unsafe</a></div>"
        self.assertEqual(generic.anchor_for(generic.parse_anchors(bad), "pos1", 0)["address"], "")

    def test_numeric(self):
        self.assertEqual(generic.parse_numeric(" 48,1 °C "), Decimal("48.1"))
        self.assertEqual(generic.parse_numeric("601,0 kWh"), Decimal("601.0"))
        self.assertIsNone(generic.parse_numeric("ZEIT/AUTO ABGESENKT"))
        self.assertIsNone(generic.parse_numeric("nan"))

    def test_dialog(self):
        dialog = write_support.parse_change_dialog(DIALOG)
        self.assertEqual(dialog.change_address(), "0F9001301D")
        self.assertEqual(dialog.selected_options["changeto"], [("3", "NORMAL")])
        self.assertEqual(dialog.encode_mode("PARTY"), "5")


class WriteTests(unittest.IsolatedAsyncioTestCase):
    async def test_change_uses_one_write_and_reads_back(self):
        from aiohttp import ClientSession
        class Response:
            status = 200
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return None
            async def text(self): return ""
        class Session:
            def __init__(self): self.calls = []
            def get(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return Response()
        session = Session()
        client = api.CMIClient(session, "CMI123456", "unused", "unused", profile="custom")
        old_html = '<div id="pos2"><a href="javascript:loadChanger(\'AA1234BB\');">NORMAL</a></div>'
        new_html = '<div id="pos2"><a href="javascript:loadChanger(\'AA1234BB\');">PARTY</a></div>'
        reads = 0
        async def read():
            nonlocal reads
            reads += 1
            client.anchors = generic.parse_anchors(old_html if reads == 1 else new_html)
            return {"pos2": "NORMAL" if reads == 1 else "PARTY"}
        client._read_with_recovery = read
        client._generic_popup = AsyncMock(return_value=write_support.parse_change_dialog(DIALOG))
        point = {"id": "a", "kind": "select", "position": "pos2", "link_index": 0}
        result = await client.set_generic(point, "PARTY")
        self.assertEqual(result["pos2"], "PARTY")
        self.assertEqual(len(session.calls), 1)
        self.assertTrue(session.calls[0][0].endswith("/INCLUDE/change.cgi"))
        self.assertEqual(session.calls[0][1]["params"], {"changeadr": "0F9001301D", "changeto": "5"})

    async def test_stale_dialog_blocks_write(self):
        class Session:
            def get(self, *a, **kw): raise AssertionError("Write must be blocked")
        client = api.CMIClient(Session(), "CMI123456", "unused", "unused", profile="custom")
        client._read_with_recovery = AsyncMock(return_value={"pos2": "PARTY"})
        client.anchors = generic.parse_anchors('<div id="pos2"><a href="javascript:loadChanger(\'AA1234BB\');">PARTY</a></div>')
        client._generic_popup = AsyncMock(return_value=write_support.parse_change_dialog(DIALOG))
        with self.assertRaisesRegex(api.CMIError, "veraltet"):
            await client.set_generic({"id":"a", "position":"pos2", "link_index":0, "kind":"select"}, "NORMAL")


if __name__ == "__main__":
    unittest.main()
