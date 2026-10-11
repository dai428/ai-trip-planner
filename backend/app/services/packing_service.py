"""打包清单服务：按"城市 + 天数 + 季节 + 偏好"生成出行清单。

【为什么这是规则而不是 LLM】
    打包清单是"确定性知识"：去三亚一定要泳衣，上玉龙雪山一定要防晒和氧气，
    这不需要模型推理，需要的是不漏项。
    用规则生成 —— 结果可复现、可测试、零成本、零延迟。
    该用规则的地方硬套 LLM，是工程上的退步。
"""

from __future__ import annotations

from app.models.trip import PackingCategory, PackingList, TripPlan
from app.services.city_service import get_city

# 城市标签 → 追加物品
_TAG_ITEMS: dict[str, list[str]] = {
    "海滨": ["泳衣 / 泳裤", "沙滩巾", "防水手机袋", "人字拖", "速干衣"],
    "度假": ["草帽", "墨镜"],
    "高海拔": ["便携氧气罐", "保温杯", "冲锋衣", "高倍防晒霜"],
    "自然": ["驱蚊液", "轻便徒步鞋"],
    "古镇": ["舒适的平底鞋", "薄外套（早晚温差大）"],
    "摄影": ["相机 + 备用电池", "存储卡", "小型三脚架"],
    "亲子": ["儿童常用药", "零食与湿巾", "备用衣物"],
    "历史": ["轻便运动鞋（走路多）"],
    "人文": ["移动电源（拍照耗电快）"],
    "美食": ["肠胃药（口味可能不适应）"],
    "夜景": ["便携补光灯"],
    "都市": ["折叠购物袋"],
    "休闲": ["眼罩耳塞（保证休息）"],
}

# 月份 → 季节补充
_SEASON_ITEMS: dict[str, list[str]] = {
    "夏": ["短袖 T 恤", "遮阳帽", "防晒冰袖", "驱蚊液", "换洗内衣 (按天数)"],
    "冬": ["羽绒服", "保暖内衣", "围巾手套", "保湿面霜", "厚袜子"],
    "春秋": ["长袖上衣", "薄外套 / 风衣", "雨伞", "润唇膏"],
}


def _season_of(month: int) -> str:
    if month in (6, 7, 8):
        return "夏"
    if month in (12, 1, 2):
        return "冬"
    return "春秋"


def build_packing_list(plan: TripPlan) -> PackingList:
    """根据行程生成打包清单。"""
    days = plan.days
    month = plan.start_date.month if plan.start_date else 6

    # 按需求条数准备换洗衣物（按 2 天一套估，至少 3 套）
    changes = max(3, (days + 1) // 2)

    categories: list[PackingCategory] = [
        PackingCategory(category="证件与财务", items=[
            "身份证（必备）",
            "学生证 / 教师证（景点有优惠）",
            "银行卡 + 少量现金",
            "酒店与交通预订确认单",
        ]),
        PackingCategory(category="衣物", items=[
            f"换洗上衣 × {changes}",
            f"换洗下装 × {max(2, changes - 1)}",
            f"内衣袜子 × {changes}",
            "睡衣 / 家居服",
            "舒适走路鞋（每天步行 1 万步起）",
        ]),
        PackingCategory(category="电子设备", items=[
            "手机 + 充电器",
            "充电宝（随身携带需 ≤ 100Wh）",
            "耳机",
            "多口 USB 充电头",
        ]),
        PackingCategory(category="洗护防晒", items=[
            "防晒霜 SPF50+",
            "牙具 / 洗漱包",
            "湿巾 + 纸巾",
            f"保湿护肤品（{days} 天用量）",
        ]),
        PackingCategory(category="药品", items=[
            "常用处方药（按需）",
            "肠胃药 / 止泻药",
            "感冒药 + 退烧药",
            "创可贴 + 消毒湿巾",
            "晕车药",
        ]),
    ]

    # 季节补充
    season = _season_of(month)
    categories.append(
        PackingCategory(category=f"{season}季专项", items=list(_SEASON_ITEMS[season]))
    )

    # 城市标签补充（去重）
    extra: list[str] = []
    for city in plan.cities:
        meta = get_city(city)
        for tag in (meta or {}).get("tags", []):
            for item in _TAG_ITEMS.get(tag, []):
                if item not in extra:
                    extra.append(item)
    if extra:
        categories.append(PackingCategory(category="目的地专项", items=extra))

    return PackingList(
        trip_id=plan.id,
        cities=list(plan.cities),
        days=days,
        categories=categories,
    )
