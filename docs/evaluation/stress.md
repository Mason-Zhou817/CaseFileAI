# 压力评测 v1：指标、阶段诊断与基线

> 2026-09-25 复核：4A 的 `date_uncertain` gold 错连到同日子串 Claim；原 judge 对完整“另一笔”Claim 返回 uncertain。逐对检查还发现该子串 Claim 确实产生一条未标注的跨交易日期误报，已在 4B 候选层处理。以下保留 4A 历史实测，修正后的可比结果见 [4B 复核](hardening-4b.md)。

材料和运行命令见 [评测集说明](../../evals/case_sets/mvp_stress_v1/README.md)。本次离线基线日期为 2026-09-25，固定 11 份合成 PDF、66 页、51 题。提交的两份紧凑摘要在 `baselines/mvp-stress-v1-2026-09-25.json` 和 `baselines/mvp-stress-v1-downstream-2026-09-25.json`；逐题和逐对报告仅保存在 `.eval-results/mvp-stress-v1/`。

## 测量口径

- `parsed_text_match`：gold snippet 在指定页解析文本中经 NFKC、大小写、空白及标点归一化后完整出现。OCR token 的 `preserved` 使用字面包含，因此严格保留大小写、字符和分隔符。扫描 gold 依据图像可见内容人工标注，OCR 失真是测量结果。
- `chunk_text_match`：整段 gold snippet 在单个 chunk 中出现。引用仍需走现有 validation；诊断不修改 service。
- `gold_ranks`：每条必需 gold 在实际 top-5 结果中的最早排名，未命中为 `null`。`hit_at_1/3/5` 是 **any-hit**；`all_required_hit_at_1/3/5` 要求每条必需 gold 都命中，分母仅为 45 道可回答题。无证据题这些值为 `null`。`mrr_at_5` 用首个 gold 的倒数排名，miss 记零。`distractor_count_at_5` 是返回的 top-5 chunk 中没有命中任何 gold 的数量；重复命中一个 gold 的 chunk 不算干扰。短歧义查询 `s49` 的单一 gold 是预先指定的诊断目标，漏召回不代表该查询有唯一合法答案。
- `evidence_match`：所有 gold 都进入 QA 本轮证据 ID。`first_failure_stage` 按 parse/OCR → chunk → retrieval@5 → evidence → QA/validation → citation 的顺序赋值。下游沿用任务 3 的首个失败阶段。`stage_failures` 按 gold 记录计数，下游依赖项可能重复计入；它不是互斥的唯一根因总数。
- citation document/page/snippet accuracy 使用 runner 的已返回引用作分母；多引用题可贡献多条。snippet 是**字面** gold 支持，不等价于语义蕴含。`semantic_support_mismatch = null`，所有已回答题设 `manual_review_required = true` 供人工复核。不能把 `null` 当成零或失败。
- Claim fact recall / category / source 是现有 downstream 契约的 gold 匹配。atomicity 只按本次明示的金额+日期、付款人+行为混合句规则检查，属于狭义代理指标。QA 与抽取均由 gold-gated FakeLLM 驱动，不代表真实模型抽取质量。
- candidate recall 分母是两个 Claim 均已就绪的 gold 矛盾对。当前标注的 12 对**并未穷尽**实际 45 个候选，因此 precision=`null`；33 个未标注对不能记作 false candidate。`annotated_pairs_found` 仅显示标注对是否出现。
- contradiction decision accuracy 分母是进入候选集且执行 judge 的 gold 对；end-to-end recall 分母是 4 个 gold 矛盾对。行动/主体走离线规则 provider，结果属于离线边界，不可归因真实模型。
- Timeline event recall 分母 10 个 gold 事件。ordering accuracy 只比较两事件均找到且可比较的不同日期对；同日 tie 不计入。`duplicate_matched_event_count` 按 `(date_value, normalized fact)` 粗略去重，不能自动判断真实事件是否重复。
- 延迟是单次冷运行：`index_ms` 和 `chunks_total` 涵盖所有 11 份文档，不能分摊为长文档专属成本；long/other 的 retrieval 与 QA p50/p95 是同一次共享索引后的逐题观测，并非独立性能基准。

## 2026-09-25 离线实测

| 项目 | 结果 |
| --- | ---: |
| 答案状态 | 47/51（92.16%） |
| 负例正确拒答 | 6/6（100%） |
| retrieval any-hit@1 / @3 / @5 | 37/45、40/45、41/45 |
| retrieval all-required@1 / @3 / @5 | 35/45、40/45、41/45 |
| OCR token 保留 | 9/12（75%） |
| citation 文档 / 页 / 片段 | 均 42/42；仅统计实际返回的引用 |
| Claim recall / 分类 / 文档来源 | 均 22/22；离线 gold-gated |
| 候选矛盾对召回 | 4/4；precision 未定义 |
| 矛盾判定 / 端到端矛盾召回 | 7/12、2/4 |
| Timeline 事件 / 顺序 | 10/10、44/44 可比较对 |
| 长文档五个目标位置 | 1、13、25、38、50 页全部 rank 1 |

首个失败阶段：`contradiction` 5 个 gold pair，`ocr` 2 题，`retrieval` 2 题，其余阶段为 0。逐题 runner `failure_count=4`，这里的 `failure_reason=answer_status_mismatch` 表示 4 道可回答题最终拒答，**不是 Python 异常或 provider 503**。`s21/s22` 的 `O0`、`I1l` 等参考码在 OCR 文字中已经变形；`s27` 的日期可读但没有进入 top-5；`s49` 的单词歧义查询没有召回预指定 gold。4 题全都未达到 all-required@5，因此没有“所有 gold 均进入证据但 QA 错误拒答”的离线样本。

4A 报告显示 5 个 judge mismatch；其中 `date_uncertain` 的标注错误后来证实来自评测 Claim 关联，另有未标注的子串候选误报，见上方复核说明。`action_conflict`、`action_compatible`、`person_conflict`、`person_compatible` 返回 uncertain，当前 RuleSemanticFakeLLM 仅识别特定“本次”表述，因此主要是**离线 provider 能力边界**。不要为了提高指标改 FakeLLM；真实模型表现需要上述可选 smoke 或独立人工复核才能判断。已返回的 citation 均命中人工 gold，但语义支持和未返回引用的题仍需人工复核。

50 页长文档共 50 个 chunk，全案 66 个；全案索引 17.59 秒。长文档题 retrieval p50/p95 为 16.55/21.50 毫秒，其他题 16.45/22.00 毫秒；QA 37.35/42.16 毫秒，其他题 36.97/44.63 毫秒。本次没有观察到明显逐题延迟恶化；单次、共享索引、硬件和模型缓存会影响数值。

4A 时建议 4B 核查 OCR 参考码损坏、OCR 正确但 top-5 漏召回、日期跨交易误判；4B 的实际复核与修正结果见上方链接。当前文档保留 4A 的原始指标作为历史对照。
