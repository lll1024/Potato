"""解释已保存载荷内的日期与来源，不推进规划或上下文。"""
import json
from typing import Any

def source_metadata(text):
    """把原文已有的来源/日期/核实边界随页面携带，不生成新的核实结论。"""
    try:
        content = json.loads(text)
    except (ValueError, TypeError):
        return {}
    if not isinstance(content, dict):
        return {}
    metadata = {key: content[key] for key in ("retrieved_at", "document_status", "content_boundary", "max_characters", "travel_dates", "applicable_dates", "verification_status") if key in content}
    if isinstance(content.get("sources"), list):
        metadata["sources"] = [{key: source[key] for key in ("url", "title", "published_date", "content_type", "retrieved_at", "applicable_dates", "verification_status") if key in source}
                               for source in content["sources"] if isinstance(source, dict)]
    return {"source_metadata": metadata} if metadata else {}


def saved_date_context(system: str) -> dict[str, Any] | None:
    if "<travel_date_context>" not in system:
        return None
    try:
        return json.loads(system.split("<travel_date_context>", 1)[1].split("</travel_date_context>", 1)[0])
    except (ValueError, IndexError):
        return None
