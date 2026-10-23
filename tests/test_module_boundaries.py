"""通过检查器命令验证公开入口、归属、依赖方向和环路。"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ModuleBoundaryTests(unittest.TestCase):
    def check(self, files, *, dependencies=None, internal_test=False, assignments=None, legacy=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = {"modules": {name: {"backend": f"travel_app/{name}", "frontend": f"frontend/src/{name}",
                        "depends": (dependencies or {"sessions": ["planning"], "planning": []})[name]}
                        for name in ("sessions", "planning")}, "legacy": legacy or {}, "files": assignments or {}}
            if internal_test:
                manifest["files"]["tests/test_internal.py"] = {"owner": "planning", "role": "internal-test"}
            for name, content in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            (root / "architecture.json").write_text(json.dumps(manifest))
            return subprocess.run([sys.executable, str(ROOT / "scripts/check_modules.py"), "--root", str(root)],
                                  capture_output=True, text=True)

    def backend(self, source):
        return {"travel_app/sessions/__init__.py": "__all__ = []\n" + source,
                "travel_app/planning/__init__.py": "from .engine import run\n__all__ = ['run']\n",
                "travel_app/planning/engine.py": "def run(): pass\n"}

    def test_public_import_passes_and_private_or_undeclared_import_fails(self):
        self.assertEqual(self.check(self.backend("from travel_app.planning import run\n")).returncode, 0)
        for source in ("from travel_app.planning.engine import run\n",
                       "from travel_app.planning import engine\n",
                       "import travel_app.planning.engine\n",
                       "from importlib import import_module as load\nengine = load('travel_app.planning.engine')\n",
                       "import importlib as il\nengine = il.import_module('travel_app.planning.engine')\n",
                       "from importlib import import_module\nengine = import_module('.engine', 'travel_app.planning')\n",
                       "import travel_app.planning as p\np.engine.run()\n"):
            with self.subTest(source=source):
                result = self.check(self.backend(source))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("公开", result.stdout)

    def test_reverse_dependency_and_cycle_fail(self):
        files = self.backend("from travel_app.planning import run\n")
        files["travel_app/planning/engine.py"] = "import travel_app.sessions\ndef run(): pass\n"
        self.assertIn("依赖方向", self.check(files).stdout)
        result = self.check(files, dependencies={"sessions": ["planning"], "planning": ["sessions"]})
        self.assertIn("环路", result.stdout)

    def test_owned_internal_test_can_use_private_seam_but_external_test_cannot(self):
        files = self.backend("")
        files["tests/test_internal.py"] = "from travel_app.planning.engine import run\n"
        self.assertEqual(self.check(files, internal_test=True).returncode, 0)
        self.assertNotEqual(self.check(files).returncode, 0)

    def test_new_unowned_file_and_missing_public_exports_fail(self):
        files = self.backend("")
        files["travel_app/forgotten.py"] = ""
        self.assertIn("归属", self.check(files).stdout)
        del files["travel_app/forgotten.py"]
        files["travel_app/planning/__init__.py"] = "from .engine import run\n"
        self.assertIn("__all__", self.check(files).stdout)

    def test_frontend_exports_reexports_private_paths_and_dynamic_imports(self):
        files = {"frontend/src/sessions/index.ts": "import {run} from '../planning'; export const start = run;",
                 "frontend/src/planning/index.ts": "export {run} from './engine';",
                 "frontend/src/planning/engine.ts": "export const run = () => {};"}
        self.assertEqual(self.check(files).returncode, 0)
        for source in ("export {run} from '../planning/engine';",
                       "const value = import('../planning/engine');",
                       "import {secret} from '../planning';",
                       "import * as p from '../planning'; p.secret();"):
            files["frontend/src/sessions/index.ts"] = source
            result = self.check(files)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("公开", result.stdout)

    def test_assembly_cannot_use_private_implementation(self):
        files = self.backend("")
        files["agent.py"] = "from travel_app.planning import run\n"
        self.assertEqual(self.check(files).returncode, 0)
        files["agent.py"] = "from travel_app.planning.engine import run\n"
        self.assertIn("公开", self.check(files).stdout)

    def test_shared_support_has_explicit_exports_and_no_reverse_dependency(self):
        files = self.backend("from travel_app.common import Clock\n")
        files["travel_app/common.py"] = "class Clock: pass\n__all__ = ['Clock']\n"
        assignments = {"travel_app/common.py": {"owner": "support", "role": "support"}}
        self.assertEqual(self.check(files, assignments=assignments).returncode, 0)
        files["travel_app/common.py"] += "from travel_app.planning import run\n"
        self.assertIn("反向依赖", self.check(files, assignments=assignments).stdout)

    def test_legacy_exception_only_covers_frozen_import_and_expires_with_file(self):
        files = self.backend("")
        files["travel_app/old.py"] = "from travel_app.planning.engine import run\n"
        legacy = {"travel_app/old.py": {"owner": "sessions", "role": "production", "remove_by": "#81",
                  "imports": ["travel_app/planning/engine.py:run"]}}
        self.assertEqual(self.check(files, legacy=legacy).returncode, 0)
        files["travel_app/old.py"] += "from travel_app.planning import engine\n"
        self.assertIn("公开", self.check(files, legacy=legacy).stdout)
        del files["travel_app/old.py"]
        self.assertIn("删除对应迁移豁免", self.check(files, legacy=legacy).stdout)
