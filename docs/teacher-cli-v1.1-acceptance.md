# 教师验收增强版 v1.1-A：CLI 验收记录

日期：2026-09-28。工作分支 `teacher-cli-v1.1`，起点为公开仓库 `main` 的 `b8f030093100bda1b095e9a908cb2634c275594c`。本阶段仅扩展 CLI；业务计算继续由原有 application service、repository 和 contracts 完成。

## 手工 E2E

在独立临时数据目录，用 CLI 命令逐步完成：

1. `case create`：创建一个合成案件。
2. `document import`：导入两份用 ReportLab 生成的合成 PDF，分别写有“交易金额为450000元。付款日期为2026年3月1日。”和“交易金额为500000元。付款日期为2026年3月2日。”。
3. `index build`：创建 2 个 chunk；`search 450000` 返回 2 条候选。
4. `ask`：三个问题均为 `answered`，从各 Answer 执行 `claim extract`，每次得到 1 条已验证 Claim。此段使用**确定性 FakeLLM**，没有请求 Qwen。
5. `contradiction run`：返回 1 条 `contradiction`；`timeline` 返回 `2026-03-01` 和另外两条日期未知的事实；`export --format json` 的案件 ID 与创建时相同。

另用**未配置 Qwen 的真实 CLI 进程和本地 BGE**，对仓库 `tests/fixtures/synthetic_case/01_询问笔录_王海.pdf` 完成创建案件、导入 2 页、索引状态 `not_indexed → ready`、建立 2 个 chunk、检索返回 2 条、时间线读取和 JSON 导出。没有模型生成的 Claim 时，时间线为空是预期结果。此流程也验证了 stdout 中的 `--json` 可由 PowerShell `ConvertFrom-Json` 直接解析。

两条流程均使用合成材料与隔离临时目录。真实 Qwen 的问答、抽取耗时及 30–40 秒视频时长尚未验证；演示前需使用合成材料和实际模型配置另做一次 smoke。

## 自动化覆盖

`tests/integration/test_cli_workflow.py` 覆盖新旧案件命令、所有子命令帮助、导入、SHA 去重、同一材料追加 v2、跨案件追加拒绝、索引建立与状态、检索、已验证问答与拒答、模型未配置、429/超时、Claim、非空矛盾、时间线、两种导出、JSON 成功与错误、退出码、跨案件引用隔离、未知案件、缺失与过期索引、目标文件不覆盖，并验证移除模型 key 后仍可读取已保存的 Claim、矛盾和 Timeline。现有 QA、Claim、矛盾、GUI/API 测试继续覆盖底层验证与来源链。

检查结果：`pytest -q` 为 309 passed、1 个上游 Starlette/anyio 弃用警告；前端 Node 测试 29 passed；`ruff check .` 和 `mypy --strict src` 均通过。文档与发行包链接检查包含在 pytest 中。`--json` 的运行期和参数解析错误均放在 stderr，stdout 保持为空。
