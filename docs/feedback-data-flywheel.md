# 反馈与数据飞轮

CaseFile AI 将真实回答上的显式反馈留在本地，供人工复核。反馈不是正确答案，也不会触发在线训练、自动更改 Prompt 或自动上传。

## 数据与隐私

迁移 `20260929_0008` 创建 `feedback`，以 `feedback_id` 标识记录，外键连接 `case_id` 和 `answer_id`。服务验证 Answer 属于指定 Case。字段有 `rating`、`category`、`note`、`created_at`、`review_status`、`reviewed_at`，以及仅人工审核时填写的 `expected_status`、`expected_answer`、`expected_evidence_json`。审核结果不可直接覆盖。不复制 Evidence 正文到反馈表。归档案件不会删除反馈。

评分为 `correct`、`partially_correct`、`incorrect`、`insufficient_evidence`；类别为 `retrieval`、`answer`、`citation`、`claim`、`other`。审核状态为 `pending`、`approved`、`rejected`。反馈默认写入本机数据目录中的 SQLite；完整备份包含它。日志只记录安全事件类型，不记录反馈正文。普通案件分析 JSON 仅加入反馈汇总，不导出用户备注；Markdown 暂不显示反馈统计。完整反馈仅由独立命令显式导出。

## 使用与人工审核

GUI 在每张回答卡片内提供评价、类别和备注表单，旁边显示当前案件评分与类别统计。CLI 例子：

```text
casefile-ai feedback add --case CASE_ID --answer ANSWER_ID --rating incorrect --category citation --note "引用页码错误"
casefile-ai feedback list --case CASE_ID --json
casefile-ai feedback summary --case CASE_ID --json
casefile-ai feedback export --case CASE_ID --output feedback-review.jsonl
```

`feedback export` 是显式操作，待审 JSONL 包含问题、回答、引用位置及备注，可能含案件信息，分享前需人工检查。新反馈始终为 `pending`。人工确认预期答案与证据后，以 JSON 文件填写 runner 使用的 `gold_evidence` 数组，例如 `[{"doc_key":"document-1","page":1,"snippet":"合成证据"}]`：

```text
casefile-ai feedback review --feedback FEEDBACK_ID --status approved --expected-status answered --expected-answer "合成正确答案" --expected-evidence evidence.json
casefile-ai feedback review --feedback OTHER_ID --status rejected
casefile-ai feedback export-eval --case CASE_ID --case-set case-set.json --output feedback-approved-eval.jsonl
```

仅指定案件内 `approved` 且通过现有 `Question` schema 校验的记录进入评测 staging 文件。`doc_key` 必须存在于提供的 case set。`insufficient_evidence` 样本需预期答案和证据均为空。`pending` 和 `rejected` 不进入 staging。开发者复核后再把样本加入版本化 eval set，并执行 `python scripts/evaluate_case_set.py --case-set case-set.json --questions feedback-approved-eval.jsonl --output regression-output`。该 runner 会给出 pass/fail，不会修改模型。

MCP 只开放 `submit_answer_feedback` 与 `get_feedback_summary`，描述明确反馈本地保存且不自动训练。人工审核和样本提升仅在 CLI。

## 持续升级的含义与限制

使用反馈 → 人工核准 → 版本化评测数据 → 离线回归 → 检索、Prompt、规则或模型版本改进 → 新 release。这是可审计的改进流程，不是实时改权重。当前没有多用户审核权限、自动脱敏或自动合入正式评测集。`doc_key` 与材料的对应关系由审核者确认；导出时仅校验它存在于 case set，runner 才会核对证据内容。导出文件由操作者保管；如果外部模型用于问答，问答本身仍遵循其单独的模型配置和隐私提示。
