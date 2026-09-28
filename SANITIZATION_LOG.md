# 公开副本净化记录

本仓库由内部 `public-release` 的受控文件按最终审计白名单单独复制而成，没有继承旧 Git 历史。复制时只选取 262 个文件；内部审计、探针、运行数据和正式 Windows ZIP 均未纳入。

对两份历史评测证据中的工作空间 endpoint 各替换一次，公开值统一为 `https://example.invalid/v1`：

- `docs/evaluation/real-model-smoke-2026-09-26.md`：报告中的 endpoint 字符串替换；问题、结果数字及结论未修改。
- `docs/evaluation/baselines/mvp-stress-v1-real-qwen-2026-09-26.json`：仅 `endpoint` 字段值替换；其余 JSON 字段经结构比较完全相同。

为适配源码先行策略，公开副本另更新了 README、Windows 安装说明、发布说明和 `.gitignore`，并新增 AGPL 许可证及第三方许可核对草稿。上述更改均只发生在本独立副本。

公开快照复测发现原白名单内两个评测/交付脚本引用了排除的 `scripts/probes/`。为保持独立副本可运行，只在本副本提取全合成夹具到 `scripts/evaluation_fixture.py`，并更新脚本引用；`docs/retrieval-foundation.md` 的旧内部 probe 命令改为公开评测入口。生产服务逻辑未修改。

公开说明复核另将 `docs/user-guide-zh.md` 中取得 ZIP 的直接表述改为条件性历史流程，并更新 `docs/windows-installation.md` 与 `docs/stage10-task6-acceptance.md` 的当前状态说明。

`LICENSE` 为 GNU AGPL v3 完整原文，取自 [PyMuPDF 官方仓库的 COPYING](https://github.com/pymupdf/PyMuPDF/blob/main/COPYING)，与 SPDX 的 `AGPL-3.0-only` 文本去除排版空白后的字词完全一致；许可证正文未改写。
