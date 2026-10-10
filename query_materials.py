"""只从已保存的成功返回整理查询资料；原始载荷仍是事实来源。"""
import json
import re
from datetime import date

from tavily_mcp import SEARCH_TOOLS, SOURCE_TOOLS


def scalar(value):
    return str(value) if isinstance(value, (str, int, float)) and not isinstance(value, bool) else None


def fields(data, names):
    return [f"{label}：{scalar(data[key])}" for key, label in names
            if key in data and scalar(data[key]) not in (None, "")]


def returned_data(result):
    try:
        envelope = json.loads(result["content"])
        if isinstance(envelope, dict):
            if isinstance(envelope.get("data"), dict):
                return envelope["data"]
            for text in envelope.get("text", []):
                try:
                    value = json.loads(text)
                except (ValueError, TypeError):
                    continue
                if isinstance(value, dict):
                    return value
    except (ValueError, TypeError, KeyError):
        pass
    return {}


def weather_limit(travel_input, covered, date_context=None):
    if date_context is not None:
        dates = date_context["travel_dates"]
        if dates["status"] == "resolved":
            start, end = date.fromisoformat(dates["start_date"]), date.fromisoformat(dates["end_date"])
            applicable = set()
            for value in covered:
                try:
                    day = date.fromisoformat(value)
                except (ValueError, TypeError):
                    continue
                if start <= day <= end:
                    applicable.add(day)
            if len(applicable) != (end - start).days + 1:
                return f"已确定旅行日期：{start.isoformat()} 至 {end.isoformat()}。旅行日期超出这份预报的覆盖范围，不能用于对应日期；天气仍待核实。"
            return f"这份预报覆盖已确定的旅行日期：{start.isoformat()} 至 {end.isoformat()}；仅适用于列出的预报日期，不能外推其他日期。"
        return "旅行年份或日期尚未确认，尚无法确认这份天气资料对旅行日期的适用性；日期仍需澄清，天气待核实。"

    explicit = []
    for year, month, day in re.findall(r"(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})日?", travel_input):
        try:
            explicit.append(date(int(year), int(month), int(day)))
        except ValueError:
            continue
    if explicit and covered:
        if any(day.isoformat() not in covered for day in explicit):
            return "旅行日期超出这份预报的覆盖范围，不能用于对应日期；天气仍待核实。"
        # 只确认原文明确给出的日期，不根据春节或未确认年份推导日期。
        return "仅适用于列出的预报日期；其他旅行日期的天气仍待核实。"
    return "旅行年份或日期尚未确认，尚无法确认这份天气资料对旅行日期的适用性；不能将其当成春节天气。"


def summarize_query(name, result, travel_input, date_context=None):
    data = returned_data(result)
    entries = []
    notes = []
    title = "查询资料"
    if name in ("maps_search_detail", "maps_text_search", "maps_around_search"):
        detail = name == "maps_search_detail"
        title = "地点详情" if detail else "地点候选（尚未核实详情）"
        places = data.get("pois", [data])
        if isinstance(places, list):
            for place in places:
                if not isinstance(place, dict):
                    continue
                lines = fields(place, [("name", "名称"), ("address", "地址"), ("location", "坐标")])
                if detail:
                    lines += fields(place, [("opentime", "常规开放时间"), ("opening_hours", "常规开放时间")])
                    business = place.get("business")
                    if isinstance(business, dict):
                        lines += fields(business, [("opentime", "常规开放时间"), ("opentime_week", "常规开放时间")])
                if lines:
                    entries.append(lines)
        if detail and any("常规开放时间" in line for entry in entries for line in entry):
            notes.append("常规开放时间不保证春节或未来当日开放，当日是否开放仍待核实。")
        if not detail:
            notes.append("候选尚未核实详情，不代表已选定地点或符合口味偏好；同名地点可能存在歧义。")
    elif name in SOURCE_TOOLS:
        search = name in SEARCH_TOOLS
        title = "官方资料搜索线索（正文尚未核实）" if search else "来源正文（身份与日期仍须核对）"
        try:
            envelope = json.loads(result["content"])
        except (ValueError, TypeError, KeyError):
            envelope = {}
        retrieved_at = envelope.get("retrieved_at") if isinstance(envelope, dict) else None
        results = data.get("results", [])
        if isinstance(results, list):
            for source in results:
                if not isinstance(source, dict):
                    continue
                lines = fields(source, [("title", "标题"), ("url", "来源链接"),
                    ("published_date", "来源发布时间"), ("applicable_dates", "适用日期")])
                body = scalar(source.get("content" if search else "raw_content"))
                if body:
                    lines.append(("搜索摘要：" if search else "取得正文：") + body)
                if not search and not body:
                    lines.append("正文未取得，规则仍待核实。")
                if retrieved_at:
                    lines.append("查询时间：" + str(retrieved_at))
                if not scalar(source.get("published_date")):
                    lines.append("来源发布时间未返回。")
                if not scalar(source.get("applicable_dates")):
                    lines.append("适用日期未返回，须核对正文与旅行日期。")
                if lines:
                    entries.append(lines)
        if entries:
            dates = date_context.get("travel_dates", {}) if date_context else {}
            if dates.get("status") == "resolved":
                notes.append(f"本次核对的旅行日期：{dates['start_date']} 至 {dates['end_date']}；须逐项核对正文的适用日期与条件，不能据此保证当日开放。")
            else:
                notes.append("旅行日期尚未确定，来源规则对旅行日期的适用性仍待核实。")
            notes.append("搜索摘要仅提供线索；来源身份、官方指定渠道及正文适用日期须核对。" if search else
                         "取得正文不等于已核实官方身份或未来当天开放；常规规则、临时公告及冲突须按旅行日期核对。")
            notes.append("预订入口须遵守官方渠道限制；入口可访问不代表有票或已预约、已预订。")
        if data.get("failed_results"):
            notes.append("部分页面正文读取失败，对应开放、预约及门票规则仍待核实。")
    elif name == "maps_weather":
        title = "天气资料"
        # 兼容高德原生 forecasts/casts 以及 MCP 按天返回的 forecasts。
        forecasts = data.get("forecasts", [])
        covered = []
        if isinstance(forecasts, list):
            for forecast in forecasts:
                if not isinstance(forecast, dict):
                    continue
                city = forecast.get("city", data.get("city"))
                days = forecast.get("casts", [forecast])
                if not isinstance(days, list):
                    continue
                for day in days:
                    if not isinstance(day, dict) or not scalar(day.get("date")):
                        continue
                    lines = fields({"city": city}, [("city", "城市")]) + fields(day, [
                        ("date", "预报日期"), ("dayweather", "白天天气"), ("nightweather", "夜间天气"),
                        ("daytemp", "白天气温（℃）"), ("nighttemp", "夜间气温（℃）")])
                    entries.append(lines)
                    covered.append(day["date"])
        lives = data.get("lives", [])
        if isinstance(lives, list):
            for live in lives:
                if isinstance(live, dict):
                    lines = fields(live, [("city", "城市"), ("reporttime", "观测时间"),
                        ("weather", "实时天气"), ("temperature", "气温（℃）")])
                    if lines:
                        entries.append(lines)
                        notes.append("实时天气不能作为未来旅行日期的预报。")
        if entries:
            notes.append(weather_limit(travel_input, covered, date_context))
    elif name in ("maps_direction_walking", "maps_direction_driving", "maps_direction_transit_integrated", "maps_direction_bicycling"):
        title = "交通路线"
        mode = {"maps_direction_walking": "步行", "maps_direction_driving": "驾车",
                "maps_direction_transit_integrated": "公交", "maps_direction_bicycling": "骑行"}[name]
        route = data.get("route", data)
        if isinstance(route, dict):
            ends = fields(route, [("origin", "起点"), ("destination", "终点")])
            paths = route.get("paths", route.get("transits", []))
            if isinstance(paths, list):
                for path in paths:
                    if isinstance(path, dict):
                        lines = fields(path, [("duration", "耗时（秒）"), ("distance", "距离（米）")])
                        if ends or lines:
                            entries.append(ends + ["方式：" + mode] + lines)
            if not entries and ends:
                entries.append(ends + ["方式：" + mode])
        if entries:
            notes.append("按返回口径展示；未查询的交通衔接、实时班次及费用仍待核实。")
    return {"title": title, "entries": entries, "notes": notes}
