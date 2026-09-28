"""Human-readable Markdown rendering of the same local structured export."""

import html
from typing import Any

from casefile_ai.application.data_contracts import AnalysisExport


def markdown_text(value: object) -> str:
    # Prevent case data from introducing raw HTML, images or active Markdown links.
    text = html.escape(str(value), quote=False).replace("\\", "\\\\")
    for char in "`*_{}[]()#+!|":
        text = text.replace(char, "\\" + char)
    return text


def render_markdown(report: AnalysisExport) -> str:
    lines = [
        "# " + markdown_text(report.case.name),
        "",
        f"案件 ID：{report.case.case_id} · 状态："
        + ("已归档" if report.case.status == "archived" else "活动"),
        "",
        "## 材料",
        "",
    ]
    for document in report.documents:
        lines.append(f"- {markdown_text(document.filename)} · 当前 v{document.current_version}")
        for version in document.versions:
            lines.append(
                f"  - v{version.version_number} · {version.page_count} 页 · "
                f"{markdown_text(version.filename)} · 版本 ID：{version.document_version_id}"
            )

    def sources(items: list[dict[str, Any]]) -> None:
        lines.extend(["", "来源："])
        if not items:
            lines.append("- 无有效引用。")
        for item in items:
            marker = "（当前版本）" if item["is_current_version"] else "（非当前版本）"
            lines.extend(
                [
                    f"- 文件：{markdown_text(item['filename'])} · "
                    f"来源：v{item['version_number']}{marker}"
                    f" · 第 {item['page_number']} 页",
                    f"  - Document：{item['document_id']} · Version：{item['document_version_id']}"
                    f" · Page：{item['page_id']} · Evidence：{item['evidence_id']}",
                ]
            )
            lines.extend("> " + markdown_text(line) for line in item["quote"].splitlines())

    lines.extend(["", "## 已验证问答", ""])
    for answer in report.answers:
        if answer["abstained"] or answer["metadata"]["validation_status"] != "validated":
            continue
        lines.extend(
            [
                "### " + markdown_text(answer["question"]),
                "",
                markdown_text(answer["answer"]),
                "",
                "Answer：" + answer["answer_id"],
            ]
        )
        sources(answer["citations"])
    lines.extend(["", "## 未形成有效答案的历史问答", ""])
    for answer in report.answers:
        if not answer["abstained"] and answer["metadata"]["validation_status"] == "validated":
            continue
        lines.extend(
            [
                "### " + markdown_text(answer["question"]),
                "",
                "证据不足或验证未通过；此条不是已验证答案。",
                markdown_text(answer["answer"]),
            ]
        )
    lines.extend(["", "## 事实与覆盖", ""])
    kinds = {"date": "日期", "amount": "金额", "person": "人物", "action": "行为", "other": "其他"}
    for claim in report.claims:
        lines.extend(
            [
                f"### {kinds.get(claim['kind'], '事实')} · {claim['claim_id']}",
                markdown_text(claim["text"]),
                "来源回答：" + claim["answer_id"],
            ]
        )
        sources(claim["sources"])
    for result in report.coverage:
        lines.append(
            f"- Answer {result['answer_id']} · 完整覆盖：{'是' if result['complete'] else '否'}"
            f" · 抽取版本：{markdown_text(result['extraction_version'])}"
            f" · 模型版本：{markdown_text(result['model_version'])}"
        )
        for source in result["sources"]:
            fragments = "；".join(x["text"] for x in source["uncovered_fragments"]) or "无"
            lines.append("  - 未覆盖片段：" + markdown_text(fragments))
    lines.extend(
        ["", "## 矛盾分析", "", f"候选事实对：{len(report.candidates)}。候选不等于已确认矛盾。"]
    )
    verdicts = {
        "contradiction": "已判定矛盾",
        "non_contradiction": "未形成矛盾",
        "uncertain": "待确认 / uncertain",
    }
    for result in report.contradictions:
        lines.extend(
            [
                "",
                f"### {verdicts.get(result['verdict'], '待确认')} · "
                f"{kinds.get(result['contradiction_type'], '事实')}",
                markdown_text(result["reason"]),
                f"Claim：{result['left_claim_id']} / {result['right_claim_id']}",
                f"Candidate：{result['candidate_id']} · Result：{result['result_id']}",
            ]
        )
        sources(result["left_evidence"])
        sources(result["right_evidence"])
    if not report.contradictions:
        lines.append("尚无保存的矛盾判断；不能据此认定材料不存在矛盾。")
    lines.extend(["", "## 时间线", ""])
    precision = {"exact": "日期明确", "partial": "部分日期", "unknown": "未知日期"}
    for event in report.timeline:
        lines.extend(
            [
                "### "
                + markdown_text(event["date_value"] or "未知日期")
                + " · "
                + precision[event["date_status"]],
                markdown_text(event["fact"]),
                "Claim：" + event["claim_id"],
            ]
        )
        sources(event["evidence"])
    lines.extend(["", "## 已知限制 / 提示", ""] + ["- " + notice for notice in report.notices])
    return "\n".join(lines) + "\n"
