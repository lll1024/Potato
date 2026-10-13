"""直接访问正式后端页面，复用原浏览器断言；配置仅免去无关的 Vite 服务。"""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[3]
evidence = Path(__file__).resolve().parent
playwright = root / "frontend/node_modules/@playwright/test/index.mjs"
with tempfile.TemporaryDirectory(prefix="backend-organization-browser-") as temporary:
    config = Path(temporary) / "playwright.config.ts"
    config.write_text(f"""import {{defineConfig}} from '{playwright.as_posix()}';
export default defineConfig({{
  testDir: '{(root / 'frontend/tests/ui').as_posix()}', workers: 1,
  use: {{channel: 'chrome'}},
  outputDir: '{(Path(temporary) / 'results').as_posix()}',
}});
""")
    for name, scenarios in (
        ("browser-recovery.log", ["recovery-live.spec.ts", "preferences-recovery-live.spec.ts"]),
        ("browser-original-context.log", ["context-compaction-live.spec.ts"]),
    ):
        with (evidence / name).open("w") as output:
            result = subprocess.run(["npm", "run", "test:ui", "--", "--config", str(config), *scenarios],
                                    cwd=root / "frontend", stdout=output, stderr=subprocess.STDOUT)
        print(f"{name}: exit={result.returncode}", flush=True)
        if name == "browser-recovery.log" and result.returncode:
            raise SystemExit(result.returncode)

# 原夹具超预算为已记录的基线问题；隔离补充保留生产预算和原行为断言。
with (evidence / "browser-adjusted-context.log").open("w") as output:
    subprocess.run([str(root / ".venv/bin/python"), str(root / "docs/acceptance/backend-package/browser_context_checks.py")],
                   cwd=root, stdout=output, stderr=subprocess.STDOUT, check=True)
print("隔离补充上下文场景通过；原场景结果须单独读取。")
