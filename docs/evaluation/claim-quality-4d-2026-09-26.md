# 4D：Claim 分类、原子性与事实范围

基线：`bc9d4e9c01d87ead4ecaebd25bef5964d7a734eb`。任务开始时工作区干净，
173 个测试通过。本轮仅硬化 Claim 抽取，并让 Coverage 和 workspace 使用同一抽取版本。
没有修改 QA prompt/alignment、检索排序/top-k、QA citation semantic judge、OCR、
candidate generator、contradiction 判定或 Timeline 算法，也没有修改已有 stress gold。

## 先诊断，再修改

从保留的首轮真实观察恢复完整链路，没有再调用云模型来“复现旧结果”。
[诊断记录](baselines/claim-quality-before-4d-2026-09-26.json) 保存四题的 validated answer、
AnswerClaim、完整 cited evidence、抽取输入、结构化模型输出、支持判定、归一化后的
持久化 Claim 和 Coverage。旧结构化调用没有保留输入时，输入由当时 v2 代码及实际引用
按同一别名顺序重建，记录中说明其来源。

| 样本 | 根因及发生位置 |
| --- | --- |
| s06 | QA 已有 R-01、CT-77 和月租金范围。模型将关系拆为 `amount`，又生成无行号/合同的金额片段。丢失发生在模型拆分，非 QA 或空白归一化；旧支持检查只问证据是否支持文字，没有对照原回答检查范围。 |
| s09 | 付款状态由模型标为 `other`；后端没有 action 分类纠正，旧 prompt 没有明确行为/状态的类别定义。 |
| s10 | 审核人由模型标为 `other`；旧 prompt 没有明确角色归属应为 person。 |
| s38 | **只有付款状态错标 other，付款人已正确标 person**。不能将这一题的两条 Claim 都描述为分类错误。 |

旧 schema 有 `amount/date/person/action/other`。分类并非全由模型决定：原有金额/完整日期
正则会纠正这两类，person/action/other 则直接采用模型值。旧代码也拒绝日期和金额混合。
归一化只合并空白；没有补充主体。旧 whitelist、支持检查和持久化来源链确实运行过，
但合法引用与字面支持不能证明分类和自包含性正确。

## 产品契约与最小改动

Claim 是**最小但仍自包含、可独立理解、可回溯的事实陈述**。类型按核心事实分类：

| 类型 | 核心事实 |
| --- | --- |
| amount | 金额属性，例如应付/实付/月租金；保留对应主体、交易、币种和属性。 |
| date | 日期、时间、期限；保留日期对应的事件或属性。 |
| person | 身份、主体/组织、角色归属；付款人和收款人不可混同。 |
| action | 行为及完成/未完成、肯定/否定、条件/计划状态；出现人物不改变行为类型。 |
| other | 确实不属于上述类别的事实，例如记录与合同编号的对应关系。 |

没有添加“包含支付就归 action”“包含公司就归 person”的后端关键词规则。
类型定义同时进入抽取 prompt、schema 描述和 Claim 专用质量校验 prompt。
原有数字类型纠正与混合日期/金额拒绝保留；复杂的带日期限定身份句仍可能保守拒绝，
不能声称所有自然语言类型都由确定性规则保证。

抽取保留必要主体、角色、对象、合同/交易/事件、金额属性、状态、否定和条件。
允许**按原顺序摘拼同一 AnswerClaim 的已有文字**，让金额/日期拆分可以重复共享主语，
不必机械复制不相关日期。没有自由改写、插入词语、跨 AnswerClaim 借事实或自造限定。
后端检查字符顺序及完整数字 token；新增词或将 `450` 摘成 `40` 均拒绝。
摘拼的文字来自回答仍不代表语义正确：角色、否定、交易错配继续由独立支持/质量检查判断。

沿用现有一次生成 / 每条一次支持检查。在同一支持调用中要求：
`verdict=supported`，且 `atomicity/scope_preservation/category_matches` 全为 `matches`。
缺字段、mismatch、uncertain、部分支持都拒绝，整批验证后才持久化。
**没有新增模型调用步骤，也没有另一个 LLM evaluation judge。**
质量校验和抽取仍可能发生相关模型错误；这不是形式证明。

继续要求 validated answer、同一 AnswerClaim 的白名单 evidence、真实持久化来源链；
跨案件/外部 Evidence 拒绝测试保留。QA 的 citation 验证规则没有变化。
Coverage 共享摘拼定位，统计原回答字符覆盖，**不能替代语义支持或范围验证**。
抽取版本升级为 `qa-atomic-claims-v3`，旧 Claim 不被当作新缓存复用；旧记录和来源链不删除。
workspace 的六处版本路由改为引用统一常量，避免生成 v3 后 Coverage/候选仍读 v2。
下游 fake provider 只适配新支持 schema，不能将其脚本化质量 flags 当作模型能力证明。

## 固定真实 Claim 回归

入口和标签：

```powershell
uv run python evals/case_sets/mvp_stress_v1/run_claim_quality.py --check-setup --output .eval-results/claim-setup-new
uv run python evals/case_sets/mvp_stress_v1/run_claim_quality.py --real-model --output .eval-results/claim-observation-new
```

两种模式必须显式选择；输出必须为空新目录。`--check-setup` 不发云请求。
真实模式只接受原 stress 配置/问题 hash 和固定合成 PDF hash，不接受替换的真实敏感材料。
数据库、案件、导入和索引位于临时目录；复用 application services。
默认测试及 CI 不调用 Qwen。真实敏感卷宗不得发送到公共模型 API。

预先固定 14 题，顺序为 s06、s09、s10、s38、s01、s02、s03、s08、s13、s04、s05、
s45、s37、s40。预期 17 个核心事实：4 amount、4 date、5 person、4 action。
s37 是同一 AnswerClaim 内的金额+日期，s40 是同一 AnswerClaim 内的否定行为+付款人；
s38 保留两个原 AnswerClaim，不能把它当作融合句拆分的唯一验证。

QA 准备使用 10 个归档的真实 validated 回答文字，以及 4 个明确标注的合成回答 fixture，
经真实检索、QA 白名单/来源持久化服务重建新的来源 ID。准备 provider 的支持/对齐判定是
确定性 fixture，**这不是新一轮真实 QA 性能评测**。仅 Claim 生成和支持/质量检查调用
真实 `qwen3.7-plus`。每题执行一次抽取 service；没有失败后重跑、修改 gold 或按结果调 prompt。

输出包括 `results.jsonl`、`summary.json`、结构化 `model_calls.jsonl`、
`contexts.jsonl`、只含数字 usage/状态的 `http_calls.json` 和 `qa_setup/`。
本地原始观察保留；提交汇总、逐条 Claim、来源和结构化输出：
[4D 基线](baselines/mvp-stress-v1-claim-4d-qwen-2026-09-26.json)。

### 指标和分母

每题记录 expected core count、实际 Claim、分类/原子性/范围/来源检查及异常。
`core_fact_recall` 单独报告缺失核心事实，不允许“返回的都正确”掩盖空输出。
s08 的独立编号关系是一条可选标注，保留在日期 Claim 的限定中即可；
18 个标注不等于要求输出 18 条，所需核心事实仍为 17 条。

自动匹配先按人工标注核心短语定位事实，**不使用模型 kind 选 gold**；分类比较、
必要范围锚点和真实 version/page/snippet 可确定性检查。原子性只对预标注完整表达自动认定，
其他表达置 null，交直接审阅。未匹配/多重匹配不猜正确。所有率都有 numerator/denominator；
null 不计分母。模型自己的质量 flags 不作为评测 gold。

自动工具不推断语义 supported，初始 17 条均需复核。此次 Codex 逐条对照原回答、
实际页原文、来源名称与 Claim 进行直接审阅，没有额外模型 judge；**不是独立人工验收**。
提交报告同时保留 automatic 与 direct review 两套结果；未来新观察必须重新审阅，
不得照搬此次 supported 判定。

| 指标 | 旧 9 题 | 同 9 题新结果 | 全 14 题新结果 |
| --- | ---: | ---: | ---: |
| 预期核心 / 实际 Claim | 10 / 12 | 10 / 10 | 17 / 17 |
| 自包含原子性 | 11/12 | 10/10 | 17/17 |
| category 正确 | 8/12 | 10/10 | 17/17 |
| scope preservation | 未建立同口径自动指标；已知 s06 丢失 | 10/10 | 17/17 |
| 来源链合法 | 12/12 | 10/10 | 17/17 |
| 严格 gold 主来源一致 | 旧率未按此定义统计 | 8/10 | **15/17** |
| literal unsupported | 0/12 | 0/10 | 0/17 |

新核心事实召回 17/17，抽取异常 0/14；无重复/未匹配 Claim。
新 source accuracy 定义严格：**每一条**选中引用都必须属于 gold 主文档、页码并含 gold
snippet。s38 两条 Claim 保留正确陈述 A 主引用，同时保留 controls 补充引用，因此计错 2 条。
未删除引用或改分母。两条都有正确主来源，主来源至少存在指标 17/17，合法来源链 17/17，
直接审阅未发现新增事实。补充来源不能独立证明“陈述 A”的范围，仍保留 2 项引用精度复核项；
不将其解释为来源指标 100% 或未支持事实。

32 次 HTTP 全成功：15 次抽取（s38 两个 AnswerClaim），17 次原有支持/质量检查。
旧 9 题 22 次，新同 9 题 20 次，减少来自取消不必要的关系碎片，而非跳过支持检查。
14 题 Claim latency p50 18,883.483 ms，p95 31,611.484 ms；合计 294,268.007 ms。
usage：prompt 21,167、completion 19,831、total 40,998 token，32/32 调用观测到 usage。
供应商未返回费用，cost 为 null；没有虚构价格或将推理 token 忽略。

## 下游与完整回归

真实 Claim 通过未修改的 candidate service 自然生成 14 对：amount 1、date 1、
person 6、action 6；没有真实 contradiction 调用，也没有为候选指标改生成器。
这些数量证明四类输入仍可进入下游，不代表候选 precision/真实 judge 的端到端准确率。

| 离线基线 | 4D 结果（与原基线一致） |
| --- | --- |
| mvp_v1 | 状态 30/30，strict retrieval 27/27，citation page 29/29，失败 0。 |
| mvp_v1 downstream | Claim 16/16，候选 recall 4/4，判定 8/8，Timeline 6/6，排序 14/14。 |
| mvp_stress_v1 | 状态 48/51，strict retrieval 42/45；仍只有 s21/s22/s49 三个 QA 失分。 |
| stress downstream | Claim recall/category 22/22、原子性 27/27，候选 4/4；判定 8/12、严格矛盾 recall 2/4，四个 action/person uncertain 口径问题保留；Timeline 10/10、排序 44/44。 |

离线 QA、Claim 和语义 provider 是确定性 provider，不能将这些成绩描述为真实模型能力。
全量 `pytest`：201 passed，1 个既有 Starlette/AnyIO deprecation warning。
新增 22 个抽取质量测试、6 个评测契约测试；保留跨案、白名单、故障、缓存、
QA alignment 和“生椰拿铁”回归。Ruff 通过，`mypy --strict src` 通过（66 source files）。

## 第 9 阶段收口判断

在已执行的范围内，未发现新的可靠性 blocker：4C 固定 QA 回归错误 validated 为 0；
4D 未发现 unsupported Claim、丢失必要范围或明显分类错误，四类候选输入正常。
这是有限样本观察，不是对任意卷宗的保证；此次没有重新跑真实 QA。

已知可接受限制：OCR O/0 等歧义字符不能安全恢复（s21/s22），极短查询 s49，
s27 对证据保守拒答，以及两项额外来源精度复核。复杂范围/否定仍依赖模型检查，
应保留审阅和失败闭锁。action/person contradiction 的 4 项 uncertain/gold 口径差异未解决，
继续作为需要人工分析的限制，不能宣称自动矛盾检测已全覆盖。真实 Claim latency 也应纳入
后续运行预算。

建议以这些边界正式结束第 9 阶段，提交本报告供人工验收；无需为达到 100% 放宽拒答或
修改 gold。本轮未开始第 10 阶段。

## 文件范围

- `application/claim_contracts.py`：类别描述及 Claim 专用联合验证 schema。
- `application/services/claims.py`：prompt、parent answer 上下文、联合质量门、v3。
- 新 `application/services/claim_provenance.py`：有序摘拼定位及完整数字检查。
- `application/services/claim_coverage.py`：共享定位。
- `application/services/workspace.py`：统一版本常量路由。
- `evaluation/downstream.py`：fake provider 新 schema 适配。
- 新 `evaluation/claim_quality.py`：标注、确定性指标和缺失事实统计。
- 新 `evals/case_sets/mvp_stress_v1/claim_quality_set.json`、`run_claim_quality.py`。
- 新 `tests/integration/test_claim_quality.py`、`test_claim_quality_evaluation.py`；
  调整 `test_claim_extraction.py`、`test_workspace_e2e.py` 的测试 provider。
- 本报告、两份诊断/评测 JSON，更新 downstream 文档及 stress README。
