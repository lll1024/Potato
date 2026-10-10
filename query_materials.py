"""只从已保存的成功返回整理查询资料；原始载荷仍是事实来源。"""
import json
import re
from datetime import date


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


def weather_limit(travel_input, covered):
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


def summarize_query(name, result, travel_input):
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
            notes.append(weather_limit(travel_input, covered))
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
