# Windows 安装与运行（第 10 阶段任务 4）

本仓库目前仅提供源码；公开 Windows 二进制交付待第三方许可审查（Binary release pending licensing review）。下列 ZIP 操作说明记录已验证的本地交付流程，不表示 GitHub Release 已提供 ZIP。可先按[开发者指南](development.md)从源码运行，或自行构建交付包。

本交付采用 Python + uv 管理环境和轻量本地入口，面向 Windows x64。无需管理员权限，不是单文件 exe，不安装系统服务或托盘，不实现自动更新。AI 判断与来源规则不变。

普通用户的案件与材料操作见 [中文用户手册](user-guide-zh.md)。当前程序版本为 1.0.0，发布能力与限制见 [发布说明](../RELEASE_NOTES.md)。开发构建、测试和内部架构见 [开发者指南](development.md)。

## 第一次准备

1. 将交付 ZIP 解压到一个可写、长期保留的程序目录，例如 `C:\Tools\案件分析 CaseFile AI\`。不要把案件数据放进该目录；不要直接在 ZIP 浏览窗口里启动。
2. 双击 `Setup CaseFile AI.cmd`。脚本检查 uv，准备固定 Python 3.12.13，按 `uv.lock` 安装 core / OCR / embeddings，并运行本地应用自检、迁移与目录检查。无需 API key，不自动开启云模型。
3. 如果 uv 不存在或版本不符，按界面提示访问 [官方 uv 0.11.21 发布页](https://github.com/astral-sh/uv/releases/tag/0.11.21)，下载 `uv-x86_64-pc-windows-msvc.zip`，解压其中 `uv.exe` 到程序目录 `.tools\uv.exe`，重新双击 Setup。脚本也识别 PATH 与 `%USERPROFILE%\.local\bin\uv.exe`。不会执行远程安装脚本、修改系统 PATH 或静默升级 uv；其他官方安装方式见 [uv 安装说明](https://docs.astral.sh/uv/getting-started/installation/)。
4. 双击 `Prepare Models CaseFile AI.cmd`。明确显示“正在准备本地向量模型”“正在检查 OCR 模型”，完成实际合成输入检查。首次环境安装 / 模型准备需要联网、磁盘空间和等待时间。也可运行 Setup 的 `-PrepareModels` 参数完成这一步。
5. 以后双击 Start；结束工作时双击 Stop。Setup 默认尝试创建桌面 Start / Stop 快捷方式，失败只提示，不影响安装；也可直接使用程序目录里的 CMD。

Setup 可以重复执行，保留配置与案件，只补齐或同步锁定环境。必须先停止 CaseFile AI 再重新 Setup。它不会自动删除旧数据或覆盖已存在的 config.env。
Setup / Prepare 完成后保留控制台结果，按任意键关闭；自动化检查可设置 `CASEFILE_NO_PAUSE=1`。

Windows 本地推理还依赖原生运行库。[ONNX Runtime 官方安装要求](https://onnxruntime.ai/docs/install/#requirements)列出 Visual C++ 运行库前置条件；若安装成功但本地依赖无法载入，请按其链接从微软官方页面安装适用的 x64 可再发行运行库，再重跑 Setup / Prepare。本项目不下载或静默安装系统级运行库。

## 程序与用户目录

```text
CaseFileAI/                       # 程序目录，可在更新时替换代码
  src/casefile_ai/
  migrations/                    # 唯一迁移源码，构建时纳入 package
  pyproject.toml
  uv.lock
  config.env.example
  scripts/windows.ps1
  scripts/windows-runtime.json
  Setup CaseFile AI.cmd
  Start CaseFile AI.cmd
  Stop CaseFile AI.cmd
  Prepare Models CaseFile AI.cmd
  .tools/uv.exe                  # uv 缺失时放置固定版本；不随源码提交
  .runtime/                      # Setup 建立的独立 Python 环境

%LOCALAPPDATA%/CaseFileAI/         # 用户目录，更新代码不会覆盖
  config.env
  data/casefile-ai.db
  data/originals/
  logs/app.log
  logs/error.log
  logs/launcher.log
  runtime/instance.json
  runtime/control-{instance_id}.token
  runtime/launcher.lock
  data-management/               # 备份、导出、预检和恢复副本
```

`CASEFILE_APP_HOME` 可明确指定其他绝对用户根目录。数据、数据库、日志可通过已有配置分别指定；产品入口拒绝它们位于程序目录内，以免更新覆盖数据。中文和空格路径受到支持，所有入口依据脚本安装位置 / package 资源定位，不要求用户先 cd 到仓库。

## 启动、停止与实例

Start 做轻量依赖 / 路径 / 迁移资源检查，不加载或下载模型。默认只监听 `127.0.0.1:8000`。等待 `/health` 以及 application identity 与进程记录匹配后才打开默认浏览器；浏览器打开失败会显示可手动访问的 URL。关闭浏览器不停止服务。

重复 Start 通过用户目录锁、PID、进程创建 FILETIME、真实可执行文件路径、实例 UUID、版本、identity endpoint 与 health 综合识别，复用已有实例，不创建第二个 server 或并发迁移。metadata JSON 不包含控制凭证、API key、问题、证据或 prompt。独立的本地停止凭证只存在 runtime token 文件，不返回 identity，不进入日志 / 备份 / 导出；依赖 Windows 用户目录权限保护，不能当作网页身份认证或多用户安全边界。

Stop 仅向进程身份匹配的实例发送带独立停止凭证的本机请求；服务设置 Uvicorn 正常退出标记，等待在途请求和 application lifespan 清理数据库 / client / encoder。停止接口拒绝无凭证、错误凭证、带浏览器 Origin 的请求。等待超时提示稍后重试，不默认强杀，不使用 taskkill/python.exe 匹配。

正常退出清理本实例 metadata 与 token；锁文件保留但操作系统释放锁。进程崩溃后 Start 验证进程不存在或创建时间不符，再清理过期记录并重新启动，PID 复用不会导致停止其他 Python。运行中的实例无法验证时安全失败，不贸然删记录或杀进程。不同版本实例要求先用原入口停止，再更新。

由开发 CLI 启动的新版本服务可凭 identity / health 被识别并打开，但没有产品控制凭证；Stop 明确要求回到原终端 Ctrl+C。较老、不提供 identity 的服务不能仅凭端口被认定为 CaseFile AI，请先在原终端停止它。

默认端口被其他程序占用时明确提示“端口 8000 已被其他程序占用”，不自动跳到 8001。可在 PowerShell 使用 `Get-NetTCPConnection -LocalPort 8000 -State Listen` 查看占用 PID，再核实该程序。不要盲目杀进程。高级测试 / 明确配置场景支持入口 `-Port 端口号`，仍只监听 127.0.0.1；后续 Stop 通过已保存的端口管理该实例。

## 配置与旧开发数据

产品入口设置产品模式，默认使用 `%LOCALAPPDATA%\CaseFileAI\config.env`；首次仅复制不含凭据的模板。开发 CLI 仍按现有规则读取源码仓库 `.env`，不依赖当前工作目录。`CASEFILE_ENV_FILE` 可明确指定配置文件，已有同名进程环境变量优先于文件。

不会自动把旧 `.env` 的 API key 复制到用户目录，也不自动切换到旧项目 data。旧开发数据应先通过旧运行方式创建完整备份，再在产品 GUI 验证并恢复到独立用户目录；如要使用恢复副本，停止服务后同时修改 `CASEFILE_DATA_DIR` 与 `CASEFILE_DATABASE_URL` 并重启。旧目录和配置保留以便回退。参见 [数据管理说明](data-management.md)。

无云配置可以创建案件、导入原生 PDF、准备本地 OCR / 向量模型后处理扫描 PDF、索引、搜索、备份、恢复、导出和查看已有结果。QA、事实抽取及部分语义判定仍按原规则要求配置并明确启用模型，GUI 会说明。不要把真实敏感卷宗发往公共模型 API；系统不自动脱敏。

## 模型准备与缓存

Setup 安装锁定依赖；单独 Prepare Models 显式下载 / 加载模型并做合成输入检查，不访问或修改案件数据库。失败返回非零退出码与安全类别，已有数据不受影响，可稍后重新运行。

向量模型使用固定 BGE revision 与 Hugging Face / sentence-transformers 原有缓存，默认 `%USERPROFILE%\.cache\huggingface\hub`。支持其原有 `HF_HOME`、`HF_HUB_CACHE`、`SENTENCE_TRANSFORMERS_HOME` 设置；使用自定义位置时要在 Setup / Prepare / Start 使用一致环境，不将几 GB 缓存复制到项目。

RapidOCR 使用锁定版本自身的模型配置与默认 `model_root_dir` / package models 机制，通常在 `.runtime\Lib\site-packages\rapidocr\models`。这类模型属于可重建运行环境，更新后可再次 Prepare，不是案件数据。系统状态与 launcher 通过配置、manifest 及必要文件存在 / 大小识别“已准备 / 未准备”，不对整个模型目录做哈希扫描；Prepare 另外执行实际加载与合成输入检查。

缓存存在不等于本进程已加载；系统状态保留这一差异。启动不主动探测云连接。

## 常见错误与日志

| 提示 / 阶段 | 处理 |
| --- | --- |
| uv 缺失 / 版本不符 | 从官方固定版本下载 uv.exe 放到 .tools，重新 Setup |
| python / dependencies 阶段失败 | 检查网络、官方下载连接、磁盘空间、安装目录权限后重跑；原数据不会清空 |
| 环境未准备 | 先 Setup；不要移动已经建立的 venv，移动程序后重新 Setup |
| 配置无效 / 数据位于程序目录 | 检查 config.env 的路径，使用独立用户目录 |
| 端口占用 | 核实占用程序并正常停止；不自动换端口 |
| 实例无法验证 | 查看原终端与系统状态，不强杀未知 PID |
| 启动失败 | 查看 logs/app.log、error.log、launcher.log 与目录权限 / 数据库情况 |
| 模型未准备 / 准备失败 | 运行或重跑 Prepare Models，检查缓存与网络 |
| 停止超时 | 等待正在进行的同步操作结束后重试；关闭浏览器不是停止 |

launcher.log 为安全 JSON，只记录时间、阶段、结果 / 类别、PID、端口，每份 2 MiB，保留 3 个轮转文件。不记录原始异常、下载输出、完整命令、配置内容、API key、Authorization、停止凭证、问题、原文、prompt 或模型响应。原有应用日志白名单保持不变。CMD 仅对本次 PowerShell 进程使用 ExecutionPolicy Bypass，不修改全局执行策略；组织组策略禁止脚本时需要按组织政策处理，不能保证绕过。

## 更新与卸载

更新前停止服务并创建完整备份。将新版本程序文件解压到新可写程序目录或替换旧目录的代码，保留同一个用户目录；重新 Setup 和 Prepare Models，再 Start。新目录不会携带旧 venv，Setup 会重建 / 同步运行环境和快捷方式；uv.lock 固定版本不会静默升级核心依赖。数据库按现有 Alembic 迁移，升级前的备份用于必要的回退，不应直接用旧代码打开新 schema。

脚本只安装程序环境、首次创建配置并运行应用迁移；没有删除用户 data / logs / 备份 / 导出的更新步骤。已存在 config.env 不覆盖，秘密不自动迁移。

卸载程序时先 Stop，删除程序代码 / 环境目录与对应桌面快捷方式，保留用户目录和 Hugging Face 缓存。如果程序目录仍有尚未迁移的旧开发 data、手工备份或 .env，请先备份并核实它们，不能整目录盲删。彻底删除案件数据是额外的手工操作：先确认备份与保留要求，再在资源管理器核实实际用户数据目录、外置数据库及恢复副本，按自己的选择删除。程序没有永久删除案件接口，不提供自动清空脚本；不要删除共享 Python / uv / 模型缓存来卸载单个应用。

## 构建与交付边界

开发者使用 `uv build --wheel` 构建 package；wheel 纳入全部 7 个迁移、Alembic env / template、web HTML / JS / CSS、CLI entry 与 package metadata。运行时不需要仓库 alembic.ini，资源由 package 定位。`uv run python scripts/build_windows_delivery.py` 生成 `dist/CaseFileAI-1.0.0-windows.zip`；按白名单打包程序源码、对应版本 wheel、锁文件、入口、用户手册和发布说明，不含 `.env`、案件、日志、venv 或模型缓存，并包含文件 SHA 清单。

MCP contract / adapter 测试保留，但正式 `casefile-ai-mcp` 入口尚未接通，明确属于开发 / 未来功能，不属于本 Windows MVP 交付。本任务没有顺带实现 MCP。

后续 v1.1-B 已提供正式本机 stdio MCP 入口；上述文字仅记录 v1.0 Windows 交付时的状态。当前使用方法见 [MCP 手册](mcp.md)。

v1.0.0 使用源码、锁定环境与 wheel 交付，没有 PyInstaller 单文件 exe。exe 不会消除模型大小、首次下载、配置、数据隔离和更新兼容问题；后续是否打包应由实际用户安装反馈决定。任务 4 当时未包括完整用户手册，现已另有任务 5 的 [中文用户手册](user-guide-zh.md)。

本任务的实际测试、Windows smoke 边界与文件清单见 [任务 4 验收记录](stage10-task4-acceptance.md)。
