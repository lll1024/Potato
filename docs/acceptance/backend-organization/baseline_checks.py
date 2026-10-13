"""在隔离主分支快照与最终实现上复用既有公开验收；不访问个人数据或外部服务。"""
import gzip
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[3]
evidence = Path(__file__).resolve().parent
baseline = "4fff3d26ef43a18a4a19263d50240a5ff4d130ae"


def run(arguments, cwd, log):
    with (evidence / log).open("w") as output:
        subprocess.run([sys.executable, *map(str, arguments)], cwd=cwd,
                       env={**os.environ, "PYTHONPATH": str(cwd / "tests")},
                       stdout=output, stderr=subprocess.STDOUT, check=True)


with tempfile.TemporaryDirectory(prefix="backend-organization-") as temporary:
    directory = Path(temporary)
    project = directory / "baseline"
    project.mkdir()
    archive = directory / "baseline.tar"
    subprocess.run(["git", "archive", baseline, "-o", str(archive)], cwd=root, check=True)
    subprocess.run(["tar", "-xf", str(archive), "-C", str(project)], check=True)
    # 原测试在原版快照中使用顶层模块；只调整临时记录脚本的导入，不添加兼容层。
    recorder = project / "docs/acceptance/backend-organization/equivalence.py"
    recorder.parent.mkdir(parents=True)
    source = (root / "docs/acceptance/turn-runtime/equivalence.py").read_text()
    recorder.write_text(source.replace("travel_app.", ""))
    before, after = directory / "before.json", directory / "after.json"
    run([recorder, before], project, "equivalence-before.log")
    run([root / "docs/acceptance/turn-runtime/equivalence.py", after], root, "equivalence-after.log")
    old, new = json.loads(before.read_text()), json.loads(after.read_text())
    for name, path in (("before", before), ("after", after)):
        with gzip.open(evidence / f"{name}.json.gz", "wb") as output:
            output.write(path.read_bytes())
    comparison = {
        "baseline": baseline,
        "implementation": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "tests": len(new),
        "http_scenarios": sum(bool(events) for events in new.values()),
        "requests": sum(kind == "request.started" for events in new.values() for kind, _ in events),
        "events": sum(map(len, new.values())),
        "equal": old == new,
    }
    (evidence / "equivalence.json").write_text(json.dumps(comparison, ensure_ascii=False, indent=2) + "\n")
    assert old == new, "重组前后公开场景的模型输入或上下文事件不同，须检查原始记录。"
    data = directory / "existing-data"
    checker = root / "docs/acceptance/backend-package/existing_data_checks.py"
    run([checker, "seed", data], project, "existing-data-before.log")
    run([checker, "verify", data], root, "existing-data-after.log")
print("主分支与最终实现的请求／事件相同；原版数据读取和续聊通过。")
