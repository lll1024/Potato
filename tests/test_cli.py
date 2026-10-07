import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class CLITests(unittest.TestCase):
    def test_missing_amap_key_has_an_actionable_message_without_traceback(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            for name in ["agent.py", "amap_mcp.py"]:
                shutil.copy(project / name, directory)
            result = subprocess.run(
                [sys.executable, "agent.py", "--check-amap"],
                cwd=directory,
                env={"PATH": os.environ["PATH"]},
                capture_output=True, text=True, timeout=10,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("AMAP_MAPS_API_KEY", result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stdout + result.stderr)
