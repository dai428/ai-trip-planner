"""行程业务：候选池 → （大模型编排 / 规则生成）→ 逐日行程。

【v0.3 相对 v0.2 的变化】
    v0.2：一个城市的素材只来自"本地攻略 RAG 检索"或"高德临时查询"二选一，
          排序和排期全是写死的规则。
    v0.3：
      - 素材来源合并成**候选池**：本地攻略（RAG）+ 高德宽召回，带 origin 标记
      - 新增**大模型编排**：把候选池喂给模型，由它选点、定主题、写推荐理由
      - 大模型不可用 / 返回不合规 → **自动退回规则生成**，功能永不断
      - 行程带上 generator 字段，如实告诉用户"这份是谁排的"

【为什么是"两条路径"而不是"把规则删掉换成大模型"】
    规则路径不是过渡产物，它是**兜底**。大模型会超时、会被限流、
    会返回编造的地名；任何一次失败都不该让用户拿不到行程。
    两条路径共用同一个候选池与同一个输出契约，
    所以"谁排的"只影响内容质量，不影响功能完整性。
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import date, datetime

from app import config
from app.models.trip import Activity, BudgetItem, DayPlan, TripPlan, TripRequest
from app.services import guide_service, llm_service, poi_pool_service
from app.services.city_service import get_city

# ---------- 预算基线（元/人/天），按城市消费水平微调 ----------
_BASE_DAILY = {"住宿": 300.0, "餐饮": 120.0, "市内交通": 40.0, "门票": 60.0}
_CITY_FACTOR = {
    "北京": 1.25, "上海": 1.35, "三亚": 1.45,
    "杭州": 1.15, "厦门": 1.10, "丽江": 1.05,
    "大理": 1.0, "成都": 0.95, "重庆": 0.95, "西安": 0.9,
}
_CITY_TRANSFER = 260.0   # 城际交通（相邻两城之间一次的估算）

# ---------- 偏好 → 高德分类的语义映射 ----------
# 用户嘴里的偏好词，跟高德的分类体系**不是一个坐标系**：
# 用户说「自然」，高德说「风景名胜;公园广场;公园」。
# 这张表就是把两者对上的桥。
#
# 【它是启发式的，所以只用于排序，绝不用于过滤】
#   排得不准，顶多是顺序不理想；判错则会凭空砍掉一批候选。
#   在候选数量本来就紧张的场景里，"宁可排错，不可漏掉"是更安全的取舍。
_PREF_HINTS: dict[str, tuple[str, ...]] = {
    "自然": ("风景名胜", "公园", "自然", "山", "湖", "湿地", "森林", "温泉", "海"),
    "人文": ("博物馆", "文化", "古迹", "纪念", "历史", "寺", "宫", "故居", "艺术"),
    "亲子": ("公园", "游乐", "动物园", "科技馆", "海洋", "植物园", "乐园"),
    "摄影": ("风景", "古镇", "地标", "夜景", "观景", "桥", "塔"),
    "美食": ("餐饮", "小吃", "美食", "餐厅"),
    "购物": ("购物", "商场", "步行街", "市场"),
    "夜生活": ("酒吧", "夜市", "剧场", "演出"),
}

# 合并后每个分类保留的**下限**条数。
# 池子存的是"宽召回"（一城可达上百条），这里要做的是"够好的供给"，不是"堆量"：
# 排完序留太多，规则路径会拿到一堆同质条目，提示词也会被稀释。
#
# 【为什么是下限而不是定值】
#   固定 30 条在小行程里够用，但"单城 10 天 × 紧凑 4 个/天"需要 40 个景点，
#   30 条会让模型无点可选，最后几天只能重复或空缺。
#   所以实际额度 = max(下限, 这个城市真正需要的量 + 余量)，
#   保证"候选数随行程规模增长"这件事成立。
_CAP_FLOOR = {"景点": 24, "美食": 12, "住宿": 8}

# 攻略条目的排序加成。
# 【为什么要有加成，而不是纯按偏好分排】
#   攻略是人工整理的，信息密度和可靠性高于机器召回；
#   但如果纯按偏好分排，一条恰好不含偏好关键词的攻略条目会被高德的长尾挤下去。
#   给一个固定加成，等于表达"同等条件下优先用攻略"。
_GUIDE_BONUS = 3


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


def _pref_score(item: dict, preferences: list[str]) -> int:
    """按用户偏好给一个候选打分（只影响排序）。"""
    hay = " ".join(
        [item.get("name", ""), item.get("area", ""), item.get("desc", "")]
        + list(item.get("tags", []))
    )
    score = _GUIDE_BONUS if item.get("origin") == "guide" else 0
    for pref in preferences:
        for hint in _PREF_HINTS.get(pref, (pref,)):
            if hint and hint in hay:
                score += 1
    return score


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
        item["_quote"] = h.doc.text
        picked.append(item)
        if len(picked) >= limit:
            break
    return picked


def _merge_pool(city: str, preferences: list[str],
                need: dict[str, int] | None = None) -> dict[str, list[dict]]:
    """组装某城市的候选池：本地攻略（RAG） + 高德宽召回，再按偏好排序。

    need：该城市真正需要的各类条目数（由天数与节奏算出），用于确定截断额度。

    【为什么是"合并"而不是"二选一"】
        v0.2 是 `有攻略就用攻略，没攻略才找高德`。
        这会让两种城市的候选**数量差一个数量级** ——
        攻略城 12 个景点，高德城可能要多少有多少。
        大模型选点的质量直接取决于候选的丰富度，
        所以改成：攻略打底（权威、有原文），高德补长尾（广、新）。
    """
    pool: dict[str, list[dict]] = {"景点": [], "美食": [], "住宿": []}
    seen: dict[str, set[str]] = {k: set() for k in pool}

    # ① 本地攻略：权威、带原文引用，优先
    if guide_service.get_guide(city):
        for kind, limit in (("景点", 12), ("美食", 8), ("住宿", 3)):
            for item in _pick(city, kind, preferences, limit):
                # 住宿的攻略条目只有 area（推荐住在哪片），没有 name，用 area 兜底
                name = item.get("name") or item.get("area") or ""
                if not name or name in seen[kind]:
                    continue
                seen[kind].add(name)
                pool[kind].append({
                    "name": name,
                    "area": item.get("area", ""),
                    "desc": item.get("desc", ""),
                    "duration": item.get("duration", ""),
                    "tags": list(item.get("tags", [])),
                    "origin": "guide",
                    "_quote": item.get("_quote", ""),
                    "_source": f"本地攻略 · {city}",
                })

    # ② 高德候选池：补长尾
    for kind in ("景点", "美食", "住宿"):
        for c in poi_pool_service.candidates(city, kind):
            if c["name"] in seen[kind]:
                continue
            seen[kind].add(c["name"])
            pool[kind].append({
                "name": c["name"],
                "area": c.get("area", ""),
                # 住宿栏展示的是"住哪儿"，门牌号（「…2期20栋1103室」）没人看得懂，
                # 所以住宿不给 desc，让展示层只显示酒店名。
                "desc": "" if kind == "住宿" else (c.get("address") or ""),
                "duration": "",
                "tags": list(c.get("tags", []))[:3],
                "origin": "amap",
                "_source": c.get("_source", "高德POI"),
            })

    # ③ 统一按偏好重排并截断
    for kind in pool:
        cap = max(_CAP_FLOOR[kind], (need or {}).get(kind, 0))
        pool[kind].sort(key=lambda x: -_pref_score(x, preferences))
        pool[kind] = pool[kind][:cap]

    return pool


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


def _expected_sequence(cities: list[str], allocation: dict[str, int]) -> list[str]:
    """按天数分配展开出「第 N 天在哪个城市」的权威序列。

    【为什么以服务端为准，而不是听模型的】
        模型返回的 day/city 可能编号错位或缺天。
        行程的骨架（哪天在哪座城）必须跟用户选的天数分配严格一致，
        否则会出现"说好 3 天北京，结果排了 4 天"。
        所以骨架由我们定，模型只负责往里填内容。
    """
    seq: list[str] = []
    for city in cities:
        seq.extend([city] * allocation[city])
    return seq


# ==================== 路径一：规则生成（兜底 / 默认） ====================

def _days_by_rules(cities: list[str], allocation: dict[str, int],
                   pool: dict[str, dict[str, list[dict]]],
                   per_day: int) -> tuple[list[DayPlan], int]:
    """纯规则排期：景点按游标循环取，每天插一条美食。"""
    itinerary: list[DayPlan] = []
    day_no = 0
    attraction_cursor: Counter[str] = Counter()
    food_cursor: Counter[str] = Counter()
    attraction_total = 0

    for city in cities:
        for d in range(allocation[city]):
            day_no += 1
            attrs = pool[city]["景点"]
            foods = pool[city]["美食"]

            todays: list[dict] = []
            for _ in range(per_day):
                if not attrs:
                    break
                todays.append(attrs[attraction_cursor[city] % len(attrs)])
                attraction_cursor[city] += 1
            attraction_total += len(todays)

            activities = [
                Activity(name=a.get("name", "自由活动"), kind="景点",
                         note=_activity_note(a), source=a.get("_source"))
                for a in todays
            ]
            if foods:
                f = foods[food_cursor[city] % len(foods)]
                food_cursor[city] += 1
                activities.append(
                    Activity(name=f["name"], kind="美食",
                             note=f.get("area", ""), source=f.get("_source"))
                )

            lodging = pool[city]["住宿"][0]["name"] if pool[city]["住宿"] else None
            itinerary.append(DayPlan(
                day=day_no,
                city=city,
                theme=_theme_of(city, todays, first_day_in_city=(d == 0)),
                activities=activities,
                meals=[f["name"] for f in foods[:2]],
                lodging=lodging,
            ))

    return itinerary, attraction_total


# ==================== 路径二：大模型编排 ====================

def _fill_day(city: str, pool: dict[str, list[dict]],
              per_day: int, offset: int) -> list[dict]:
    """某天没有被模型排上有效内容时，从池子里按游标补满。

    补出来的条目要带上 note —— 池子里的 `_activity_note` 是给规则路径用的，
    这里得手动补上，否则模型漏排的那天会显示成一片空白的活动项。
    """
    attrs = pool["景点"]
    if not attrs:
        return []
    picked = [attrs[(offset + i) % len(attrs)] for i in range(min(per_day, len(attrs)))]
    return [{**a, "note": _activity_note(a)} for a in picked]


def _days_from_llm(days_raw: list[dict], seq: list[str],
                   pool: dict[str, dict[str, list[dict]]],
                   per_day: int) -> tuple[list[DayPlan], int]:
    """把模型排好的天，对齐到服务端确定的城市骨架上。

    逐天核对活动名是否仍在候选池里 —— llm_service 已核过一遍，
    这里是最后一道闸。两道不是冗余：池子在这两步之间可能已经被
    （并发的另一次请求触发的）刷新换掉，多核一次的成本几乎为零。
    """
    itinerary: list[DayPlan] = []
    attraction_total = 0
    offset: Counter[str] = Counter()

    for i, city in enumerate(seq):
        d = days_raw[i] if i < len(days_raw) else {}
        cpool = pool[city]
        legal = ({x["name"] for x in cpool["景点"]}
                 | {x["name"] for x in cpool["美食"]})

        acts = [a for a in (d.get("activities") or []) if a.get("name") in legal]
        if not acts:
            acts = _fill_day(city, cpool, per_day, offset[city])
        offset[city] += len(acts)

        activities = [
            Activity(name=a["name"], kind="景点",
                     note=a.get("note", ""), source=a.get("_source"))
            for a in acts
            if a.get("name") in {x["name"] for x in cpool["景点"]}
        ]
        # 模型可能把美食也列进 activities，保留它们，但类型标清楚
        for a in acts:
            if a.get("name") in {x["name"] for x in cpool["美食"]}:
                activities.append(Activity(name=a["name"], kind="美食",
                                           note=a.get("note", ""),
                                           source=a.get("_source")))
        attraction_total += len(activities)

        meals = d.get("meals") or [f["name"] for f in cpool["美食"][:2]]
        lodging = d.get("lodging") or (cpool["住宿"][0]["name"] if cpool["住宿"] else None)

        itinerary.append(DayPlan(
            day=i + 1,
            city=city,
            theme=d.get("theme") or _theme_of(city, acts, first_day_in_city=False),
            activities=activities,
            meals=meals[:2],
            lodging=lodging,
        ))

    return itinerary, attraction_total


# ==================== 入口 ====================

def generate_trip(request: TripRequest) -> TripPlan:
    """生成一份多城市行程。

    流程：建候选池 → 试大模型 → 不合规就退回规则生成。
    无论走哪条路，返回的都是同一个契约，且 itinerary 长度严格等于总天数。
    """
    cities = request.cities[: config.MAX_CITIES]
    days_total = min(request.days, config.MAX_DAYS)
    per_day = config.DAILY_ACTIVITY_BUDGET.get(request.pace, 3)
    start = request.start_date or date.today()

    allocation = _allocate_days(cities, days_total)
    seq = _expected_sequence(cities, allocation)

    # 每个城市组装一次候选池（内部命中缓存，不会每城重复外呼）。
    # need 决定截断额度：行程越长、节奏越紧，需要的候选就越多，
    # 否则会出现"排到第 8 天已无点可选"。
    pool = {
        city: _merge_pool(city, request.preferences, need={
            "景点": per_day * allocation[city] + 6,
            "美食": 2 * allocation[city] + 4,
            "住宿": _CAP_FLOOR["住宿"],
        })
        for city in cities
    }

    itinerary, attraction_total, generator, note = None, 0, "rule", None

    # ---- 先试大模型 ----
    if llm_service.enabled():
        days_raw, meta = llm_service.compose_days({
            "cities": cities,
            "allocation": allocation,
            "preferences": request.preferences,
            "pace": request.pace,
            "per_day": per_day,
            "pool_by_city": pool,
        })
        if days_raw:
            itinerary, attraction_total = _days_from_llm(days_raw, seq, pool, per_day)
            generator = "llm"
            note = f"{config.LLM_MODEL} · {meta['elapsed']}s"
        else:
            note = f"已降级为规则生成：{meta.get('reason')}"

    # ---- 兜底：规则生成 ----
    if itinerary is None:
        itinerary, attraction_total = _days_by_rules(cities, allocation, pool, per_day)
        if not llm_service.enabled():
            note = "未配置大模型，使用规则生成"

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
        generator=generator,
        generator_note=note,
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
        nights = max(allocation.get(city, 1), 1)   # 每城至少 1 晚
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
