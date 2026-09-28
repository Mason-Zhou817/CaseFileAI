"""Regenerate the wholly synthetic MVP v1 PDF corpus using local fonts."""

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


def _native(name: str, pages: list[tuple[str, list[str]]], font: str) -> None:
    output = canvas.Canvas(str(DOCUMENTS / name), pagesize=A4, pageCompression=1,
                           invariant=1)
    for page_number, (title, lines) in enumerate(pages, start=1):
        output.setFont(font, 17)
        output.drawString(50, HEIGHT - 62, title)
        output.setFont(font, 10)
        output.drawString(50, HEIGHT - 83, "CASEFILE AI · 合成评测材料 · 不含真实个人或案件数据")
        output.line(50, HEIGHT - 93, WIDTH - 50, HEIGHT - 93)
        output.setFont(font, 11)
        y = HEIGHT - 125
        for line in lines:
            output.drawString(56, y, line)
            y -= 25
        output.setFont(font, 9)
        output.drawRightString(WIDTH - 48, 38, f"第 {page_number} / {len(pages)} 页")
        output.showPage()
    output.save()


def _scanned(name: str, title: str, lines: list[str], font_path: Path,
             *, low_resolution: bool = False) -> None:
    scale = 0.75 if low_resolution else 1.0
    image = Image.new("RGB", (int(1200 * scale), int(1680 * scale)), "white")
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.truetype(str(font_path), int(39 * scale))
    body_font = ImageFont.truetype(str(font_path), int(33 * scale))
    small_font = ImageFont.truetype(str(font_path), int(23 * scale))
    draw.text((65 * scale, 72 * scale), title, fill="#222222", font=title_font)
    draw.text((65 * scale, 135 * scale), "合成扫描件 / CaseFile AI 评测专用",
              fill="#555555", font=small_font)
    draw.line((65 * scale, 185 * scale, 1130 * scale, 185 * scale), fill="#888888",
              width=2)
    for index, line in enumerate(lines):
        draw.text((76 * scale, (240 + index * 105) * scale), line,
                  fill="#303030", font=body_font)
    if low_resolution:
        # Mild blur and resampling exercise OCR without making the source unreadable.
        image = image.filter(ImageFilter.GaussianBlur(0.35))
    output = canvas.Canvas(str(DOCUMENTS / name), pagesize=A4, invariant=1)
    output.drawImage(ImageReader(image), 0, 0, width=WIDTH, height=HEIGHT)
    output.showPage()
    output.save()


def _long_pages() -> list[tuple[str, list[str]]]:
    sites = (
        "东港", "北岭", "南桥", "西浦", "河畔", "远山",
        "中庭", "新仓", "青禾", "松林", "石湾", "云台",
    )
    teams = (
        "物流组", "财务组", "设备组", "采购组", "法务组", "审计组",
        "运维组", "质检组", "客服组", "安全组", "计划组", "验收组",
    )
    pages: list[tuple[str, list[str]]] = []
    for index, (site, team) in enumerate(zip(sites, teams, strict=True), start=1):
        lines = [
            f"记录编号：LG-2022-{index:02d}；地点：{site}；负责部门：{team}。",
            f"第{index}次核验聚焦{site}节点的凭证、交接和完成情况。",
            f"现场人员先核对编号LG-2022-{index:02d}，随后填写该页独立的检查记录。",
            f"本页材料归属于{team}，不得与其他地点的同名字段混作同一事件。",
            f"{site}节点保留纸面记录和内部签收摘要，供后续逐页核对。",
            f"备注：第{index}页的时间、金额和主体仅以本页明确记载的字段为准。",
        ]
        if index == 2:
            lines.append("北岭设备交付日期：2022年6月8日；签收人：林舟。")
        elif index == 7:
            lines.append("中庭设备维修金额：人民币275000元；付款状态：尚未支付。")
        elif index == 12:
            lines.append("云台终验结论：设备已于2022年9月21日完成验收。")
        else:
            lines.append(f"{site}节点本页未记载最终结算额，仅记录第{index}次流程检查。")
        lines.extend((
            f"复核说明甲：{site}资料由{team}单独整理并按编号封存。",
            f"复核说明乙：第{index}次现场检查不替代正式付款或最终验收凭据。",
            "复核说明丙：若问题涉及其他页，须回到对应的原始页核对。",
        ))
        pages.append((f"项目履行与核验记录 · {site}", lines))
    return pages


def main() -> None:
    DOCUMENTS.mkdir(parents=True, exist_ok=True)
    font_path = _font_path()
    pdfmetrics.registerFont(TTFont("EvalCJK", str(font_path)))
    font = "EvalCJK"
    _native("01_bank_payment.pdf", [
        ("银行付款凭证", [
            "凭证编号：BK-2021-0318。", "交易日期：2021年3月18日。",
            "交易金额：人民币438000元。", "付款人：赵明。",
            "收款人：青岳设备有限公司。", "交易用途：设备预付款。",
            "交易状态：银行已受理；本凭证不证明收款方最终到账。",
        ]),
        ("银行凭证复核记录", [
            "复核编号：BK-R-0319。", "复核日期：2021年3月19日。",
            "复核金额：人民币438000元。", "复核人：孙怡。",
            "本页复核的是前页凭证编号BK-2021-0318所载金额。",
        ]),
    ], font)
    _native("02_lease_contract.pdf", [
        ("仓库租赁合同 · 主体与租期", [
            "合同编号：LS-2022-04。", "出租方：柳川仓储有限公司。",
            "承租方：青岳设备有限公司。", "租赁标的：南桥仓库二层。",
            "租期自2022年4月1日起至2024年3月31日止。",
        ]),
        ("仓库租赁合同 · 费用与义务", [
            "月租金：人民币32000元。", "押金：人民币64000元。",
            "出租方应于2022年3月28日前交付南桥仓库二层。",
            "承租方应在每月5日前支付当月租金。",
            "双方确认设备维护费不包含在月租金内。",
        ]),
    ], font)
    _scanned("03_clear_receipt_scan.pdf", "收款收据（清晰扫描）", [
        "收据编号：RC-2021-009", "收款日期：2021年4月2日",
        "收款金额：人民币128000元", "交款人：李珊",
        "收款单位：青岳设备有限公司", "款项用途：设备尾款",
    ], font_path)
    _scanned("04_lowres_check_scan.pdf", "付款核对单（轻度低清）", [
        "核对编号：CK-2021-011", "核对日期：2021年4月6日",
        "核对金额：人民币76000元", "经办人：周宁",
        "核对状态：尚未完成支付", "提示：编号中含字母O与数字0",
    ], font_path, low_resolution=True)
    _native("05_wrapped_table.pdf", [
        ("结算表 · 跨行字段", [
            "行号    项目名称          字段           字段值",
            "TR-07   存储服务费        结算金额       人民币",
            "                                           86000元",
            "TR-07   存储服务费        结算日期       2021年5月",
            "                                           9日",
            "TR-08   运输服务费        结算金额       人民币92000元",
            "备注：TR-07与TR-08是两条不同记录，不得混用金额。",
        ]),
        ("结算表 · 连续行说明", [
            "记录TR-09：检测服务费。", "经办人：方宁。",
            "付款状态：尚未支付。", "记录TR-10：安装服务费。",
            "付款状态：已经支付。", "两条记录的状态不可互相替代。",
        ]),
    ], font)
    _native("06_long_record_12_pages.pdf", _long_pages(), font)
    _native("07_order_v1.pdf", [("采购订单 · 初版", [
        "订单编号：PO-2022-17。", "供应方：青岳设备有限公司。",
        "应付金额：人民币120000元。", "生效日期：2022年5月1日。",
        "交付内容：三套测试设备。", "版本标识：v1初版。",
    ])], font)
    _native("08_order_v2.pdf", [("采购订单 · 修订版", [
        "订单编号：PO-2022-17。", "供应方：青岳设备有限公司。",
        "应付金额：人民币128000元。", "生效日期：2022年5月3日。",
        "交付内容：三套测试设备。", "版本标识：v2修订版。",
        "修订说明：本版替代上一版，以本版金额和日期为准。",
    ])], font)
    _native("09_disputed_transaction.pdf", [
        ("交易核对陈述 · A", [
            "以下均为同一笔设备预付款交易的核对陈述，尚未作真伪裁定。",
            "本次交易金额为人民币438000元。",
            "本次交易日期为2021年3月18日。",
            "本次交易款项已支付。",
            "本次交易唯一付款人是赵明。",
        ]),
        ("交易核对陈述 · B", [
            "以下均指向前页所述同一笔设备预付款交易，陈述内容相互独立。",
            "本次交易金额为人民币448000元。",
            "本次交易日期为2021年3月19日。",
            "本次交易款项未支付。",
            "本次交易唯一付款人是钱平。",
        ]),
        ("交易核对陈述 · 对照", [
            "以下对照陈述专供兼容与不确定情形评测。",
            "本次交易金额为人民币438,000元。",
            "另一笔交易日期为2021年3月20日。",
            "另一笔交易款项已支付。",
            "本次交易唯一付款人为赵明。",
            "本次交易计划日期为2021年3月。",
        ]),
    ], font)


if __name__ == "__main__":
    main()
