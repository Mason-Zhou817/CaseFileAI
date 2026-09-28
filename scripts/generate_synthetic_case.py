"""Generate the small, fully synthetic Day 1 evaluation case.

The scanned PDF deliberately contains no text layer. All names and events are
fictional and exist only for automated tests and demonstrations.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "synthetic_case"
TEXT_PDF = FIXTURE_DIR / "01_询问笔录_王海.pdf"
SCANNED_PDF = FIXTURE_DIR / "02_银行凭证_扫描件.pdf"
FONT_PATH = Path(r"C:\Windows\Fonts\simhei.ttf")

TEXT_PAGES = [
    [
        "合成案件材料（一）",
        "询问笔录（节选）",
        "被询问人：王海",
        "王海陈述：2021年6月1日，本人通过银行转账向星河科技有限公司支付投资款人民币50万元。",
        "王海陈述：星河科技有限公司于当日确认收到上述款项，交易已经完成。",
        "本材料为合成测试数据，不对应任何真实个人、公司或案件。",
    ],
    [
        "合成案件材料（一）",
        "询问笔录（补充说明）",
        "王海再次确认：付款人是王海本人，收款主体是星河科技有限公司。",
        "王海称李明只负责介绍项目，没有代替其他主体付款。",
        "本材料为合成测试数据。",
    ],
]

SCANNED_PAGES = [
    [
        "合成案件材料（二）",
        "银行交易凭证（扫描件）",
        "交易日期：2021年3月5日",
        "付款人：陈军",
        "收款人：星河科技有限公司",
        "交易金额：人民币450000元",
        "交易状态：失败，未入账",
        "备注：本凭证为合成测试数据。",
    ],
    [
        "合成案件材料（二）",
        "账户核验记录（扫描件）",
        "经核验，星河科技有限公司账户未收到王海支付的上述投资款。",
        "核验日期：2021年6月2日",
        "本材料为合成测试数据。",
    ],
]


def _draw_text_pdf() -> None:
    pdfmetrics.registerFont(TTFont("SimHei", str(FONT_PATH)))
    pdf = canvas.Canvas(str(TEXT_PDF), pagesize=A4)
    _, height = A4
    for lines in TEXT_PAGES:
        y = height - 72
        for index, line in enumerate(lines):
            size = 18 if index == 0 else 12
            pdf.setFont("SimHei", size)
            pdf.drawString(72, y, line)
            y -= 34 if index == 0 else 26
        pdf.showPage()
    pdf.save()


def _draw_scanned_pdf() -> None:
    page_images: list[Image.Image] = []
    title_font = ImageFont.truetype(str(FONT_PATH), 42)
    body_font = ImageFont.truetype(str(FONT_PATH), 30)
    for lines in SCANNED_PAGES:
        image = Image.new("RGB", (1240, 1754), "white")
        draw = ImageDraw.Draw(image)
        y = 120
        for index, line in enumerate(lines):
            font = title_font if index == 0 else body_font
            draw.text((110, y), line, fill="black", font=font)
            y += 82 if index == 0 else 64
        page_images.append(image)

    page_images[0].save(
        SCANNED_PDF,
        "PDF",
        resolution=150.0,
        save_all=True,
        append_images=page_images[1:],
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_manifest() -> None:
    manifest = {
        "case_name": "星河投资款争议（合成）",
        "synthetic": True,
        "documents": [
            {
                "filename": TEXT_PDF.name,
                "sha256": _sha256(TEXT_PDF),
                "pages": len(pymupdf.open(TEXT_PDF)),
                "has_text_layer": True,
            },
            {
                "filename": SCANNED_PDF.name,
                "sha256": _sha256(SCANNED_PDF),
                "pages": len(pymupdf.open(SCANNED_PDF)),
                "has_text_layer": False,
            },
        ],
    }
    (FIXTURE_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    if not FONT_PATH.exists():
        raise FileNotFoundError(f"Chinese fixture font not found: {FONT_PATH}")
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    _draw_text_pdf()
    _draw_scanned_pdf()
    _write_manifest()


if __name__ == "__main__":
    main()
