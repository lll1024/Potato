"""分阶段检查 module 归属、公开导出和依赖；规则见 docs/modules/migration.md。"""
import argparse
import ast
from importlib.util import resolve_name
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".mjs", ".css"}


def active_files(root):
    files = [path for path in root.glob("*.py")]
    for directory in ("travel_app", "scripts", "tests", "frontend/src", "frontend/tests"):
        files.extend(path for path in (root / directory).rglob("*") if path.suffix in EXTENSIONS)
    files.extend(path for path in (root / "frontend").glob("*") if path.suffix in EXTENSIONS)
    return sorted({path.relative_to(root).as_posix() for path in files if "__pycache__" not in path.parts})


def python_imports(root, relative):
    tree = ast.parse((root / relative).read_text(), filename=relative)
    package = relative.removesuffix(".py").split("/")[:-1]
    imports, exports, aliases = [], None, {}

    def resolve(name):
        for base in (root, root / "tests"):
            path = base.joinpath(*name.split("."))
            for candidate in (path.with_suffix(".py"), path / "__init__.py"):
                if candidate.is_file():
                    return candidate.relative_to(root).as_posix()
        return None

    def add(name, names):
        target = resolve(name)
        if target:
            imports.append({"target": target, "names": names})

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                add(item.name, [])
                aliases[item.asname or item.name.split(".")[0]] = item.name if item.asname else item.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            prefix = package[:len(package) - node.level + 1] if node.level else []
            name = ".".join([*prefix, *([node.module] if node.module else [])])
            names = [item.name for item in node.names]
            add(name, names)
            for item in node.names:
                # `from travel_app import module` 和相对包导入同样要检查。
                if not resolve(name):
                    add(name + "." + item.name, [])
                aliases[item.asname or item.name] = name + "." + item.name
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
                try:
                    value = ast.literal_eval(node.value) if node.value is not None else None
                    exports = value if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value) else None
                except (ValueError, TypeError):
                    exports = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute)):
            name = ast.unparse(node.func)
            head, *tail = name.split(".")
            name = ".".join([aliases.get(head, head), *tail])
            if name in ("__import__", "builtins.__import__", "importlib.import_module"):
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    module_name = node.args[0].value
                    if module_name.startswith("."):
                        package_arg = node.args[1] if len(node.args) > 1 else next(
                            (keyword.value for keyword in node.keywords if keyword.arg == "package"), None)
                        if isinstance(package_arg, ast.Constant) and isinstance(package_arg.value, str):
                            try:
                                module_name = resolve_name(module_name, package_arg.value)
                            except (ValueError, ImportError):
                                module_name = "<dynamic>"
                        else:
                            module_name = "<dynamic>"
                    if module_name == "<dynamic>":
                        imports.append({"target": "<dynamic>", "names": []})
                    else:
                        # 动态返回整个包，不能证明调用方只访问显式导出；跨能力改用静态具名导入。
                        add(module_name, ["*"])
                else:
                    imports.append({"target": "<dynamic>", "names": []})
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            parts = ast.unparse(node).split(".")
            if parts[0] in aliases:
                expanded = aliases[parts[0]].split(".") + parts[1:]
                for index in range(len(expanded) - 1, 0, -1):
                    if resolve(".".join(expanded[:index])):
                        add(".".join(expanded[:index]), [expanded[index]])
                        break
    return {"imports": imports, "exports": exports}


def import_key(edge):
    return edge["target"] + ":" + ",".join(sorted(edge["names"]))


def inspect(root, manifest):
    paths = active_files(root)
    modules = manifest["modules"]
    legacy = manifest.get("legacy", {})
    assigned = manifest.get("files", {})
    errors, ownership, info = [], {}, {}
    for path in paths:
        entry = legacy.get(path) or assigned.get(path)
        for name, module in modules.items():
            if any(path.startswith(module[side] + "/") for side in ("backend", "frontend")):
                entry = {"owner": name, "role": "production"}
                break
        if path in ("agent.py", "web.py") and not entry:
            entry = {"owner": "assembly", "role": "assembly"}
        if not entry:
            errors.append(f"{path}: 未声明代码归属")
            continue
        if entry["owner"] not in {*modules, "assembly", "support", "tooling", "integration", "compatibility"}:
            errors.append(f"{path}: 未知 module 归属 {entry['owner']}")
        ownership[path] = entry
    for path, entry in legacy.items():
        if path not in paths:
            errors.append(f"{path}: 旧实现已移除，请删除对应迁移豁免")
        if not entry.get("remove_by"):
            errors.append(f"{path}: 缺少迁移豁免移除任务")
    for path in paths:
        if path.endswith(".py"):
            info[path] = python_imports(root, path)
        elif path.endswith(".css"):
            imports = []
            for match in re.finditer(r'@import\s+(?:url\(\s*)?[\'\"]([^\'\"]+)', (root / path).read_text()):
                target = (root / path).parent / match[1]
                if target.is_file():
                    imports.append({"target": target.resolve().relative_to(root.resolve()).as_posix(), "names": []})
            info[path] = {"imports": imports}
    frontend = [path for path in paths if Path(path).suffix in {".ts", ".tsx", ".mjs", ".js"}]
    if frontend:
        result = subprocess.run(["node", str(Path(__file__).with_name("frontend_imports.mjs")), str(root)],
                                input=json.dumps(frontend), capture_output=True, text=True, check=True)
        info.update(json.loads(result.stdout))
    public = {}
    for name, module in modules.items():
        for side, entry_file in (("backend", "__init__.py"), ("frontend", "index.ts")):
            directory = module[side]
            if not any(path.startswith(directory + "/") for path in paths):
                continue
            entry = directory + "/" + entry_file
            declarations = info.get(entry, {})
            if "exports" not in declarations or declarations["exports"] is None or declarations.get("wildcard"):
                errors.append(f"{entry}: 公开入口须有显式导出（Python __all__ / TS named exports），禁止星号导出")
            public[entry] = set(declarations.get("exports") or [])
    for path, entry in assigned.items():
        if entry.get("role") == "support" and path in info:
            public[path] = set(info[path].get("exports") or [])
    graph = {name: set() for name in modules}
    for path, data in info.items():
        if path not in ownership:
            continue
        source = ownership[path]
        for edge in data["imports"]:
            target = edge["target"]
            if path in legacy and import_key(edge) in legacy[path].get("imports", []):
                continue
            if target == "<dynamic>":
                errors.append(f"{path}: 动态导入无法检查公开入口，请使用静态路径")
                continue
            if target not in ownership:
                errors.append(f"{path} -> {target}: 目标缺少归属")
                continue
            destination = ownership[target]
            same_module = source["owner"] == destination["owner"]
            internal = source["role"] in ("production", "internal-test", "tooling")
            if same_module and internal:
                continue
            if target not in public or any(name == "*" or name not in public[target] for name in edge["names"]):
                errors.append(f"{path} -> {target}: 跨 module 只能使用显式公开入口与公开符号 {edge['names']}")
            owner, dependency = source["owner"], destination["owner"]
            if owner in modules and dependency in modules and owner != dependency:
                graph[owner].add(dependency)
                if dependency not in modules[owner]["depends"]:
                    errors.append(f"{path} -> {target}: 禁止的依赖方向 {owner} -> {dependency}")
            elif owner == "support" and dependency != "support":
                errors.append(f"{path} -> {target}: 共享支撑不得反向依赖业务 module")
    # 同时检查声明图和实际已迁移图，尚未落地的 module 也不能声明环路。
    for name, module in modules.items():
        graph[name].update(module["depends"])
    def walk(name, stack):
        if name in stack:
            errors.append("module 依赖环路: " + " -> ".join([*stack, name]))
            return
        for target in graph.get(name, []):
            if target not in modules:
                errors.append(f"{name}: 未知依赖 {target}")
            else:
                walk(target, [*stack, name])
    for name in graph:
        walk(name, [])
    return sorted(set(errors)), info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    arguments = parser.parse_args()
    manifest = json.loads((arguments.root / "architecture.json").read_text())
    errors, _ = inspect(arguments.root, manifest)
    for error in errors:
        print(error)
    if not errors:
        print(f"公开入口检查通过；旧实现豁免 {len(manifest.get('legacy', {}))} 项，逐项移除任务见 architecture.json。")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
