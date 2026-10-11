"""路线规划与城市搜索的数据契约。"""

from pydantic import BaseModel, Field


class CitySuggestion(BaseModel):
    """城市搜索的一条结果。"""

    name: str = Field(..., description="城市名")
    province: str | None = Field(None, description="省份（若已知）")
    tags: list[str] = Field(default_factory=list, description="标签")
    has_guide: bool = Field(False, description="本地是否已有攻略库支撑")
    avg_days: int | None = Field(None, description="建议游玩天数（仅已收录城市有）")
    source: str = Field("library", description="来源：library（本地库）/ amap（高德）")


class RouteLeg(BaseModel):
    """路线的一段（相邻两点之间）。"""

    from_city: str
    to_city: str
    distance_km: float
    duration_hours: float
    source: str = Field(..., description="amap（实测）/ estimate（估算）")


class WaypointSuggestion(BaseModel):
    """一个中途停留城市的推荐。"""

    city: str
    detour_km: float = Field(..., description="相比直达多绕的里程")
    progress: float = Field(..., description="在全程中的位置比例 0~1")
    attractions: int = Field(0, description="本地攻略收录的景点数")
    tags: list[str] = Field(default_factory=list)
    reason: str = Field("", description="一句话推荐理由")


class RoutePlan(BaseModel):
    """出发地 → 目的地 的路线方案。"""

    origin: str
    destination: str
    origin_point: str | None = Field(None, description="出发地坐标 lng,lat")
    destination_point: str | None = Field(None, description="目的地坐标 lng,lat")
    days: int

    total_distance_km: float
    total_duration_hours: float
    source: str = Field(..., description="amap / estimate")

    needs_stop: bool = Field(..., description="是否建议中途停留")
    advice: str = Field(..., description="一句话结论")

    legs: list[RouteLeg] = Field(default_factory=list, description="分段行程")
    waypoints: list[WaypointSuggestion] = Field(default_factory=list, description="中途推荐")
    suggested_cities: list[str] = Field(default_factory=list, description="可一键采纳的城市序列")
