"""每轮固定的北京时间与有限旅行日期解释；日期只属于本次旅行。"""
import json
import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

BEIJING = ZoneInfo("Asia/Shanghai")


def current_time() -> datetime:
    return datetime.now(BEIJING)


def trip_duration(text: str) -> int | None:
    match = re.search(r"(?<![\d年月/-])(\d+|[一二两三四五六七八九十])(?:天|日游|日旅行)", text)
    if not match:
        return None
    value = match.group(1)
    return int(value) if value.isdigit() else {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
        "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}[value]


def explicit_dates(text: str, today: date) -> dict[str, Any] | None:
    matches = list(re.finditer(r"(?<![\d:])(?=\d{4}[年/-]|\d{1,2}月)(?:(\d{4})[年/-])?(\d{1,2})[月/-](\d{1,2})(?:日|号)?(?![\d:])", text))
    if not matches:
        return None
    year = today.year
    parsed = []
    try:
        for match in matches:
            year = int(match[1]) if match[1] else year
            parsed.append(date(year, int(match[2]), int(match[3])))
        short_end = re.match(r"\s*(?:至|到|—|-|~|～)\s*(\d{1,2})日", text[matches[-1].end():])
        if short_end:
            parsed.append(parsed[-1].replace(day=int(short_end[1])))
    except ValueError:
        return {"status": "needs_clarification", "source": "explicit", "explanation": "给出的公历日期无效，请确认具体日期。"}
    if len(matches) > 2 or (len(matches) == 2 and not re.fullmatch(r"\s*(?:至|到|—|-|~|～)\s*", text[matches[0].end():matches[1].start()])):
        return {"status": "needs_clarification", "source": "explicit", "explanation": "给出多个日期但未明确一个旅行日期范围，请确认要采用哪个日期。"}
    duration = trip_duration(text)
    start = parsed[0]
    end = parsed[-1] if len(parsed) > 1 else start + timedelta(days=(duration or 1) - 1)
    if end < start or (duration is not None and duration != (end - start).days + 1):
        return {"status": "needs_clarification", "source": "explicit",
                "explanation": f"日期范围 {start.isoformat()} 至 {end.isoformat()} 与起止顺序或要求的天数冲突，请确认日期及天数。"}
    return {"status": "resolved", "start_date": start.isoformat(), "end_date": end.isoformat(),
            "source": "explicit", "explanation": "显式日期优先；省略年份时采用本轮北京时间的年份，起始日期与天数推导结束日期。"}


def turn_date_context(text: str, now: datetime, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    local = now.astimezone(BEIJING)
    today = local.date()
    dates: dict[str, Any] = explicit_dates(text, today) or {"status": "unspecified"}
    changed_relative = re.findall(r"(?:改|换|调整|推迟|提前|延|挪)(?:到|成|为|在)?\s*(明天|本周末|下周末)", text)
    relative = changed_relative if len(changed_relative) == 1 else [value for value in ("明天", "本周末", "下周末") if value in text]
    if dates["status"] == "unspecified" and len(relative) > 1:
        dates = {"status": "needs_clarification", "source": "relative",
                 "explanation": "给出了多个相对日期，请确认采用明天、本周末或下周末中的哪一个。"}
    if dates["status"] == "unspecified" and ("本周末" in relative or "下周末" in relative):
        following = "下周末" in relative
        saturday = today + timedelta(days=5 - today.weekday() + (7 if following else 0))
        dates = {"status": "resolved", "start_date": max(today, saturday).isoformat(),
                 "end_date": (saturday + timedelta(days=1)).isoformat(), "source": "下周末" if following else "本周末",
                 "explanation": "周一为一周首日；周末为周六、周日。"}
        if not following and today.weekday() == 6:
            dates["explanation"] = "周六已过去，本周末只剩周日当天；须在回答中说明。"
            if re.search(r"(?:两|二|2)(?:天|日游)", text):
                dates = {"status": "needs_clarification", "source": "本周末",
                         "date_basis": {"start_date": today.isoformat(), "end_date": today.isoformat()},
                         "reference_time": local.isoformat(),
                         "explanation": f"周六已过去，本周末只剩 {today.isoformat()} 当天，与两日要求冲突；请确认改成一日或另选两日，不擅自延到下周。"}
    elif dates["status"] == "unspecified" and "明天" in relative:
        tomorrow = (today + timedelta(days=1)).isoformat()
        dates = {"status": "resolved", "start_date": tomorrow, "end_date": tomorrow,
                 "source": "明天", "explanation": "按本轮北京时间的次日解释。"}
    duration = trip_duration(text)
    if dates["status"] == "resolved" and duration is not None:
        start, end = date.fromisoformat(dates["start_date"]), date.fromisoformat(dates["end_date"])
        if dates["source"] == "明天":
            dates["end_date"] = (start + timedelta(days=duration - 1)).isoformat()
        elif duration != (end - start).days + 1:
            dates = {"status": "needs_clarification", "source": dates["source"],
                     "date_basis": {"start_date": start.isoformat(), "end_date": end.isoformat()},
                     "reference_time": local.isoformat(),
                     "explanation": f"旅行日期 {start.isoformat()} 至 {end.isoformat()} 仅有 {(end-start).days+1} 日，与 {duration} 日要求冲突，请确认日期与天数，不擅自缩短或延长。"}
    previous_dates = previous.get("travel_dates", {}) if previous else {}
    # 已定日期的相对表达可能只是回指原旅行；只在明确提出新日期时重算。
    historical_reference = bool(re.search(r"(?:之前(?:已)?(?:定|确定|安排)|原来(?:定|安排)|已定|已确定|原定)(?:的)?\s*(?:明天|本周末|下周末)|安排不变|日期不变", text))
    date_request = bool(changed_relative) or (not historical_reference and bool(re.search(
        r"(?:定(?:到|成|为|在)?\s*(?:明天|本周末|下周末)|(?:明天|本周末|下周末)\s*(?:去|出发|启程|开始))", text))) or text.strip() in relative
    explicit_change = bool(re.search(
        r"(?:日期|出行时间|出发时间)\s*(?:改|换|调整|推迟|提前|延|挪)"
        r"|(?:改|换|调整)(?:旅行|出行|出发)?(?:日期|时间)", text))
    date_request = date_request or explicit_change
    if dates["status"] == "unspecified" and explicit_change:
        dates = {"status": "needs_clarification", "source": "date_change",
                 "explanation": "用户明确要求修改旅行日期，但新日期尚不能可靠确定；请确认具体公历日期或日期范围，不沿用旧日期。"}
    duration_change = duration is not None and bool(re.search(r"(?:改|换|调整)(?:成|为)?\s*(?:\d+|[一二两三四五六七八九十])(?:天|日游|日旅行)", text))
    basis = previous_dates.get("date_basis", {})
    if (dates["status"] == "unspecified" and (duration_change or re.fullmatch(r"(?:\d+|[一二两三四五六七八九十])(?:天|日游|日旅行)", text.strip()))
            and previous_dates.get("status") == "needs_clarification" and basis):
        start, end = date.fromisoformat(basis["start_date"]), date.fromisoformat(basis["end_date"])
        if duration == (end - start).days + 1:
            dates = {"status": "resolved", **basis, "source": previous_dates["source"],
                     "reference_time": previous_dates["reference_time"],
                     "explanation": "用户已调整天数，原日期范围与新的天数一致；使用已保存的日期解释基准。"}
    inherit = dates["status"] == "unspecified" or (
        previous_dates.get("status") == "resolved" and dates.get("source") != "explicit" and not date_request)
    if inherit and previous_dates and previous:
        dates = previous_dates.copy()
        if dates.get("status") == "resolved":
            dates.setdefault("reference_time", previous["now"])
    elif dates["status"] == "resolved":
        dates.setdefault("reference_time", local.isoformat())
    if (duration_change and duration is not None and dates["status"] == "resolved" and inherit
            and not explicit_change and not date_request):
        dates["end_date"] = (date.fromisoformat(dates["start_date"]) + timedelta(days=duration - 1)).isoformat()
        dates["explanation"] = "用户明确调整旅行天数，保留原起始日期并更新结束日期。"
    context = {"timezone": "Asia/Shanghai", "now": local.isoformat(), "today": today.isoformat(),
               "weekday": "星期" + "一二三四五六日"[today.weekday()], "travel_dates": dates}
    if (previous_dates.get("status") == dates.get("status") == "resolved"
            and any(previous_dates.get(key) != dates.get(key) for key in ("start_date", "end_date"))):
        context["dates_changed"] = True
        context["previous_travel_dates"] = previous_dates.copy()
    return context


def model_date_context(context: dict[str, Any]) -> str:
    return "\n本轮时间与旅行日期（同轮固定，以此解释本轮用户输入）：\n<travel_date_context>" + json.dumps(context, ensure_ascii=False) + "</travel_date_context>"


def saved_date_context(system: str) -> dict[str, Any] | None:
    if "<travel_date_context>" not in system:
        return None
    try:
        return json.loads(system.split("<travel_date_context>", 1)[1].split("</travel_date_context>", 1)[0])
    except (ValueError, IndexError):
        return None
