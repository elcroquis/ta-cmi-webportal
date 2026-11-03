"""Regression checks for per-CMI polling options; no HA runtime required."""

import ast
from datetime import timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'custom_components' / 'ta_cmi_webportal'
spec = spec_from_file_location('cmi_polling_constants', ROOT / 'const.py')
const = module_from_spec(spec)
spec.loader.exec_module(const)


class PollingIntervalTests(unittest.TestCase):
    def test_default_is_120_seconds_for_older_entries(self):
        entry = SimpleNamespace(options={})
        self.assertEqual(const.scan_interval_for_entry(entry), timedelta(seconds=120))

    def test_each_entry_has_independent_settings(self):
        first = SimpleNamespace(options={const.OPT_SCAN_INTERVAL: 60})
        second = SimpleNamespace(options={const.OPT_SCAN_INTERVAL: 300})
        self.assertEqual(const.scan_interval_for_entry(first), timedelta(seconds=60))
        self.assertEqual(const.scan_interval_for_entry(second), timedelta(seconds=300))

    def test_out_of_bounds_saved_settings_are_clamped(self):
        too_fast = SimpleNamespace(options={const.OPT_SCAN_INTERVAL: 1})
        too_slow = SimpleNamespace(options={const.OPT_SCAN_INTERVAL: 9000})
        self.assertEqual(const.scan_interval_for_entry(too_fast), timedelta(seconds=60))
        self.assertEqual(const.scan_interval_for_entry(too_slow), timedelta(seconds=3600))

    def test_polling_is_wired_into_coordinator_and_options_flow(self):
        coordinator = ast.parse((ROOT / 'coordinator.py').read_text())
        # The coordinator uses its entry rather than a global constant.
        self.assertIn('update_interval=scan_interval_for_entry(entry)',
                      ast.unparse(coordinator))
        config_flow = ast.parse((ROOT / 'config_flow.py').read_text())
        classes = {n.name: n for n in config_flow.body if isinstance(n, ast.ClassDef)}
        methods = {n.name for n in classes['CMIOptionsFlow'].body
                   if isinstance(n, ast.AsyncFunctionDef)}
        self.assertIn('async_step_settings', methods)
        self.assertIn('OptionsFlowWithReload', ast.unparse(classes['CMIOptionsFlow']))


if __name__ == '__main__':
    unittest.main()
