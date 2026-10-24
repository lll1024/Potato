"""查询 adapter 与持久历史共用的工具名称分类，不持有执行状态。"""
PLACE_TOOLS = {"maps_text_search", "maps_search_detail"}
DINING_TOOLS = {"maps_around_search"}
WEATHER_TOOLS = {"maps_weather"}
ROUTE_TOOLS = {
    "maps_geo", "maps_regeocode", "maps_distance",
    "maps_direction_walking", "maps_direction_bicycling",
    "maps_direction_driving", "maps_direction_transit_integrated",
}

SEARCH_TOOLS = {"web_search_exa"}
EXTRACT_TOOLS = {"web_fetch_exa"}
SOURCE_TOOLS = SEARCH_TOOLS | EXTRACT_TOOLS

__all__ = ["PLACE_TOOLS", "DINING_TOOLS", "WEATHER_TOOLS", "ROUTE_TOOLS", "SEARCH_TOOLS", "EXTRACT_TOOLS", "SOURCE_TOOLS"]
