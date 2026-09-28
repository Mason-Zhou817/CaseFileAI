# 第 9 阶段 4C：问题、字段与来源对齐

起始基线：`7fdc349edd6cb63679c4cec181ae37d16019e64d`，工作区干净。
本轮修复 QA 对齐，不处理 Claim 分类或 contradiction uncertain 口径。

## 修改前诊断

先检查现有服务与首轮 smoke 原始观察，再选择修改点。可审计的中间状态见
[before trace](baselines/mvp-stress-v1-alignment-before-2026-09-26.json)：两题的完整问题、
有序 retrieval/Evidence（含实际 ID、版本、页码、文本）、实际生成输入、结构化生成与
support 输出、最终引用映射、服务状态。该文件来自首轮留存结果，不是再次调用 Qwen。

| 链路 | s19 | s27 |
| --- | --- | --- |
| question | 第一笔设备预付款最终到账日期是哪天？ | 低清扫描第2页的日期是什么？ |
| retrieval | E1 甲账簿第3页有实际转账日 2026年3月1日；E4 陈述A有付款记录日期 | 正确 OCR 第2页 2026-03-08 在 E3/rank3；E2 是长卷第2页参考日 2026年3月3日 |
| Evidence | case/document/version/page/evidence ID 已存在后端，引用链合法 | 同左；正确 OCR Evidence 已进入实际 QA context |
| generation payload | question + 每条 citation_id/quote | 同左；没有独立文件名、document/version ID、page number、Evidence ID |
| raw structured claims | 实际转账日期为2026年3月1日，引用 E1/E4 | 长卷参考日期及没有目标交付日期两条，均引用 E2 |
| alias mapping | E1/E4 映射回本案本轮真实 Evidence | E2 映射到长卷真实 Evidence，而非 OCR |
| hard validation | 本轮白名单、跨案、来源链均通过 | 均通过 |
| support validation | 仅收到 claim + 引用原文/实际 Evidence ID；supported | 两条分别 supported |
| final | answered / validated | answered / validated；gold 不匹配 |

根因不是没有检索到资料。s19 的真实陈述只回答相邻字段；support 没有 question，
无法区分转账日与最终到账日。s27 缺少结构化来源提示，模型选错文件；校验只验证所选
原文支持那句话，没有验证“低清扫描”来源范围。页码数字同为 2 不能说明文件正确。
现有后端白名单按设计发挥作用，但不能独自证明答对问题。

## 最小实现

- 生成及聚焦表格重试统一增加 `document_name`（实际 filename）、`document_id`、
  `document_version_id`、`page_number`、`evidence_type`；保留 `citation_id`、`quote`。
  业务服务没有 eval doc_key，因此不注入评测标签。实际 Evidence ID 仍由后端映射，
  不作为模型的可选 citation；报告可通过 aliases/版本/页码关联到实际 ID。
- 生成要求识别主体、事件、具体属性及来源范围。不同属性不能互相替代；明确同义字段、
  换行表格可回答，缺少所问属性或来源无法确认则拒答。没有字段字典或题目特例。
- 保留原来的 alias whitelist、case/version lineage、逐 Claim support 校验和安全答案重建。
  在它们通过后，调用独立 `ModelAnswerAlignment` 一次，输入 question、从已支持 Claim
  重建的最终 answer、实际绑定引用的 claims 及全部带来源的 Evidence context。
- 严格 schema，全部字段必填，禁止额外字段。三个维度均为
  `matches | mismatch | uncertain`：`answers_requested_fact`、`field_matches`、
  `source_scope_matches`；另外记录 requested_fact、requested_scope、reason。
  三项全部 matches 才 validated；无来源限制时不人为增加限制；同义表达不要求字面相等。
- 不符合或不确定：持久化 `insufficient_evidence / alignment_validation_failed`，
  refusal_reason 区分 field、source_scope、requested_fact 的 mismatch/uncertain。
  对外没有拒绝答案的 Claim/citation，避免下游将其作为 validated 分析输入。
- 模型/协议故障：沿用 QAUnavailableError/通用 API 503，不保存不安全答案或临时故障缓存。
  support 失败仍是 semantic_validation_failed，硬引用失败仍是 hard_validation_failed。
- 聚焦重试可能将原 E3 临时改名 E1；alignment 从实际 Claim 的 Evidence ID 重新绑定上下文
  aliases，避免把临时 E1 误映射到原第一条来源。
- prompt/cache 版本变为 `grounded-qa-citation-alias-v7+claim-support-v1+answer-alignment-v1`。
  旧回答保留历史来源链，但不会命中新问答缓存。未修改数据库 schema 或公共答案字段。

新增一次 alignment 调用仅发生于硬引用和 support 均通过的冷候选回答。原有聚焦重试和
provider 有限 schema 修正上限不变；没有 alignment 重试循环、修复循环或额外 generation。
validation_ms/qa_ms 包含 alignment。warm cache 不调用模型。

这是结构化语义校验，不是数学证明；生成器与校验器使用同一配置模型，仍可能有相关误判。
离线 A–J 验证的是服务拦截、绑定、缓存和失败契约；不能宣称脚本化 judge 证明了真实语义能力。

## 验证与运行

固定真实子集：`s19,s27,s01,s02,s06,s09,s13,s16,s17`。使用原 top-k=5、原本地 BGE，
只发送 hash 固定的 mvp_stress_v1 合成 PDF；新临时案件/数据库。每题一次冷服务执行，
既有内部聚焦或有限协议修正按实际调用计数；不按结果改题或重跑。

```powershell
uv run python evals/case_sets/mvp_stress_v1/run_alignment_smoke.py --real-model --output .eval-results/mvp-stress-v1-alignment-4c
```

输出逐题 results.jsonl、summary.json、contexts.jsonl、结构化 model_calls.jsonl 和
HTTP 状态/耗时/数值 token usage。输出目录必须为空。只保存合成业务上下文，不保存
Authorization、API key 或原始网络响应。真实敏感卷宗仍不得发送到公共 API。

确定性 A–J、三个 uncertain 维度、超时/非法 schema/未校验模型对象、缓存版本变更、
原引用隔离、表格“生椰拿铁”等已纳入回归。Gold fake 为独立、显式的离线 oracle，
不能作为实际模型回答率；现在使用新来源页码，避免重复相同 snippet 时总选第一条的
旧测试 double 行为（s33 需要甲账簿第3页和乙账簿第2页两条引用）。gold、材料和排序不变。

## 首次真实结果

[可提交真实基线](baselines/mvp-stress-v1-real-alignment-4c-2026-09-26.json)
保存分母、逐题结果、实际版本/引用、两题的 after 中间状态及结构化校验输出。
完整首次输出在 `.eval-results/mvp-stress-v1-alignment-4c/`，没有再次调用失败题。

| 指标 | 首次 4C 结果 |
| --- | ---: |
| 总题数 / 正例 / 负例 | 9 / 6 / 3 |
| correct answered | 5/6；全部 5 道此前正确的回答控制题通过 |
| correct abstain | 3/3（s19、s16、s17） |
| false answer / 正例错误 validated | 0/3 / 0/6 |
| false abstain | 1/6，s27；此前正确回答控制题 0/5 |
| validation failure | 1/9，s27 的旧 support 部分支持拒绝 |
| alignment validator rejected answers | 0；实际执行的 5 次均 matches |
| provider failure / timeout / 429 / 503 / 5xx | 全部 0，21 次 HTTP 都是 2xx |
| retrieval all-required@5 | 6/6；正确 gold 都在实际 context |
| 最终返回 citation 文档 / 文档+页 / snippet | 均 5/5；拒答没有返回引用，不进入分母 |
| 逐 answered 问题所问事实与来源匹配 | 5/5，直接对照问题和引用原文 |
| 严格问题正确率 | 8/9；不把安全拒答算成正例回答成功 |
| 自动 answer match | 4/4 可判定题；s13 仍为 null/manual review，复核正确 |

复核由 Codex 直接检查合成问题、gold、模型输出及引用原文完成，不是独立人工签字。
s13 的日期与 gold 相同，只是允许答案字符串的格式不同；保留原始 lexical 结果，
在 review 中单独说明，不修改 runner 匹配规则来提高统计。

### s19 / s27 before → after

- **s19**：原来用转账日替代最终到账日，并 validated；现在 generation 即
  insufficient_evidence，最终 model_abstained，无引用。没有调用 support/alignment。
- **s27**：原来引用长卷第2页 2026年3月3日，并 validated；现在 generation
  选择 E4：`01_ocr_confusions.pdf` 第2页的 **2026-03-08**。
  但原 support prompt 仍只有 claim+quote，它因 quote 没有“低清扫描第2页”文字而
  返回 partially_supported。最终 semantic_validation_failed / insufficient_evidence，
  无引用，安全失败。**不是正确 validated，也不是 alignment 拒绝。**没有放宽支持性检查。

原 s27 gold rank3，本轮 rank4，仍在 top5 与实际 context。未改 retrieval 代码、排序或
top-k。现有 BM25/向量/RRF 在同分时按 chunk_id 排序，临时案件的 ID 不同是比较中的变量；
前后输入相同不意味着逐条上下文完全相同。因此不能把一次结果变化全部归因于 prompt。
`evidence_type` 当前值是 `chunk`，不是 OCR 解析方式或清晰度标签；没有伪造这些元数据。

### 每题实际调用和延迟

| ID | before 调用 | after 调用 | 新 alignment | before QA 秒 | after QA 秒 |
| --- | ---: | ---: | ---: | ---: | ---: |
| s19 | 2 | 1 | 0 | 21.476 | 11.360 |
| s27 | 3 | 2 | 0 | 30.396 | 23.957 |
| s01 | 2 | 3 | 1 | 18.564 | 20.875 |
| s02 | 2 | 3 | 1 | 14.391 | 22.153 |
| s06 | 2 | 3 | 1 | 14.126 | 23.128 |
| s09 | 2 | 3 | 1 | 12.318 | 21.883 |
| s13 | 2 | 3 | 1 | 19.333 | 25.685 |
| s16 | 1 | 1 | 0 | 2.797 | 2.712 |
| s17 | 2 | 2 | 0 | 7.187 | 9.045 |

表中 before 来自留存首轮，准确逐题毫秒在 before/after JSON 中；两轮均只比较这 9 题，
不拿原 20 题总体延迟与新 9 题直接比较。QA 耗时含其内部 retrieval/generation/validation，
不含共享 import/index 和外部诊断 retrieval。

- 真实 HTTP/结构化调用 **18 → 21**，协议补发 0。新 alignment **5/9 = 平均 0.556 次/题**，
  每个进入此阶段的冷答案恰好 1 次。整体净增加 **3/9 = 0.333 次/题**；s19/s27 提前结束
  分别减少 1 次旧调用。这不是所有未来查询的固定平均成本。
- 同一子集 QA p50 **14.391 → 21.883 秒**，p95 **30.396 → 25.685 秒**。
  正确控制题多了一次语义校验而更慢；尾部变小包含旧错误答案多 Claim 路径结束的影响。
  仅单次网络观测，不构成性能保证。
- transport 捕获全部 21 次 usage：prompt **22,957**、completion **10,215**、total
  **33,172 tokens**。现有 application provider 本身不暴露 usage；这里只读数值字段。
  供应商没有返回费用，cost=null；没有猜价格，也没有为成本计数重跑模型。
- 9 题 cache_hit 均 false/cold。已有确定性 warm/cache/version 失效测试通过，没有新增真实
  warm 重放。s17 的第二次 generation 是既有聚焦逻辑，不是人工重新执行该题。

本样本**没有实际观察到 alignment 拒绝错误候选**：s19 生成拒答，s27 被已有 support
拦截；不能宣称真实 validator 的拒错能力已经被这 9 题全面证明。确定性 A–J 检验了该
拒绝路径、来源绑定、同义放行及不确定时关闭；仍需未来固定错误候选的语义能力评测。

## 离线与质量门

[离线摘要](baselines/mvp-v1-4c-offline-2026-09-26.json)保留各阶段分母与失败原因：

- mvp_v1：30/30 状态正确，27/27 retrieval all-required@5，失败 0。
- mvp_stress_v1：48/51 状态正确，42/45 retrieval all-required@5；失败仍为
  s21、s22 的 OCR 参考码损坏及 s49 的歧义检索。OCR token 保留率仍 9/12，没有修正 gold。
- 压力下游：Claim 核心 recall 22/22，candidate 4/4，judge 8/12，timeline 10/10，排序44/44；
  行为/主体 rule fake 的 4 个 uncertain 保持原有分布。这些是 oracle/rule 结果，不是 Qwen。
- mvp_v1 下游：Claim 16/16，candidate 4/4，judge 8/8，timeline 6/6、排序14/14，阶段失败 0。
- `uv run pytest -q`：**173 passed**，1 条已有 Starlette/AnyIO deprecation warning。
- `uv run ruff check .`：通过。`uv run mypy --strict src`：64 个源文件通过。
- “生椰拿铁的配方是什么？”原表格聚焦回归仍 validated，引用与来源元数据保持正确。

## 验收边界与下一步

本次满足 4C 的有限安全目标：s19 不再 false answer，s27 没有错误 validated，原成功
控制题没有大面积 false abstain。没有修改 Claim prompt/分类、contradiction 规则、
uncertain gold、retrieval 或 citation whitelist/来源链；没有新增依赖。

**仍不建议结束第 9 阶段。** 首轮真实 Claim 的类别 8/12、s06 独立范围丢失、
s09/s10/s38 分类问题未在本轮修复；QA 更完整不等于 Claim 抽取已经恢复。
建议下一步单独开始 Claim 专项 **4D**。另保留 OCR 参考码损坏、语义 uncertain 评测口径
以及 s27 支持性来源元数据导致保守拒答的后续问题。本轮没有开始 4D 或第 10 阶段。
