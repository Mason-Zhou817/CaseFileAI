# MVP stress v1（全合成）

这是独立于 `mvp_v1` 的压力评测集。11 份 PDF 共 66 页，51 道人工标注问题；所有姓名、公司、合同和交易均为虚构。`case_set.json` 禁止云 QA，文档有 SHA-256 固定值。生成脚本只用于重建材料，重建后需重新人工检查页面和更新哈希。

| 文档键 | 页数 | 主要压力点 |
| --- | ---: | --- |
| `ocr` | 2 | 纯图片扫描、O/0、I/1/l、5/S、8/B、中文姓名、金额小数、日期分隔符、低清模糊 |
| `mixed` | 2 | 第 1 页原生文本、第 2 页纯图片 OCR |
| `table` | 2 | 字段和值跨行、相邻行、近似金额与姓名 |
| `ledger_a` | 3 | 主体多次出现、相似金额、多日期、目标事实在末页 |
| `ledger_b` | 2 | 跨文档复现与不同事实 |
| `long` | 50 | 每页不同干扰文本；目标事实在 1、13、25、38、50 页 |
| `contract_v1`、`contract_v2` | 各 1 | 旧版与当前版本事实冲突 |
| `conflict_a`、`conflict_b`、`controls` | 各 1 | 四种矛盾类型、兼容与不确定对照 |

`questions.jsonl` 有 45 道可回答题、6 道无证据题。`s01`–`s20` 是固定 smoke 子集：普通、表格、改写、负例各 5 道。`s21`–`s27` 检查 OCR；`s28`–`s32` 覆盖长文档五个位置；`s33`–`s34` 需要跨文档证据；`s37`–`s48` 绑定下游 Claim 和矛盾对照；`s49` 故意只有“金额？”这一歧义查询。单题可带多个 tag，tag 总数不能当作题数。

`downstream_gold.json` 包含 22 个 Claim、12 个 pair（金额、日期、行为、主体各有矛盾/兼容/不确定）及 10 个 Timeline 事件。`stress_gold.json` 单独标注 12 个 OCR token 和长文档目标页。扫描件 gold 是**图像上人工标注的可见内容**：`s21`、`s22` 的参考码在实际 OCR 文本中已损坏，不能把解析后文本当成 gold，也不能因为它们缺失而删除样本。

在仓库根目录运行离线基线：

```powershell
uv run python evals/case_sets/mvp_stress_v1/run_stress.py --output .eval-results/mvp-stress-v1
```

需要本地 `CASEFILE_EMBEDDING_MODEL`；基线使用 `BAAI/bge-small-zh-v1.5`。QA 使用现有 gold-gated FakeLLM；Claim 使用抽取 FakeLLM；行动/主体语义判定使用现有规则 provider。数据库、导入文件、索引均在临时目录，结果只写入本地 `.eval-results`。主命令不调用 Qwen。

**可选、需人为显式执行的真实模型 smoke**：

```powershell
uv run python evals/case_sets/mvp_stress_v1/run_stress.py --real-model --output .eval-results/mvp-stress-v1
```

这一步单独读取 `real_model_case_set.json`（`allow_cloud_model: true`），先核对文件路径和 SHA-256 与固定合成集完全相同，然后只发送上述 20 个 smoke 问题。需要本地配置 `CASEFILE_LLM_BASE_URL`、`CASEFILE_LLM_API_KEY`、`CASEFILE_LLM_MODEL`；**绝不可将真实敏感卷宗替换进该配置或发送到公共模型 API**。默认命令及 CI 不执行此模式；历史离线 baseline 不包含真实模型结果。输出为单独的 `real_model_results.jsonl` / `real_model_summary.json`，包含 false abstain、false answer、503 和其他 provider 故障；模型结果不是 CI 断言。

### 第 9 阶段最终真实模型验收

显式执行（会调用配置中的真实 Qwen，只发送上述固定 SHA-256 的合成材料）：

```powershell
uv run python evals/case_sets/mvp_stress_v1/run_final_smoke.py --real-model --output .eval-results/mvp-stress-v1-real-model
```

此入口保持 20 道主 QA 题，具体分组由脚本 `GROUPS` 固定：普通题加入 `s38` 以覆盖行为和多事实回答，较难题加入 `s27` 以覆盖 OCR 页码及 ISO 日期。先核对原始问题文件的 SHA-256，再复用 `run_evaluation` 的临时数据库和服务。拒绝复用已有观察结果目录，避免覆盖首轮失败。

主 QA 后，对最多 9 个成功 validated 的真实回答调用现有 Claim service。另用已有 gold FakeLLM 建立经过真实持久化和引用验证的固定 Claim 对照，再由真实 Qwen 判断三个行为对、三个人物对；这些是 **judge 阶段验证，不是端到端真实 QA/Claim 指标**。最后重复最多 3 题验证缓存，并单独记录 warm；不对失败题进行额外网络重试。已有业务的聚焦片段重试、模型 JSON 校验重试保持原样。

输出包括主 `real_model_results.jsonl` / `real_model_summary.json`、`claim_results.json`、`contradiction_results.json`、结构化 `model_calls.jsonl` 和不含请求内容的 `http_calls.json`。HTTP 成功不等于模型结构合格或事实正确。citation 语义支持及 Claim 的事实、分类、原子性需逐项对照原文复核，自动工具初始标记 `pending_review`；不能把它当成通过。CI 仅用 MockTransport 验证计数与错误归类，不发送云请求。

详细指标定义、基线与失败解释见 [stress.md](../../../docs/evaluation/stress.md)。

### 4C QA 对齐专项回归

```powershell
uv run python evals/case_sets/mvp_stress_v1/run_alignment_smoke.py --real-model --output .eval-results/mvp-stress-v1-alignment-4c
```

预先固定 9 题：s19、s27、s01、s02、s06、s09、s13、s16、s17。每题一次冷服务流程，
不按结果重跑；只发送与原基线 hash 一致的合成材料。复用既有输入安全检查、runner、
HTTP/结构化调用观察器，输出必须为新空目录。逐题结果、带来源的生成和 alignment 输入、
校验结果、每题调用数、token usage、p50/p95 均留在输出目录；费用没有供应商账单则为 null。
不执行新的真实 Claim/contradiction 调用。详情及提交基线见
[4C 报告](../../../docs/evaluation/qa-alignment-4c-2026-09-26.md)。

### 4D Claim 分类、原子性与范围专项

```powershell
uv run python evals/case_sets/mvp_stress_v1/run_claim_quality.py --check-setup --output .eval-results/claim-setup-new
uv run python evals/case_sets/mvp_stress_v1/run_claim_quality.py --real-model --output .eval-results/claim-observation-new
```

`claim_quality_set.json` 预先固定 14 个阶段样本、17 个必需核心事实，含四种类别及融合句。
使用归档真实回答/标注合成回答，经确定性 QA provider 和真实服务重新绑定实际检索 evidence；
仅第二条命令调用真实 Qwen Claim，**不调用真实 QA 或 contradiction**。
两种模式均验证原问题及合成材料 hash，使用临时案件/数据库和新空输出目录。
不替换真实敏感材料，不按结果重跑。CI 只测试离线标签、契约和 fake provider。
输出逐题 `results.jsonl`、`summary.json`、结构化输入/输出与数字 usage；
scope/category/source 可确定性检查的部分自动统计，未知语义支持留给直接审阅。
详情、严格来源 15/17 的解释及单次回归结果见
[4D 报告](../../../docs/evaluation/claim-quality-4d-2026-09-26.md)。
