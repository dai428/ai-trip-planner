"""城市服务：读取"支持哪些目的地"，以及搜索城市。

【两条数据来源】
    本地库   10 座已收录城市 —— 有攻略、有坐标、有建议天数，生成的行程质量最高
    高德     全国任意城市 —— 没有攻略，但可以靠 POI 检索动态生成（需配 Key）
    搜索时先给本地命中，再用高德补全，用户一眼能看出哪条"有攻略支撑"。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache

from app import config
from app.models.route import CitySuggestion
from app.services import amap_service

# 行政区后缀，用于把「成都市」归一成「成都」
_SUFFIX_RE = re.compile(r"(自治州|地区|市辖区|盟|市|县|区)$")
_ETHNIC_RE = re.compile(
    r"(回族|藏族|白族|彝族|壮族|苗族|土家族|蒙古族|朝鲜族|满族|哈尼族|傣族|黎族|"
    r"布依族|侗族|瑶族|傈僳族|纳西族|拉祜族|佤族|景颇族|羌族|水族|东乡族|裕固族).*$"
)


@lru_cache(maxsize=1)
def _load_raw() -> tuple[dict, ...]:
    """读取城市元数据（只读一次，返回不可变元组防止被误改）。"""
    with config.CITIES_FILE.open(encoding="utf-8") as f:
        return tuple(json.load(f)["cities"])


def normalize_city_name(raw: str) -> str:
    """把高德返回的行政区名归一成常用叫法。

    「大理白族自治州」→「大理」，「成都市」→「成都」。
    """
    name = (raw or "").strip()
    if not name:
        return name
    name = _ETHNIC_RE.sub("", name)
    name = _SUFFIX_RE.sub("", name)
    return name or raw


def list_supported_cities() -> list[str]:
    """城市名列表，保持数据文件里的顺序。"""
    return [c["name"] for c in _load_raw()]


def list_cities_meta() -> list[dict]:
    """城市元数据（标签 / 最佳季节 / 建议天数 / 坐标），给前端做选择器。"""
    return [dict(c) for c in _load_raw()]


def get_city(name: str) -> dict | None:
    """按名称取单个城市的元数据，不存在返回 None。"""
    for c in _load_raw():
        if c["name"] == name:
            return dict(c)
    return None


def is_supported(name: str) -> bool:
    """该城市是否在支持列表内。"""
    return get_city(name) is not None


def search_cities(query: str, limit: int = 12) -> list[CitySuggestion]:
    """搜索城市：本地库命中优先，高德补全其余。"""
    q = (query or "").strip()
    # 查询词本身也要归一：「大理白族自治州」得能匹配到「大理」。
    # 不做这一步，用户照着高德/地图上抄来的全称就会什么都搜不到。
    q_norm = normalize_city_name(q)

    def terms() -> list[str]:
        # 原样与归一两版都参与匹配，任一命中即可
        out = [q] if q else []
        if q_norm and q_norm != q:
            out.append(q_norm)
        return out

    def matches(c: dict) -> bool:
        if not q:
            return True
        haystack = " ".join([c["name"], c.get("province", ""), *c.get("tags", [])])
        return any(t in haystack for t in terms())

    results: list[CitySuggestion] = [
        CitySuggestion(
            name=c["name"],
            province=c.get("province"),
            tags=list(c.get("tags", [])),
            has_guide=True,
            avg_days=c.get("avg_days"),
            source="library",
        )
        for c in _load_raw() if matches(c)
    ][:limit]

    # 高德补全：把本地库没有的全国城市也搜出来
    if q and len(q) >= 2 and len(results) < limit:
        known = {r.name for r in results}
        local_names = set(list_supported_cities())

        for tip in amap_service.input_tips(q, limit=limit):
            name = normalize_city_name(tip["name"])
            # 本地库已有的不再重复列出 —— 高德那条没有攻略，列出来只会让用户选错
            if not name or name in known or name in local_names:
                continue
            results.append(CitySuggestion(
                name=name, province=None, tags=[],
                has_guide=False, avg_days=None, source="amap",
            ))
            known.add(name)
            if len(results) >= limit:
                break

    return results
