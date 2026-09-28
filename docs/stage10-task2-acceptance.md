# 第 10 阶段任务 2 验收记录

基线：`be82175c5ed08b9b11799def38af9abef66f6432`。验证日期：2026-09-27。

## 材料状态与持久化

新增 `GET /api/cases/{case_id}/materials`，通过 application 的 `MaterialsService` 返回每份当前版本的状态、中文解释和案件汇总原因。前端不判断分块、向量或算法版本。

| 状态 | 判断依据 | 用户动作 |
| --- | --- | --- |
| `not_indexed` | 当前材料版本无索引记录、无当前成功索引记录 | 建立索引 |
| `ready` | 当前预期分块、当前向量版本、当前 FTS 分词版本完整；空文本版本须有当前成功记录 | 可检索与提问 |
| `stale` | 当前版本有旧索引或旧尝试，但索引不完整或版本不符合当前要求 | 更新索引 |
| `processing` | 本进程正在同步执行该案件的索引请求 | 等待完成 |
| `failed` | 当前材料及算法配置签名对应的上次索引失败 | 重试，查看系统状态与诊断编号 |

状态只针对文档最新版本；导入 v2 不把 v1 的可用索引当成 v2 可用。签名包含材料版本、内容 SHA、解析版本、分块版本、向量版本和分词版本。历史引用与原文不变。

迁移 `20260927_0007` 新增 `material_index_attempts`，只存材料版本 ID、配置签名、成功 / 失败及安全诊断编号，不存异常正文或材料文本。失败跨正常重启保留，重试成功清除失败标记。同步处理标记只在进程内存在，不会在重启后永久显示“处理中”。突然终止未完成的请求不承诺记录为“失败”；之后仍以实际索引和既有记录判断。未引入后台队列或多进程锁。

`index_incomplete` 的原 QA contract 与拒答算法保持不变。GUI 读取同一 materials service 返回的原因，分别提示未建立索引、需要更新、上次失败；旧历史回答生成时索引不全、现在已可用时，提示重新提问。

## GUI 行为

- 卡片显示文件名、页数、当前版本、索引状态与下一步；ID、哈希、解析版本折叠到技术详情。
- 导入按钮说明 PDF 解析 / OCR；索引按钮为“建立索引 / 更新索引”，说明不会重新 OCR。
- 系统状态在主内容上方折叠面板，复用已有 `/api/system/status`。展示数据库、目录可写、本地向量模型、OCR、AI 配置 / 启用 / 曾成功连接 / 未验证、安全日志，不显示凭据和模型地址。刷新不测试云连接。
- 未配置、未启用分别说明本地功能和启用条件。系统不会自动启用外发。
- `model_insufficient_evidence`、`model_abstained`、空检索与普通 abstain 为信息提示；引用或事实验证失败为错误；不完整索引为警告。待确认检测结果为警告。
- provider 已知安全类别全部使用中文映射：认证、权限、限流、超时、不可用、连接、格式错误、未配置、未启用及未知错误。接口 `detail` 或原始响应不会直接插入错误 UI；错误技术区只允许已知内部代码、合法诊断 UUID 和安全 HTTP 状态。
- 导入 / OCR、索引、问答、事实抽取、矛盾检测以及检索、覆盖检查、版本读取都有实际 `disabled`、局部文字、案件名、重复请求 guard 和 `finally` 恢复。创建案件另有同样的提交 guard。
- 每次案件切换递增 generation；请求和刷新只写回相同案件及相同 generation。每个视图还有加载序号，较早响应不能覆盖较新响应。切换 A→B→A 也不会接收旧 A 响应。
- 历史版本展开后展示 v1 / v2、当前标记、页数和文件名；链接明确指定 `document_version_id`。添加新版本复用已有 multipart `document_id`，不删除旧版。现有版本没有创建时间字段，未伪造日期。
- 主要术语为材料与索引、提问与引用、事实与覆盖检查、候选与矛盾、时间线。保留必要的 Claim / Coverage / Timeline 帮助文字及原 API enum。
- 无案件、无材料、无索引、无回答、无事实分别说明下一步；无候选或无检测结果明确不等于案件没有矛盾。
- `model_unavailable` / `model_output_invalid` 的 POST 返回结果与 GET 的持久化结果合并显示；标注“本次临时反馈、待确认、未持久化”，不显示原始失败正文，不写数据库。浏览器重新加载会丢失临时反馈，可重试。
- success / info / warning / error 均有文字，输入均有 label，焦点可见，错误与长标识可换行。

## 实际验证与限制

Python 集成测试使用真实 SQLite 迁移、合成 PDF、生产应用组合与可控本地 encoder；覆盖五类状态、版本及索引过期、失败重启和恢复、处理中重复请求、案件隔离、精确版本原文、索引不重跑 parser / OCR，以及三类 `index_incomplete` 原因且不调用模型。

Node 自带测试器执行真实 `workspace.js` 函数，使用小型 DOM stub 测试错误安全字段、中文提示、材料与版本渲染、临时结果、实际禁用与恢复、重复提交、案件切换及乱序响应。没有引入 npm 依赖。Node 缺失时该轻量检查会明确 skip，Python 后端检查仍可运行。

另完成隔离临时目录中的真实 loopback HTTP smoke：

1. 无云配置启动，获取 HTML / JS / CSS 和系统状态，创建合成案件。
2. 导入合成原生 PDF 与扫描 PDF，真实 OCR；状态为 `not_indexed`。
3. 使用真实本地缓存的向量模型建立索引，状态变为 `ready`；检索命中。
4. QA 返回安全的 `model_not_configured`；本地功能仍可用。
5. 为一份材料增加 v2，显示重新需要索引；读取 v1 / v2，指定版本原文均可访问；再次建立索引后 `ready`。
6. 仅在临时运行配置中复用已有测试模型配置，关闭调用时 QA 返回 `model_disabled`；原 `.env` 未修改。
7. 临时明确启用后，银行凭证金额问答 `answered / validated`，有 2 条文件 + 页码 + 原文引用。
8. 房屋租赁期限 / 租金问题正常 `insufficient_evidence / model_abstained / model_insufficient_evidence`。
9. 抽取事实 HTTP 200，共 1 条；读取回答历史 2 条、事实 1 条、候选 0、持久化矛盾 0、时间线 1 条；系统显示本次运行曾成功连接。此小样本没有非空矛盾对，矛盾及临时失败通过现有后端回归和新增 JS 行为测试检查。

上述为 HTTP 与程序行为验证，**未完成真实浏览器点击或视觉验收**。此前自动审批拒绝了浏览器自动化启动，理由为 `blocked by policy`；本任务未绕过此限制。仍需浏览器人工检查布局、窄屏、键盘 Enter 连续提交、长文件名 / 长错误、文件选择、实际按钮禁用及案件切换、原文 PDF 页码打开和折叠版本区。没有宣称人工点击已完成。

未更改检索排名或索引构建算法、QA prompt / alignment、Claim prompt / 规则、引用验证、矛盾判断、OCR、Timeline。检索 service 只增加外围状态 monitor。保留 Task 1 的隐私、路径、迁移、日志与资源释放行为。

## 交付检查

- `uv run pytest -q`：251 passed，72.69 秒；保留 238 项基线检查，新增 12 项材料集成检查及 1 项 JS suite 入口。
- `node --test tests/frontend/workspace.test.cjs`：17 passed，0 skipped。
- `uv run ruff check .`：通过。
- `uv run mypy --strict src`：通过，检查 76 个源文件。
- `git diff --check`：通过。
- 仅有原先的 Starlette / AnyIO BlockingPortal 弃用警告。

改动文件共 17 个：

- `README.md`
- `docs/stage10-task2-acceptance.md`
- `migrations/versions/20260927_0007_material_index_attempts.py`
- `src/casefile_ai/application/bootstrap.py`
- `src/casefile_ai/application/material_contracts.py`
- `src/casefile_ai/application/services/materials.py`
- `src/casefile_ai/application/services/retrieval.py`
- `src/casefile_ai/infrastructure/db/materials.py`
- `src/casefile_ai/infrastructure/db/models.py`
- `src/casefile_ai/interfaces/api/main.py`
- `src/casefile_ai/interfaces/web/index.html`
- `src/casefile_ai/interfaces/web/workspace.js`
- `src/casefile_ai/interfaces/web/app.css`
- `tests/frontend/workspace.test.cjs`
- `tests/integration/test_material_status.py`
- `tests/integration/test_frontend_behaviors.py`
- `tests/integration/test_product_runtime.py`（缺表检查读取当前迁移号，不再写死旧 head）

无任务 3 的已确认代码阻塞；浏览器视觉验收仍待完成。建议下一步进入任务 3，但本次没有开始备份、导出、打包、启动脚本或其他任务 3 功能。
