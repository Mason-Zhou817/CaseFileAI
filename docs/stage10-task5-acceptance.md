# 第 10 阶段任务 5：中文手册与交付文档核验

基线：`5e3ac2c9c6ad738efe126b0a3e4dfd4691ca4004`。本任务编写交付文档，不开始任务 6，不改程序版本或事实判断算法。

## 文档交付

- [中文用户手册](user-guide-zh.md)：33 章，含可跳转目录、真实按钮步骤、状态表、错误处理表、隐私与已知限制、最后检查清单。
- [README](../README.md)：中文项目入口、普通用户快速开始、能力 / 隐私 / 边界及文档入口。
- [开发者指南](development.md)：迁出源码环境、测试 / lint / Mypy、架构与评估入口；开发命令明确需要完整源码。
- [Windows 安装说明](windows-installation.md)：衔接完整手册，区分任务 4 历史范围与当前文档。
- [数据管理说明](data-management.md)：保留历史技术验证，纠正 GUI 路径与产品配置切换说明。

交付 ZIP 白名单纳入手册、开发者指南、本记录及开发者指南链接所需的架构文档；没有扩展成打包模型、案件或配置秘密。未添加截图，不以旧截图或模拟截图证明现有行为。

## 与实现逐项核对

| 用户可见内容 | 核对依据 / 结果 |
| --- | --- |
| 入口名、uv / Python 版本、ZIP 版本 | 四个根目录 CMD、windows-runtime.json、windows.ps1、pyproject.toml；实际入口是 Prepare Models CaseFile AI.cmd，包版本仍为 0.1.0 |
| 默认数据、日志、配置位置与显式 AI 启用 | config.py Settings / configuration_file 与 Windows launcher；仅有凭据不自动启用 |
| 页面 / 按钮 / 状态文字 | index.html、workspace.js、materials.py；使用材料与索引、事实与覆盖检查等当前文字 |
| 导入 / OCR / 索引 | documents.py、parsers/pdf.py、retrieval.py；PDF 50 MiB / 1–500 页，重复内容复用，导入解析后索引单独执行，更新索引不重做 OCR |
| QA 拒答、引用与来源 | qa.py、qa_alignment.py、GUI、analysis_export.py；安全拒答不证明事实不存在，来源有效不等于专业结论正确 |
| Claim / Coverage / 矛盾 / Timeline | claims.py、claim_coverage.py、contradictions.py、timeline.py；模型调用与本地派生区分；未知日期不推断，覆盖不代表完整案件分析 |
| 备份 / 恢复 / 导出 | data_management.py、backup_storage.py、analysis_export.py 与 GUI；完整备份为全部案件和历史材料，未加密；恢复为新副本且不自动切换；JSON / Markdown 导出不是备份 |
| 模型状态与错误 | system_status.py、模型适配器、GUI 消息；配置、资源可用、加载、曾连接成功分别解释；没有新增连接测试按钮 |
| 外发内容 | QA / alignment / Claim / semantic contradiction 的实际请求构造；问题、事实、原文、文件名 / 页码 / 版本标识等可能外发；不自动脱敏 |
| 本地实例与更新边界 | 已验收 Windows 入口；ready 后打开浏览器、同实例复用、端口冲突不跳号、Stop 不按 Python 名称杀进程、程序与用户数据分离 |

官方 uv 固定发布页、安装说明及 ONNX Runtime 运行库要求链接已核实；没有复制供应商的实时价格或数据留存政策，用户应自行确认所用服务政策。

## 任务 5 时的差异与限制

以下为基线时的历史记录；任务 6 对三处界面差异做了最小修正，当前操作以用户手册为准。

1. 用户要求中的 Prepare Models.cmd 简称不是实际文件名，本文使用完整文件名。
2. v1.0 是手册产品目标名称；程序 / 安装包仍为 0.1.0，未宣称最终发布或验收完成。
3. GUI 引用卡片没有直接版本 / 非当前标签；链接绑定真实来源版本，版本标签可在历史版本和导出中核对。
4. GUI 恢复结果仍提示“.env”；Windows 产品入口应修改 config.env，手册显式说明。界面本身没有在本任务改动。
5. GUI 系统状态只呈现简要状态，不显示完整路径；结构化接口含路径。纠正旧数据管理文档的笼统表述。
6. 重复内容可能复用版本而没有独立提示；Timeline 允许日期未知条目，当前不是只列带完整日期的事实。
7. 原任务 3 / 4 文档中的“未开始下一任务”属于历史范围，已说明，不能据此误读当前交付。

这些差异已在相应操作章节披露，没有把缺少的 GUI 展示写成已实现。独立物理机器、冷缓存下载、企业执行策略和最终浏览器人工流程仍由任务 6 决定验收范围；本次没有新增完整 GUI 人工验收或云模型实测。

## 检查范围

新增 8 项轻量 Python 检查，验证入口存在、版本 / 导入限制、配置 key、Windows Settings 默认路径及 opt-in、README / 33 章锚点、实际 GUI 标签、隐私与功能边界、凭据占位值，以及实际构建 ZIP 的文档链接与 SHA 清单。没有引入大型 Markdown 工具或新依赖。

实际修改范围为文档、交付脚本的文档白名单及文档测试；src/、迁移、前端、模型配置模板、稳定 ID、引用链、service contract、算法与缓存版本均未修改。

## 质量结果

- `uv run pytest -q`：320 passed，217.97 秒；312 基线 + 8 项文档检查。
- `node --test tests/frontend/workspace.test.cjs`：25 passed，0 skipped。
- `uv run ruff check .`：通过。
- `uv run mypy --strict src`：通过，89 个源文件。
- 构建源码交付 ZIP，检查归档内文档、全部相对链接及锚点、SHA 清单：通过。
- 只有既有 Starlette / AnyIO BlockingPortal 弃用警告；未添加 Markdown 工具依赖。

## 下一步

没有发现必须阻止任务 6 的新代码问题。建议进入最终人工验收，核对普通用户能否照手册完成流程，并确认上述 GUI 展示差异是否接受、是否补截图。本任务未开始任务 6。
