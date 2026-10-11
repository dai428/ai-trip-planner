"""行程业务：多城市串联 + 基于攻略检索的行程生成。

【v0.2 相对 v0.1 的变化】
    v0.1：一个城市、mock 数据、activities 是字符串列表。
    v0.2：
      - 支持多城市按顺序串联，天数自动分配
      - 每天的景点不再拍脑袋，而是走 RAG 检索攻略库
      - 每个活动都挂上 source（命中的攻略原文），前端可展示"凭什么推荐它"
      - 附带预算拆分

【为什么检索要带上"偏好"】
    「亲子」和「摄影」两个偏好，去成都该去的完全不是同一个地方。
    把偏好塞进 query，检索器就会把"熊猫基地"顶上来 —— 这是 RAG 相对
    "从列表里顺序取"最直接的收益。
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import date, datetime

from app import config
from app.models.trip import Activity, BudgetItem, DayPlan, TripPlan, TripRequest
from app.services import amap_service, guide_service
from app.services.city_service import get_city

# ---------- 预算基线（元/人/天），按城市消费水平微调 ----------
_BASE_DAILY = {"住宿": 300.0, "餐饮": 120.0, "市内交通": 40.0, "门票": 60.0}
_CITY_FACTOR = {
    "北京": 1.25, "上海": 1.35, "三亚": 1.45,
    "杭州": 1.15, "厦门": 1.10, "丽江": 1.05,
    "大理": 1.0, "成都": 0.95, "重庆": 0.95, "西安": 0.9,
}
_CITY_TRANSFER = 260.0   # 城际交通（相邻两城之间一次的估算）

# 无本地攻略的城市用它兜底：(关键词, 高德分类编码)
_POI_TYPES = {
    "景点": ("风景名胜", "110000"),
    "美食": ("餐饮", "050000"),
    "住宿": ("住宿", "100000"),
}


def _allocate_days(cities: list[str], total_days: int) -> dict[str, int]:
    """把总天数尽量平均地分给各城市，余数从前往后补。

    例：3 城 8 天 → 3/3/2。
    """
    n = len(cities)
    base, rest = divmod(total_days, n)
    return {
        city: base + (1 if i < rest else 0)
        for i, city in enumerate(cities)
    }


def _pick(city: str, kind: str, preferences: list[str], limit: int) -> list[dict]:
    """走 RAG 从攻略库里挑指定类型的素材，返回带出处的字典列表。

    query 里带上偏好标签，让检索器按"这个人想玩什么"来排序。
    """
    pref = " ".join(preferences)
    query = f"{city} {kind} {pref}".strip()
    hits = guide_service.retrieve(query, city=city, top_k=max(limit * 3, 8))

    picked: list[dict] = []
    seen: set[str] = set()
    for h in hits:
        if h.doc.kind != kind:
            continue
        name = h.doc.title
        if name in seen:
            continue
        seen.add(name)
        item = dict(h.doc.meta)
        item["_source"] = h.source
        item["_quote"] = h.doc.text
        picked.append(item)
        if len(picked) >= limit:
            break
    return picked


def _poi_pool(city: str, kind: str, limit: int) -> list[dict]:
    """用高德 POI 给"没有本地攻略"的城市凑素材。

    【为什么必须做这件事】
        城市搜索能搜到全国任意城市，但攻略库只收录了 10 座。
        如果对没攻略的城市什么都不返回，用户会拿到一份**天天空白**的行程 ——
        这比直接报"不支持该城市"更糟，因为它看起来像是生成成功了。

        所以这里用高德兜底，并且**逐条标注出处**（高德POI · 分类），
        让用户一眼看出"这条不是本地攻略里来的"，而不是被蒙在鼓里。
    """
    keyword, types = _POI_TYPES.get(kind, ("", None))
    pois = amap_service.place_text(keyword, city, limit=limit, types=types)

    out: list[dict] = []
    for p in pois:
        # 住宿栏展示的是"住哪儿"，POI 的 address 是门牌号（「…2期20栋1103室」），
        # 直接显示没人看得懂；换成酒店名更接近攻略里"推荐住 XX 区域"的表达。
        area = p.name if kind == "住宿" else p.address
        out.append({
            "name": p.name,
            "area": area,
            "desc": p.address if kind == "住宿" else "",
            "duration": "",
            "tags": [p.category] if p.category else [],
            "_source": f"高德POI · {p.category or kind}",
        })
    return out


def _pool_for(city: str, preferences: list[str]) -> dict[str, list[dict]]:
    """取某城市的素材池：有攻略走 RAG 检索，没有就走高德 POI。"""
    if guide_service.get_guide(city):
        return {
            "景点": _pick(city, "景点", preferences, limit=12),
            "美食": _pick(city, "美食", preferences, limit=8),
            "住宿": _pick(city, "住宿", preferences, limit=3),
        }
    return {
        "景点": _poi_pool(city, "景点", 18),
        "美食": _poi_pool(city, "美食", 10),
        "住宿": _poi_pool(city, "住宿", 3),
    }


def _activity_note(a: dict) -> str:
    """拼活动备注。缺字段时不留悬空的「建议」。

    景点没有 duration 时，「地址 ｜ 建议 」这种半截文案会直接显示给用户。
    """
    parts = [a.get("area") or ""]
    if a.get("duration"):
        parts.append(f"建议 {a['duration']}")
    return " ｜ ".join(p for p in parts if p)


def _theme_of(city: str, attractions: list[dict], first_day_in_city: bool) -> str:
    """给一天起个主题名，让行程读起来像「有人替你安排过」，而不是列表。"""
    if first_day_in_city:
        return f"{city} · 抵达与漫步"
    tags: Counter[str] = Counter()
    for a in attractions:
        for t in a.get("tags", []):
            tags[t] += 1
    top = [t for t, _ in tags.most_common(2) if t not in ("必去",)]
    return f"{city} · {'与'.join(top)}" if top else f"{city} · 深度游玩"


def generate_trip(request: TripRequest) -> TripPlan:
    """生成一份多城市行程。"""
    cities = request.cities[: config.MAX_CITIES]
    days_total = min(request.days, config.MAX_DAYS)
    per_day = config.DAILY_ACTIVITY_BUDGET.get(request.pace, 3)
    start = request.start_date or date.today()

    allocation = _allocate_days(cities, days_total)

    # 每个城市预先取一次素材池（避免每天重复检索）
    pool: dict[str, dict[str, list[dict]]] = {
        city: _pool_for(city, request.preferences) for city in cities
    }

    itinerary: list[DayPlan] = []
    day_no = 0
    attraction_cursor: Counter[str] = Counter()
    food_cursor: Counter[str] = Counter()
    attraction_total = 0

    for city in cities:
        city_days = allocation[city]
        guide = guide_service.get_guide(city) or {}

        for d in range(city_days):
            day_no += 1
            attrs = pool[city]["景点"] or [
                {"name": x["name"], "tags": x.get("tags", []), "desc": x.get("desc", "")}
                for x in guide.get("attractions", [])
            ]
            foods = pool[city]["美食"] or [
                {"name": x["name"], "desc": x.get("desc", "")}
                for x in guide.get("foods", [])
            ]

            todays: list[dict] = []
            for _ in range(per_day):
                if not attrs:
                    break
                i = attraction_cursor[city] % len(attrs)
                todays.append(attrs[i])
                attraction_cursor[city] += 1
            attraction_total += len(todays)

            activities = [
                Activity(
                    name=a.get("name", "自由活动"),
                    kind="景点",
                    note=_activity_note(a),
                    source=a.get("_source"),
                )
                for a in todays
            ]
            # 每天加一条美食体验，让行程有烟火气
            if foods:
                f = foods[food_cursor[city] % len(foods)]
                food_cursor[city] += 1
                activities.append(
                    Activity(name=f["name"], kind="美食",
                             note=f.get("area", ""), source=f.get("_source"))
                )

            meals = [f["name"] for f in foods[:2]] if foods else []
            lodging = None
            if pool[city]["住宿"]:
                lodging = pool[city]["住宿"][0].get("area")
            elif guide.get("lodging"):
                lodging = guide["lodging"][0].get("area")

            itinerary.append(
                DayPlan(
                    day=day_no,
                    city=city,
                    theme=_theme_of(city, todays, first_day_in_city=(d == 0)),
                    activities=activities,
                    meals=meals,
                    lodging=lodging,
                )
            )

    budget = _budget(cities, allocation, attraction_total, request.budget)

    return TripPlan(
        id=uuid.uuid4().hex[:12],
        cities=cities,
        days=days_total,
        start_date=start,
        pace=request.pace,
        itinerary=itinerary,
        budget=budget,
        created_at=datetime.now(),
    )


def _budget(cities: list[str], allocation: dict[str, int],
            attraction_count: int, cap: float | None) -> list[BudgetItem]:
    """按城市消费水平估算预算拆分。

    【诚实声明】这是估算，不是报价：门票按人均、住宿按中档酒店，
    真实花费受季节与个人选择影响很大。前端会把这句提示一并展示。
    """
    lodging = food = local = 0.0
    for city in cities:
        f = _CITY_FACTOR.get(city, 1.0)
        nights = max(allocation.get(city, 1) - 0, 1)   # 每城至少 1 晚
        lodging += _BASE_DAILY["住宿"] * f * nights
        food += _BASE_DAILY["餐饮"] * f * allocation.get(city, 1)
        local += _BASE_DAILY["市内交通"] * f * allocation.get(city, 1)

    tickets = _BASE_DAILY["门票"] * attraction_count * 0.6
    transfer = _CITY_TRANSFER * max(len(cities) - 1, 0)

    items = [
        BudgetItem(label="住宿", amount=round(lodging, 2)),
        BudgetItem(label="餐饮", amount=round(food, 2)),
        BudgetItem(label="市内交通", amount=round(local, 2)),
        BudgetItem(label="门票", amount=round(tickets, 2)),
    ]
    if transfer:
        items.append(BudgetItem(label="城际交通", amount=round(transfer, 2)))
    return items


def total_budget(plan: TripPlan) -> float:
    return round(sum(b.amount for b in plan.budget), 2)
