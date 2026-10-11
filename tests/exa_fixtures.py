"""将测试页面按已实测 Exa MCP 文本格式发送，不模拟应用适配层。"""


def source_text(data, *, search):
    if not isinstance(data, dict) or "results" not in data:
        return str(data)
    pages = []
    for source in data["results"]:
        url = source.get("url", "")
        title = source.get("title", "参观须知")
        if search:
            pages.append(f"Title: {title}\nURL: {url}\nPublished: {source.get('published_date', 'N/A')}\nAuthor: N/A\nHighlights:\n{source.get('content', '')}")
        else:
            pages.append(f"# {title}\nURL: {url}\n\n{source.get('text', '')}")
    for failure in data.get("failed_results", []):
        pages.append(f"Error fetching {failure['url']}: {failure.get('error', 'CRAWL_UNKNOWN_ERROR')}")
    return "\n\n---\n\n".join(pages)
