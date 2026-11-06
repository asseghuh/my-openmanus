from app.mcp.map_args import sanitize_map_tool_call


def test_drop_guessed_nanjing_for_hongshan():
    name, args = sanitize_map_tool_call(
        "maps_geo",
        {"address": "红山地铁站", "city": "南京"},
        "红山地铁站附近有什么美食",
        ["maps_geo", "maps_text_search"],
    )
    assert name == "maps_text_search"
    assert args == {"keywords": "红山地铁站"}
    assert "city" not in args


def test_keep_explicit_shenzhen():
    name, args = sanitize_map_tool_call(
        "maps_text_search",
        {"keywords": "红山地铁站", "city": "深圳"},
        "深圳红山地铁站附近美食",
        ["maps_geo", "maps_text_search"],
    )
    assert name == "maps_text_search"
    assert args["city"] == "深圳"


def test_keep_city_suffix_variant():
    _, args = sanitize_map_tool_call(
        "maps_text_search",
        {"keywords": "红山地铁站", "city": "深圳市"},
        "去深圳红山地铁站",
        ["maps_text_search"],
    )
    assert args["city"] == "深圳市"


def test_structured_address_stays_geo():
    name, args = sanitize_map_tool_call(
        "maps_geo",
        {"address": "南山区腾讯大厦", "city": "深圳"},
        "深圳南山区腾讯大厦怎么走",
        ["maps_geo", "maps_text_search"],
    )
    assert name == "maps_geo"
    assert args["city"] == "深圳"
