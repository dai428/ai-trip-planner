"""应用入口（组装层）。

【它在项目里的位置】
    这一层只做"组装"：建应用、挂中间件、挂路由、管生命周期。
    任何业务逻辑都不许写在这里 —— 入口一乱，整个项目就没法扩展。

【踩过的坑：fastapi 0.143 的 include_router 不再展开子路由】
    旧版本里 include_router 会把子路由"摊平"进 app.routes，
    所以以前可以用 `[r.path for r in app.routes]` 自检路由有没有挂上。
    新版本改成了挂一个包装对象，遍历 app.routes 看不到子路由的 path。
    正确的自检方式是看 `app.openapi()["paths"]`。
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import config
from app.api.routes.meta import router as meta_router
from app.api.routes.share import router as share_router
from app.api.routes.trip import router as trip_router
from app.services import storage_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时建表，关闭时留白（SQLite 连接用完即关，无需清理）。"""
    storage_service.init_db()
    yield


app = FastAPI(
    title=config.APP_NAME,
    description=config.APP_DESCRIPTION,
    version=config.APP_VERSION,
    lifespan=lifespan,
)

# 前端开发期跑在 5173，打包后由 Nginx 统一入口 —— 这里放开常见来源
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],   # 前端要读下载文件名
)


@app.get("/", tags=["元信息"])
def read_root() -> dict[str, str]:
    """根路由：回答"你是一个什么服务"。"""
    return {"message": f"{config.APP_NAME} 已启动", "version": config.APP_VERSION}


@app.get("/health", tags=["元信息"])
def health_check() -> dict[str, str]:
    """健康检查：给运维与容器探针用，永远不参与业务。"""
    return {"status": "ok"}


app.include_router(meta_router)
app.include_router(trip_router)
app.include_router(share_router)
