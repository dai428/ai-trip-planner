"""ICS 日历导出服务（RFC 5545）。

【为什么值得单独做一个导出格式】
    Markdown / PDF 是"给人看的"，ICS 是"给日历应用看的"：
    导进手机日历后，每天去哪、几点集合会自动出现在提醒里。
    这是"行程"真正落到生活里的那一步，也是同类 Demo 极少做的部分。

【实现要点（都是踩过才知道的坑）】
    1. 换行必须写成字面量 \\n，不能真换行 —— 真换行会把一条事件劈成两行而解析失败
    2. 逗号、分号、反斜杠都要转义
    3. 单行超过 75 字节要折行，续行以一个空格开头（UTF-8 按字节算）
    4. 全天事件的 DTEND 是"次日"—— ICS 的结束时间是开区间
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.models.trip import TripPlan

_PRODID = "-//xingji-trip-planner//行迹行程规划//CN"


def _escape(text: str) -> str:
    """转义 ICS 文本字段。"""
    return (
        text.replace("\\", "\\\\")
            .replace(";", "\\;")
            .replace(",", "\\,")
            .replace("\r\n", "\\n")
            .replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """按 75 字节折行（UTF-8），续行以单个空格开头。"""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line

    parts: list[str] = []
    buf = bytearray()
    limit = 75
    for ch in line:
        b = ch.encode("utf-8")
        if len(buf) + len(b) > limit:
            parts.append(buf.decode("utf-8"))
            buf = bytearray()
            limit = 74          # 续行要留 1 字节给前导空格
        buf.extend(b)
    parts.append(buf.decode("utf-8"))
    return "\r\n ".join(parts)


def build_ics(plan: TripPlan, *, calendar_name: str | None = None) -> str:
    """把行程转成 ICS 文本：每天一个全天事件。"""
    name = calendar_name or f"{'、'.join(plan.cities)} {'+'.join(str(plan.days))}天行程"
    start = plan.start_date or date.today()
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")

    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{_PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(name)}",
    ]

    for day in plan.itinerary:
        d0 = start + timedelta(days=day.day - 1)
        d1 = d0 + timedelta(days=1)

        summary = f"D{day.day} {day.city} · {day.theme}" if day.theme else f"D{day.day} {day.city}"

        desc_parts: list[str] = []
        for act in day.activities:
            line = f"[{act.kind}] {act.name}"
            if act.note:
                line += f"（{act.note}）"
            desc_parts.append(line)
        if day.meals:
            desc_parts.append("推荐餐食：" + "、".join(day.meals))
        if day.lodging:
            desc_parts.append("住宿区域：" + day.lodging)
        description = "\n".join(desc_parts) or "自由安排"

        lines += [
            "BEGIN:VEVENT",
            f"UID:{plan.id}-d{day.day}@xingji",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{d0.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{d1.strftime('%Y%m%d')}",
            f"SUMMARY:{_escape(summary)}",
            f"DESCRIPTION:{_escape(description)}",
            f"LOCATION:{_escape(day.city)}",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def ics_filename(plan: TripPlan) -> str:
    """给下载文件起名，如 xingji_大理_成都_2026-10-10.ics。"""
    cities = "_".join(plan.cities[:3])
    d = (plan.start_date or date.today()).isoformat()
    return f"xingji_{cities}_{d}.ics"
