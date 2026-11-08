"""Exercise the verification entrypoint without granting it a Docker daemon."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from support import ROOT


class VerificationSafetyTests(unittest.TestCase):
    def test_inherited_namespace_and_existing_resources_are_preserved(self):
        for failure in ("build", "up", "collision"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "ci").mkdir()
                (root / "scripts").mkdir()
                shutil.copy(ROOT / "ci" / "verify.sh", root / "ci" / "verify.sh")
                (root / ".env.example").write_text("")
                (root / "scripts" / "check_public.py").write_text("")
                log = root / "docker.jsonl"
                stub = root / "docker_stub.py"
                stub.write_text('''import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with Path(os.environ["DOCKER_LOG"]).open("a") as output:
    output.write(json.dumps({"args": args, "project": os.environ["COMPOSE_PROJECT_NAME"], "port": os.environ["MCP_PORT"]}) + "\\n")
failure = os.environ["FAILURE"]
if failure == "collision" and args[:2] == ["container", "ls"]:
    print("existing-resource")
if (failure == "build" and "build" in args) or (failure == "up" and "up" in args):
    sys.exit(1)
''')
                environment = dict(os.environ, COMPOSE_PROJECT_NAME="existing-demo",
                                   DOCKER_LOG=str(log), DOCKER_STUB=str(stub), FAILURE=failure,
                                   STUB_PYTHON=sys.executable)
                # An exported shell function works even on noexec /tmp mounts.
                result = subprocess.run(["bash", "-c", '''
docker() { "$STUB_PYTHON" "$DOCKER_STUB" "$@"; }
export -f docker
bash "$1"
''', "fixture", str(root / "ci" / "verify.sh")], env=environment, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                calls = [json.loads(line) for line in log.read_text().splitlines()]
                projects = {call["project"] for call in calls}
                self.assertEqual(len(projects), 1)
                self.assertTrue(next(iter(projects)).startswith("agent-memory-kit-verify-"))
                self.assertNotIn("existing-demo", projects)
                self.assertTrue(all(call["port"] == "0" for call in calls))
                cleanup = [call for call in calls if "down" in call["args"]]
                self.assertEqual(len(cleanup), 1 if failure == "up" else 0)
