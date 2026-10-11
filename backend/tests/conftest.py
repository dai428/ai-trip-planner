"""测试配置：让 pytest 能找到 app 包，并把数据库指向临时文件。

【为什么要把数据库改到 tmp_path】
    测试不能碰生产库。每个测试用独立的 SQLite 文件，
    跑完即弃 —— 测试之间互不污染，也不会把开发数据洗掉。
"""

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app import config  # noqa: E402


@pytest.fixture(autouse=True)
def offline_llm(monkeypatch):
    """强制测试走"未配大模型"的规则路径。

    【为什么必须全局关掉】
        本机 .env 里配了真实的百炼 Key。如果不关，
        `trip_service.generate_trip` 会真的发起 HTTP 调用 ——
        结果是测试变慢几十秒、结果不可复现（模型输出不确定），
        还会消耗真实配额。测试一旦依赖网络就不再是测试。

        需要覆盖大模型分支的用例，自行用 monkeypatch 打开即可。
    """
    from app import config as cfg
    from app.services import llm_service

    monkeypatch.setattr(cfg, "LLM_ENABLED", False)
    monkeypatch.setattr(cfg, "LLM_API_KEY", "")
    monkeypatch.setattr(llm_service, "enabled", lambda: False)
    yield


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """每个测试用独立的临时数据库。

    两张表都要建：trips/shares 之外还有 poi_pool（候选池）。
    漏建后者的话，任何走到行程生成的用例都会以
    "no such table: poi_pool" 失败 —— 而报错点离真正的原因很远。
    """
    monkeypatch.setattr(config, "DB_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    from app.services import poi_pool_service, storage_service

    storage_service.init_db()
    poi_pool_service.init_db()
    # 候选池的"负结果冷却"是进程内状态，会跨用例残留 —— 清掉，
    # 否则前一个用例把某城市标记为"刚试过且为空"，后面同城市的用例就拿不到候选。
    poi_pool_service._last_empty.clear()
    yield


@pytest.fixture
def client():
    """FastAPI 测试客户端。"""
    from fastapi.testclient import TestClient

    from app.api.main import app

    with TestClient(app) as c:
        yield c
