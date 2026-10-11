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
def temp_db(tmp_path, monkeypatch):
    """每个测试用独立的临时数据库。"""
    monkeypatch.setattr(config, "DB_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    from app.services import storage_service

    storage_service.init_db()
    yield


@pytest.fixture
def client():
    """FastAPI 测试客户端。"""
    from fastapi.testclient import TestClient

    from app.api.main import app

    with TestClient(app) as c:
        yield c
