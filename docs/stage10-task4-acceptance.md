# 第 10 阶段任务 4 验收记录

基线：`7eae1ac73e9874da50d2770bd6ffbd38df3e1036`。验证日期：2026-09-27。
任务 2 已由用户人工验收；任务 3 数据生命周期 / 备份 / 恢复 / 导出保持。本任务不开始任务 5。

## 审计与交付方案

实施前确认：迁移依赖仓库根目录的 alembic.ini / migrations，旧 wheel 不完整；web 文件本来依据模块路径定位；源码配置已不依赖 cwd，但仍使用仓库 .env；正式 MCP entry 尚未接通。此次修复 wheel 资源，增加产品配置模式，并修正 MCP 文档与退出提示，没有扩大 MCP 功能。

采用源码交付目录 + uv 锁定环境，不使用 PyInstaller。四个 CMD 入口调用 UTF-8 BOM PowerShell 5 脚本，再调用安装环境中的 Python Windows adapter。应用仍通过同一 application bootstrap / service 处理案件。

运行参数固定在 `scripts/windows-runtime.json`：Python 3.12.13、uv 0.11.21。Hatchling 固定为实际构建验证的 1.32.4，运行依赖由既有 uv.lock 锁定，`uv lock --check` 通过，锁文件未升级。Setup 安装 core / OCR / embeddings，重复运行只强制重装本应用以确保源码更新生效，已满足的依赖保留。没有 API key 输入要求，也不自动启用云模型。

模型准备单独显示阶段、加载真实向量 / OCR 模型并用合成输入检查。使用既有缓存机制，不拷贝大模型到源码目录，不访问业务数据库。失败保留数据、返回非零退出码和安全分类，可以重试。

## 配置、资源与数据隔离

- 产品入口默认使用用户根目录 config.env；仅首次复制无凭据模板，不覆盖已有文件，不复制开发 .env。发现安装目录旧开发配置 / 数据时明确提示迁移步骤。
- 源码开发模式仍读取仓库 .env，显式 CASEFILE_ENV_FILE 保留；相对产品配置路径以用户根目录为基准。
- 用户默认目录 `%LOCALAPPDATA%\CaseFileAI`，程序环境为安装目录 `.runtime`。拒绝 app_home / data / database / logs 位于程序目录内。
- wheel 纳入全部 7 个迁移、env.py / template、HTML / JS / CSS、CLI 与 package metadata。迁移优先 package 内资源，无仓库 alembic.ini 依赖；源码仍只有一份迁移源文件。
- 从非仓库 cwd 和中文 / 空格路径启动时，应用资源、用户配置、数据库、日志均可定位。更新程序不会删除用户目录。

## 实例与停止机制

用户目录 runtime/launcher.lock 排除并发 Setup / Start / Stop。实例 metadata 只记录 application、version、UUID、PID、真实进程创建 FILETIME、真实 executable、启动时间、端口；不含控制凭证、云 API key 或业务内容。

Windows venv 可能通过 redirector 创建真正的 Python 子进程，因此由服务自身登记实际 PID 和 native process identity，而不把父启动器 PID 当作 server PID。Start 同时核对进程仍存活、创建时间、可执行文件、identity endpoint、版本与 health；重复启动复用已有实例。死进程 / PID 复用 / 损坏过期记录可恢复，身份不符时不强杀进程。

独立随机控制凭证保存在 `runtime/control-{instance_id}.token`，不在命令行、metadata JSON、identity 响应或日志中。它是本机停止能力凭证，不是网页认证；依赖用户目录权限，不承诺多用户隔离。

Stop 只向已核对的实例发送带凭证的本机停止请求；Uvicorn 正常停止并执行 application lifespan 清理，等待退出后清理本实例 metadata / token。没有生产 taskkill、os.kill 或按 Python 名称杀进程。超时保留服务并提示重试；测试中的强制结束只用于模拟测试所属实例崩溃。

默认绑定 `127.0.0.1:8000`，其他程序占用时明确失败，不自动换端口。明确指定其他端口时记录实际端口，Stop 可不重复传入端口。身份 endpoint 不返回 secret。停止接口拒绝无 / 错凭证和浏览器 Origin。

服务的 health 与身份可用后才调用默认浏览器。关闭浏览器不停止服务。开发入口 / 其他用户目录的实例没有当前入口的控制凭证时，提示使用其原入口停止。

## 自动化覆盖

新增 `tests/integration/test_windows_delivery.py` 共 29 项（包含参数化实例），使用真实 Windows 进程、SQLite、生产 HTTP、PowerShell 与 CMD。

| 用户要求 | 验证 |
| --- | --- |
| A–B | 真实首次及重复 Setup；无云凭据成功；锁定 Python / Torch / OCR / embedding 版本 |
| C–E | 非仓库 cwd、ready 后浏览器 callback、三次及并发 Start、同一 PID / UUID |
| F–G | 死 PID、PID 复用、损坏过期 metadata 恢复；不向其他 Python 发送停止请求 |
| H | 其他监听程序占用时失败，浏览器不打开 |
| I–K | 正常停止 / 重启、其他 Python 保留、模拟崩溃恢复、停止超时不强杀 |
| L / X–Y | metadata 无凭据配置、独立控制 token 不入公开身份或日志；故障异常包含的 synthetic secret 不泄漏 |
| M–P | 中文 / 空格路径、用户数据在程序外、更新源码确实进入安装环境、配置 / 数据字节保留 |
| Q–R | 未准备模型提示、模拟模型失败的非零退出码与现有数据保留 |
| S–W | 构建并安装真实 wheel 到新 venv；非仓库 cwd CLI serve；迁移、health、首页、JS / CSS、版本信息 |
| Z | 用 Windows Get-NetTCPConnection 核对实际监听仅为 127.0.0.1，真实 loopback HTTP 可用 |
| 额外 | 丢失 uv 的官方流程 / 阶段提示、单独停止认证 / Origin 拒绝、启动失败不打开页面、旧版本拒绝、相对配置路径、源码包白名单 / SHA、执行策略不全局修改、实际 CMD 管理自定义端口 |

## 实际 Windows smoke

在当前 Windows 上执行，不是另一台物理机器：

1. 核实原开发 `uv run casefile-ai serve` 的进程链与专用控制台，仅向该控制台发送正常 Ctrl+C；8000 原服务退出、开发入口文件解除占用。
2. 运行真实 Setup。默认用户位置创建独立配置 / 自检；桌面 Start / Stop 快捷方式创建成功，并核对目标为程序 CMD。
3. 构建源码交付 ZIP，解压到临时 `案件分析 CaseFile AI` 目录；用户数据位于另一个 `用户 数据 中文` 目录，操作 cwd 为 `非仓库 工作目录`；从零创建该程序的独立 venv。
4. 实际执行 CMD Setup 与 Prepare Models，加载真实 BGE 与 RapidOCR，以合成输入检查。没有 Qwen 配置或调用。
5. 默认 8000 Start 等待就绪后调用实际默认浏览器。Windows 接受打开请求，并检测到 CaseFile AI 浏览器窗口；没有仅用 mock 宣称打开完成。
6. 建立合成测试案件，第二 / 第三次 Start 保持相同 PID / UUID，正常 Stop 后端口和记录清理。
7. 再次 Setup，检查既有配置 SHA 与数据标记不变；Start 后测试案件 ID 保持。
8. 导入中文文件名的合成 PDF，以真实 BGE 建立索引并混合搜索命中；Stop / Start 后文档 ID 和 ready 索引状态保留。
9. 模拟 stale metadata，再次 Start 恢复；停止后让其他测试监听程序占用 8000，Start 返回非零码和“端口 8000 已被其他程序占用”，不另起 server；释放测试端口后可再次启动 / 停止。

可重复脚本为 `scripts/smoke_windows_delivery.py`，本次结果 JSON 为 passed，browser_dispatch_accepted 与 casefile_browser_window_seen 均为 true。测试用 user data / 程序目录保留在本机临时目录，不修改真实业务卷宗或原 .env。浏览器只核对自动打开与窗口出现，案件 / 导入 / 索引的断言通过真实 HTTP 完成，没有声称人工点击全部 GUI。

独立环境复用本机系统原生运行库、uv 包缓存及已存在的 Hugging Face 缓存；不代表全新物理机器或空模型缓存的完整下载测试。缺 uv 分支与模型失败重试边界有自动化检查，原生运行库要求在 Windows 说明中列明。

## 质量检查

- `uv run pytest -q`：312 passed，203.83 秒；283 基线 + 29 新增。
- Windows 集成检查单独复核：29 passed，108.35 秒。
- `node --test tests/frontend/workspace.test.cjs`：25 passed，0 skipped。
- `uv run ruff check .`：通过。
- `uv run mypy --strict src`：通过，89 个源文件。
- `uv build --wheel`：通过，Hatchling 1.32.4。
- wheel isolated smoke 与 Windows launcher smoke：通过。
- 仅有既有 Starlette / AnyIO BlockingPortal 弃用警告。

## 文件清单

共 24 个文件：

- `.gitignore`
- `README.md`
- `pyproject.toml`
- `config.env.example`
- `Setup CaseFile AI.cmd`
- `Start CaseFile AI.cmd`
- `Stop CaseFile AI.cmd`
- `Prepare Models CaseFile AI.cmd`
- `scripts/windows.ps1`
- `scripts/windows-runtime.json`
- `scripts/build_windows_delivery.py`
- `scripts/smoke_windows_delivery.py`
- `docs/windows-installation.md`
- `docs/stage10-task4-acceptance.md`
- `src/casefile_ai/config.py`
- `src/casefile_ai/application/services/model_preparation.py`
- `src/casefile_ai/infrastructure/db/migration.py`
- `src/casefile_ai/infrastructure/safe_logging.py`
- `src/casefile_ai/interfaces/api/main.py`
- `src/casefile_ai/interfaces/mcp/server.py`（只纠正入口提示）
- `src/casefile_ai/interfaces/windows/__init__.py`
- `src/casefile_ai/interfaces/windows/process_identity.py`
- `src/casefile_ai/interfaces/windows/launcher.py`
- `tests/integration/test_windows_delivery.py`

## 交付边界

未修改 retrieval、QA / Claim prompt、alignment、citation validation、contradiction judge、OCR 或 Timeline；数据 ID、迁移内容与缓存版本不变。无永久删除、云备份、自动更新、托盘、Windows 服务、大型 exe bundle 或本地 LLM。

可进入任务 5，没有已确认代码 blocker。独立物理机器、空缓存网络安装、企业组策略等仍是验证边界；大型同步操作的等待和磁盘成本保留。根据已通过的 setup / start / stop 和 wheel 结果，本阶段 MVP 暂不建议增加 PyInstaller；本次不开始任务 5。
