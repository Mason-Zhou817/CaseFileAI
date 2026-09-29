# CaseFile AI v1.1.0 教师最终验收报告

日期：2026-09-29。分支：`teacher-final-v1.1`。基线：`96eebc2a7829cb08b778ddaf8c5e0e733550c47e`。本报告记录本轮已经执行的检查及待本人完成的人工验收，不把历史或离线结果写作本轮真实模型成绩。老师要求逐项对照见[要求矩阵](teacher-requirements-v1.1.md)。项目地址：[GitHub 源码仓库](https://github.com/Mason-Zhou817/CaseFileAI)。本分支尚未 merge、push 或打 `v1.1.0` tag；公开仓库当前能看到的内容以实际远端为准。

## 架构与入口

GUI / HTTP、Typer CLI、标准 stdio MCP 共用 application services、repository 和来源校验逻辑。PDF 与 OCR、本地 SQLite/FTS5/BGE/RRF、Evidence、QA、Claim、矛盾、Timeline、反馈、备份与分析导出形成同一案件数据链。Qwen 是**可选云端调用**，并非本地模型；外发需明确启用。GUI 操作见[人工验收 runbook](teacher-gui-acceptance-v1.1.md)，CLI 见[手册](cli.md)，MCP 见[手册](mcp.md)。

## 本轮自动化与正式入口

| 检查 | 本轮结果 | 边界 |
| --- | --- | --- |
| Python 全量 Pytest | **319 passed、1 skipped、1 warning**；0 failed，耗时 334.72 秒 | 1 项 Windows symlink 测试因 OSError 跳过；warning 来自 Starlette/anyio 弃用 API |
| 前端 Node | 30 passed、0 failed | 行为测试，不等于浏览器视觉验收 |
| Ruff / strict Mypy | 均通过；Mypy 检查 93 个源码文件 | 静态检查不能代替运行时测试 |
| Wheel | `casefile_ai-1.1.0-py3-none-any.whl` 与 sdist 离线构建成功 | 尚未公开 Windows binary |
| 正式 CLI | `casefile-ai.exe version --json` 返回 1.1.0；隔离合成案件创建、PDF 导入、索引、检索、人类输出、JSON 输出、矛盾、时间线、反馈摘要、JSON 导出成功 | 搜索 2 条；无 Qwen 时 `ask` 明确返回 `model_not_configured`，因此本轮没有正式入口的已验证 Answer、Claim 或可提交的真实反馈 |
| 正式 MCP | `.venv/Scripts/casefile-ai-mcp.exe` 与标准 `ClientSession` 完成 initialize、tools/list 及合成案件工具调用，协议 stdout 可解析；17 工具，版本 1.1.0；检索 2 条、allowlist 外路径拒绝、状态不含本地路径。Codex CLI 也完成 8 次正式工具调用 | 无 Qwen 时 `ask_case` 返回 `model_not_configured`；Timeline 和 Summary 为空，无法提交有效 Answer 的反馈 |
| Feedback → Regression | `tests/integration/test_feedback_flywheel.py` 使用合成 PDF 与确定性 provider，实际提交 `incorrect/citation`、pending、汇总、人工 gold 审核、approved、导出 1 条 staging、runner 1/1 pass | 这是离线服务/CLI 测试，不是在线训练；正式无模型环境不能新建真实模型 Answer |
| 迁移 / 备份恢复 / 交付 | 全量 Pytest 对应测试覆盖迁移 0008、备份验证、恢复新副本、ZIP 清单和链接 | 交付 ZIP 只用于本地测试，没有公开发布 |

分类重跑：CLI `test_cli_workflow.py` **4 passed**；MCP 协议与路径策略 **8 passed、1 skipped**；Feedback 闭环 **1 passed**；迁移与数据管理 **35 passed、1 上游 warning**；文档交付检查 **8 passed**。这些均是 319 项全量 Pytest 的子集，不能相加当作额外测试数。README 的 6 个图片路径与本次检查的 139 个本地 Markdown 链接均存在；README 的 2 个 Mermaid 图通过解析器。`uv lock --check --offline`、Ruff、strict Mypy 和 `git diff --check` 通过。

首轮失败分类：`test_installed_entrypoint_stdio_start_and_exit` 是环境问题，直接执行 `.venv/Scripts/pytest.exe` 时 `shutil.which` 找不到未进 PATH 的已安装 `casefile-ai-mcp.exe`；正式 MCP 进程已由绝对入口独立验证，最终重跑将 `.venv/Scripts` 放入 PATH。`test_windows_archive_contains_manual_and_resolvable_document_graph` 是文档问题：新发布说明指向未打包的教师报告；已改为源码路径文字。未修改测试掩盖失败。Windows symlink 创建返回 OSError，1 项测试按环境限制跳过；另有 1 项上游 Starlette/anyio 弃用警告。最终全量重跑无失败。

Codex CLI 的初次非交互运行受本机客户端工具调用批准设置影响，调用被运行时取消；受控重试显式允许无交互测试工具调用，并在 MCP server 配置中指定隔离数据目录。最终 `list_cases` 找到合成案件，`search_evidence` 返回 2 条，`ask_case` 返回 `model_not_configured`，`get_timeline` 为空，`submit_answer_feedback` 对不存在的 Answer 返回 `answer_not_found`，`get_feedback_summary` 为 0，`get_system_status` 报数据库、嵌入、OCR、日志和索引就绪、AI 未配置。Codex Agent 随后根据检索片段自行写出金额；**这是客户端推断，不是 CaseFile AI 的已验证 Ask 答案**，不能计入真实模型 QA 指标。标准客户端退出后未发现残留 `casefile-ai-mcp` 进程。

## 数据飞轮、有效性与效率

反馈属于本地显式数据：Question → Answer/Citation → `incorrect/citation` → pending → `rating/category` Summary → 人工填写 expected answer/evidence → approved → eval staging → 离线 regression。本轮自动化闭环导出 **1 条样本、1 pass、0 fail**，使用确定性 Fake provider。审核、升入正式版本化评测集和模型/规则改进均需人工决定；**No automatic training**。详情见[反馈流程](feedback-data-flywheel.md)。

有效性历史证据分开读：v1.0 历史 320 passed、公开 v1.0 305 passed、v1.1-A 309 passed、v1.1-B 317 passed、v1.1-C 319 passed；v1.1-D 是本轮实际 319 passed。320 与 309 有仓库验收文档；305、317、319（v1.1-C）来自本阶段任务给出的历史口径，当前源码文档未找到独立日志，不应视为本轮复测。压力集 11 份 PDF、66 页、51 题；离线检索必需证据命中@5 为 42/45。真实 Qwen 历史 9 题对齐专项：正例正确回答 5/6、负例正确拒答 3/3、false abstain 1/6；Claim 专项 17 个核心事实，严格来源 15/17；矛盾标注候选对判定 8/12。来源分别见[压力集](evaluation/stress.md)、[4C 对齐](evaluation/qa-alignment-4c-2026-09-26.md)、[Claim 专项](evaluation/claim-quality-4d-2026-09-26.md)。这些分母和测试 provider 不同，不能合成一个总体准确率。

效率历史证据：同一离线压力集 11 份文档共享索引 `18,406.804 ms`，逐题总耗时 p50 `73.775 ms`，包含 Fake provider/离线条件；[基线 JSON](evaluation/baselines/mvp-stress-v1-4b-2026-09-25.json)。真实 Qwen 9 题专项 QA p50 `21.883 s`，见[4C 记录](evaluation/qa-alignment-4c-2026-09-26.md)。它们不是同一种负载，不能直接比较成倍数或性能保证。首次模型加载、OCR、网络和 provider 费用会改变实际体验。

## 真实 Qwen 最终 smoke

2026-09-29 使用本机已有**产品模式配置**，在独立临时数据目录创建全合成案件并导入 `tests/fixtures/synthetic_case/01_询问笔录_王海.pdf`（2 页）。正式 `Settings → build_application_runtime → OpenAICompatibleLLMProvider → DefaultQAService` 路径确认 provider 已启用且配置完整。源码模式默认读取仓库 `.env`，此前隔离 CLI/MCP 测试显式使用不存在的配置文件，所以返回 `model_not_configured`；这不代表本机产品配置缺失。测试只读取产品配置供运行时使用，没有复制凭据到仓库或输出密钥、完整 endpoint。7 题按预定顺序各执行一次，未重刷失败题。

| 指标 | 本轮真实 Qwen 结果 |
| --- | --- |
| 固定问题 | 7；可回答 5、无证据 2 |
| answered / validated | **5 / 5** |
| abstained | **2**，均为预期无证据题 |
| false answer / false abstain | **0 / 0**，仅限此固定小样本 |
| provider error / 其他运行错误 | **0 / 0** |
| Citation legality | 5/5 已回答题通过：Evidence ID 属于该轮、文件与版本/页匹配、引用原文位于实际 PDF 页、Claim 引用均绑定返回 Citation |
| Question / Field / Source Alignment | 5/5 已回答题的服务验证状态为 `validated`；指定补充说明第 2 页的付款人题只引用第 2 页，否定/主体题引用均来自第 2 页 |
| Claim 抽取 | 金额与日期两个已验证 Answer 各抽取 1 条；类别分别为 `amount`、`date`，2/2 来源绑定合法 |

逐题结果：①金额 50 万元，answered/validated，引用第 1 页；②付款日 2021-06-01，answered/validated，引用第 1 页；③收款主体星河科技有限公司，answered/validated，引用第 1、2 页；④限定补充说明第 2 页的付款人王海，answered/validated，只引用第 2 页；⑤李明未代付、付款人为王海，answered/validated，两条引用均在第 2 页；⑥银行流水最终入账**时刻**无证据，正确拒答；⑦约定年利率无证据，正确拒答。回答总耗时依次约 28.9、36.9、30.2、32.0、39.6、3.9、6.3 秒，仅为本机本次观测，不能当作性能承诺。**7 题小样本中没有观察到错误回答，不意味着系统永不幻觉。**

## 稳定、安全与可观测性

`/health` 供存活检查；`/api/system/status` 显示数据库/迁移、本地目录、OCR/嵌入、QA provider、日志与版本；材料和索引状态说明能否检索；QA 记录验证/拒答原因与 timing；反馈汇总按 rating/category 计数。GUI 可见系统是否就绪和主要失败类别。API 状态可以含供本机用户排障的路径，MCP `get_system_status` 则仅返回脱敏白名单字段；两者用途不同。没有集中监控、告警或 Prometheus。

安全边界包括 local-first、案件和 Answer 归属检查、Citation 本轮白名单与跨案校验、日志敏感文本控制、provider 凭据不回显、MCP 导入 allowlist 默认拒绝、路径穿越/网络路径处理、备份内容校验、ZIP slip 防御和只返回内容的 MCP 导出。正式 MCP 工具列表没有 delete、restore、shell、SQL 或任意文件读取工具。自动测试覆盖跨案件及恶意路径；Windows 上符号链接创建不可用，相关一项跳过。允许目录存在文件系统竞态，备份/导出未加密；云 Qwen 和 MCP 客户端各有外发边界，不声称绝对安全。

## 待本人完成的交付检查

- 按[GUI runbook](teacher-gui-acceptance-v1.1.md)在真实浏览器逐项签收四种反馈评分、summary、备份和状态。
- 在有模型配置的合成案件上完成 CLI 的 Ask → Claim → Feedback 正式入口人工检查，并复核 `--json`。
- 在有模型配置的合成案件上由本人复核 Codex CLI 的 `ask_case` 成功回答、Citation 与真实 Answer 反馈；当前 Codex CLI 已完成协议和安全拒绝路径，未验证生成成功路径。
- 录制并人工检查[5 分钟讲解](teacher-demo-5min.md)，填入[企业微信文案](teacher-submission-message.md)的视频链接，再由本人发送。当前没有视频链接，也未发送。
- 最终人工验收后再决定 merge、push、tag。当前不建议立即 merge/push/tag，也不建议声称已完成教师交付。
