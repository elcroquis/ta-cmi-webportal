"""Safety and migration regression tests; no Home Assistant runtime required."""
from pathlib import Path
import importlib.util
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "ta_cmi_webportal"
PKG = "ta_cmi_universal_test"
package = types.ModuleType(PKG)
package.__path__ = [str(ROOT)]
sys.modules[PKG] = package

def load(name):
    spec = importlib.util.spec_from_file_location(f"{PKG}.{name}", ROOT / f"{name}.py")
    obj = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = obj
    spec.loader.exec_module(obj)
    return obj

const = load("const")
generic = load("generic")
mapping = load("climate_mapping")
links = load("write_support")

class UniversalTests(unittest.TestCase):
    def setUp(self):
        self.points = [
            {"id":"room","kind":"sensor","unit":"°C","position":"pos4"},
            {"id":"target","kind":"number","unit":"°C","position":"pos7"},
            {"id":"mode","kind":"select","unit":"","position":"pos9"},
        ]
        self.config = {"room_point":"room","setpoint_point":"target","mode_point":"mode",
                       "mode_heat":"NORMAL","mode_off":"STANDBY","mode_auto":"ZEIT/AUTO"}

    def test_mapping_is_explicit(self):
        self.assertIsNotNone(mapping.mapping_points(self.points, self.config))
        self.assertIsNone(mapping.mapping_points(self.points, {**self.config,"room_point":"bad"}))
        self.assertIsNone(mapping.mapping_points(self.points, {**self.config,"mode_heat":""}))

    def test_unknown_mode_is_not_guessed(self):
        self.assertEqual(mapping.active_mode(self.config,"ZEIT/AUTO"),"auto")
        self.assertIsNone(mapping.active_mode(self.config,"UNKNOWN"))

    def test_live_edit_links_not_hardcoded(self):
        html = """<div id="pos16"><a href="javascript:loadChanger('AABBCCDD');">20 °C</a></div>
        <div id="pos18"><a href="javascript:loadChanger('AB12AB12');">URLAUB</a>
        <a href="javascript:loadChanger('DEADBEEF');">15.11.</a></div>"""
        result = links.parse_edit_links(html)
        self.assertEqual(result["pos16"], "AABBCCDD")
        self.assertEqual(result["special_end"], "DEADBEEF")
        self.assertEqual(links.parse_special_end(html), "15.11.")

    def test_default_custom_and_legacy_migration(self):
        self.assertIn("vol.Required(CONF_PROFILE, default=PROFILE_GENERIC)",
                      (ROOT/"config_flow.py").read_text())
        self.assertIn("data.setdefault(CONF_PROFILE, PROFILE_UVR)",
                      (ROOT/"__init__.py").read_text())

    def test_no_embedded_installation_secrets(self):
        source = "\n".join(p.read_text() for p in ROOT.glob("*.py"))
        for forbidden in ("CMI014920","Negenborn","meinhard","0FB9013020","101300C0F80"):
            self.assertNotIn(forbidden,source)

if __name__ == "__main__":
    unittest.main()
