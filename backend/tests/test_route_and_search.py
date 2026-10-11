"""路线规划与城市搜索的测试。

【为什么整份文件都把高德 Key 关掉】
    高德是**可选增强**。若测试依赖真实网络，会变成：
      - 没配 Key 的机器上必挂
      - 配了 Key 的机器上慢、且有配额与限流风险
      - 结果随实时路况变化，断言无法稳定
    所以这里统一走"无 Key 降级"分支 —— 恰好也验证了降级路径本身是可用的。
    高德那条分支用 monkeypatch 注入假数据来覆盖，同样不碰网络。
"""

import pytest

from app import config
from app.services import amap_service


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """强制走本地估算分支，并清空跨用例的缓存。"""
    monkeypatch.setattr(config, "AMAP_API_KEY", "")
    monkeypatch.setattr(amap_service, "_cache", {})


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.api.main import app

    with TestClient(app) as c:
        yield c


# ==================== 几何基础 ====================

def test_haversine_known_distance():
    """成都 → 重庆 的球面距离应在 260~280 km 量级。"""
    a = amap_service.GeoPoint(lng=104.0665, lat=30.5728)
    b = amap_service.GeoPoint(lng=106.5516, lat=29.5630)
    d = amap_service.haversine_km(a, b)
    assert 260 < d < 280


def test_estimate_applies_road_factor():
    """估算里程必须大于直线距离 —— 路网一定比直线绕。"""
    a = amap_service.GeoPoint(lng=104.0665, lat=30.5728)
    b = amap_service.GeoPoint(lng=106.5516, lat=29.5630)
    est = amap_service.estimate_drive(a, b)
    assert est.distance_km > amap_service.haversine_km(a, b)
    assert est.source == "estimate"


# ==================== 降级：无 Key 也要能规划 ====================

def test_route_works_without_amap_key():
    """没配 Key 也必须能出结果，只是 source 标记为 estimate。"""
    from app.services import route_service

    plan = route_service.plan_route("成都", "重庆", 3)
    assert plan.source == "estimate"
    assert plan.total_distance_km > 0
    assert plan.total_duration_hours > 0
    assert plan.legs and plan.legs[0].source == "estimate"


def test_short_hop_does_not_need_stop():
    """成都 → 重庆 约 300 km，一天直达，不该劝人中途停。"""
    from app.services import route_service

    plan = route_service.plan_route("成都", "重庆", 3)
    assert plan.needs_stop is False
    assert plan.waypoints == []
    assert "不必中途停留" in plan.advice


def test_long_hop_suggests_corridor_waypoints():
    """北京 → 大理 横跨半个中国，西安/成都都落在路线走廊内。"""
    from app.services import route_service

    plan = route_service.plan_route("北京", "大理", 7)
    assert plan.needs_stop is True

    cities = [w.city for w in plan.waypoints]
    assert "西安" in cities

    # 每个中途站都要满足"在中间位置 + 确实顺路"两个条件
    for w in plan.waypoints:
        assert 0.15 <= w.progress <= 0.85
        assert w.attractions > 0          # 没有攻略内容就不该推
        assert w.reason                   # 必须给出可读理由

    # 一键采纳序列 = 中途城市 + 目的地；出发地通常是自己家，不计入游玩城市
    assert plan.suggested_cities[-1] == "大理"
    assert plan.suggested_cities[:-1] == cities


def test_waypoints_sorted_by_best_first():
    """推荐顺序按分数（绕行越少越好）排列，不能随机。"""
    from app.services import route_service

    plan = route_service.plan_route("北京", "大理", 7)
    scores = [w.detour_km - w.attractions * 12 for w in plan.waypoints]
    assert scores == sorted(scores)


def test_advice_matches_returned_totals():
    """结论文本里的里程必须与返回字段一致。

    这两者曾经不一致：结论引用两点直达里程，字段却是分段求和。
    推荐了中途城市时，用户会看到两个不同的"全程里程"。
    """
    from app.services import route_service

    plan = route_service.plan_route("北京", "大理", 7)
    assert f"{plan.total_distance_km} km" in plan.advice
    assert f"{plan.total_duration_hours} 小时" in plan.advice


def test_legs_cover_every_hop_including_waypoints():
    """分段数 = 城市数 - 1，且首尾首尾相接。"""
    from app.services import route_service

    plan = route_service.plan_route("北京", "大理", 7)
    names = [plan.origin] + [w.city for w in plan.waypoints] + [plan.destination]
    assert len(plan.legs) == len(names) - 1
    for leg, a, b in zip(plan.legs, names, names[1:]):
        assert (leg.from_city, leg.to_city) == (a, b)


# ==================== 非法输入 ====================

def test_route_rejects_unknown_place():
    """地点解析不了要抛 ValueError，由接口层转 400。"""
    from app.services import route_service

    with pytest.raises(ValueError, match="无法识别出发地"):
        route_service.plan_route("不存在的城市xyz", "大理", 5)


def test_route_rejects_same_endpoints():
    from app.services import route_service

    with pytest.raises(ValueError, match="相同"):
        route_service.plan_route("成都", "成都", 5)


def test_route_endpoint_unknown_place_returns_400(client):
    """接口层必须把 ValueError 翻成 400，而不是让它冒泡成 500。"""
    resp = client.post("/api/trip/route", json={"origin": "不存在的城市xyz", "destination": "大理", "days": 5})
    assert resp.status_code == 400
    assert "无法识别出发地" in resp.json()["detail"]


def test_route_endpoint_same_place_returns_400(client):
    resp = client.post("/api/trip/route", json={"origin": "成都", "destination": "成都"})
    assert resp.status_code == 400
    assert "相同" in resp.json()["detail"]


def test_route_endpoint_rejects_bad_days(client):
    resp = client.post("/api/trip/route", json={"origin": "成都", "destination": "重庆", "days": 0})
    assert resp.status_code == 422


def test_route_endpoint_success(client):
    resp = client.post("/api/trip/route", json={"origin": "北京", "destination": "大理", "days": 7})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["origin"] == "北京"
    assert data["destination"] == "大理"
    assert data["days"] == 7
    assert data["needs_stop"] is True
    assert data["waypoints"]


# ==================== 城市搜索 ====================

def test_search_cities_matches_local_library():
    from app.services.city_service import search_cities

    results = search_cities("成")
    names = [r.name for r in results]
    assert "成都" in names
    assert all(r.has_guide for r in results)
    assert all(r.source == "library" for r in results)


def test_search_cities_matches_tags_and_province():
    """搜索要能命中标签和省份，不能只比城市名。"""
    from app.services.city_service import search_cities

    assert any(r.name == "三亚" for r in search_cities("海滨"))
    assert any(r.name == "丽江" for r in search_cities("云南"))


def test_search_cities_empty_query_returns_all_local():
    from app.services.city_service import search_cities

    assert len(search_cities("")) == 10


def test_search_cities_accepts_full_official_name():
    """用户照着地图抄全称也要能搜到。

    「大理白族自治州」曾经一条都匹配不到 —— 归一化只做在了高德返回上，
    没做在用户输入上。
    """
    from app.services.city_service import search_cities

    assert [r.name for r in search_cities("大理白族自治州")] == ["大理"]
    assert [r.name for r in search_cities("成都市")] == ["成都"]


def test_search_cities_respects_limit():
    from app.services.city_service import search_cities

    assert len(search_cities("", limit=3)) == 3


def test_search_cities_falls_back_to_amap(monkeypatch):
    """高德命中要能补进结果，并标记 has_guide=False。

    用假数据替换 input_tips —— 既覆盖了合并逻辑，又不碰网络。
    """
    from app.services import city_service

    monkeypatch.setattr(
        city_service.amap_service, "input_tips",
        lambda q, limit=10: [{"name": "克拉玛依市", "adcode": "650200"}],
    )
    results = city_service.search_cities("克拉玛依")
    assert [r.name for r in results] == ["克拉玛依"]
    assert results[0].has_guide is False
    assert results[0].source == "amap"


def test_search_cities_normalizes_amap_names(monkeypatch):
    """高德返回「大理白族自治州」，要归一成「大理」并去重。"""
    from app.services import city_service

    monkeypatch.setattr(
        city_service.amap_service, "input_tips",
        lambda q, limit=10: [{"name": "大理白族自治州", "adcode": "532900"}],
    )
    results = city_service.search_cities("大理白族")
    # 「大理」已在本地库，高德这条应被识别为重复并剔除
    assert [r.name for r in results] == ["大理"]
    assert results[0].has_guide is True


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("大理白族自治州", "大理"),
        ("成都市", "成都"),
        ("北京市", "北京"),
        ("湘西土家族苗族自治州", "湘西"),
        ("丽江", "丽江"),
    ],
)
def test_normalize_city_name(raw, expected):
    from app.services.city_service import normalize_city_name

    assert normalize_city_name(raw) == expected


def test_search_endpoint(client):
    resp = client.get("/api/meta/cities/search", params={"q": "成都"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == "成都"
    assert any(r["name"] == "成都" for r in data["results"])


def test_search_endpoint_limit_validation(client):
    assert client.get("/api/meta/cities/search", params={"limit": 999}).status_code == 422


# ==================== 高德 POI 筛选：真机返回结构 ====================

@pytest.mark.parametrize(
    "name,district,expected",
    [
        # 城市级：district 是「省 + 市」两层，以城市自己的名字结尾
        ("克拉玛依市", "新疆维吾尔自治区克拉玛依市", True),
        ("张家界市", "湖南省张家界市", True),
        ("桂林市", "广西壮族自治区桂林市", True),
        ("呼伦贝尔市", "内蒙古自治区呼伦贝尔市", True),
        ("大理白族自治州", "云南省大理白族自治州", True),
        # POI：district 是「省 + 市 + 区」，以区/县/旗结尾
        ("张家界西站", "湖南省张家界市永定区", False),
        ("张家界国家森林公园", "湖南省张家界市武陵源区", False),
        ("桂林站", "广西壮族自治区桂林市象山区", False),
        ("呼伦贝尔大草原", "内蒙古自治区呼伦贝尔市陈巴尔虎旗", False),
        # 名字里带"市"但其实是 POI：district 不以自己结尾
        ("香格里拉市独克宗古城", "云南省迪庆藏族自治州香格里拉市", False),
        # 市辖区不该被当成城市
        ("朝阳区", "北京市朝阳区", False),
    ],
)
def test_is_city_level(name, district, expected):
    from app.services.amap_service import _is_city_level

    assert _is_city_level(name, district) is expected


def test_input_tips_without_key_returns_empty(monkeypatch):
    """没 Key 时输入提示要静默降级，不能抛异常。"""
    assert amap_service.input_tips("成都") == []


# ==================== 地理编码的粒度把关 ====================
# 用真实抓取到的响应结构做样例（不改动字段名与取值）

def _geocode_payload(*items):
    return {"status": "1", "geocodes": list(items)}


def _geo_item(level, location, formatted, city=""):
    return {"level": level, "location": location, "formatted_address": formatted, "city": city}


def test_geocode_accepts_administrative_levels(monkeypatch):
    """省/市/区县要能正常解析。"""
    monkeypatch.setattr(config, "AMAP_API_KEY", "fake")
    for level, loc in [("省", "116.407526,39.904030"), ("市", "104.066500,30.572800"),
                       ("区县", "100.267638,25.606486")]:
        monkeypatch.setattr(
            amap_service, "_get",
            lambda *a, **k: _geocode_payload(_geo_item(level, loc, "某地")),
        )
        monkeypatch.setattr(amap_service, "_cache", {})
        assert amap_service.geocode("成都") is not None, level


def test_geocode_rejects_village_level(monkeypatch):
    """「不存在的城市xyz」真机会匹配到 level=村庄 的野点，必须拒掉。

    这是真实抓到过的响应 —— 高德把「城市」当成关键词去匹配了。
    """
    monkeypatch.setattr(config, "AMAP_API_KEY", "fake")
    monkeypatch.setattr(
        amap_service, "_get",
        lambda *a, **k: _geocode_payload(
            _geo_item("村庄", "106.278179,38.466370", "宁夏回族自治区银川市兴庆区城市", "银川市")
        ),
    )
    monkeypatch.setattr(amap_service, "_cache", {})
    assert amap_service.geocode("不存在的城市xyz") is None


def test_geocode_skips_bad_then_takes_good(monkeypatch):
    """第一条粒度不达标时要继续往下找，而不是直接放弃。"""
    monkeypatch.setattr(config, "AMAP_API_KEY", "fake")
    monkeypatch.setattr(
        amap_service, "_get",
        lambda *a, **k: _geocode_payload(
            _geo_item("村庄", "1.0,1.0", "某村庄"),
            _geo_item("市", "104.066500,30.572800", "四川省成都市", "成都市"),
        ),
    )
    monkeypatch.setattr(amap_service, "_cache", {})
    got = amap_service.geocode("成都")
    assert got is not None
    assert got.level == "市"
    assert got.formatted == "四川省成都市"


def test_geocode_returns_none_when_all_levels_rejected(monkeypatch):
    monkeypatch.setattr(config, "AMAP_API_KEY", "fake")
    monkeypatch.setattr(
        amap_service, "_get",
        lambda *a, **k: _geocode_payload(
            _geo_item("兴趣点", "1.0,1.0", "某景区"),
            _geo_item("道路", "2.0,2.0", "某路"),
        ),
    )
    monkeypatch.setattr(amap_service, "_cache", {})
    assert amap_service.geocode("火星基地") is None


def test_geocode_rejects_malformed_location(monkeypatch):
    """坐标格式不对要跳过，不能崩。"""
    monkeypatch.setattr(config, "AMAP_API_KEY", "fake")
    monkeypatch.setattr(
        amap_service, "_get",
        lambda *a, **k: _geocode_payload(
            _geo_item("市", "not-a-coord", "坏数据"),
            _geo_item("市", "104.066500,30.572800", "四川省成都市", "成都市"),
        ),
    )
    monkeypatch.setattr(amap_service, "_cache", {})
    assert amap_service.geocode("成都").formatted == "四川省成都市"


def test_route_rejects_garbage_when_amap_is_fuzzy(monkeypatch):
    """端到端：即便高德给了含糊答案，也不能生成一条荒唐路线。"""
    from app.services import route_service

    monkeypatch.setattr(config, "AMAP_API_KEY", "fake")
    monkeypatch.setattr(
        amap_service, "_get",
        lambda *a, **k: _geocode_payload(
            _geo_item("村庄", "106.278179,38.466370", "宁夏回族自治区银川市兴庆区城市", "银川市")
        ),
    )
    monkeypatch.setattr(amap_service, "_cache", {})

    with pytest.raises(ValueError, match="无法识别出发地"):
        route_service.plan_route("不存在的城市xyz", "大理", 5)


# ==================== 无攻略城市：高德 POI 兜底 ====================

def _fake_pois():
    """模拟高德分类检索的返回。"""
    return [
        amap_service.Poi(name="天门山国家森林公园", address="天门路", category="风景名胜"),
        amap_service.Poi(name="张家界土司城", address="南庄坪", category="风景名胜"),
    ]


def test_poi_fallback_fills_uncovered_city(monkeypatch):
    """没有本地攻略的城市也必须生成出有内容的行程。

    曾经的行为是：攻略库里查不到 → 每天 0 个活动。
    用户拿到一份"看起来生成成功、其实全空白"的行程，比直接报错更糟。
    """
    from app.models.trip import TripRequest
    from app.services import trip_service

    monkeypatch.setattr(amap_service, "place_text", lambda *a, **k: _fake_pois())

    plan = trip_service.generate_trip(
        TripRequest(cities=["张家界"], days=2, preferences=["自然"], pace="标准")
    )
    assert plan.cities == ["张家界"]
    for day in plan.itinerary:
        assert day.activities, f"D{day.day} 是空白的"
        for a in day.activities:
            assert a.source and a.source.startswith("高德POI")


def test_covered_city_does_not_use_poi(monkeypatch):
    """有本地攻略的城市必须走 RAG，不能被 POI 覆盖 —— 否则高质量内容被降级了。"""
    from app.models.trip import TripRequest
    from app.services import trip_service

    def boom(*a, **k):
        raise AssertionError("有攻略的城市不该调用高德 POI")

    monkeypatch.setattr(amap_service, "place_text", boom)

    plan = trip_service.generate_trip(
        TripRequest(cities=["成都"], days=2, preferences=["美食"], pace="标准")
    )
    sources = [a.source for d in plan.itinerary for a in d.activities]
    assert sources and all(s.startswith("成都 ·") for s in sources)


def test_poi_fallback_available_without_key(monkeypatch):
    """没配 Key 时 place_text 返回空，不应崩，只是行程里没有活动。"""
    from app.models.trip import TripRequest
    from app.services import trip_service

    plan = trip_service.generate_trip(
        TripRequest(cities=["张家界"], days=1, preferences=[], pace="标准")
    )
    assert plan.itinerary[0].activities == []


# ==================== 备注文案 ====================

def test_activity_note_drops_empty_duration():
    """景点没有 duration 时不能留下悬空的「建议」。"""
    from app.services.trip_service import _activity_note

    assert _activity_note({"area": "武陵山大道1号"}) == "武陵山大道1号"
    assert _activity_note({"area": "西湖区", "duration": "2-3 小时"}) == "西湖区 ｜ 建议 2-3 小时"
    assert _activity_note({}) == ""


def test_poi_lodging_shows_hotel_name(monkeypatch):
    """住宿栏要显示酒店名，不是门牌号。"""
    from app.models.trip import TripRequest
    from app.services import trip_service

    monkeypatch.setattr(
        amap_service, "place_text",
        lambda kw, city, limit=8, types=None: (
            [amap_service.Poi(name="张家界荷花主题酒店", address="南庄坪大庸路旺角佳园二期旁",
                              category="住宿服务")]
            if types == "100000" else []
        ),
    )
    plan = trip_service.generate_trip(
        TripRequest(cities=["张家界"], days=1, preferences=[], pace="标准")
    )
    assert plan.itinerary[0].lodging == "张家界荷花主题酒店"


# ==================== 依赖自检 ====================

def test_amap_status_reports_degraded_mode(client):
    """没配 Key 时，自检接口要明确告诉调用方"现在是估算模式"。"""
    data = client.get("/api/meta/amap").json()
    assert data["enabled"] is False
    assert data["mode"] == "estimate"
    assert data["fallback"]


def test_amap_status_reports_live_mode(client, monkeypatch):
    monkeypatch.setattr(config, "AMAP_API_KEY", "fake-key")
    data = client.get("/api/meta/amap").json()
    assert data["enabled"] is True
    assert data["mode"] == "amap"
    assert data["fallback"] is None
