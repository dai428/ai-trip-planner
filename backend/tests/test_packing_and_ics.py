"""打包清单与 ICS 导出的测试。

这两块都是"确定性逻辑"，最适合写测试：
同样的输入必须给出同样的输出，否则就是 bug。
"""

from datetime import date

from app.models.trip import TripRequest
from app.services import ics_service, packing_service, trip_service


def _plan(cities, days, start, prefs=None):
    return trip_service.generate_trip(
        TripRequest(cities=cities, days=days, start_date=start, preferences=prefs or [])
    )


# ==================== 打包清单 ====================

def test_packing_has_core_categories():
    pk = packing_service.build_packing_list(_plan(["成都"], 3, date(2026, 5, 1)))
    names = [c.category for c in pk.categories]
    for expected in ("证件与财务", "衣物", "电子设备", "药品"):
        assert expected in names


def test_packing_adds_seaside_items():
    pk = packing_service.build_packing_list(_plan(["三亚"], 4, date(2026, 11, 1)))
    all_items = [i for c in pk.categories for i in c.items]
    assert any("泳衣" in i for i in all_items), "海滨城市必须提醒带泳衣"


def test_packing_adds_high_altitude_items():
    pk = packing_service.build_packing_list(_plan(["丽江"], 3, date(2026, 9, 1)))
    all_items = [i for c in pk.categories for i in c.items]
    assert any("氧气" in i for i in all_items), "高海拔目的地必须提醒带氧气"


def test_packing_season_switches_by_month():
    summer = packing_service.build_packing_list(_plan(["北京"], 3, date(2026, 7, 1)))
    winter = packing_service.build_packing_list(_plan(["北京"], 3, date(2026, 1, 1)))
    s_cats = [c.category for c in summer.categories]
    w_cats = [c.category for c in winter.categories]
    assert "夏季专项" in s_cats
    assert "冬季专项" in w_cats


# ==================== ICS 导出 ====================

def test_ics_structure_is_valid():
    plan = _plan(["大理", "成都"], 4, date(2026, 10, 20))
    text = ics_service.build_ics(plan)

    assert text.startswith("BEGIN:VCALENDAR")
    assert text.rstrip().endswith("END:VCALENDAR")
    assert text.count("BEGIN:VEVENT") == text.count("END:VEVENT") == 4
    # 换行必须是 CRLF，这是 RFC 5545 的硬要求
    assert "\r\n" in text
    assert "\n\n" not in text.replace("\r\n", "")


def test_ics_uses_all_day_events_with_exclusive_end():
    """全天事件的 DTEND 是次日（开区间），写错会导致日历少一天。"""
    plan = _plan(["西安"], 2, date(2026, 6, 1))
    text = ics_service.build_ics(plan)
    assert "DTSTART;VALUE=DATE:20260601" in text
    assert "DTEND;VALUE=DATE:20260602" in text
    assert "DTSTART;VALUE=DATE:20260602" in text
    assert "DTEND;VALUE=DATE:20260603" in text


def test_ics_escapes_special_characters():
    assert ics_service._escape("a,b;c\\d") == "a\\,b\\;c\\\\d"
    assert ics_service._escape("第一行\n第二行") == "第一行\\n第二行"


def test_ics_folds_long_lines():
    """超过 75 字节的行必须折行，续行以空格开头。"""
    long_line = "SUMMARY:" + "很长的一段描述" * 20
    folded = ics_service._fold(long_line)
    assert "\r\n " in folded
    for part in folded.split("\r\n"):
        assert len(part.encode("utf-8")) <= 75


def test_ics_filename_is_safe():
    plan = _plan(["大理", "成都"], 3, date(2026, 10, 20))
    name = ics_service.ics_filename(plan)
    assert name.endswith(".ics")
    assert "/" not in name and "\\" not in name
