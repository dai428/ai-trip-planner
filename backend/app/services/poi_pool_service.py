"""高德 POI 候选池：把"某个城市有什么可玩的"提前攒成一张表。

【为什么要做候选池，而不是查一次用一次】
    v0.2 的写法是"要生成行程了，临时去问高德" —— 每次生成都打一轮接口。
    这带来三个很实际的问题：

    1. **大模型没法选点。**
       让 LLM「从这几个候选里挑」，前提是候选得**先摆出来**。
       临时查询只能拿到三五条（因为一次只问一个关键词），
       候选太少，LLM 只能在极小的空间里挑，等于白搭。

    2. **延迟不可控。**
       一次行程要串好几个城市，每个城市几个分类 —— 几十次串行外呼，
       用户就盯着转圈。攒成池子后，第二次开始全是本地 SQLite 查询。

    3. **高德配额是有限的。**
       免费配额按日计量，反复问同一件事纯属浪费。

    所以：**宽召回 + 落盘 + 按需刷新**。
    池子负责"有什么"，排序和挑选交给上层 —— 各管一段，职责清晰。

【为什么池子里要记 origin】
    候选来自两个地方：本地攻略库（人工整理）和高德（机器召回）。
    两者的可信度、粒度、表达方式都不一样。
    记下来源，上层才能决定"优先用攻略里的，不够再用高德补"，
    也才能在界面上诚实地告诉用户"这条是哪来的"。
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta

from app import config
from app.services import amap_service

# ==================== 召回配方 ====================
# (kind, 关键词, 高德分类编码, 翻几页, 分类链白名单)
#
# 【为什么景点要搜两次】
#   110000「风景名胜」只有自然/人文景区，博物馆美术馆不在里面；
#   140000「科教文化」补上博物馆，但**同时混进大量学校**。
#   实测：大理 140000 第一页里就有「育才二小」「下关一中初中部」。
#   所以 140000 必须配白名单，只留真正能逛的场馆。
#
# 【为什么住宿只翻 1 页】
#   行程每天只需要 1 条住宿建议，25 条候选已经远超所需。
#   翻 3 页等于白烧配额。
_CULTURE_OK = (
    "博物馆", "展览", "美术馆", "纪念", "科技馆", "图书馆",
    "文化宫", "剧院", "剧场", "档案馆", "会展", "艺术",
)

# 名字里出现这些词，无论分类是什么都判为"不是去玩的地方"。
# 【为什么白名单之外还要一道黑名单】
#   高德的分类是"机构属性"而不是"游玩属性"：公园的停车场分类是"交通设施"，
#   但景区里的停车场很多会被归进风景名胜。分类判不出来，名字往往能。
_DENY_NAME = (
    "停车场", "售票处", "游客中心", "服务中心", "派出所", "加油站",
    "充电站", "卫生间", "公共厕所", "大门", "出入口", "地铁站",
    "研究所", "科学院", "研究院", "办事处", "管理处", "政务",
)

RECALL = (
    ("景点", "景点", "110000", 2, None),
    ("景点", "博物馆", "140000", 1, _CULTURE_OK),
    ("美食", "美食", "050000", 2, None),
    ("住宿", "酒店", "100000", 1, None),
)

KINDS = ("景点", "美食", "住宿")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS poi_pool (
    city       TEXT NOT NULL,
    kind       TEXT NOT NULL,
    name       TEXT NOT NULL,
    address    TEXT NOT NULL DEFAULT '',
    area       TEXT NOT NULL DEFAULT '',
    lng        REAL,
    lat        REAL,
    category   TEXT NOT NULL DEFAULT '',
    tags       TEXT NOT NULL DEFAULT '',
    origin     TEXT NOT NULL DEFAULT 'amap',
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (city, kind, name)
);

CREATE INDEX IF NOT EXISTS idx_poi_city_kind ON poi_pool(city, kind);
"""

# 负结果冷却：高德返回空（没配 Key / 该城无覆盖 / 网络抖动）时，
# 如果不用冷却，**每一个请求都会重新打一轮高德**，把配额烧光还拖慢响应。
# 进程内记住"刚试过且没拿到东西"，短时间内不再重试。
# 用内存而非建表：这个信息只在进程生命周期内有意义，重启后重试是正确行为。
_NEGATIVE_TTL = 600.0
_last_empty: dict[tuple[str, str], float] = {}


def _connect() -> sqlite3.Connection:
    config.DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """建表（幂等）。"""
    with _connect() as conn:
        conn.executescript(_SCHEMA)


# ==================== 写：召回并落盘 ====================

def _relevant(poi: amap_service.Poi, whitelist: tuple[str, ...] | None) -> bool:
    """分类链白名单 + 名字黑名单过滤。whitelist 为 None 表示只做黑名单。

    【关键细节：白名单只比 type_chain[1:]，跳过第一段】
        分类链形如「科教文化服务;科研机构;科研机构」，
        第一段是**大类**，后面才是具体属性。
        如果连第一段一起比，白名单里的"文化"会命中"科教文化服务"这个大类，
        于是**整个 140000 分类全部放行** —— 实测就是这么把
        「首都儿科研究所」「中国社会科学院」放进景点池的。
        只比子段，等于让白名单去挑具体属性，这才是它该干的事。
    """
    if any(d in poi.name for d in _DENY_NAME):
        return False
    if not whitelist:
        return True
    return any(ok in seg for seg in poi.type_chain[1:] for ok in whitelist)


def refresh(city: str, kinds: tuple[str, ...] = KINDS) -> dict[str, int]:
    """为一个城市宽召回 POI 并写入池子，返回各分类写入条数。

    只对**单城**操作：调用方按城市循环，失败的城市不影响其他城市。
    """
    if not amap_service.enabled():
        return {}

    now = datetime.now().isoformat()
    written: dict[str, int] = {}

    for kind, keyword, code, pages, whitelist in RECALL:
        if kind not in kinds:
            continue
        pois = amap_service.place_recall(keyword, city, types=code,
                                         pages=pages, page_size=config.POI_POOL_PAGE_SIZE)
        kept = [p for p in pois if _relevant(p, whitelist)]

        if not kept:
            # 记下负结果，短时间内不再打高德
            _last_empty[(city, kind)] = time.time()
            written[kind] = 0
            continue

        _last_empty.pop((city, kind), None)
        rows = [
            (city, kind, p.name, p.address, p.area, p.lng, p.lat,
             p.category, "|".join(p.type_chain), "amap", now)
            for p in kept
        ]
        with _connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO poi_pool "
                "(city, kind, name, address, area, lng, lat, category, tags, origin, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        written[kind] = len(rows)

    return written


# ==================== 读：取候选 ====================

def _fresh_rows(city: str, kind: str) -> list[sqlite3.Row]:
    cutoff = (datetime.now() - timedelta(days=config.POI_POOL_TTL_DAYS)).isoformat()
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM poi_pool WHERE city = ? AND kind = ? AND fetched_at >= ? "
            "ORDER BY name LIMIT ?",
            (city, kind, cutoff, config.POI_POOL_MAX_PER_CITY),
        ).fetchall()


def _is_stale(city: str, kind: str) -> bool:
    """池子里该分类是否需要（重新）召回。"""
    if _fresh_rows(city, kind):
        return False
    last = _last_empty.get((city, kind))
    if last is not None and time.time() - last < _NEGATIVE_TTL:
        return False          # 刚试过没拿到，先别试了
    return True


def ensure(city: str, kinds: tuple[str, ...] = KINDS) -> None:
    """按需刷新：缺哪类补哪类。已经有新鲜数据的分类不会重复请求。"""
    if not config.POI_POOL_ENABLED or not amap_service.enabled():
        return
    need = tuple(k for k in kinds if _is_stale(city, k))
    if need:
        refresh(city, need)


def candidates(city: str, kind: str | None = None,
               limit: int | None = None) -> list[dict]:
    """从池子取候选（先确保新鲜）。

    返回的是 dict 而不是 Row —— 上层要往里加 `_source` 之类的展示字段，
    直接给 sqlite3.Row 容易让人误以为能就地改。
    """
    kinds = (kind,) if kind else KINDS
    ensure(city, kinds)

    out: list[dict] = []
    for k in kinds:
        rows = _fresh_rows(city, k)
        if limit:
            rows = rows[:limit]
        for r in rows:
            out.append({
                "name": r["name"],
                "kind": r["kind"],
                "area": r["area"] or r["address"],
                "address": r["address"],
                "lng": r["lng"],
                "lat": r["lat"],
                "category": r["category"],
                "tags": [t for t in (r["tags"] or "").split("|") if t],
                "origin": r["origin"],
                "_source": f"高德POI · {r['category'] or r['kind']}",
            })
    return out


def pool_stats() -> dict:
    """候选池概览，给 /api/meta/poi 自检用。"""
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT city, kind, COUNT(*) AS n FROM poi_pool GROUP BY city, kind"
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) AS n FROM poi_pool").fetchone()["n"]
    except sqlite3.OperationalError:
        return {"enabled": config.POI_POOL_ENABLED, "total": 0, "cities": {}}

    by_city: dict[str, dict[str, int]] = {}
    for r in rows:
        by_city.setdefault(r["city"], {})[r["kind"]] = r["n"]

    return {
        "enabled": config.POI_POOL_ENABLED,
        "total": total,
        "ttl_days": config.POI_POOL_TTL_DAYS,
        "cities": dict(sorted(by_city.items())),
    }


def clear(city: str | None = None) -> int:
    """清空池子（可按城市）。用于强制重新召回。"""
    with _connect() as conn:
        if city:
            cur = conn.execute("DELETE FROM poi_pool WHERE city = ?", (city,))
        else:
            cur = conn.execute("DELETE FROM poi_pool")
    _last_empty.clear()
    return cur.rowcount
