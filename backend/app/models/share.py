"""分享相关的数据结构。

【为什么单独建一个模型而不是塞进 trip.py】
    分享是"行程"之外的一条独立能力线：
    它不改变行程本身，只是给一份行程发一张"只读门票"。
    契约分开，将来分享加密码、加有效期就不会污染行程模型。
"""

from datetime import datetime

from pydantic import BaseModel, Field


class ShareInfo(BaseModel):
    """一次分享的元信息。"""

    token: str = Field(..., description="分享短码")
    trip_id: str = Field(..., description="被分享的行程 ID")
    url: str = Field(..., description="可访问的只读链接")
    created_at: datetime = Field(..., description="创建时间")
