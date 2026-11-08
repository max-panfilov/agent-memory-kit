from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

from support import ROOT


spec = importlib.util.spec_from_file_location("check_public", ROOT / "scripts" / "check_public.py")
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


class PublicSafetyTests(unittest.TestCase):
    def test_generic_markers_and_checker_itself(self):
        self.assertIn("private-host", checks.findings("runner" + ".internal"))
        self.assertIn("private-path", checks.findings("/".join(["", "home", "example", "data"])))
        self.assertIn("non-example-address", checks.findings("10" + ".20.30.40"))
        self.assertIn("non-example-address", checks.findings("10" + r"\.20\.30\.40"))
        self.assertEqual(checks.findings("http://127.0.0.1:8775/mcp"), [])
        self.assertEqual(checks.findings("0.0.0.0"), [])
        self.assertEqual(checks.findings("192.0.2.1"), [])
        self.assertEqual(checks.findings(Path(checks.__file__).read_text()), [])
