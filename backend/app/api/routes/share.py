"""分享接口（前台）。

【只读分享的边界】
    访问者只能"看"，不能改、不能删、不能列出别人的行程。
    token 换行程 ID 之后，走的仍是同一套读取逻辑 ——
    分享只是多了一把钥匙，不是多了一套权限。
"""

from fastapi import APIRouter, HTTPException

from app.models.trip import TripPlan
from app.services import share_service, storage_service

router = APIRouter(prefix="/api/share", tags=["分享"])


@router.get("/{token}", response_model=TripPlan, summary="查看分享的行程")
def get_shared_trip(token: str) -> TripPlan:
    """用分享码读取行程（只读）。"""
    trip_id = share_service.resolve(token)
    if trip_id is None:
        raise HTTPException(status_code=404, detail="分享链接无效或已失效")

    plan = storage_service.get_trip(trip_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="该行程已被删除")
    return plan
