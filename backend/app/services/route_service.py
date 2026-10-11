"""路线规划服务：出发地 → 目的地，并回答"中间值不值得停"。

【核心问题拆成三步】
    ① 这两点到底多远、开多久？      —— 高德实测，无 Key 用估算
    ② 值不值得中途停？              —— 里程/时长双阈值判定
    ③ 停哪儿？                      —— 在"路线走廊"里找顺路的已收录城市

【什么叫"顺路"】
    把路线拉成一条直线，算候选城市到这条直线的垂直距离：
      - 垂直距离小 → 城市就在路旁边，不用绕
      - 投影位置在 15%~85% → 才叫"中途"，贴出发地或目的地的都不算
      - 绕行里程 = 出发地→候选 + 候选→目的地 - 出发地→目的地
    三者一起看，才能判出"顺路且值得"的中途站。
"""

from __future__ import annotations

import math

from app import config
from app.models.route import RouteLeg, RoutePlan, WaypointSuggestion
from app.services import amap_service, guide_service
from app.services.amap_service import GeoPoint
from app.services.city_service import get_city, list_supported_cities


def _resolve(name: str) -> GeoPoint | None:
    """把地名解析成坐标：先查本地库，再走高德地理编码。"""
    name = (name or "").strip()
    if not name:
        return None

    meta = get_city(name)
    if meta:
        return GeoPoint(lng=meta["lng"], lat=meta["lat"], name=name, city=name)

    return amap_service.geocode(name)


def _project_onto_route(o: GeoPoint, d: GeoPoint, p: GeoPoint) -> tuple[float, float]:
    """把 p 投影到 o→d 直线上。

    返回 (位置比例 t, 垂直距离 km)。
    用等距圆柱近似把经纬度摊平成平面 —— 在几百公里尺度上误差可忽略。
    """
    k = math.cos(math.radians((o.lat + d.lat) / 2))

    def to_xy(pt: GeoPoint) -> tuple[float, float]:
        return ((pt.lng - o.lng) * 111.32 * k, (pt.lat - o.lat) * 110.57)

    ax, ay = to_xy(d)
    px, py = to_xy(p)
    l2 = ax * ax + ay * ay
    if l2 == 0:
        return 0.0, math.hypot(px, py)

    t = (px * ax + py * ay) / l2
    perp = math.hypot(px - t * ax, py - t * ay)
    return t, perp


def _find_waypoints(o: GeoPoint, d: GeoPoint, total_km: float) -> list[WaypointSuggestion]:
    """在路线走廊内找值得停留的已收录城市。"""
    origin_name, dest_name = o.name, d.name
    candidates: list[tuple[float, WaypointSuggestion]] = []

    for name in list_supported_cities():
        if name in (origin_name, dest_name):
            continue

        meta = get_city(name)
        if not meta:
            continue

        p = GeoPoint(lng=meta["lng"], lat=meta["lat"], name=name)
        progress, perp = _project_onto_route(o, d, p)

        # 必须在"中途"区间、且离路线不太远
        if not (config.CORRIDOR_MIN_PROGRESS <= progress <= config.CORRIDOR_MAX_PROGRESS):
            continue
        if perp > config.CORRIDOR_MAX_KM:
            continue

        # 绕行代价的基数必须与"基准里程"同量纲：
        #   基准 base.distance_km 是**公路里程**（高德实测），
        #   而 haversine 是**球面直线**。若拿直线和去减公路里程，
        #   直线和常常反而更小，绕行被压成 0，用户会误读成"绕路不要钱"。
        #   所以这里统一套用 estimate_drive 的路网系数，把它换算到公路尺度。
        detour = (
            amap_service.estimate_drive(o, p).distance_km
            + amap_service.estimate_drive(p, d).distance_km
            - total_km
        )
        if detour > config.DETOUR_MAX_KM:
            continue

        guide = guide_service.get_guide(name) or {}
        attractions = len(guide.get("attractions", []))
        tags = meta.get("tags", [])

        # 打分：绕路越少越好，可玩内容越多越好
        # 1 个景点折抵约 12 公里绕路 —— 这个系数是拍出来的经验值，写在这里方便调
        score = max(detour, 0) - attractions * 12

        reason = (
            f"位于全程约 {round(progress * 100)}% 处，"
            f"绕行仅 {round(max(detour, 0))} km，"
            f"本地攻略收录 {attractions} 个景点"
            + (f"（{'、'.join(tags[:3])}）" if tags else "")
        )

        candidates.append((
            score,
            WaypointSuggestion(
                city=name,
                detour_km=round(max(detour, 0), 1),
                progress=round(progress, 3),
                attractions=attractions,
                tags=tags,
                reason=reason,
            ),
        ))

    candidates.sort(key=lambda x: x[0])

    # 取分数最高的若干个：分数 = 绕行里程 − 景点数 × 12，越小越顺路越好玩。
    # 上限由 MAX_WAYPOINTS 控制 —— 中途塞太多城市，行程就散了。
    picked = [c for _, c in candidates][: config.MAX_WAYPOINTS]
    return picked


def _build_legs(names: list[str], points: dict[str, GeoPoint]) -> tuple[list[RouteLeg], float, float, str]:
    """按顺序算每一段的里程与时长。"""
    legs: list[RouteLeg] = []
    total_km = total_h = 0.0
    sources: set[str] = set()

    for a, b in zip(names, names[1:]):
        result = amap_service.driving(points[a], points[b])
        legs.append(RouteLeg(
            from_city=a, to_city=b,
            distance_km=result.distance_km,
            duration_hours=result.duration_hours,
            source=result.source,
        ))
        total_km += result.distance_km
        total_h += result.duration_hours
        sources.add(result.source)

    source = "amap" if sources == {"amap"} else "estimate"
    return legs, round(total_km, 1), round(total_h, 2), source


def plan_route(origin: str, destination: str, days: int) -> RoutePlan:
    """规划一条 出发地 → 目的地 的路线，并给出中途停留建议。

    抛 ValueError 表示地点无法解析（由接口层转成 400）。
    """
    o = _resolve(origin)
    if o is None:
        raise ValueError(f"无法识别出发地「{origin}」，请填写城市名（如 成都）")

    d = _resolve(destination)
    if d is None:
        raise ValueError(f"无法识别目的地「{destination}」，请填写城市名（如 大理）")

    if abs(o.lng - d.lng) < 1e-6 and abs(o.lat - d.lat) < 1e-6:
        raise ValueError("出发地与目的地相同，无需规划路线")

    base = amap_service.driving(o, d)
    needs_stop = (
        base.distance_km > config.STOP_DISTANCE_KM
        or base.duration_hours > config.STOP_DURATION_H
    )

    waypoints = _find_waypoints(o, d, base.distance_km) if needs_stop else []
    names = [o.name] + [w.city for w in waypoints] + [d.name]

    # 逐段计算（含中途城市时，各段单独查，里程比"直达 + 估算绕行"更准）
    points: dict[str, GeoPoint] = {o.name: o, d.name: d}
    for w in waypoints:
        meta = get_city(w.city)
        if meta:
            points[w.city] = GeoPoint(lng=meta["lng"], lat=meta["lat"], name=w.city)

    legs, total_km, total_h, source = _build_legs(names, points)

    # ---- 组装结论 ----
    # 注意：这里一律引用 total_km / total_h（分段求和），而不是 base（两点直达）。
    # 否则当推荐了中途城市时，"结论里的里程"和"返回的里程字段"会差出几十公里。
    if waypoints:
        advice = (
            f"全程 {total_km} km、约 {total_h} 小时，单日开到很累，建议中途在 "
            + "、".join(f"{w.city}（绕行 {w.detour_km} km）" for w in waypoints)
            + " 停留。"
        )
    elif needs_stop:
        advice = (
            f"全程 {total_km} km、约 {total_h} 小时，路程偏长，"
            "但沿途没有已收录攻略的城市。建议直飞/高铁，或在高速服务区拆分两天开。"
        )
    else:
        advice = (
            f"全程 {total_km} km、约 {total_h} 小时，一天内可直达，不必中途停留。"
        )

    # 一键采纳后要生成的行程城市：中途城市 + 目的地（出发地通常是自己家，不计入游玩城市）
    suggested = [w.city for w in waypoints] + [d.name]

    return RoutePlan(
        origin=o.name,
        destination=d.name,
        origin_point=o.lnglat,
        destination_point=d.lnglat,
        days=days,
        total_distance_km=total_km,
        total_duration_hours=total_h,
        source=source,
        needs_stop=needs_stop,
        advice=advice,
        legs=legs,
        waypoints=waypoints,
        suggested_cities=suggested,
    )
