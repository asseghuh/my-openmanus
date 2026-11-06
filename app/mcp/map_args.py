"""校正地图类 MCP 参数，避免模型臆造城市或用地理编码去解析地铁站。"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional, Tuple

from app.logger import logger

_MAP_CITY_TOOLS = {"maps_geo", "maps_text_search"}
# 这类名称是 POI，maps_geo 常会误匹配成「红山区」等行政区
_POI_HINT = re.compile(
    r"(地铁|公交站|高铁站|火车站|机场|商场|公园|医院|学校|附近)"
)


def collect_user_text(memory) -> str:
    """拼接会话里所有用户原文，用于判断城市是否用户亲口说过。"""
    messages = getattr(memory, "messages", None) or []
    parts = []
    for msg in messages:
        role = getattr(msg.role, "value", msg.role)
        if role != "user":
            continue
        content = (getattr(msg, "content", None) or "").strip()
        if content:
            parts.append(content)
    return "\n".join(parts)


def city_mentioned(city: str, user_text: str) -> bool:
    """城市是否出现在用户原文中（兼容「深圳/深圳市」）。"""
    city = (city or "").strip()
    text = user_text or ""
    if not city or not text:
        return False
    if city in text:
        return True
    short = city[:-1] if city.endswith("市") else city
    return bool(short) and short in text


def sanitize_map_tool_call(
    name: str,
    args: Optional[Dict[str, Any]],
    user_text: str,
    available_tools: Optional[Iterable[str]] = None,
) -> Tuple[str, Dict[str, Any]]:
    """丢掉用户没提的 city；地铁站等 POI 改走关键词搜索。"""
    args = dict(args or {})
    available = set(available_tools or [])

    address = str(args.get("address") or "").strip()
    if (
        name == "maps_geo"
        and address
        and _POI_HINT.search(address)
        and "maps_text_search" in available
    ):
        logger.info(
            f"maps_geo 地址「{address}」像 POI，改用 maps_text_search 且不猜测城市"
        )
        name = "maps_text_search"
        args = {"keywords": address}

    if name in _MAP_CITY_TOOLS:
        city = str(args.get("city") or "").strip()
        if city and not city_mentioned(city, user_text):
            logger.warning(f"丢弃臆造的 city={city}（用户原文未提及），工具={name}")
            args.pop("city", None)

    return name, args
