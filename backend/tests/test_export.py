"""导出行程单（Word / PDF）的测试。

【为什么这两个格式值得单独写测试】
    它们跟 ICS 一样是"确定性逻辑"：同样一份行程必须每次都导出成功。
    而且它们比 ICS 脆 —— 中文要字体、文本要 XML 转义、
    二进制格式坏掉时不一定报错（可能只是打开后少了一页）。
    所以这里既查"能不能生成"，也查"里面到底有没有该有的东西"。
"""

from datetime import date
from io import BytesIO

from app.models.trip import TripRequest
from app.services import export_service, trip_service


def _plan(cities, days, start, prefs=None):
    return trip_service.generate_trip(
        TripRequest(cities=cities, days=days, start_date=start, preferences=prefs or [])
    )


# ==================== 共用内容模型 ====================

def test_summary_rows_marks_generator():
    """编排来源必须出现在导出物的抬头里 —— 界面之外也要能看出是谁排的。"""
    plan = _plan(["西安"], 3, date(2026, 10, 16))
    rows = dict(export_service.summary_rows(plan))
    assert "编排来源" in rows
    # 测试环境强制关闭大模型，所以这里应如实显示"规则生成"
    assert "规则生成" in rows["编排来源"]
    assert rows["总天数"] == "3 天"


def test_date_range_covers_all_days():
    """日期区间要含头含尾：3 天行程是 16~18 号，不是 16~19 号。"""
    plan = _plan(["西安"], 3, date(2026, 10, 16))
    assert export_service.date_range(plan) == "2026-10-16 ~ 2026-10-18"


def test_day_blocks_keep_source_and_never_empty():
    plan = _plan(["西安"], 2, date(2026, 10, 16))
    blocks = export_service.day_blocks(plan)
    assert len(blocks) == 2
    for b in blocks:
        assert b["activities"], "每天都必须有内容，空表在文档里很难看"
        for kind, name, note, source in b["activities"]:
            assert source, "出处不能为空，缺了要用 — 占位"


def test_budget_rows_end_with_total():
    plan = _plan(["西安"], 3, date(2026, 10, 16))
    rows = export_service.budget_rows(plan)
    assert rows[-1][0].startswith("合计")
    assert rows[-1][1].startswith("¥")


def test_export_filename_strips_path_separators():
    plan = _plan(["西安"], 3, date(2026, 10, 16))
    name = export_service.export_filename(plan, "docx")
    assert name.endswith(".docx")
    assert "/" not in name and "\\" not in name


# ==================== Word ====================

def test_docx_is_valid_zip_with_content():
    """docx 本质是 zip，先证明它不是一坨坏字节；再证明内容真的写进去了。"""
    import zipfile

    plan = _plan(["西安", "成都"], 4, date(2026, 10, 16))
    data = export_service.build_docx(plan)

    assert data[:2] == b"PK", "docx 必须是 zip 容器"
    with zipfile.ZipFile(BytesIO(data)) as z:
        assert "word/document.xml" in z.namelist()
        xml = z.read("word/document.xml").decode("utf-8")

    for token in ("逐日行程", "预算拆分", "打包清单", "编排来源", "身份证（必备）"):
        assert token in xml, f"docx 里缺少「{token}」"
    # 中文不能一个字都没落进去（字体没设对时典型症状就是中文丢失）
    assert "西安" in xml


# ==================== PDF ====================

def test_pdf_has_valid_header_and_trailer():
    plan = _plan(["西安", "成都"], 4, date(2026, 10, 16))
    data = export_service.build_pdf(plan)
    assert data.startswith(b"%PDF-"), "PDF 必须以 %PDF- 开头"
    assert b"%%EOF" in data[-2048:], "PDF 必须以 %%EOF 收尾"
    assert len(data) > 5_000, "内容这么多，文件不该小得可疑"


def test_pdf_escapes_xml_special_characters():
    """活动名里带 & 或 < 时不能炸 —— 转义漏了 reportlab 会直接抛异常。"""
    plan = _plan(["西安"], 2, date(2026, 10, 16))
    plan.itinerary[0].activities[0].name = "牛肉面 & 小吃 <招牌>"
    data = export_service.build_pdf(plan)
    assert data.startswith(b"%PDF-")


def test_pdf_font_falls_back_when_no_ttf(monkeypatch):
    """系统字体全取不到时，必须退回内置 CID 字体而不是抛异常。"""
    monkeypatch.setattr(export_service, "_PDF_FONT_CACHE", None)
    monkeypatch.setattr(export_service, "_PDF_TTF_CANDIDATES", ())
    try:
        regular, bold = export_service._pdf_fonts()
        assert regular == "STSong-Light"
        assert bold == "STSong-Light"
    finally:
        # 缓存是模块级状态，别把"没有系统字体"这个假象留给后面的用例
        monkeypatch.setattr(export_service, "_PDF_FONT_CACHE", None)


# ==================== 接口层 ====================

def test_docx_sets_east_asian_font():
    """Normal 样式必须显式钉住中文字体。

    【为什么断言要精确到 Normal 样式，而不是 `"eastAsia" in styles`】
        styles.xml 的 docDefaults 里本来就有一个 `w:eastAsiaTheme="minorEastAsia"`
        —— 它让"eastAsia 这个词存在"永远为真。
        拿它当断言是**假通过**：字体真没设上时也照样绿。
        所以这里剥出 Normal 样式块，只认 literal 字体名。
    """
    import re
    import zipfile

    plan = _plan(["西安"], 2, date(2026, 10, 16))
    with zipfile.ZipFile(BytesIO(export_service.build_docx(plan))) as z:
        styles = z.read("word/styles.xml").decode("utf-8")

    block = re.search(r'<w:style [^>]*w:styleId="Normal".*?</w:style>', styles, re.S)
    assert block, "styles.xml 里没有 Normal 样式"
    assert f'w:eastAsia="{export_service._DOCX_FONT}"' in block.group(0)


def test_export_endpoints_return_downloadable_files(client):
    """两个导出接口都要带上正确的 MIME 与带 UTF-8 文件名的下载头。"""
    resp = client.post("/api/trip/generate", json={
        "cities": ["西安"], "days": 3, "start_date": "2026-10-16", "preferences": [],
    })
    assert resp.status_code == 200
    trip_id = resp.json()["id"]

    docx = client.get(f"/api/trip/{trip_id}/export.docx")
    assert docx.status_code == 200
    assert docx.content[:2] == b"PK"
    assert "wordprocessingml" in docx.headers["content-type"]
    assert "filename*=UTF-8''" in docx.headers["content-disposition"]

    pdf = client.get(f"/api/trip/{trip_id}/export.pdf")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF-")
    assert pdf.headers["content-type"] == "application/pdf"
    assert "filename*=UTF-8''" in pdf.headers["content-disposition"]


def test_export_endpoints_404_on_unknown_trip(client):
    assert client.get("/api/trip/nope/export.docx").status_code == 404
    assert client.get("/api/trip/nope/export.pdf").status_code == 404


def test_ics_filename_header_still_utf8_encoded(client):
    """ICS 也收敛到了同一个响应函数，回归一次防退化。"""
    resp = client.post("/api/trip/generate", json={
        "cities": ["大理"], "days": 2, "start_date": "2026-10-20", "preferences": [],
    })
    trip_id = resp.json()["id"]
    ics = client.get(f"/api/trip/{trip_id}/calendar.ics")
    assert ics.status_code == 200
    assert "filename*=UTF-8''" in ics.headers["content-disposition"]
