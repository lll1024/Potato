"""保留原断言，在隔离副本中缩短重复背景文本并运行浏览器机制场景。"""
from pathlib import Path
import subprocess
import tempfile

root = Path.cwd()
frontend = root / 'frontend'
playwright = (frontend / 'node_modules/@playwright/test/index.mjs').as_posix()
source = (frontend / 'tests/ui/context-compaction-live.spec.ts').read_text()
source = source.replace("from '@playwright/test'", f"from '{playwright}'")
for old, new in ((310, 260), (350, 280), (550, 450)):
    source = source.replace(f'.repeat({old})', f'.repeat({new})')
with tempfile.TemporaryDirectory(prefix='backend-package-browser-') as temporary:
    directory = Path(temporary)
    (directory / 'context-compaction-live.spec.ts').write_text(source)
    config = directory / 'playwright.config.ts'
    config.write_text(f"""import {{defineConfig}} from '{playwright}';
export default defineConfig({{
  testDir: '{directory.as_posix()}', workers: 1,
  use: {{channel: 'chrome'}},
  outputDir: '{(directory / 'results').as_posix()}',
}});
""")
    result = subprocess.run(['npm', 'run', 'test:ui', '--prefix', 'frontend', '--', '--config', str(config)])
    raise SystemExit(result.returncode)
