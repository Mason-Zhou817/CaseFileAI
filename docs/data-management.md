# 案件数据管理与任务 3 验证记录

普通用户操作请先阅读 [中文用户手册](user-guide-zh.md)；本文保留数据结构、校验和任务 3 历史验证细节。以下基线与“未开始任务 4”指当时任务边界，不是当前版本状态。

基线：`16092f0acaeb8ce635cc0709b987a77e3e05ed12`。验证日期：2026-09-27。
任务 2 已由用户人工验收。本任务增加生命周期、完整备份 / 副本恢复与单案件分析导出，不开始任务 4。

## 实施前数据审计

任务 3 当时的 Alembic head 为 `20260927_0007`。`cases.status` 已存在，默认 `active`，因此归档复用该字段，无新增迁移。稳定 ID、原 contract、算法版本与缓存版本不变；新增数据管理 contract 独立放在 `application/data_contracts.py`。当前 head 与 Feedback 的变更见 [反馈与数据飞轮](feedback-data-flywheel.md)。

| 数据 | 持久化关系 |
| --- | --- |
| 案件直接关联 | documents、chunks、evidence、questions、contradiction_results 直接引用 case_id |
| 材料与页面 | document → document_version → page；每个版本保存内容 SHA、storage key、解析版本、页数与版本号 |
| 检索 | chunk 关联版本 / 页、文本位置、内容哈希与分块版本；embedding 关联 chunk、模型版本与维数；FTS 关联 chunk / case 与分词版本 |
| 回答来源 | question → answer；AnswerCitation → answer / evidence，回答 JSON 保存回答内的来源 Claim ID |
| 原子事实 | Claim → answer，JSON 保留来源回答 Claim ID；ClaimEvidence → AnswerCitation，最终追溯到 Evidence → chunk → page → document_version |
| 矛盾 | 保存候选 ID、左右 Claim ID、判定和引用快照；快照保留材料 / 版本 / 页 / 原文 |
| Coverage / 候选 / 时间线 | 当前没有独立结果表，由现有确定性 service 从已保存回答、Claim 和矛盾结果派生 |

原始 PDF 位于实际 `CASEFILE_DATA_DIR/originals/{document_version_id}.pdf`。每个版本保存独立文件，增加 v2 不替换 v1。导入先保存版本 PDF 再提交数据库引用；既有原文件不可变，本任务没有删除功能。数据库可位于 data 目录之外，备份从正在使用的 engine 取得数据库快照。

## 重命名与归档

在当前案件附近输入名称并保存。API 和 application service 都验证去除首尾空格后非空、最长 200 字符，不要求全局唯一。只更新名称与更新时间，案件 ID 和来源记录不变；成功后同步案件列表、当前标题和浏览器标题。

归档将 `status` 设置为 `archived`，恢复归档设置为 `active`。默认 GUI、CLI 与 MCP 案件列表只返回 active；在本任务验收时正式 MCP 入口尚未接通，后续 v1.1-B 已接通。GUI 勾选“显示已归档案件”后可选择已归档案件。归档案件仍可读材料、原文件、历史问答、引用、事实、矛盾和时间线，也可继续处理 / 分析 / 导出。UI 明确说明这一行为。没有案件或文档永久删除 API、按钮或 cascade 删除。

## 完整备份与分析导出的区别

| 操作 | 范围与用途 | 能否恢复完整工作数据 |
| --- | --- | --- |
| 创建完整备份 | 全部案件、SQLite、所有被数据库引用的 PDF 版本及 manifest | 可以，恢复到新副本 |
| 导出案件分析 | 一个案件已有分析，JSON 或 Markdown，包含必要原文引用 | 不可以，没有完整数据库、PDF 与索引 |

两种操作均在本机执行，不调用模型、不自动上传。配置、API key、Authorization、`.env`、日志、模型权重缓存不打包。数据库自身包含业务问题、回答、证据等，PDF 也包含案件内容；排除凭据配置不等于数据脱敏。用户放入案件材料中的秘密文本仍会成为材料的一部分。产物未经加密，请妥善保存。

## 数据位置与备份结构

Windows 默认根目录为 `%LOCALAPPDATA%\CaseFileAI`。结构化系统状态接口返回实际配置路径，GUI 系统状态折叠技术详情也显示具体路径；默认位置和自定义配置说明见用户手册。数据管理产物位于 `CASEFILE_APP_HOME/data-management/`：

```text
data-management/
  artifacts/{artifact_id}/             # 已验证备份或 JSON / Markdown，附产物登记
  restore-previews/{restore_id}/       # 成功预检的解包副本和预检信息
  restored/{restore_id}/               # 确认后创建的新工作数据副本
    casefile-ai.db
    originals/{document_version_id}.pdf
    backup_manifest.json
    restore-result.json
```

失败预检会清理本次生成的预检目录。成功预检、产物与恢复副本暂不自动过期，需要预留磁盘空间；大备份的创建、立即验证、预检和恢复会产生额外临时副本。复制、迁移或数据库验证失败的恢复目录保留 `RESTORE_INCOMPLETE` 标记；只有最终成功响应中的副本才应作为切换目标。

备份文件名为 `casefile-ai-{artifact_id}.casefile-backup.zip`，内部只有：

```text
manifest.json
database/casefile-ai.db
files/originals/{document_version_id}.pdf  # 包含所有历史版本
```

manifest 使用封闭字段模型：

| 字段 | 含义 |
| --- | --- |
| backup_format_version | 当前为 1 |
| application_version | 创建时应用版本，当前 1.0.0 |
| created_at | 含时区的 UTC ISO 时间 |
| database_schema_version | 创建时 Alembic revision |
| database | 数据库相对路径、SHA-256、字节数 |
| files | 每个 PDF 的相对路径、SHA-256、字节数 |
| total_file_count / total_size | 数据库加 PDF 的数量 / 未压缩字节数，不包括 manifest 自身 |

manifest 不复制 Evidence 正文、prompt 或配置内容。

## 备份一致性与立即验证

数据库使用 SQLite Backup API 创建快照，包含已提交的 WAL 数据，排除未提交事务；不直接复制运行中的 `.db` 文件。参见 [SQLite Backup API](https://www.sqlite.org/backup.html)。随后只复制快照中引用的不可变版本 PDF，并将每个文件 SHA-256 与数据库版本哈希比对。快照之后新导入的版本不会混入此次备份；缺失文件、哈希不符或来源链异常都会失败。

ZIP 完成后重新读取整个产物，检查 manifest、精确 entry 集合、数量、大小、所有 SHA、PDF 与数据库引用是否一一对应，验证数据库和来源链；通过后才登记产物并返回下载 ID。不存在“zip 写完即成功”。下载只使用系统生成的 UUID，不接受用户提供的文件路径。

## 恢复步骤与配置切换

1. 在“数据管理”选择备份包，点击预检。上传写入系统生成的临时目录，验证后展示案件 / 材料 / 版本 / 来源记录数量及源、目标 schema。
2. 勾选“理解恢复会创建新副本，不覆盖或自动切换当前数据”，再点击确认恢复。API 必须提交 `{"confirmation":"create_new_copy"}`，不依赖浏览器原生 confirm。
3. 后端再次验证预检文件，创建未存在的 `restored/{restore_id}`，复制数据库与 PDF、必要时迁移该副本，验证完成后显示“恢复副本已验证完成”及实际路径。
4. 若要使用副本，先停止当前服务，保留原数据与原配置；在当前使用的配置文件中同时设置以下两个值，用恢复结果中的实际路径替换示例。Windows 产品入口默认为 `%LOCALAPPDATA%\CaseFileAI\config.env`；恢复结果会提示当前配置入口，不要误改程序目录 `.env`：

   ```dotenv
   CASEFILE_DATA_DIR=C:/Users/example/AppData/Local/CaseFileAI/data-management/restored/RESTORE_ID
   CASEFILE_DATABASE_URL=sqlite:///C:/Users/example/AppData/Local/CaseFileAI/data-management/restored/RESTORE_ID/casefile-ai.db
   ```

5. 如果进程环境已设置同名变量，也需同步调整；重新启动服务，在系统状态确认数据库可用，再检查案件、原文件、历史回答和索引状态。具体路径可在 GUI 系统状态的技术详情核对。仅修改 data 目录无法改变显式配置在别处的数据库。
6. 需要回退时停止服务，恢复原来的两个配置值后重启。整个过程不删除或覆盖原数据。

同一 restore ID 已有目标目录时拒绝重复恢复；异常只影响新副本。没有自动改写 `.env` 或原地恢复功能。

## 完整性、ZIP 安全与版本规则

预检和恢复均执行 SQLite `integrity_check`、`foreign_key_check`，检查表结构和记录数量。额外校验页 / 分块内容哈希与偏移、Evidence 所属案件及引用片段、回答 JSON 与 AnswerCitation 的对应关系、Claim 来源回答内 ID、ClaimEvidence 归属、矛盾引用快照与源 Claim / Evidence 对应、FTS 的 chunk / case 关系，以及所有 PDF 哈希。稳定 ID 和历史来源链保持原样。

解压逐条执行，路径限制在本次生成的目录。拒绝 `../`、绝对路径、盘符、反斜杠、冒号、非规范路径、重复 entry、符号链接 / 非普通文件、加密 entry 与不在 manifest 中的附加文件；不用直接 `extractall`。限制上传 ZIP 为 10 GiB、展开总量为 20 GiB、entry 为 50,000、manifest 为 8 MiB，并拒绝单项压缩比超过 2000 的文件。因此超大或极高压缩比备份即使来源可信，也需要后续版本支持。

仅接受 format 1；应用版本必须为三段数字，且不高于当前版本。schema 必须是项目已知 Alembic revision。已知旧 schema 先在临时数据库克隆上迁移并验证，再在新恢复副本上迁移；源备份与工作数据库均不改写。测试验证了 `20260927_0006` 恢复到 `20260927_0007`。未知未来 format / schema / 更高应用版本拒绝，不能强行读取。迁移失败或来源完整性不通过的旧包也拒绝。

恢复目录中的 `backup_manifest.json` 保留原备份信息；旧 schema 迁移后，它记录的是迁移前数据库哈希与 schema，而不是迁移后文件哈希。恢复后的数据库另经实际完整性、来源链和数量验证。

## JSON / Markdown 分析导出

当前案件附近点击 JSON 或 Markdown 导出。归档案件同样允许。导出读取一致数据库快照，保留全部已保存回答、事实和矛盾模型 / 算法作用域，不因当前云模型关闭而隐藏历史结果。

JSON 包含 metadata、case（稳定 ID / 名称 / status）、documents（全部版本及页面信息）、answers（问题 / 回答 / 状态 / 引用）、claims（分类 / 来源回答 / Evidence 与材料来源）、coverage、candidates、contradictions、timeline 和 notices。metadata 标明导出版本、生成时间、应用版本与派生区段。

Coverage、候选与时间线当前没有持久化结果表，导出复用原有确定性 service，从已保存数据派生；这不是重新抽取事实或重新判定矛盾。不改检索排序、QA / Claim prompt、alignment、引用验证、contradiction judge、OCR 或 Timeline 算法，不调用 LLM 或 embedding。临时且未持久化的 provider 失败反馈不进入导出。

Markdown 包含材料、已验证问答、未验证历史反馈、事实与覆盖、矛盾分析、时间线、已知限制 / 隐私提示。引用展示文件、实际版本、页码、原文与稳定来源 ID；旧版明确标注 `来源：v1（非当前版本）`，JSON 同时提供版本号、当前版本号与 `is_current_version`。

矛盾按“已判定矛盾 / 未形成矛盾 / 待确认”区分，`uncertain` 保留原因，不转换成确定矛盾。不声称已穷尽事实或“材料不存在矛盾”。文本转义 HTML 和 Markdown 链接 / 图片语法，避免原文被误当作报告中的主动内容。

GUI 和报告提醒：“导出的分析报告可能包含卷宗原文片段和个人/商业信息，请妥善保存。”下载接口仅接受已登记产物 UUID，检查目录与文件名，不提供任意路径读取；返回 attachment、nosniff、no-store。

## 实际验证

新增 32 项 Python 集成测试，覆盖用户 A–AB 要求，以及 WAL 未提交写入、并发导入 / 索引写入、预检后篡改、已知旧 schema 迁移、匹配文件 SHA 但数据库外键 / JSON 来源链 / 页面内容 / FTS 损坏等情况。测试使用真实迁移、真实 SQLite、数据库位于 data 目录外、合成双版本 PDF 与受控测试模型。生成 backup 时缺失或篡改 PDF 不登记产物。

新增 8 项前端行为检查，验证重命名标题 / 列表更新及迟到回复隔离、归档筛选与可继续修改说明、受控下载、预检后显式确认、恢复结果说明，以及忙碌态恢复 / 重复提交阻止。既有 17 项保留。空白名称拒绝与归档 / 恢复的实际数据行为由 Python 集成测试覆盖。

完成隔离目录中的真实 loopback HTTP smoke（生产 FastAPI / Uvicorn 与 HTTP 下载，不使用真实敏感卷宗）：

1. 创建带 v1 / v2 PDF、历史回答与 Claim 的合成测试案件；重命名并检查稳定 ID。
2. 归档后默认列表隐藏，显示归档后可见，读取两条历史回答，再恢复归档。
3. 创建并下载完整备份，打开 ZIP，检查数据库加两份 PDF、manifest 数量及全部 SHA。
4. 上传预检，缺少确认的请求返回 422，明确确认后恢复到新目录；原工作数据库字节哈希不变。
5. 验证恢复副本案件 / 文档 / 版本数量、SQLite 完整性、外键、来源链与每份 PDF 哈希。
6. 导出并下载 JSON / Markdown，实际读取文件，检查文件名 / v1 非当前标记 / 页码 / 原文以及 uncertain 仍为待确认。
7. 停止原运行时，以恢复副本启动独立运行时，检查案件 ID、两个版本原文、两条历史回答与可用索引。

恢复记录数量为：案件 1、文档 1、版本 2、页面 2、chunk 2、embedding 2、Evidence 2、问题 2、回答 2、AnswerCitation 2、Claim 2、ClaimEvidence 2、矛盾 1、索引尝试 2。HTTP smoke 使用本地测试 encoder 和预先保存的受控模型结果，数据管理操作不访问云模型；不宣称本任务已完成真实浏览器点击或视觉检查。

质量检查：

- `uv run pytest -q`：283 passed，122.11 秒；基线 251 加新增 32。
- `node --test tests/frontend/workspace.test.cjs`：25 passed，0 skipped。
- `uv run ruff check .`：通过。
- `uv run mypy --strict src`：通过，85 个源文件。
- 仅保留已有 Starlette / AnyIO BlockingPortal 弃用警告。

## 修改 / 新增文件

共 23 个文件：

- `README.md`
- `docs/data-management.md`
- `src/casefile_ai/application/bootstrap.py`
- `src/casefile_ai/application/data_contracts.py`
- `src/casefile_ai/application/data_messages.py`
- `src/casefile_ai/application/services/cases.py`
- `src/casefile_ai/application/services/case_lifecycle.py`
- `src/casefile_ai/application/services/data_management.py`
- `src/casefile_ai/application/services/analysis_export.py`
- `src/casefile_ai/infrastructure/db/repositories.py`
- `src/casefile_ai/infrastructure/safe_logging.py`
- `src/casefile_ai/infrastructure/data_snapshot.py`
- `src/casefile_ai/infrastructure/backup_storage.py`
- `src/casefile_ai/infrastructure/analysis_reader.py`
- `src/casefile_ai/interfaces/api/main.py`
- `src/casefile_ai/interfaces/api/schemas.py`
- `src/casefile_ai/interfaces/api/routes/cases.py`
- `src/casefile_ai/interfaces/api/routes/data_management.py`
- `src/casefile_ai/interfaces/web/index.html`
- `src/casefile_ai/interfaces/web/workspace.js`
- `src/casefile_ai/interfaces/web/app.css`
- `tests/frontend/workspace.test.cjs`
- `tests/integration/test_data_management.py`

## 限制与下一步

本版完整备份保存工作数据库与全部引用的历史材料，不保存运行配置或模型权重。跨机器恢复仍需相同依赖 / 模型配置，模型版本不同可能要求更新索引；模型文件存在与可推理不是同一条件。备份哈希用于完整性校验，不是签名，不能证明未知来源备份可信。

没有加密、自动保留策略、后台任务 / 取消、单案件可恢复备份、增量备份、云同步、原地覆盖恢复、永久删除或 PDF 报告。同步大备份期间请求需要等待，磁盘不足会失败并给出安全类别。真实浏览器布局和交互仍可在后续交付检查中复核。

没有发现必须阻止任务 4 的新代码问题；建议进入任务 4，携带大包磁盘成本与浏览器视觉复核两项已知限制。本次未实现 launcher、setup / start / stop、PyInstaller、自动更新或本地 LLM。
