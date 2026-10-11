"""高德地图服务：地理编码 / 驾车路径 / POI 检索。

【设计要点：能力可选，功能必达】
    整个模块遵循一条规则 —— **高德是可选增强，不是硬依赖**。
    配了 Key：坐标、里程、时长、POI 全部来自高德真实数据；
    没配 Key：用本地经纬度做球面距离估算，路网系数补偿直线与公路的差距。
    调用方永远拿得到结果，只是 `source` 字段会告诉它"这是估算还是实测"。

    这样做的好处是：本地开发、CI、断网演示都不会因为一个第三方服务挂掉而全线崩。
"""

from __future__ import annotations

import json
import math
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from app import config


# ==================== 数据结构 ====================

@dataclass
class GeoPoint:
    """一个带坐标的地点。"""

    lng: float
    lat: float
    name: str = ""
    city: str | None = None
    adcode: str | None = None
    formatted: str | None = None
    level: str | None = None        # 高德返回的粒度：省/市/区县/兴趣点/村庄…

    @property
    def lnglat(self) -> str:
        return f"{self.lng:.6f},{self.lat:.6f}"


@dataclass
class DriveResult:
    """两点之间的驾车结果。"""

    distance_km: float
    duration_hours: float
    source: str = "estimate"        # amap / estimate


@dataclass
class Poi:
    """高德 POI 检索结果。"""

    name: str
    address: str = ""
    lng: float | None = None
    lat: float | None = None
    category: str = ""
    tags: list[str] = field(default_factory=list)
    area: str = ""          # 所在区县（高德 adname），用于「位于XX区」这类表达
    type_chain: list[str] = field(default_factory=list)   # 完整分类链，供上层做二次筛选


# ==================== 简易 TTL 缓存 ====================
# 高德的免费配额按日计量，且同一路线会被反复查询 ——
# 缓存能显著降低调用量。用内存字典即可，不引 Redis（保持零部署）。
_cache: dict[tuple, tuple[float, object]] = {}


def _cached(key: tuple, producer):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < config.AMAP_CACHE_TTL:
        return hit[1]
    value = producer()
    if value is not None:
        _cache[key] = (now, value)
    return value


def cache_size() -> int:
    return len(_cache)


# ==================== 基础请求 ====================

def enabled() -> bool:
    """是否配置了高德 Key。"""
    return bool(config.AMAP_API_KEY)


def mode() -> str:
    """当前坐标/里程的数据来源模式。"""
    return "amap" if enabled() else "estimate"


def _get(path: str, **params):
    """请求高德接口。失败一律返回 None，由调用方走降级分支。"""
    if not enabled():
        return None

    clean = {k: v for k, v in params.items() if v not in (None, "", [])}
    clean["key"] = config.AMAP_API_KEY
    url = f"{config.AMAP_BASE_URL}{path}?{urllib.parse.urlencode(clean)}"

    try:
        with urllib.request.urlopen(url, timeout=config.AMAP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    # 高德用字符串 "1" 表示成功，"0" 表示失败（并带 info 说明）
    if str(data.get("status")) != "1":
        return None
    return data


# ==================== 本地几何计算（降级用） ====================

def haversine_km(a: GeoPoint, b: GeoPoint) -> float:
    """两点球面距离（公里）。"""
    r = 6371.0
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp = math.radians(b.lat - a.lat)
    dl = math.radians(b.lng - a.lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def estimate_drive(a: GeoPoint, b: GeoPoint) -> DriveResult:
    """无 Key 时的驾车估算：直线距离 × 路网系数 ÷ 平均时速。"""
    straight = haversine_km(a, b)
    road = straight * config.ROAD_FACTOR
    hours = road / config.AVG_SPEED_KMH if config.AVG_SPEED_KMH else 0.0
    return DriveResult(distance_km=round(road, 1), duration_hours=round(hours, 2), source="estimate")


# ==================== 对外能力 ====================

def geocode(address: str, city: str | None = None) -> GeoPoint | None:
    """地址/城市名 → 坐标。

    【为什么要挑 level，而不是取第一条】
        高德对任意输入都会尽量给一个答案。输入「不存在的城市xyz」，
        第一条结果是 level=村庄 的野点 —— 取它就会生成一条从那里出发的
        荒唐路线，用户还以为是自己输对了。
        所以这里跳过粒度不达标的结果，全都不达标就返回 None，
        由上层给出"无法识别"的明确提示。
    """
    address = (address or "").strip()
    if not address:
        return None

    def produce():
        data = _get("/geocode/geo", address=address, city=city)
        if not data:
            return None
        for g in data.get("geocodes") or []:
            level = g.get("level") or ""
            # level 缺失时不拦（高德偶尔不返回），有值就必须在允许清单内
            if level and level not in config.AMAP_ACCEPT_LEVELS:
                continue
            loc = (g.get("location") or "").split(",")
            if len(loc) != 2:
                continue
            try:
                lng, lat = float(loc[0]), float(loc[1])
            except ValueError:
                continue
            return GeoPoint(
                lng=lng, lat=lat,
                name=address,
                city=g.get("city") or None,
                adcode=g.get("adcode") or None,
                formatted=g.get("formatted_address") or None,
                level=level or None,
            )
        return None

    return _cached(("geo", address, city or ""), produce)


def driving(origin: GeoPoint, destination: GeoPoint) -> DriveResult:
    """两地驾车里程与时长。高德不可用则自动估算。"""
    key = ("drive", origin.lnglat, destination.lnglat)

    def produce():
        data = _get("/direction/driving",
                    origin=origin.lnglat, destination=destination.lnglat,
                    extensions="base", strategy=0)
        if not data:
            return None
        paths = (data.get("route") or {}).get("paths") or []
        if not paths:
            return None
        p = paths[0]
        try:
            return DriveResult(
                distance_km=round(int(p["distance"]) / 1000, 1),
                duration_hours=round(int(p["duration"]) / 3600, 2),
                source="amap",
            )
        except (KeyError, ValueError, TypeError):
            return None

    return _cached(key, produce) or estimate_drive(origin, destination)


def _parse_poi(p: dict) -> Poi | None:
    """把高德返回的一条 pois 元素转成 Poi。

    抽出来是因为 place_text / place_recall 都用它 —— 字段解析只写一遍，
    以后高德改字段名也只需要改这一个地方。
    """
    name = (p.get("name") or "").strip()
    if not name:
        return None

    lng = lat = None
    loc = (p.get("location") or "").split(",")
    if len(loc) == 2 and loc[0]:
        try:
            lng, lat = float(loc[0]), float(loc[1])
        except ValueError:
            lng = lat = None

    chain = [s for s in (p.get("type") or "").split(";") if s]
    return Poi(
        name=name,
        address=p.get("address") if isinstance(p.get("address"), str) else "",
        lng=lng, lat=lat,
        category=chain[0] if chain else "",
        tags=chain,
        area=(p.get("adname") or "").strip(),
        type_chain=chain,
    )


def place_text(keywords: str, city: str, limit: int = 8, types: str | None = None) -> list[Poi]:
    """关键词/分类检索 POI（用于没有本地攻略的城市）。

    【为什么还要 types】
        纯关键词搜「景点」会混进一堆「地名地址信息」（比如就返回一个"张家界"），
        用高德的分类编码（110000 风景名胜 / 050000 餐饮 / 100000 住宿）
        能把结果锁在真正想要的那一类上。
    """
    if not enabled():
        return []

    def produce():
        data = _get("/place/text", keywords=keywords, city=city, types=types,
                    citylimit="true", offset=min(limit, 25), page=1, extensions="base")
        if not data:
            return []
        out: list[Poi] = []
        for p in data.get("pois") or []:
            poi = _parse_poi(p)
            if poi:
                out.append(poi)
        return out

    return _cached(("poi", keywords, city, limit, types or ""), produce) or []


def place_recall(keyword: str, city: str, types: str | None = None,
                 pages: int = 2, page_size: int = 25) -> list[Poi]:
    """宽召回：同一条件翻页取多页，用于建「候选池」。

    【跟 place_text 的区别】
        place_text 是"够用就行"——只要几条，取第一页。
        place_recall 是"宁可多取"——候选池要的是**供给充足**，
        后续再按偏好排序、按分类过滤、交给大模型挑。
        把两个诉求塞进一个函数会让参数含义变得含糊，所以拆开。

    【翻页的边界】
        高德的 offset 上限是 25，page 理论上限 100，但实际总量有限：
        超过真实结果数时它仍返回 200，只是 pois 为空数组。
        所以一旦某页为空就立刻停 —— 不做无谓的请求，也避免死循环。
    """
    if not enabled():
        return []

    def produce():
        out: list[Poi] = []
        seen: set[str] = set()
        for page in range(1, max(pages, 1) + 1):
            data = _get("/place/text", keywords=keyword, city=city, types=types,
                        citylimit="true", offset=min(page_size, 25), page=page,
                        extensions="base")
            pois = (data or {}).get("pois") or []
            if not pois:
                break
            for raw in pois:
                poi = _parse_poi(raw)
                if poi and poi.name not in seen:
                    seen.add(poi.name)
                    out.append(poi)
        return out

    return _cached(("recall", keyword, city, types or "", pages, page_size), produce) or []


# 城市级行政区后缀。用来挡住「朝阳区」「陈巴尔虎旗」这类下级区划冒充城市。
_CITY_SUFFIX_RE = re.compile(r"(市|自治州|地区|盟)$")


def _is_city_level(name: str, district: str) -> bool:
    """判断输入提示里的一条到底是不是"城市"，而不是 POI。

    【判别依据来自实测，不是猜的】
        高德对**城市级**和 **POI** 都会返回 district，区别在层数：
            城市  张家界市   district=湖南省张家界市          ← 省 + 市，两层
            POI   张家界西站  district=湖南省张家界市永定区      ← 省 + 市 + 区，三层
        所以城市级那条的 district **一定以城市自己的名字结尾**，
        POI 的 district 则以区/县/旗结尾。

        光看"有没有 district"是错的 —— 城市级也有，会一条都筛不出来。
    """
    if not _CITY_SUFFIX_RE.search(name):
        return False
    # district 缺失时无法判定，宁可放行（有后缀兜底，误收概率很低）
    return not district or district.endswith(name)


def input_tips(keywords: str, city: str = "全国", limit: int = 10) -> list[dict]:
    """输入提示：给搜索框做"边打边猜"，只保留城市级候选。

    用 _is_city_level 把 POI（车站、停车场、医院）过滤掉 ——
    否则搜索框里会冒出"成都东站停车场"这种结果。
    """
    keywords = (keywords or "").strip()
    if not enabled() or not keywords:
        return []

    def produce():
        data = _get("/assistant/inputtips", keywords=keywords, city=city, datatype="poi")
        if not data:
            return []
        out: list[dict] = []
        seen: set[str] = set()
        for tip in data.get("tips") or []:
            name = (tip.get("name") or "").strip()
            adcode = tip.get("adcode") or ""
            if not name or not adcode:
                continue
            if not _is_city_level(name, tip.get("district") or ""):
                continue
            if name in seen:
                continue
            seen.add(name)

            loc = (tip.get("location") or "").split(",")
            lng = lat = None
            if len(loc) == 2 and loc[0]:
                try:
                    lng, lat = float(loc[0]), float(loc[1])
                except ValueError:
                    lng = lat = None

            out.append({"name": name, "adcode": adcode, "lng": lng, "lat": lat})
            if len(out) >= limit:
                break
        return out

    return _cached(("tips", keywords, city, limit), produce) or []


def status() -> dict:
    """给 /api/meta/amap 用的自检信息。"""
    return {
        "enabled": enabled(),
        "mode": mode(),
        "base_url": config.AMAP_BASE_URL if enabled() else None,
        "cache_entries": cache_size(),
        "fallback": "本地经纬度球面距离 + 路网系数估算" if not enabled() else None,
    }
