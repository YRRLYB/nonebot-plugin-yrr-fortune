"""Pure, testable core for the YRR fortune plugin.

Nothing in this module imports NoneBot, so the algorithms and the HTML
render context can be unit-tested without starting a bot.
"""

from __future__ import annotations

import base64
import hashlib
import html
import math
import random
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Static data
# ---------------------------------------------------------------------------

FORTUNE_LEVELS: List[Tuple[str, str]] = [
    ("大凶", "★☆☆☆☆☆☆"),
    ("小凶", "★★☆☆☆☆☆"),
    ("末吉", "★★★☆☆☆☆"),
    ("小吉", "★★★★☆☆☆"),
    ("中吉", "★★★★★☆☆"),
    ("大吉", "★★★★★★☆"),
    ("天命", "★★★★★★★"),
]

RADAR_AXES: List[str] = ["财运", "事业", "健康", "人缘", "爱情", "智慧"]

COLORS: List[str] = ["红色", "蓝色", "绿色", "黄色", "紫色", "粉色", "橙色", "白色", "黑色", "金色"]
DIRECTIONS: List[str] = ["东", "南", "西", "北", "东南", "西南", "东北", "西北"]

TEMPLATE_NAMES: Dict[str, str] = {
    "1": "经典",
    "2": "杂志",
    "3": "写真",
    "4": "水墨",
    "5": "星际",
    "6": "漫画",
}

TEMPLATE_VIEWPORTS: Dict[str, Dict[str, int]] = {
    "1": {"width": 1000, "height": 1000},
    "2": {"width": 1080, "height": 1920},
    "3": {"width": 1080, "height": 1920},
    "4": {"width": 1080, "height": 1920},
    "5": {"width": 1080, "height": 1920},
    "6": {"width": 1080, "height": 1920},
}

THEME_COLORS: Dict[str, str] = {
    "大凶": "#ff4757",
    "小凶": "#ff6b7a",
    "末吉": "#ffa726",
    "小吉": "#26de81",
    "中吉": "#3742fa",
    "大吉": "#a55eea",
    "天命": "#fd79a8",
}

# Seven mutually exclusive activity dimensions.  A group may contribute at
# most one entry to either 宜 or 忌, and the same group may not appear on both
# sides, so a card can never say both "早睡早起" and "报复性熬夜".
ACTIVITY_DATA: Dict[str, List[Dict[str, str]]] = {
    "good": [
        {"name": "搞钱", "group": "action_state"},
        {"name": "认真工作", "group": "action_state"},
        {"name": "深度学习", "group": "action_state"},
        {"name": "彻底休息", "group": "action_state"},
        {"name": "光明正大摸鱼", "group": "action_state"},
        {"name": "出行", "group": "location"},
        {"name": "出门放风", "group": "location"},
        {"name": "亲近自然", "group": "location"},
        {"name": "快乐阿宅", "group": "location"},
        {"name": "社交拓展", "group": "social"},
        {"name": "老友聚会", "group": "social"},
        {"name": "高质量独处", "group": "social"},
        {"name": "陪陪家人", "group": "social"},
        {"name": "投资理财", "group": "finance"},
        {"name": "记账存钱", "group": "finance"},
        {"name": "搞点副业", "group": "finance"},
        {"name": "清空购物车", "group": "finance"},
        {"name": "早睡早起", "group": "schedule"},
        {"name": "午休充电", "group": "schedule"},
        {"name": "运动健身", "group": "body_diet"},
        {"name": "享受碳水", "group": "body_diet"},
        {"name": "吃顿好的", "group": "body_diet"},
        {"name": "健康饮食", "group": "body_diet"},
        {"name": "冥想放空", "group": "mind_ent"},
        {"name": "看书沉淀", "group": "mind_ent"},
        {"name": "追番看剧", "group": "mind_ent"},
        {"name": "打游戏", "group": "mind_ent"},
    ],
    "bad": [
        {"name": "内耗", "group": "action_state"},
        {"name": "做重大决策", "group": "action_state"},
        {"name": "过度劳累", "group": "action_state"},
        {"name": "懒惰摸鱼", "group": "action_state"},
        {"name": "好高骛远", "group": "action_state"},
        {"name": "出远门", "group": "location"},
        {"name": "去人多的地方", "group": "location"},
        {"name": "死宅不出", "group": "location"},
        {"name": "线上跟人杠", "group": "social"},
        {"name": "翻旧账", "group": "social"},
        {"name": "轻信大饼", "group": "social"},
        {"name": "社恐发作", "group": "social"},
        {"name": "冲动消费", "group": "finance"},
        {"name": "深夜网购", "group": "finance"},
        {"name": "借钱给别人", "group": "finance"},
        {"name": "赌运气", "group": "finance"},
        {"name": "报复性熬夜", "group": "schedule"},
        {"name": "熬夜修仙", "group": "schedule"},
        {"name": "严重拖延", "group": "schedule"},
        {"name": "久坐不动", "group": "body_diet"},
        {"name": "暴饮暴食", "group": "body_diet"},
        {"name": "深夜吃夜宵", "group": "body_diet"},
        {"name": "钻牛角尖", "group": "mind_ent"},
        {"name": "情绪失控", "group": "mind_ent"},
        {"name": "沉迷短视频", "group": "mind_ent"},
        {"name": "过度共情", "group": "mind_ent"},
    ],
}


# ---------------------------------------------------------------------------
# Algorithms
# ---------------------------------------------------------------------------


def get_lucky_activities(
    num: int = 2, rng: Optional[random.Random] = None
) -> Tuple[str, str]:
    """Pick ``num`` 宜 and ``num`` 忌 entries with no shared dimension."""

    rng = rng or random.Random()
    used_groups: set[str] = set()
    selected_good: List[str] = []
    selected_bad: List[str] = []

    good_pool = list(ACTIVITY_DATA["good"])
    bad_pool = list(ACTIVITY_DATA["bad"])
    rng.shuffle(good_pool)
    rng.shuffle(bad_pool)

    for item in good_pool:
        if len(selected_good) >= num:
            break
        if item["group"] not in used_groups:
            selected_good.append(item["name"])
            used_groups.add(item["group"])

    for item in bad_pool:
        if len(selected_bad) >= num:
            break
        if item["group"] not in used_groups:
            selected_bad.append(item["name"])
            used_groups.add(item["group"])

    return "、".join(selected_good), "、".join(selected_bad)


def _stable_seed(uid: str, today: str) -> int:
    digest = hashlib.sha256(f"yrr-fortune:{today}:{uid}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def generate_fortune(
    uid: str, today: str, texts: Mapping[str, Sequence[str]]
) -> Dict[str, Any]:
    """Generate one deterministic daily fortune for ``uid`` on ``today``.

    A private ``random.Random`` instance is used so the plugin never touches
    the process-wide RNG (the old code called ``random.seed`` globally, which
    changed the output of every other plugin in the same bot).
    """

    rng = random.Random(_stable_seed(uid, today))
    level, stars = rng.choice(FORTUNE_LEVELS)
    base = stars.count("★") / 10.0
    radar_data = [
        {"axis": axis, "value": max(0.1, min(1.0, base + rng.uniform(-0.2, 0.2)))}
        for axis in RADAR_AXES
    ]
    good_acts, bad_acts = get_lucky_activities(2, rng)

    level_texts = texts.get(level) or ()
    fortune_text = rng.choice(level_texts) if level_texts else "今天也要好好生活。"

    return {
        "fortune": f"{level}|{stars}",
        "fortune_date": today,
        "fortune_text": fortune_text,
        "radar_data": radar_data,
        "lucky_items": {
            "color": rng.choice(COLORS),
            "number": rng.randint(1, 99),
            "direction": rng.choice(DIRECTIONS),
            "good": good_acts,
            "bad": bad_acts,
        },
    }


def is_fresh(user: Optional[Mapping[str, Any]], today: str) -> bool:
    """Whether a stored record already holds today's complete fortune."""

    if not user:
        return False
    if user.get("fortune_date") != today:
        return False
    if not user.get("fortune") or user.get("fortune") == "?":
        return False
    if not user.get("radar_data"):
        return False
    return bool(user.get("lucky_items"))


def normalize_user(user: Dict[str, Any], uid: str) -> Dict[str, Any]:
    """Fill missing fields without discarding an existing fortune."""

    user.setdefault("qq_number", uid)
    user.setdefault("fortune", "?")
    user.setdefault("fortune_date", "")
    user.setdefault("fortune_text", "")
    user.setdefault("radar_data", [])
    user.setdefault("lucky_items", {})
    if user.get("template_choice") not in TEMPLATE_NAMES:
        user["template_choice"] = "1"
    return user


def split_fortune(value: str) -> Tuple[str, str]:
    try:
        level, stars = str(value).split("|", 1)
    except (ValueError, AttributeError):
        return "末吉", "★★★☆☆☆☆"
    if level not in THEME_COLORS:
        level = "末吉"
    if len(stars) != 7:
        stars = dict(FORTUNE_LEVELS).get(level, "★★★☆☆☆☆")
    return level, stars


def star_percent(stars: str) -> float:
    return stars.count("★") / 7.0 * 100.0


# ---------------------------------------------------------------------------
# SVG / data-URI assets
# ---------------------------------------------------------------------------


def generate_radar_svg(radar_data: Sequence[Mapping[str, Any]], theme_color: str) -> str:
    """Render the six-axis radar chart used by every HTML template."""

    data = [
        {"axis": str(item.get("axis", "")), "value": float(item.get("value", 0.1))}
        for item in radar_data
        if item.get("axis")
    ]
    if len(data) < 3:
        return ""

    angles = [math.radians(i * (360.0 / len(data)) - 90) for i in range(len(data))]
    cosines = [math.cos(angle) for angle in angles]
    sines = [math.sin(angle) for angle in angles]

    def point(index: int, value: float, radius: float) -> Tuple[float, float]:
        return (110 + value * radius * cosines[index], 110 + value * radius * sines[index])

    vertices = [point(i, item["value"], 69) for i, item in enumerate(data)]
    path = "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in vertices) + " Z"

    grid = "".join(
        f'<circle cx="110" cy="110" r="{i * 13.75:.2f}" fill="none" '
        f'stroke="{theme_color}" stroke-opacity="0.35" stroke-width="1"/>'
        for i in range(1, 6)
    )
    axes = "".join(
        f'<line x1="110" y1="110" x2="{110 + 69 * cosines[i]:.2f}" '
        f'y2="{110 + 69 * sines[i]:.2f}" stroke="{theme_color}" '
        f'stroke-opacity="0.45" stroke-width="1"/>'
        for i in range(len(data))
    )
    circles = "".join(
        f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="#fff" '
        f'stroke="{theme_color}" stroke-width="1.5"/>'
        for x, y in vertices
    )
    labels = "".join(
        f'<text x="{110 + 81 * cosines[i]:.2f}" y="{110 + 81 * sines[i] + 4:.2f}" '
        f'font-size="12" font-weight="bold" fill="#333" text-anchor="middle">'
        f"{html.escape(item['axis'])}</text>"
        for i, item in enumerate(data)
    )

    return (
        '<svg width="220" height="220" viewBox="0 0 220 220" '
        'xmlns="http://www.w3.org/2000/svg">'
        "<defs>"
        f'<radialGradient id="radarGradient" cx="50%" cy="50%" r="50%">'
        f'<stop offset="0%" stop-color="{theme_color}" stop-opacity="0.8"/>'
        f'<stop offset="55%" stop-color="{theme_color}" stop-opacity="0.55"/>'
        f'<stop offset="100%" stop-color="{theme_color}" stop-opacity="0.25"/>'
        "</radialGradient>"
        f'<linearGradient id="lineGradient" x1="0%" y1="0%" x2="100%" y2="100%">'
        f'<stop offset="0%" stop-color="{theme_color}" stop-opacity="1"/>'
        f'<stop offset="100%" stop-color="{theme_color}" stop-opacity="0.8"/>'
        "</linearGradient>"
        "</defs>"
        f"{grid}{axes}"
        f'<path d="{path}" fill="url(#radarGradient)" stroke="url(#lineGradient)" '
        'stroke-width="2"/>'
        f"{circles}{labels}"
        "</svg>"
    )


def _svg_data_uri(svg: str) -> str:
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def background_data_uri() -> str:
    """A neutral gradient used when the photo API is unreachable."""

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1920">'
        "<defs>"
        '<linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#111827"/>'
        '<stop offset="0.45" stop-color="#1f2937"/>'
        '<stop offset="1" stop-color="#312e81"/>'
        "</linearGradient>"
        "</defs>"
        '<rect width="1080" height="1920" fill="url(#g)"/>'
        '<circle cx="180" cy="330" r="280" fill="#ffffff" opacity="0.045"/>'
        '<circle cx="880" cy="1480" r="360" fill="#a55eea" opacity="0.07"/>'
        '<circle cx="760" cy="240" r="140" fill="#26de81" opacity="0.05"/>'
        "</svg>"
    )
    return _svg_data_uri(svg)


def avatar_data_uri(uid: str) -> str:
    """Placeholder avatar drawn as SVG when the QQ avatar cannot be fetched."""

    palette = ["#a55eea", "#3742fa", "#26de81", "#ffa726", "#fd79a8", "#00cec9"]
    digest = hashlib.md5(str(uid).encode("utf-8")).hexdigest()
    color = palette[int(digest[:8], 16) % len(palette)]
    initials = html.escape(str(uid)[-2:] or "?")
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200">'
        f'<rect width="200" height="200" rx="24" fill="{color}"/>'
        '<text x="100" y="122" font-family="sans-serif" font-size="76" '
        'font-weight="700" fill="#ffffff" text-anchor="middle">'
        f"{initials}</text>"
        "</svg>"
    )
    return _svg_data_uri(svg)


# ---------------------------------------------------------------------------
# Render context
# ---------------------------------------------------------------------------

CONTEXT_KEYS = (
    "font_face_rule",
    "theme_color",
    "theme_color_dark",
    "theme_color_transparent",
    "background_image",
    "avatar_url",
    "qq_number",
    "level",
    "stars",
    "star_percent",
    "fortune_text",
    "radar_svg",
    "lucky_color",
    "lucky_number",
    "lucky_direction",
    "good_activities",
    "bad_activities",
    "current_time",
    "current_date",
)


def build_render_context(
    *,
    user: Mapping[str, Any],
    level: str,
    stars: str,
    background_image: str,
    avatar_url: str,
    font_face_rule: str = "",
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build the exact placeholder set consumed by the six HTML templates."""

    now = now or datetime.now()
    theme_color = THEME_COLORS.get(level, "#a55eea")
    lucky = user.get("lucky_items") or {}
    radar = user.get("radar_data") or []
    fortune_text = str(user.get("fortune_text") or "今天也要好好生活。")

    return {
        "font_face_rule": font_face_rule,
        "theme_color": theme_color,
        "theme_color_dark": theme_color + "CC",
        "theme_color_transparent": theme_color + "66",
        "background_image": background_image,
        "avatar_url": avatar_url,
        "qq_number": str(user.get("qq_number") or ""),
        "level": level,
        "stars": stars,
        "star_percent": star_percent(stars),
        "fortune_text": fortune_text,
        "radar_svg": generate_radar_svg(radar, theme_color),
        "lucky_color": str(lucky.get("color", "未知")),
        "lucky_number": str(lucky.get("number", "?")),
        "lucky_direction": str(lucky.get("direction", "未知")),
        "good_activities": str(lucky.get("good", "无")),
        "bad_activities": str(lucky.get("bad", "无")),
        "current_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "current_date": now.strftime("%Y-%m-%d"),
    }


def dedupe_texts(texts: Mapping[str, Sequence[str]]) -> Dict[str, List[str]]:
    """Drop repeated lines that accumulated in the original config."""

    return {level: list(dict.fromkeys(lines)) for level, lines in texts.items()}
