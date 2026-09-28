# CaseFile AI CLI v1.1-A

CLI 和 GUI/API 使用同一套本地案件数据与 application service。以下命令在完整源码目录运行；安装后的环境可省略 `uv run`。目前仍是源码交付，没有发布新的 Windows ZIP。

## 安装与配置

需要 Python 3.12 和 uv。在仓库根目录执行：

```powershell
uv sync --all-groups
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
uv run casefile-ai --help
```

`.env` 是可选的。默认案件数据位于当前用户的 CaseFile AI 数据目录；CLI、GUI 和 API 在相同配置下访问同一 SQLite 数据库。首次本地索引或扫描 PDF 的 OCR 可能需要下载模型文件。可通过 `CASEFILE_ENV_FILE` 指定其他配置文件；请勿把真实密钥提交到仓库。

## 命令速查

以下示例中的 `<case_id>` 和 `<answer_id>` 替换为实际输出的 ID。每个命令可追加 `--help`；`serve` 仍为 GUI 入口。

```powershell
uv run casefile-ai case list
uv run casefile-ai case create "合成付款争议"
uv run casefile-ai document list --case <case_id>
uv run casefile-ai document import --case <case_id> .\synthetic.pdf
uv run casefile-ai document import --case <case_id> --document <document_id> .\revised.pdf
uv run casefile-ai index build --case <case_id>
uv run casefile-ai index status --case <case_id>
uv run casefile-ai search --case <case_id> "450000" --limit 10
uv run casefile-ai ask --case <case_id> "付款金额是多少？"
uv run casefile-ai claim extract --answer <answer_id>
uv run casefile-ai claim list --case <case_id>
uv run casefile-ai contradiction run --case <case_id>
uv run casefile-ai contradiction list --case <case_id>
uv run casefile-ai timeline --case <case_id>
uv run casefile-ai export --case <case_id> --format json --output .\analysis.json
uv run casefile-ai export --case <case_id> --format markdown --output .\analysis.md
```

`case-create` 和 `case-list` 保留为旧命令别名，仍接受原有参数。`document import` 使用 GUI 相同的 PDF 解析、OCR、SHA 去重和版本服务；使用 `--document` 时只为该案件内的指定材料追加新版本，跨案件 document ID 会被拒绝。`index status` 返回 `not_indexed`、`ready`、`stale`、`processing` 或 `failed`，并附每个材料版本的状态。索引未就绪时 `search` 和 `ask` 退出码为 4；`ask` 在索引就绪后仍可能因证据不足返回 `insufficient_evidence`，这属于正常结果。

人类模式直接打印可读文本或结构化内容。`search` 显示文件、版本 ID、页码、片段、分数和排名；`ask` 显示状态、回答、Answer ID、拒答原因和引用。`contradiction` 的 `verdict` 可为 `contradiction`、`non_contradiction`、`uncertain`；`uncertain` 不能解释为已确认矛盾。`timeline` 的 `date_value` 可能为空，证据中包含来源文件和页码。

## 机器可读输出

除 `serve` 外，上述核心命令均支持 `--json`。成功时 stdout 只有一个 UTF-8 JSON 对象：

```json
{"ok":true,"data":[{"case_id":"<case_id>","name":"合成付款争议","description":null}]}
```

运行期错误写入 stderr，stdout 为空：

```json
{"ok":false,"error":{"code":"index_not_ready","message":"当前案件索引未就绪，请先建立或更新索引。"}}
```

`data` 沿用相应 service contract 的字段。`search` 每条记录含 `document_id`、`document_version_id`、`filename`、`page_number`、`text`、`score` 和 `rank`；`ask` 含 `status`、`answer`、`citations`、`metadata.refusal_reason` 和已保存的 timing。`claim list`、`contradiction list`、`timeline` 使用本地分析读取器，读取该案**所有已保存模型版本**，并为来源附加文件、版本和页码；GUI 当前视图按当前模型版本筛选。`export --json` 的 `data.content` 为 JSON 报告对象或 Markdown 字符串；加 `--output` 时返回 `{format,written,bytes}`，目标文件必须不存在。未加 `--json` 且未指定 `--output` 时，`export` 直接输出报告正文。带 `--json` 的参数解析错误也写入 stderr JSON 并返回 2；不要把 stdout 和 stderr 合并后当作 JSON 解析。

## 退出码

| 码 | 含义 | 示例 |
| --- | --- | --- |
| 0 | 成功，包括证据不足的正常拒答 | `ask` 的 `status=insufficient_evidence` |
| 1 | 未预期的本地运行错误，附安全日志诊断编号 | 存储初始化失败 |
| 2 | 参数或输入无效 | 空问题、无效 PDF、命令参数错误 |
| 3 | 案件、回答、材料或输入文件不存在 | 未知 case ID |
| 4 | 当前状态无法执行 | 索引未就绪、目标文件已存在 |
| 5 | 模型未配置、未启用或供应商故障 | `model_not_configured`、`rate_limited`、`timeout` |
| 6 | 来源或结果验证失败 | Claim 证据验证失败 |

错误信息不包含原始 Python 异常、密钥、Authorization 或数据库绝对路径。`--output` 只创建新文件，不覆盖任何现有文件。导出内容本身可能包含卷宗原文和个人信息，请妥善保管。

## 本地模式与 Qwen

无 Qwen 配置仍可执行 `case`、`document`、`index`、`search`、已有 Claim 的 `claim list`、`contradiction list`、基于已有数据的 `timeline` 和 `export`。这些只读命令保留跨模型版本的已保存分析，即使本次没有配置模型名或密钥也可查看。`contradiction run` 对当前模型版本的金额、日期候选使用本地规则；行为或主体候选需要模型。新 `ask` 与 `claim extract` 需要启用模型，否则明确返回 `model_not_configured` 或 `model_disabled`，退出码 5。

如需连接兼容接口的 Qwen，在本地 `.env` 设置以下值，并仅对合成或人工脱敏材料启用外发：

```dotenv
CASEFILE_LLM_ENABLED=true
CASEFILE_LLM_BASE_URL=https://MODEL_ENDPOINT.invalid/v1
CASEFILE_LLM_API_KEY=YOUR_API_KEY
CASEFILE_LLM_MODEL=YOUR_MODEL_NAME
```

把占位符换成供应商实际支持的地址与模型。系统不会自动脱敏文件名、问题或证据。问答与 Claim 抽取遇到 429、超时时分别返回 `rate_limited`、`timeout`，不打印供应商原始响应。行为或主体矛盾判断沿用现有服务的保守语义：模型故障时给出未保存的 `uncertain`，不伪称已确认矛盾。答案只从完整 QA 服务取得，包含本轮 Evidence 白名单、Citation 验证与问题对齐；CLI 不直接输出未经验证的模型文本。

## 完整验收示例

先准备**合成** PDF `synthetic.pdf`，其文字可以是“交易金额为450000元。付款日期为2026年3月1日。”。配置模型后执行：

```powershell
uv run casefile-ai case create "合成付款争议" --json
# 从上一步 data.case_id 记录 ID，以下以 $caseId 表示
uv run casefile-ai document import --case $caseId .\synthetic.pdf --json
uv run casefile-ai index build --case $caseId --json
uv run casefile-ai search --case $caseId "450000" --json
uv run casefile-ai ask --case $caseId "交易金额是多少？" --json
# 只有 status=answered 且 metadata.validation_status=validated 时，记录 data.answer_id
uv run casefile-ai claim extract --answer $answerId --json
uv run casefile-ai contradiction run --case $caseId --json
uv run casefile-ai timeline --case $caseId --json
uv run casefile-ai export --case $caseId --format json --output .\analysis.json --json
```

一个 Claim 不一定形成矛盾候选；要展示矛盾，可另导入一份**同一事件且金额不同**的合成材料，并对它取得已验证 Answer、抽取 Claim 后运行 `contradiction run`。未确认事件或金额字段对应关系时，结果可能是 `uncertain`。

未来 30–40 秒演示可使用已预先索引的同一案件：

```powershell
uv run casefile-ai case list
uv run casefile-ai search --case $caseId "450000"
uv run casefile-ai ask --case $caseId "付款金额是多少？"
uv run casefile-ai timeline --case $caseId
```

GUI 与 CLI 使用相同配置时，这四步展示同一数据库的案件和已保存分析。`ask` 的耗时取决于 Qwen 响应与网络，不能保证一定在 40 秒内完成；演示前应确认模型配置和案件索引状态。
