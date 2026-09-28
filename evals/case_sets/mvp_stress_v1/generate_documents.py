"""Generate synthetic stress PDFs with stable bytes and no external assets."""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent
DOCUMENTS = ROOT / "documents"
WIDTH, HEIGHT = A4


def _font_path() -> Path:
    supplied = os.environ.get("CASEFILE_EVAL_CJK_FONT")
    candidates = [Path(supplied)] if supplied else []
    windows_root = os.environ.get("WINDIR")
    if windows_root:
        candidates.extend(Path(windows_root) / "Fonts" / name
                          for name in ("simhei.ttf", "msyh.ttc"))
    candidates.extend((
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("set CASEFILE_EVAL_CJK_FONT to a local Chinese TrueType font")


def _header(pdf: canvas.Canvas, font: str, title: str, page: int, total: int) -> None:
    pdf.setFont(font, 16)
    pdf.drawString(47, HEIGHT - 62, title)
    pdf.setFont(font, 9)
    pdf.drawString(47, HEIGHT - 82, "CaseFile AI · 全合成压力材料 · 无真实案件数据")
    pdf.line(47, HEIGHT - 91, WIDTH - 47, HEIGHT - 91)
    pdf.drawRightString(WIDTH - 47, 38, f"第 {page} / {total} 页")


def _native(name: str, pages: list[tuple[str, list[str]]], font: str) -> None:
    pdf = canvas.Canvas(str(DOCUMENTS / name), pagesize=A4, invariant=1)
    for page_number, (title, lines) in enumerate(pages, start=1):
        _header(pdf, font, title, page_number, len(pages))
        pdf.setFont(font, 10)
        for index, line in enumerate(lines):
            pdf.drawString(54, HEIGHT - 122 - index * 23, line)
        pdf.showPage()
    pdf.save()


def _image(lines: list[str], font_path: Path, *, weak: bool = False) -> Image.Image:
    scale = 0.65 if weak else 1.0
    image = Image.new("RGB", (int(1200 * scale), int(1680 * scale)), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(font_path), int((26 if weak else 34) * scale))
    title = ImageFont.truetype(str(font_path), int((31 if weak else 42) * scale))
    draw.text((70 * scale, 80 * scale), "合成扫描压力页", font=title, fill="#262626")
    for index, line in enumerate(lines):
        draw.text((72 * scale, (210 + index * 110) * scale), line,
                  font=font, fill="#555555" if weak else "#303030")
    if weak:
        image = image.filter(ImageFilter.GaussianBlur(0.65))
    return image


def _scans(font_path: Path, font: str) -> None:
    pdf = canvas.Canvas(str(DOCUMENTS / "01_ocr_confusions.pdf"),
                        pagesize=A4, invariant=1)
    for lines, weak in (([
        "参考码：O0I1l5S8B", "金额：人民币1580.50元",
        "日期：2026/03/07", "核对人：林琳", "状态：尚未支付",
    ], False), ([
        "参考码：B8S5I1lO0", "金额：人民币8510.05元",
        "日期：2026-03-08", "经办人：柳钧", "收款单位：北川设备有限公司",
    ], True)):
        pdf.drawImage(ImageReader(_image(lines, font_path, weak=weak)), 0, 0,
                      width=WIDTH, height=HEIGHT)
        pdf.showPage()
    pdf.save()

    pdf = canvas.Canvas(str(DOCUMENTS / "02_mixed_layer.pdf"),
                        pagesize=A4, invariant=1)
    _header(pdf, font, "混合文本层材料 · 原生页", 1, 2)
    pdf.setFont(font, 11)
    pdf.drawString(54, HEIGHT - 130, "主合同标识：MIX-28。")
    pdf.drawString(54, HEIGHT - 155, "登记主体：北川设备有限公司。")
    pdf.drawString(54, HEIGHT - 180, "登记日期：2026年3月2日。")
    pdf.showPage()
    pdf.drawImage(ImageReader(_image([
        "MIX-28附件验收日期：2026年3月11日",
        "验收人：林琳", "附件状态：尚未最终结算",
    ], font_path)), 0, 0, width=WIDTH, height=HEIGHT)
    pdf.showPage()
    pdf.save()


def _long_pages() -> list[tuple[str, list[str]]]:
    targets = {
        1: ("东端", "501001", "2026年1月3日"),
        13: ("北湾", "513013", "2026年2月7日"),
        25: ("中岭", "525025", "2026年3月12日"),
        38: ("南浦", "538038", "2026年4月16日"),
        50: ("终", "550050", "2026年5月21日"),
    }
    pages: list[tuple[str, list[str]]] = []
    for page in range(1, 51):
        lines = [
            f"海岚设备有限公司第{page:02d}次进度核验记录。",
            f"关联编号：LG-2026-{page:02d}；负责部门：现场组{page % 7 + 1}。",
            "每页均涉及交付、金额、日期、责任人等相似字段。",
            f"本页参考报价：人民币{400000 + page * 101}元，非交付结算金额。",
            f"本页参考日期：2026年{(page % 12) + 1}月{(page % 25) + 1}日，非目标交付日期。",
            "海岚设备有限公司再次核对旧项目摘要，未作最终确认。",
            "海岚设备有限公司的主体名称在本页重复，但仅一项为目标字段。",
            "注意：相邻页的相似金额和日期不可替代本页事实。",
        ]
        if page in targets:
            site, amount, when = targets[page]
            lines.extend((
                f"{site}站实际交付金额：人民币{amount}元。",
                f"{site}站实际交付日期：{when}。",
            ))
        else:
            lines.append("本页未记载目标站点的实际交付金额或日期。")
        pages.append((f"履行长卷宗 · 记录 {page:02d}", lines))
    return pages


def main() -> None:
    DOCUMENTS.mkdir(parents=True, exist_ok=True)
    font_path = _font_path()
    pdfmetrics.registerFont(TTFont("StressCJK", str(font_path)))
    font = "StressCJK"
    _scans(font_path, font)
    _native("03_broken_table.pdf", [
        ("租金与保证金对照表 · 跨行", [
            "行号    合同      项目          数值",
            "R-01    CT-77     月租金        人民币",
            "                                  85000.50元",
            "R-01    CT-77     起租日期      2026年3月",
            "                                  9日",
            "R-02    CT-78     月租金        人民币85000.80元",
            "R-02    CT-78     起租日期      2026年3月10日",
            "附注：两个合同的行号相邻，金额不可混用。",
        ]),
        ("相邻行错位 · 付款状态", [
            "CT-77   设备交接费       人民币17000元",
            "                              付款状态：尚未支付",
            "CT-78   设备交接费       人民币18000元",
            "                              付款状态：已经支付",
            "CT-77   审核人           柳钧",
            "CT-78   审核人           柳均",
        ]),
    ], font)
    _native("04_ledger_alpha.pdf", [
        ("甲账簿 · 多次主体提及", [
            "海岚设备有限公司为第一笔设备预付款的记载主体。",
            "海岚设备有限公司进行了资料交接。",
            "海岚设备有限公司等待银行回单。",
            "海岚设备有限公司已核对合同编号。",
            "海岚设备有限公司尚未披露付款人出生日期。",
            "保证金：人民币460000元；这不是设备预付款金额。",
        ]),
        ("甲账簿 · 相近金额", [
            "设备预付款预算：人民币455000元，非实际转账金额。",
            "甲公司是北京甲有限公司的简称。",
            "柳钧与柳均为不同经办人，不得混同。",
            "核对日：2026年3月5日；签收日：2026年3月6日。",
        ]),
        ("甲账簿 · 唯一付款记录", [
            "第一笔设备预付款实际转账金额：人民币450000元。",
            "第一笔设备预付款实际转账日期：2026年3月1日。",
            "付款人：北京甲有限公司；经办人：柳钧。",
            "本页同时记载参考编号 O0I1l5S8B。",
        ]),
    ], font)
    _native("05_ledger_beta.pdf", [
        ("乙账簿 · 近似主体", [
            "第一笔设备预付款的收款人为北川设备有限公司。",
            "第二笔设备预付款付款人为北京乙有限公司。",
            "第二笔设备预付款实际转账金额：人民币480000元。",
            "第二笔设备预付款实际转账日期：2026年3月10日。",
            "经办人柳均与甲账簿经办人柳钧并非同一人。",
        ]),
        ("乙账簿 · 重复事实", [
            "第一笔设备预付款实际转账金额：人民币450000元。",
            "但此页为前页以外的重复摘要，不能替代原始甲账簿。",
            "第二笔设备预付款实际转账金额：人民币480000元。",
        ]),
    ], font)
    _native("06_long_50_pages.pdf", _long_pages(), font)
    _native("07_contract_v1.pdf", [("仓储合同 · v1", [
        "合同编号：ST-2026-11。", "承租人：海岚设备有限公司。",
        "月租金：人民币79000元。", "租期：2026年3月1日至2027年2月28日。",
        "版本标识：v1初版。",
    ])], font)
    _native("08_contract_v2.pdf", [("仓储合同 · v2", [
        "合同编号：ST-2026-11。", "承租人：海岚设备有限公司。",
        "月租金：人民币82000元。", "租期：2026年4月1日至2027年3月31日。",
        "本修订版替代v1，版本标识：v2。",
    ])], font)
    _native("09_conflict_a.pdf", [("付款争议陈述 A", [
        "以下是同一笔付款记录的陈述，尚未经最终裁定。",
        "付款记录金额为人民币450000元。",
        "付款记录日期为2026年3月1日。",
        "付款记录款项已支付。",
        "付款记录唯一付款人为北京甲有限公司。",
    ])], font)
    _native("10_conflict_b.pdf", [("付款争议陈述 B", [
        "以下指向陈述A的同一笔付款记录，内容相互独立。",
        "付款记录金额为人民币480000元。",
        "付款记录日期为2026年3月10日。",
        "付款记录款项尚未支付。",
        "付款记录唯一付款人为乙公司。",
    ])], font)
    _native("11_conflict_controls.pdf", [("付款争议陈述 · 对照", [
        "付款记录金额为人民币45万元。",
        "付款记录金额为美元450000。",
        "付款记录日期为2026年3月。",
        "另一笔付款记录日期为2026年3月10日。",
        "付款记录款项确已支付。",
        "付款记录款项在收到发票后支付。",
        "付款记录唯一付款人为甲公司。",
        "另一笔付款记录唯一付款人为乙公司。",
        "简称说明：甲公司即北京甲有限公司。",
    ])], font)


if __name__ == "__main__":
    main()
