# 教师要求与 v1.1.0 验收矩阵

本矩阵以 2026-09-29 的源码为对象。自动化证据见[最终报告](teacher-v1.1-final-report.md)；历史评测不能当作本轮重新测得的成绩。所有演示材料为全合成材料。人工 GUI、CLI、MCP 及视频验收仍须由提交者确认。

| 要求 | 实际实现及入口 | 可复核证据 | 限制 |
| --- | --- | --- | --- |
| 有效性 | `application/services/qa.py` 将检索、问答、引用、支持与问题对齐串联；[问答设计](grounded-qa.md) | `tests/integration/test_qa_service.py`、[真实模型历史记录](evaluation/real-model-smoke-2026-09-26.md) | 历史集与本轮 smoke 很小；校验通过不是事实正确性的证明 |
| 高效性 | `infrastructure/retrieval/` 的本地 BM25、BGE、RRF；[检索设计](retrieval-foundation.md) | [压力集](evaluation/stress.md)、[4C 对齐记录](evaluation/qa-alignment-4c-2026-09-26.md) | 首次模型加载、索引、Qwen 网络延迟不同；不能把离线毫秒与真实模型秒数混合 |
| 稳定性 | Alembic 迁移、索引状态、provider 分类错误、备份校验；[数据管理](data-management.md) | `tests/integration/test_database_migrations.py`、`test_material_status.py`、`test_data_management.py` | 单机 SQLite；无后台任务取消和高可用集群 |
| 安全性 | 案件隔离、Citation 白名单、MCP 导入允许目录、路径和备份校验；[MCP 边界](mcp.md) | `tests/unit/test_mcp_path_policy.py`、`tests/integration/test_mcp_protocol.py`、`test_data_management.py` | 本机有权限的恶意进程仍可能造成路径检查与打开间竞态；云模型需自行判断隐私 |
| 可观测性 | `/health`、`/api/system/status`、材料/索引状态、QA 状态、反馈汇总与安全日志；`application/services/system_status.py` | `tests/integration/test_product_runtime.py`、`test_material_status.py`、`test_feedback_flywheel.py` | 无集中式监控或告警；`/health` 不能替代逐项状态检查 |
| 智能随使用升级 | 反馈经人工审核成为版本化离线评测样本，回归结果指导下一版本改进；[反馈流程](feedback-data-flywheel.md) | `tests/integration/test_feedback_flywheel.py` | 当前不自动训练、不自动更新 Prompt 或模型 |
| 数据飞轮 | Answer/Citation → feedback → pending → review → approved → eval staging → runner；`application/services/feedback.py` | `test_feedback_flywheel.py` 的合成闭环与[最终报告](teacher-v1.1-final-report.md) | 审核由人执行；导出可能含案件信息，需人工检查 |
| GUI | `interfaces/web/`、`interfaces/api/` 提供材料、问答、Claim、矛盾、时间线、反馈和系统状态；[GUI 手册](user-guide-zh.md) | `tests/integration/test_gui_shell.py`、`test_frontend_behaviors.py`、30 项 Node 测试；[人工 runbook](teacher-gui-acceptance-v1.1.md) | HTTP/DOM 自动测试不等于真实浏览器视觉和交互验收 |
| CLI | `interfaces/cli/main.py` 正式入口 `casefile-ai`，支持人类输出和 `--json`；[CLI 手册](cli.md) | `tests/integration/test_cli_workflow.py`、[v1.1-A 记录](teacher-cli-v1.1-acceptance.md) | 新 Ask/Claim 需明确配置模型；无配置时安全拒绝 |
| MCP | `interfaces/mcp/server.py` 正式 stdio 入口，17 个工具；[MCP 手册](mcp.md) | `tests/integration/test_mcp_protocol.py` 与[最终报告](teacher-v1.1-final-report.md)的正式入口检查 | 本地 stdio；客户端对工具结果的转发不受服务端控制 |
| GitHub | [公开源码仓库](https://github.com/Mason-Zhou817/CaseFileAI) | 仓库 Git 历史与本分支提交 | 本轮未 push；公开仓库能否看到 v1.1 取决于后续人工决定 |
| 原创性 / AI 辅助 | [原创性与 AI 使用说明](originality-and-ai-usage.md)、`THIRD_PARTY_NOTICES.md` | 真实 Git 历史、依赖与许可证声明、提交者人工审阅 | 扫描不能证明绝无抄袭；个人完成责任需由本人确认 |
| 5 分钟 Demo | [中文旁白与操作 runbook](teacher-demo-5min.md) | 按时间段录制后人工检查时长、画面和声音 | 视频尚需本人录制并提供链接；脚本不是已完成视频 |

## 口径

历史 Pytest 记录：v1.0 历史封版 320 passed；公开 v1.0 305 passed；v1.1-A 309 passed；v1.1-B 317 passed；v1.1-C 319 passed。其中 320 与 309 有仓库验收文档；305、317、319 为本阶段任务给出的历史数字，未在当前源码文档中找到独立日志，不能作为本轮复测结果。这些快照不能相加。v1.1-D 仅采用[最终报告](teacher-v1.1-final-report.md)的实际本轮结果。离线检索、Fake provider、真实 Qwen、Claim 和矛盾指标也各自有不同分母，不能合成一个“总体准确率”。
