"""接口层测试：从 HTTP 入口验证整条链路。

测试走的是真实路由 + 真实验证 + 真实存储（临时库），
不是 mock —— 这样才能挡住"业务层对了但接口没挂上"这类问题。
"""


def _make_trip(client, **overrides):
    body = {
        "cities": ["大理", "成都"],
        "days": 4,
        "start_date": "2026-10-20",
        "preferences": ["自然", "美食"],
        "pace": "标准",
    }
    body.update(overrides)
    resp = client.post("/api/trip/generate", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert "已启动" in client.get("/").json()["message"]


def test_meta_cities(client):
    data = client.get("/api/meta/cities").json()
    assert len(data["cities"]) == 10
    names = [c["name"] for c in data["cities"]]
    assert names[0] == "北京"
    assert all({"name", "tags", "lat", "lng"} <= set(c) for c in data["cities"])


def test_meta_rag_self_check(client):
    data = client.get("/api/meta/rag").json()
    assert data["cities"] == 10
    assert "bm25" in data["channels"]


def test_generate_returns_multi_city_itinerary(client):
    plan = _make_trip(client)
    assert plan["cities"] == ["大理", "成都"]
    assert plan["days"] == 4
    assert len(plan["itinerary"]) == 4
    # 天数要按城市分段分配：2 城 4 天 → 各 2 天
    assert [d["city"] for d in plan["itinerary"]] == ["大理", "大理", "成都", "成都"]
    # 每条活动都要挂出处，否则"RAG"就只是装饰
    activities = [a for d in plan["itinerary"] for a in d["activities"]]
    assert activities and all(a["source"] for a in activities)


def test_generate_rejects_empty_cities(client):
    resp = client.post("/api/trip/generate", json={"cities": [], "days": 3})
    assert resp.status_code == 422


def test_generate_rejects_bad_days(client):
    resp = client.post("/api/trip/generate", json={"cities": ["北京"], "days": 99})
    assert resp.status_code == 422


def test_packing_list_endpoint(client):
    plan = _make_trip(client)
    data = client.get(f"/api/trip/{plan['id']}/packing-list").json()
    assert data["trip_id"] == plan["id"]
    assert data["cities"] == ["大理", "成都"]
    cats = [c["category"] for c in data["categories"]]
    assert "证件与财务" in cats
    assert "目的地专项" in cats


def test_calendar_export_headers(client):
    plan = _make_trip(client)
    resp = client.get(f"/api/trip/{plan['id']}/calendar.ics")
    assert resp.status_code == 200
    assert "text/calendar" in resp.headers["content-type"]
    # 中文文件名走 RFC 5987
    assert "filename*=UTF-8''" in resp.headers["content-disposition"]
    assert resp.text.startswith("BEGIN:VCALENDAR")


def test_share_roundtrip(client):
    plan = _make_trip(client)
    share = client.post(f"/api/trip/{plan['id']}/share").json()
    assert len(share["token"]) == 8

    got = client.get(f"/api/share/{share['token']}")
    assert got.status_code == 200
    assert got.json()["id"] == plan["id"]


def test_share_invalid_token(client):
    assert client.get("/api/share/notexist").status_code == 404


def test_history_and_delete(client):
    plan = _make_trip(client)
    items = client.get("/api/trip").json()
    assert any(t["id"] == plan["id"] for t in items)

    assert client.delete(f"/api/trip/{plan['id']}").status_code == 200
    assert client.get(f"/api/trip/{plan['id']}").status_code == 404
    # 删除行程后，分享链接必须一起失效（否则会残留悬空引用）
    assert client.delete(f"/api/trip/{plan['id']}").status_code == 404


def test_delete_removes_share(client):
    plan = _make_trip(client)
    token = client.post(f"/api/trip/{plan['id']}/share").json()["token"]
    assert client.get(f"/api/share/{token}").status_code == 200

    client.delete(f"/api/trip/{plan['id']}")
    assert client.get(f"/api/share/{token}").status_code == 404


def test_unknown_trip_returns_404(client):
    assert client.get("/api/trip/nonexistent").status_code == 404
    assert client.get("/api/trip/nonexistent/packing-list").status_code == 404
    assert client.get("/api/trip/nonexistent/calendar.ics").status_code == 404
