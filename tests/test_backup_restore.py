from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from agent_memory_kit.backup import acceptance, verify_manifest
from support import ROOT


class BackupTests(unittest.TestCase):
    def test_manifest_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dump = root / "memory.dump"
            dump.write_bytes(b"synthetic")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "file": "memory.dump",
                "bytes": 9,
                "sha256": "2d24d9246589ad9e2d9859d35a1a9d5e7b7f1d2a7f4c8b5a1f2e3d4c5b6a7f8e9",
            }))
            # Use the real digest helper through a freshly written valid manifest.
            from agent_memory_kit.backup import sha256
            value = json.loads(manifest.read_text())
            value["sha256"] = sha256(dump)
            manifest.write_text(json.dumps(value))
            self.assertTrue(verify_manifest(manifest))
            value["sha256"] = "0" * 64
            manifest.write_text(json.dumps(value))
            self.assertFalse(verify_manifest(manifest))

    @unittest.skipUnless(os.environ.get("AGENT_MEMORY_DOCKER_ACCEPTANCE") == "1", "explicit Docker acceptance only")
    def test_fresh_restore_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "receipt"
            result = acceptance(ROOT, output)
            self.assertTrue(result["restore_verified"])
            self.assertTrue(result["source_unchanged"])


if __name__ == "__main__":
    unittest.main()
