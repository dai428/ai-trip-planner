"""行程接口（前台）。

【前台只干三件事】
    1. 接住请求（校验参数）
    2. 把活交给业务层（services）
    3. 把结果送回去（转 JSON）

【本版新增的接口】
    POST /api/trip/route               出发地 → 目的地 路线规划 + 中途推荐
    GET  /api/trip/{id}/packing-list   打包清单
    GET  /api/trip/{id}/calendar.ics   导出日历
    GET  /api/trip/{id}/export.docx    导出 Word 行程单
    GET  /api/trip/{id}/export.pdf     导出 PDF 行程单
    POST /api/trip/{id}/share          生成只读分享链接
"""

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.models.route import RoutePlan
from app.models.share import ShareInfo
from app.models.trip import PackingList, TripPlan, TripRequest, TripSummary
from app.services import (
    export_service,
    ics_service,
    packing_service,
    route_service,
    share_service,
    storage_service,
    trip_service,
)

router = APIRouter(prefix="/api/trip", tags=["行程"])


class RouteRequest(BaseModel):
    """路线规划入参。"""

    origin: str = Field(..., min_length=1, description="出发地城市名")
    destination: str = Field(..., min_length=1, description="目的地城市名")
    days: int = Field(5, ge=1, le=30, description="计划天数")


def _require_trip(trip_id: str) -> TripPlan:
    """取行程，取不到就 404 —— 把重复的"找不到"判断收在一处。"""
    plan = storage_service.get_trip(trip_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="行程不存在")
    return plan


def _file_response(content: bytes | str, filename: str, media_type: str) -> Response:
    """把生成好的文件包成下载响应。

    【为什么要抽成一个函数】
        ICS / Word / PDF 三个导出接口除了内容和 MIME 之外完全一样。
        中文文件名必须走 RFC 5987 的 filename* 形式，
        否则部分浏览器会把「西安」显示成乱码 —— 这条规则只该写一次，
        漏写一处就是一个只有中文用户才会遇到的 bug。
    """
    body = content.encode("utf-8") if isinstance(content, str) else content
    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "Cache-Control": "no-store",
        },
    )


@router.post("/generate", response_model=TripPlan, summary="生成行程")
def create_trip(request: TripRequest) -> TripPlan:
    """根据城市列表、天数、偏好生成行程，并落盘保存。"""
    plan = trip_service.generate_trip(request)
    storage_service.save_trip(plan)
    return plan


@router.post("/route", response_model=RoutePlan, summary="路线规划 + 中途推荐")
def plan_route(request: RouteRequest) -> RoutePlan:
    """规划 出发地 → 目的地，并判断"中间要不要停、停哪儿"。

    【为什么把 ValueError 转成 400】
        地点解析不了是**用户输入问题**，不是服务端故障。
        返回 400 前端才能把中文提示直接显示给用户；
        若放任它冒泡成 500，用户只会看到"服务器错误"，无从下手。
    """
    try:
        return route_service.plan_route(request.origin, request.destination, request.days)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("", response_model=list[TripSummary], summary="历史行程列表")
def list_trips(limit: int = 50) -> list[TripSummary]:
    """只返回摘要，不返回完整行程 —— 列表页不需要那么多数据。"""
    return storage_service.list_trips(limit)


@router.get("/{trip_id}", response_model=TripPlan, summary="获取行程详情")
def get_trip(trip_id: str) -> TripPlan:
    return _require_trip(trip_id)


@router.delete("/{trip_id}", summary="删除行程")
def delete_trip(trip_id: str) -> dict[str, str]:
    if not storage_service.delete_trip(trip_id):
        raise HTTPException(status_code=404, detail="行程不存在")
    return {"message": "已删除", "id": trip_id}


@router.get("/{trip_id}/packing-list", response_model=PackingList, summary="打包清单")
def get_packing_list(trip_id: str) -> PackingList:
    """按城市标签与出发季节生成出行清单（规则生成，结果可复现）。"""
    return packing_service.build_packing_list(_require_trip(trip_id))


@router.get("/{trip_id}/calendar.ics", summary="导出 ICS 日历")
def download_calendar(trip_id: str) -> Response:
    """导出可导入手机日历的 .ics 文件。"""
    plan = _require_trip(trip_id)
    content = ics_service.build_ics(plan)
    filename = ics_service.ics_filename(plan)
    return _file_response(content, filename, "text/calendar; charset=utf-8")


@router.get("/{trip_id}/export.docx", summary="导出 Word 行程单")
def download_docx(trip_id: str) -> Response:
    """导出可编辑、可打印的 Word 行程单（逐日行程 + 预算 + 打包清单）。"""
    plan = _require_trip(trip_id)
    content = export_service.build_docx(plan)
    return _file_response(
        content,
        export_service.export_filename(plan, "docx"),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@router.get("/{trip_id}/export.pdf", summary="导出 PDF 行程单")
def download_pdf(trip_id: str) -> Response:
    """导出服务端生成的 PDF 行程单 —— 不依赖用户浏览器打印。"""
    plan = _require_trip(trip_id)
    content = export_service.build_pdf(plan)
    return _file_response(content, export_service.export_filename(plan, "pdf"), "application/pdf")


@router.post("/{trip_id}/share", response_model=ShareInfo, summary="创建分享链接")
def create_share(trip_id: str, request: Request) -> ShareInfo:
    """生成一条只读分享链接（token 随机、可撤销）。"""
    _require_trip(trip_id)
    return share_service.create_share(trip_id, str(request.base_url))
