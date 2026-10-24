"""检查当前导航、公开契约和最终验收的本地 Markdown 链接；历史报告按其版本保留。"""
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def main():
    sources = [ROOT / name for name in (
        "AGENTS.md", "README.md", "GLOSSARY.md", "docs/backend-map.md",
        "docs/adr/0006-backend-package-and-execution-responsibilities.md",
        "docs/adr/0011-deep-modules-and-progressive-disclosure.md",
        "docs/acceptance/README.md", "docs/acceptance/deep-modules-final.md",
        "docs/acceptance/deep-modules-final/spec-mapping.md",
    )]
    sources.extend(sorted((ROOT / "docs/modules").glob("*.md")))
    errors = []
    for source in sources:
        if not source.is_file():
            errors.append(f"{source.relative_to(ROOT)}: 文件不存在")
            continue
        for match in re.finditer(r'\]\((?:<([^>]+)>|([^\s)]+))(?:\s+"[^\"]*")?\)', source.read_text()):
            target = match[1] or match[2]
            url = urlsplit(target)
            if url.scheme or url.netloc or not url.path:
                continue
            path = source.parent / unquote(url.path)
            if not path.exists():
                errors.append(f"{source.relative_to(ROOT)} -> {target}: 链接不存在")
    for error in errors:
        print(error)
    if not errors:
        print(f"当前导航与公开契约链接检查通过（{len(sources)} 份文档）。")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
