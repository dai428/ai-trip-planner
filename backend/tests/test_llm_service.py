"""大模型编排层的测试。

【为什么这一层最需要测试】
    这里的代码全是"防御性"的：拆壳、清洗、核对、判定降级。
    它们的共同点是——**正常情况下根本不会被执行到**。
    正因为平时不跑，一旦逻辑写错也不会有人发现，
    直到某天模型返回了意外形状，行程悄悄变成一份半成品。

    所以这些用例全部用假的模型返回来构造"异常场景"，
    不碰网络，也不依赖模型的实际输出。
"""

import json

import pytest

from app.services import llm_service


# ==================== 拆壳：应对端点的单元素数组 ====================

def test_unwrap_pops_single_element_array():
    """端点会把声明的对象包进一层单元素数组，解析时要拆掉。

    这是实测契约：连续 5 次调用，顶层永远是 [{...}]。
    """
    assert llm_service._unwrap([{"a": 1}]) == {"a": 1}


def test_unwrap_passes_object_through():
    """端点哪天修好了、直接返回对象，拆壳也必须无害。"""
    assert llm_service._unwrap({"a": 1}) == {"a": 1}


def test_unwrap_handles_garbage():
    assert llm_service._unwrap([]) is None
    assert llm_service._unwrap(["不是对象"]) is None
    assert llm_service._unwrap("字符串") is None
    assert llm_service._unwrap(None) is None


def test_complete_json_unwraps_wrapped_array(monkeypatch):
    """整条链路：端点返回 [{...}] → 调用方拿到 {...}。"""
    payload = [{"days": [{"day": 1}]}]
    monkeypatch.setattr(llm_service, "_chat",
                        lambda *a, **k: json.dumps(payload, ensure_ascii=False))
    assert llm_service.complete_json("sys", "user", {}) == payload[0]


def test_complete_json_strips_markdown_fence(monkeypatch):
    """提示词明确禁止代码块，模型偶尔仍会加 —— 剥掉再解析，别浪费一次调用。"""
    monkeypatch.setattr(llm_service, "_chat",
                        lambda *a, **k: '```json\n{"days": []}\n```')
    assert llm_service.complete_json("sys", "user", {}) == {"days": []}


def test_complete_json_returns_none_on_bad_json(monkeypatch):
    monkeypatch.setattr(llm_service, "_chat", lambda *a, **k: "这不是 JSON")
    assert llm_service.complete_json("sys", "user", {}) is None


# ==================== 清洗：去装饰，但不猜 ====================

def test_clean_name_strips_delimited_suffix():
    """模型会把整行候选当成名字抄回来，必须截掉分隔符之后的部分。

    实测第一版用「名字｜区域｜标签」拼串给候选，
    模型返回的 name 就是整行 —— 逐一核对时一条都对不上，合规率 0%。
    """
    assert llm_service._clean_name("故宫博物院｜东城区｜历史/必去") == "故宫博物院"
    assert llm_service._clean_name("天坛公园|东城区|历史") == "天坛公园"


def test_clean_name_strips_wrapping_quotes():
    assert llm_service._clean_name("《颐和园》") == "颐和园"
    assert llm_service._clean_name("「成都」") == "成都"
    assert llm_service._clean_name('  "宽窄巷子"  ') == "宽窄巷子"


def test_clean_name_never_invents():
    """清洗只做"去装饰"。猜名字会让防幻觉形同虚设。"""
    assert llm_service._clean_name("不存在的景点") == "不存在的景点"
    assert llm_service._clean_name(None) == ""
    assert llm_service._clean_name(123) == ""


# ==================== 核对：把模型输出对齐到候选池 ====================

def _pool():
    return {
        "北京": {
            "景点": [
                {"name": "故宫博物院", "area": "东城区", "tags": ["历史"], "origin": "guide",
                 "_source": "本地攻略 · 北京"},
                {"name": "天坛公园", "area": "东城区", "tags": ["历史"], "origin": "guide",
                 "_source": "本地攻略 · 北京"},
                {"name": "颐和园", "area": "海淀区", "tags": ["自然"], "origin": "amap",
                 "_source": "高德POI · 风景名胜"},
            ],
            "美食": [{"name": "北京烤鸭", "area": "前门", "tags": [], "origin": "guide",
                      "_source": "本地攻略 · 北京"}],
            "住宿": [{"name": "王府井", "area": "王府井", "tags": [], "origin": "guide",
                      "_source": "本地攻略 · 北京"}],
        }
    }


def _ctx():
    return {
        "cities": ["北京"],
        "allocation": {"北京": 2},
        "preferences": ["历史"],
        "pace": "标准",
        "per_day": 2,
        "pool_by_city": _pool(),
    }


def _day_ok():
    return {
        "days": [
            {"day": 1, "city": "北京", "theme": "皇城漫步",
             "activities": [{"name": "故宫博物院", "note": "中轴线"},
                            {"name": "天坛公园", "note": "祭天建筑"}],
             "meals": ["北京烤鸭"], "lodging": "王府井"},
            {"day": 2, "city": "北京", "theme": "园林",
             "activities": [{"name": "颐和园", "note": "皇家园林"},
                            {"name": "故宫博物院", "note": "再看一次"}],
             "meals": [], "lodging": "王府井"},
        ]
    }


def test_validate_accepts_pool_names():
    meta = {}
    days = llm_service._validate(_day_ok()["days"], _ctx(), meta)
    assert days is not None
    assert days[0]["activities"][0]["name"] == "故宫博物院"
    assert meta["valid_ratio"] == 1.0


def test_validate_drops_hallucinated_places():
    """模型编的地名必须被丢掉 —— 这是防幻觉的唯一有效手段。

    提示词里写"只能用候选里的名字"只是强烈建议，模型仍会编。
    """
    raw = _day_ok()["days"]
    raw[0]["activities"].append({"name": "南锣鼓巷", "note": "编的"})
    raw[0]["meals"].append("不存在的餐厅")
    raw[0]["lodging"] = "不存在的酒店"

    meta = {}
    days = llm_service._validate(raw, _ctx(), meta)
    assert days is not None

    names = [a["name"] for a in days[0]["activities"]]
    assert "南锣鼓巷" not in names
    assert days[0]["meals"] == ["北京烤鸭"]
    # 住宿被丢弃后置空，而不是硬塞一个不在池子里的名字。
    # 补位是 trip_service 的职责（见 test_llm_lodging_filled_from_pool）——
    # 分层清楚：这一层只负责"把不可信的剔掉"，不负责"补什么"。
    assert days[0]["lodging"] is None

    # 编造的东西被记了账。注意 valid_ratio 仍是 1.0 ——
    # 它衡量的是"行程被合法内容填满的程度"，不是"模型有没有编"。
    # 两者测的是不同的事：前者决定是否采用，后者只用于可观测性。
    assert meta["dropped"] == 3          # 1 个景点 + 1 个餐厅 + 1 个酒店
    assert meta["valid_ratio"] == 1.0
    assert meta["hallucination_rate"] > 0


def test_llm_lodging_filled_from_pool():
    """大模型没给出可信住宿时，由候选池的第一条补位（已按偏好排序）。"""
    from app.services import trip_service

    raw = [{"day": 1, "city": "北京", "theme": "x",
            "activities": [{"name": "故宫博物院", "note": "a"}],
            "meals": [], "lodging": None}]
    pool = {"北京": _pool()["北京"]}
    itinerary, _ = trip_service._days_from_llm(raw, ["北京"], pool, per_day=1)
    assert itinerary[0].lodging == "王府井"


def test_validate_rejects_whole_result_when_compliance_too_low():
    """大部分名字都是编的 → 整份作废。

    与其交出一份七零八落的行程，不如整个退回规则生成 ——
    至少那份是完整可用的。静默交付半成品比明确降级糟糕得多。
    """
    raw = [{
        "day": 1, "city": "北京", "theme": "编的",
        "activities": [{"name": "虚构景点A", "note": ""}, {"name": "虚构景点B", "note": ""}],
        "meals": [], "lodging": "",
    }]
    meta = {}
    assert llm_service._validate(raw, _ctx(), meta) is None
    assert "合规率" in meta["reason"]


def test_validate_cleans_names_before_matching():
    """带分隔符后缀的名字，清洗后应当能匹配上候选。"""
    raw = _day_ok()["days"]
    raw[0]["activities"] = [{"name": "故宫博物院｜东城区｜历史/必去", "note": "x"},
                            {"name": "天坛公园", "note": "y"}]
    meta = {}
    days = llm_service._validate(raw, _ctx(), meta)
    assert days[0]["activities"][0]["name"] == "故宫博物院"
    assert meta["valid_ratio"] == 1.0


def test_validate_reassigns_city_from_server_skeleton():
    """城市归属以服务端的分段为准，不听模型的。

    模型返回的 day/city 可能错位；骨架错了会出现
    "说好 3 天北京，结果排了 4 天"这种硬伤。
    """
    raw = _day_ok()["days"]
    raw[0]["city"] = "上海"          # 模型报了个不存在的城市
    meta = {}
    days = llm_service._validate(raw, _ctx(), meta)
    assert days[0]["city"] == "北京"


def test_validate_rejects_non_list():
    meta = {}
    assert llm_service._validate({"days": []}, _ctx(), meta) is None
    assert llm_service._validate([], _ctx(), meta) is None
    assert llm_service._validate("字符串", _ctx(), meta) is None
    assert meta["reason"]


# ==================== 入口：开关与降级 ====================

def test_compose_days_returns_none_when_disabled(monkeypatch):
    """未配置大模型时明确返回 None，而不是抛异常。"""
    monkeypatch.setattr(llm_service, "enabled", lambda: False)
    days, meta = llm_service.compose_days(_ctx())
    assert days is None
    assert meta["used"] is False
    assert "未配置" in meta["reason"]


def test_compose_days_uses_validated_result(monkeypatch):
    monkeypatch.setattr(llm_service, "enabled", lambda: True)
    monkeypatch.setattr(llm_service, "_chat",
                        lambda *a, **k: json.dumps(_day_ok(), ensure_ascii=False))
    days, meta = llm_service.compose_days(_ctx())
    assert meta["used"] is True
    assert len(days) == 2


def test_compose_days_degrades_on_network_failure(monkeypatch):
    """调用失败要安静降级，绝不能把"大模型挂了"变成"整个生成 500"。"""
    monkeypatch.setattr(llm_service, "enabled", lambda: True)
    monkeypatch.setattr(llm_service, "_chat", lambda *a, **k: None)
    days, meta = llm_service.compose_days(_ctx())
    assert days is None
    assert meta["used"] is False
    assert meta["reason"]


def test_status_hides_key(monkeypatch):
    """自检接口不能把密钥原文吐出来。"""
    from app import config
    monkeypatch.setattr(config, "LLM_API_KEY", "sk-abcdefghijklmnop")
    monkeypatch.setattr(llm_service, "enabled", lambda: True)
    st = llm_service.status()
    assert "sk-abcdefghijklmnop" not in json.dumps(st, ensure_ascii=False)
    assert st["key_masked"].startswith("sk-abc")


# ==================== 缺天补齐 ====================

def test_fill_day_compensates_missing_content():
    """模型漏排某天时，只补那一天，不整份作废。"""
    from app.services import trip_service

    pool = {"景点": _pool()["北京"]["景点"], "美食": [], "住宿": []}
    filled = trip_service._fill_day("北京", pool, per_day=2, offset=0)
    assert len(filled) == 2
    # 补出来的条目必须带 note，否则那天会显示成一片空白
    assert all(f["note"] for f in filled)
