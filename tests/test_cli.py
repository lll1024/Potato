import os
import asyncio
import contextlib
import io
import logging
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from travel_app.planning import main


class CLITests(unittest.TestCase):
    def test_grouped_interrupt_and_errors_exit_without_credentials_or_traceback(self):
        for error, expected in [
            (BaseExceptionGroup("SDK", [KeyboardInterrupt()]), 130),
            (BaseExceptionGroup("SDK", [asyncio.CancelledError()]), 130),
            (ExceptionGroup("SDK", [RuntimeError("https://mcp.example.test?key=test-secret")]), 1),
        ]:
            with self.subTest(exit=expected):
                captured = io.StringIO()
                handler = logging.StreamHandler(captured)
                async def fail(*args, **kwargs):
                    logging.getLogger("client").error("https://mcp.example.test?key=test-secret")
                    raise error

                logging.getLogger().addHandler(handler)
                try:
                    with (
                        patch.dict(os.environ, {"AMAP_MAPS_API_KEY": "test-secret", "MODEL_ID": "test-model",
                                               "ANTHROPIC_API_KEY": "test-model-key"}, clear=True),
                        patch("sys.argv", ["agent.py"]),
                        contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured),
                    ):
                        self.assertEqual(main(load_environment=lambda *_a, **_k: None, cli_runner=fail), expected)
                finally:
                    logging.getLogger().removeHandler(handler)
                self.assertNotIn("test-secret", captured.getvalue())
                self.assertNotIn("Traceback", captured.getvalue())

    def test_missing_amap_key_has_an_actionable_message_without_traceback(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            shutil.copy(project / "agent.py", directory)
            shutil.copytree(project / "travel_app", Path(directory) / "travel_app",
                            ignore=shutil.ignore_patterns("__pycache__"))
            result = subprocess.run(
                [sys.executable, "agent.py", "--check-amap"],
                cwd=directory,
                env={"PATH": os.environ["PATH"]},
                capture_output=True, text=True, timeout=10,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("AMAP_MAPS_API_KEY", result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_compaction_configuration_fails_before_cli_connects(self):
        for capacity in (None, "0", "invalid", "10048"):
            with self.subTest(capacity=capacity):
                env = {"AMAP_MAPS_API_KEY": "key", "MODEL_ID": "test-model", "ANTHROPIC_API_KEY": "model-key",
                       "CONTEXT_COMPACTION_ENABLED": "true"}
                if capacity is not None:
                    env["MODEL_CONTEXT_WINDOW"] = capacity
                captured = io.StringIO()
                runner = AsyncMock()
                with patch.dict(os.environ, env, clear=True), patch("sys.argv", ["agent.py"]), contextlib.redirect_stderr(captured):
                    self.assertEqual(main(load_environment=lambda *_a, **_k: None, cli_runner=runner), 1)
                    runner.assert_not_called()
                self.assertIn("MODEL_CONTEXT_WINDOW", captured.getvalue())
                self.assertNotIn("Traceback", captured.getvalue())
