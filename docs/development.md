# CaseFile AI 开发者指南

本文面向维护者；普通用户请从 [中文用户手册](user-guide-zh.md) 开始。当前包版本 1.0.0，Python 为 3.12；Windows 交付固定 Python / uv 版本见 `scripts/windows-runtime.json`，依赖解析结果由 `uv.lock` 锁定。

以下开发命令需要完整源码 checkout。普通用户 ZIP 不分发开发测试、评估数据、构建脚本或开发 `.env.example`；不要在安装目录照抄这些开发命令。

## 源码环境与启动

在源码目录使用 PowerShell：

```powershell
uv sync --all-groups
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
uv run casefile-ai serve
```

`.env` 可选，已有文件不得覆盖。开发 CLI 读取源码根目录配置，产品 CMD 使用独立 `config.env`；均不搜索调用者 cwd，不自动迁移秘密或数据。默认仅监听 `127.0.0.1:8000`，打开该地址查看 GUI，原终端 Ctrl+C 正常停止。

`serve` 预检并执行已知 Alembic 迁移；开发时也可直接运行 factory（普通操作建议用 CLI / Windows 入口）：

```powershell
uv run uvicorn casefile_ai.interfaces.api.main:create_app --factory --reload
```

不要把 reload 开发进程和产品 launcher 混为同一实例管理入口。身份接口用于识别本机服务，不是用户认证。

## 测试与静态检查

```powershell
uv run pytest -q
uv run ruff check .
uv run mypy --strict src
node --test tests/frontend/workspace.test.cjs
```

前端检查使用 Node 自带测试器，没有新增 npm 依赖；有 Node 时 Python 回归也会运行该检查。Windows 集成检查需要真实 Windows。测试、开发和演示只使用合成或人工脱敏材料，不发送真实敏感卷宗。测试数和实际运行边界见对应阶段验收记录，不能把测试通过当成总体 AI 准确率。

## 构建与交付

```powershell
uv build --wheel
uv run python scripts/build_windows_delivery.py
```

wheel 内包含迁移、Alembic env / template、网页资源与 CLI metadata，运行不依赖仓库 cwd 或 alembic.ini。源码交付 ZIP 按白名单纳入入口、锁文件及文档，不包含 `.env`、案件、日志、模型或 venv。交付文档链接也应在 ZIP 中有效。

可用 `scripts/smoke_windows_delivery.py` 在当前 Windows 创建隔离安装和用户目录，实际调用 Setup / Prepare / Start / Stop，使用合成 PDF 验证索引与检索。该脚本会启动实际浏览器并临时使用默认端口；应先停止现有测试服务，不能宣称这是另一台物理机器或冷缓存下载验证。实际任务 4 结果见 [Windows 验收记录](stage10-task4-acceptance.md)。

## 配置与诊断

| 配置 | 用途 |
| --- | --- |
| `CASEFILE_LLM_ENABLED` | 默认 false；明确允许相应模型请求 |
| `CASEFILE_LLM_BASE_URL`、`CASEFILE_LLM_MODEL`、`CASEFILE_LLM_API_KEY` | 兼容接口地址、模型名、凭据；三项缺一视为未配置 |
| `CASEFILE_LLM_TIMEOUT_SECONDS` | 单次 HTTP 超时，默认 60 秒，不是整个分析的总时限 |
| `CASEFILE_LLM_STRUCTURED_OUTPUT_MODE` | 默认 json_schema；仅供应商要求时考虑 json_object |
| `CASEFILE_EMBEDDING_MODEL`、`CASEFILE_EMBEDDING_REVISION` | 本地模型及固定 revision，与索引可复现性有关 |
| `CASEFILE_APP_HOME`、`CASEFILE_DATA_DIR`、`CASEFILE_DATABASE_URL` | 用户目录、材料路径与 SQLite 位置 |
| `CASEFILE_LOG_DIR`、`CASEFILE_LOG_LEVEL` | 白名单安全日志位置及级别 |

`GET /health` 只表示进程存活；`GET /api/system/status` 返回结构化本地诊断，含实际数据库 / 数据 / 日志路径。GUI 面板的折叠技术详情展示 data / database / logs / config 路径，没有连接测试按钮。`connection_verified` 是本进程曾成功调用，不是实时探测。启动与状态检查不主动下载模型、加载向量或发出收费请求。

正式 serve 关闭原始 access log；白名单日志不记录问题、证据、模型内容、配置凭据或原始异常。排错使用安全类别、诊断编号与操作时间，不能扩大日志到材料正文。

## 架构与接口入口

GUI / HTTP、CLI 和正式 stdio MCP Server 统一调用 application/service。检索、事实判断和引用校验不能复制到界面。稳定 ID、引用链、缓存版本原则与 service contract 保持既定规则。

- [需求与 MVP 范围](requirements.md)
- [内部架构](architecture.md)
- [数据模型](data-model.md)
- [Service contracts](service-contracts.md)
- [证据问答规则](grounded-qa.md)
- [事实覆盖与候选](claim-coverage-candidates.md)
- [矛盾与时间线](contradictions-timeline.md)
- [数据管理及接口](data-management.md)
- [任务 2 前端验收记录](stage10-task2-acceptance.md)

评估资料需要完整源码中的合成数据和评测脚本，不随普通用户 ZIP 分发：打开 `docs/evaluation/mvp.md`、`docs/evaluation/stress.md` 与 `evals/case_sets/` 查看方法和历史基线。评估入口为 `scripts/evaluate_case_set.py`；请按这些源码说明运行，不能把小样本结果外推为总体准确率。

常用 HTTP 路径包括 `/api/cases`、案件下的 `/documents`、`/materials`、`/index`、`/search`、`/ask`、`/answers`、`/claims`、`/conflict-candidates`、`/contradictions`、`/timeline`。备份 / 恢复位于 `/api/data/`，单案件分析导出位于 `/api/cases/{case_id}/exports/{json|markdown}`，下载仅用受控 artifact ID。

MCP Python SDK 锁定为 2.2.0；正式 `casefile-ai-mcp` 入口通过 stdio 对接标准 MCP Client。导入允许目录和工具边界见 [MCP 手册](mcp.md)。普通用户操作、错误及隐私解释仍以用户手册为入口。
