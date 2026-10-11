"""行程相关的数据结构（契约）。

【它在项目里的位置】
    app/models/ —— 数据模型层，只定义"东西长什么样"，不含任何逻辑。

【本版相对 v0.1 的变化】
    v0.1 只能规划"一个城市"，返回 activities: list[str]。
    v0.2 支持多城市串联，并把"活动"升级为结构化对象，
    这样才能挂上攻略出处（source）、区分类型（kind），
    也才能被前端拖拽重排。
"""

from datetime import date, datetime

from pydantic import BaseModel, Field


# ==================== 输入 ====================

class TripRequest(BaseModel):
    """用户提交的"生成行程"请求（输入契约）。"""

    cities: list[str] = Field(
        ...,
        min_length=1,
        max_length=6,
        description="目的地城市列表，按游览顺序排列（支持多城市串联）",
    )
    days: int = Field(..., ge=1, le=30, description="总旅行天数")
    start_date: date | None = Field(None, description="出发日期，留空表示未定")
    budget: float | None = Field(None, ge=0, description="总预算（元），留空表示不限")
    preferences: list[str] = Field(
        default_factory=list,
        description="偏好标签，如 美食 / 自然 / 人文 / 亲子 / 摄影",
    )
    pace: str = Field("标准", description="节奏：轻松 / 标准 / 紧凑")


# ==================== 行程内容 ====================

class Activity(BaseModel):
    """行程中的一个活动（景点、美食、体验等）。"""

    name: str = Field(..., description="活动名称")
    kind: str = Field("景点", description="类型：景点 / 美食 / 体验 / 交通")
    note: str = Field("", description="一句说明或建议")
    source: str | None = Field(None, description="攻略出处（检索命中的原文片段）")


class DayPlan(BaseModel):
    """行程里的一天。"""

    day: int = Field(..., description="第几天（全局序号）")
    city: str = Field(..., description="当天所在城市")
    theme: str = Field("", description="当天主题，如「古城漫步」")
    activities: list[Activity] = Field(default_factory=list, description="当天的活动安排")
    meals: list[str] = Field(default_factory=list, description="推荐餐食")
    lodging: str | None = Field(None, description="住宿建议区域")


class BudgetItem(BaseModel):
    """预算拆分中的一项。"""

    label: str = Field(..., description="项目名，如 住宿 / 餐饮")
    amount: float = Field(..., description="金额（元）")


class TripPlan(BaseModel):
    """返回给用户的行程（输出契约）。"""

    id: str = Field(..., description="行程唯一 ID")
    cities: list[str] = Field(..., description="城市顺序")
    days: int = Field(..., description="总天数")
    start_date: date | None = Field(None, description="出发日期")
    pace: str = Field("标准", description="节奏")
    itinerary: list[DayPlan] = Field(default_factory=list, description="逐日安排")
    budget: list[BudgetItem] = Field(default_factory=list, description="预算拆分")
    created_at: datetime = Field(..., description="生成时间")


# ==================== 打包清单 ====================

class PackingCategory(BaseModel):
    """打包清单中的一个分类。"""

    category: str = Field(..., description="分类名，如 证件 / 衣物 / 电子")
    items: list[str] = Field(default_factory=list, description="该类下的物品")


class PackingList(BaseModel):
    """按城市、天数、季节规则生成的出行清单。"""

    trip_id: str
    cities: list[str]
    days: int
    categories: list[PackingCategory]


# ==================== 列表项（历史） ====================

class TripSummary(BaseModel):
    """历史列表里的行程摘要（不返回完整行程，省流量）。"""

    id: str
    cities: list[str]
    days: int
    created_at: datetime
