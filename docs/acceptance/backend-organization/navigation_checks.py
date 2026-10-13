"""核对最终导航、活动 Python 导入和未调整的行为基础；不执行外部查询。"""
import ast
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote

root = Path(__file__).resolve().parents[3]
evidence = Path(__file__).resolve().parent
baseline = "4fff3d26ef43a18a4a19263d50240a5ff4d130ae"
pages = ["README.md", "docs/backend-map.md", "docs/acceptance/README.md", ".scratch/README.md",
         "docs/adr/0006-backend-package-and-execution-responsibilities.md",
         "docs/acceptance/backend-package.md", "docs/acceptance/context-maintenance.md",
         "docs/acceptance/turn-runtime.md", "docs/acceptance/backend-organization.md",
         "docs/acceptance/backend-organization/review.md"]
missing = []
checked = 0
for name in pages:
    page = root / name
    for target in re.findall(r"\]\(([^)]+)\)", page.read_text()):
        if "://" in target or target.startswith("#"):
            continue
        checked += 1
        destination = (page.parent / unquote(target.split("#")[0])).resolve()
        if destination != evidence / "navigation.json" and not destination.exists():
            missing.append({"page": name, "target": target})

modules = subprocess.check_output(["git", "ls-tree", "--name-only", baseline], cwd=root, text=True).splitlines()
old_modules = {Path(name).stem for name in modules if name.endswith(".py")}
obsolete = []
files = sorted((root / "travel_app").glob("*.py")) + sorted((root / "tests").glob("*.py"))
files += sorted((root / "docs/acceptance").rglob("*.py"))
for file in files:
    for node in ast.walk(ast.parse(file.read_text())):
        imports = [node.module] if isinstance(node, ast.ImportFrom) else (
            [name.name for name in node.names] if isinstance(node, ast.Import) else [])
        for module in imports:
            if module and module.split(".")[0] in old_modules:
                obsolete.append({"file": str(file.relative_to(root)), "line": node.lineno, "module": module})

unchanged = []
for module in sorted(old_modules - {"agent", "web"}):
    old = subprocess.check_output(["git", "show", f"{baseline}:{module}.py"], cwd=root, text=True)
    expected = re.sub(r"(?m)^from (" + "|".join(sorted(old_modules)) + r") import", r"from travel_app.\1 import", old)
    assert (root / f"travel_app/{module}.py").read_text() == expected, module
    unchanged.append(module)

def constants(source):
    return {node.targets[0].id: ast.literal_eval(node.value) for node in ast.parse(source).body
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {"SYSTEM", "BUDGET_MESSAGE", "SERVICE_MESSAGE"}}

old_agent = subprocess.check_output(["git", "show", f"{baseline}:agent.py"], cwd=root, text=True)
assert constants(old_agent) == constants((root / "travel_app/agent.py").read_text())
assert sorted(file.name for file in root.glob("*.py")) == ["agent.py", "web.py"]
for entry in ("agent", "web"):
    tree = ast.parse((root / f"{entry}.py").read_text())
    if ast.get_docstring(tree):
        tree.body.pop(0)
    expected = ast.parse(f'from travel_app.{entry} import main\nif __name__ == "__main__":\n    raise SystemExit(main())\n')
    assert ast.dump(tree) == ast.dump(expected), entry
archives = subprocess.check_output(["git", "diff", "--name-only", baseline, "--", ".scratch"], cwd=root, text=True).splitlines()
assert archives == [".scratch/README.md"], archives
result = {"baseline": baseline, "pages": pages, "links_checked": checked, "missing": missing,
          "python_files_checked": len(files), "obsolete_imports": obsolete,
          "unchanged_except_package_imports": unchanged, "agent_constants_unchanged": True,
          "root_entrypoints_only": True, "historical_task_contents_preserved": True}
(evidence / "navigation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
assert not missing and not obsolete, result
print("导航链接、包导入、根入口、原行为基础与历史档案核对通过。")
