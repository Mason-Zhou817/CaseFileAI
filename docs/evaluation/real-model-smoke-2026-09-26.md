# 第 9 阶段最终验收：真实 Qwen smoke

**结论：验证任务已完成，暂不建议正式结束第 9 阶段。** `s19` 在无证据负例上给出了事实性答案，`s27` 在正确 gold 已进入 context 时回答了错误来源，两题均被现有服务标为 `validated`。本轮保留失败，没有修改业务代码、prompt、检索、top-k、拒答或引用规则，也没有重跑失败题。

基线 commit：`0047fb28943105eb8fe7c0167796c42bb3b11f50`。实际运行日为 **2026-09-26**，故报告使用该日期，未沿用请求示例中的 2026-09-25。可提交摘要：[real Qwen baseline](baselines/mvp-stress-v1-real-qwen-2026-09-26.json)。完整逐题、Claim、语义输出和 HTTP 计数留在 `.eval-results/mvp-stress-v1-real-model/`，不提交原始运行目录。

## 隔离、配置和复现

- 启动时 HEAD 与基线一致，工作区干净。只导入 `evals/case_sets/mvp_stress_v1/documents/` 中固定 SHA-256 的 11 份全合成 PDF；在线配置与已提交离线材料清单、路径、hash 完全一致，原始问题文件 hash 也与 4A 基线一致。
- 在线配置明确 `allow_cloud_model=true`。调用使用项目现有模型参数 **`qwen3.7-plus`**，endpoint 为 `https://example.invalid/v1`，`json_schema`，结构校验最多 3 次、HTTP timeout 60 秒。没有独立验证该供应商别名背后的固定模型快照。
- 复用 evaluation runner：临时数据库、临时导入目录和新案件；没有打开现有用户案件数据库或用户 PDF。BGE 模型及 revision 保持项目配置，top-k=5。
- API key 只用于 HTTP 授权，不写入代码、模型 prompt、日志、报告或提交。HTTP 观察只记录状态码、错误类型、耗时；结构化模型输出仅来自上述合成材料。

```powershell
uv run python evals/case_sets/mvp_stress_v1/run_final_smoke.py --real-model --output .eval-results/mvp-stress-v1-real-model
```

输出目录不能已有 `model_calls.jsonl`，防止覆盖首轮结果。复现新运行需另选目录。此入口初始输出 `pending_review`；本次提交基线已逐项对照原文。复核由 **Codex 直接检查已输出的文字与证据**完成，没有调用额外模型 API 当 judge；它不是用户或独立人工的签字验收。

| 主 QA 分组 | 固定 question ID | 结果 |
| --- | --- | --- |
| 普通明确题 | s01、s02、s03、s04、s38 | 5 题答案正确；s38 有额外对照页引用待确认事件关联 |
| 表格 / 换行 | s06、s07、s08、s09、s10 | 5 题答案正确，没有 false abstain |
| 改写 / 较难 | s11、s12、s13、s14、s27 | 4 题正确；s27 来源错误 |
| 无证据 | s16、s17、s18、s19、s20 | 4 题正确拒答；s19 false answer |

相对原来的 s01–s20 子集，普通组用 s38 替换 s05，补齐行为及多事实回答；较难组用 s27 替换 s15，补齐 OCR 页码及 ISO 日期风险。保留其余题目与全部材料、gold，不按运行结果选择题。

这 20 题不包含已知 OCR 参考码失败 s21/s22，不能视为该问题已经恢复，也不能替代完整 51 题压力基线。

## QA、Citation 和 Provider

| 指标 | 结果与分母 |
| --- | ---: |
| 主 QA 数 | 20（15 可回答、5 负例） |
| answered / model_abstained | 16 / 4 |
| 回答状态准确率 | 19/20；s27 状态正确但答案错误，不能据此验收 |
| 对照所问事实后的整体正确率 | 18/20；可回答题 14/15，负例 4/5 |
| retrieval all-required@5 / gold 进入实际 QA context | 15/15 / 15/15 |
| false abstain / 完整证据下 false abstain | 0/15 / 0/15 |
| 无证据 false answer | 1/5（s19）；模型输出和最终服务输出均出现 |
| 可回答题错误答案 | 1/15（s27） |
| provider failure / validation failure | 0 / 0；两道错误答案没有被 validation 拒绝 |
| citation document / document+page / snippet 命中 gold | 16/20、15/20、15/20 |
| 所问来源与字段的 citation set 支持性 | supported 14、unsupported 2、not_applicable 4；分母 16 个实际 answered 问题 |
| 实际模型 HTTP 调用 / 2xx / 结构化成功 | 67 / 67 / 67 |
| 429 / 503 / 5xx / timeout | 0 / 0 / 0 / 0 |

HTTP 调用分布：QA 39、Claim 22、semantic contradiction 6。结构化调用也为 67 次，因此没有发生 JSON 校验补发。`s17` 的两次回答生成来自现有业务的聚焦片段逻辑；不属于代理重跑。一个样本没有 503，不能说明历史 503 已永久解决。

Citation gold 指标分母是**可回答题实际返回的 20 条引用关联**；负例 s19 的 2 条引用不进入这些指标，单独记录。页码指标要求文档也正确，不是只比较数字。所有 22 条已返回关联都通过现有来源链验证，不能把 16/20 的 gold 文档精确率误读为有 4 条伪造引用。

- `s11` 额外引用甲账簿第 1 页，给出海岚与第一笔付款的主体关联；第 3 页给出金额。这是合理补充上下文，第 1 页不匹配窄 gold，不构成实际错页。
- `s38` 的陈述 A 第 1 页直接支持两项答案；模型又给两项事实引用对照页。对照页虽有已支付、主体简称，却未明确绑定 A，需用户确认额外引用的事件关联。保留 gold mismatch，不能自动将其全部改算正确。
- 16 个最终回答的**字面陈述**都有原文支持，但 `s27/s19` 没有回答所问来源或字段。`citation_semantic_support` 本次按“原文支持所问事实”评为 unsupported；同时记录 `literal_returned_claim_support=supported`，避免把问题范围错误与凭空编造事实混淆。

### 两个阻止无条件验收的 QA 样本

| ID | 预期 | 实际服务输出及证据 | 归类 |
| --- | --- | --- | --- |
| s27 | 低清扫描第 2 页日期：2026-03-08 | “第2页的参考日期为2026年3月3日；第2页未记载目标交付日期”，引用 `06_long_50_pages.pdf` 第 2 页；正确 OCR gold 为 rank 3，已在 context | qa_wrong_answer、citation_wrong_document、citation_wrong_page、citation_unsupported；不是 retrieval_miss |
| s19 | 最终到账日期没有证据，应拒答 | “第一笔设备预付款实际转账日期为2026年3月1日”，引用甲账簿第 3 页及陈述 A 第 1 页 | false_answer / qa_wrong_answer、citation_unsupported；转账不能等同最终到账 |

## Claim smoke

真实成功 validated 回答：**s01、s02、s03、s06、s08、s09、s10、s13、s38**，共 9 个；覆盖 amount、date、person、action、table、OCR 和多事实回答。

| 指标 | 结果 |
| --- | ---: |
| 预期核心事实 / 实际 Claim | 10 / 12；多出的 2 条是表格行号与合同的关系事实 |
| 独立原子事实（逐 Claim） | 11/12；逐回答 8/9 |
| category 正确（全部实际 Claim） | 8/12 |
| 核心事实 category 正确 | 7/10 |
| 来源链合法（逐 Claim）/ 已绑定真实 evidence ID | 12/12 / 13/13 关联 |
| 严格 gold 来源精确率 | 12/13 关联；保留 s38 的额外引用 |
| Coverage complete | 8/9 |
| 字面 unsupported Claim / 抽取异常 | 0 / 0 |

`unsupported=0` 只表示未发现无出处或数值不实的文字，不代表分类和事件范围正确。逐条 actual Claim、expected category 及来源检查都在基线中。

- **s06**：`结算表R-01对应CT-77` 被标 amount（关系事实应是 other）；`月租金是人民币85000.50元` 没有保留行号/合同主体，不能作为独立完整金额断言。Coverage 的 incomplete 实际来自未覆盖连接词“的”，是字面覆盖信号，不能声称系统已经识别了语义范围丢失。
- **s09、s38 的付款状态**被标 other，预期 action；**s10 的审核人**被标 other，预期 person。现有候选服务会跳过 other，这会影响相应类型的下游分析。
- s08 的额外行号/合同关系为 other，日期 Claim 保留 CT-77 主体，属于合理拆分。
- s38 的两个事实在真实 QA 阶段已拆为两个 AnswerClaim，真实抽取得到两个 Claim；它不证明 Claim 模型能从单个融合片段中稳定完成拆分。

## 行为 / 主体真实语义判断

为区分抽取与 judge 失败，用现有 gold FakeLLM 在同一临时案件建立已验证、持久化的合成 Claim，并经现有 candidate service 生成指定 6 对；**只有语义 judge 使用真实 Qwen**。这不是端到端真实 QA/Claim recall，也未把本次分类错误的真实 Claim 替换成“通过”的统计。

| Pair | gold | 真实 Qwen / 服务 verdict |
| --- | --- | --- |
| action_conflict | contradiction | contradiction |
| action_compatible | non_contradiction | non_contradiction |
| action_uncertain | uncertain | non_contradiction |
| person_conflict | contradiction | contradiction |
| person_compatible | non_contradiction | non_contradiction |
| person_uncertain | uncertain | non_contradiction |

行为和主体分别 **2/3 严格 gold 匹配**，两条真正矛盾均检出，未把两个 uncertain 对判成 contradiction。原始模型结构化输出保存在提交基线与本地 `model_calls.jsonl`。

两处不一致需要复核 gold/context，而不能直接归因模型错误：

1. action_uncertain 的完整右侧引用同时包含“款项确已支付”和“收到发票后支付”。Qwen 将条件槽位与发生状态区分，且依据同页已支付文字判断相容；原 gold 仅把条件句视为不能证明发生。
2. person_uncertain 明说“另一笔付款”，真实模型识别为不同事件并判 non_contradiction；原 gold 更保守地写 uncertain。需确认评测口径接受确定不同事件为 non-conflict 还是仍强制 uncertain。

本轮不改 gold、不改 provider 规则，也不宣称这两对通过。

## 延迟、缓存和 FakeLLM 差异

- 主 QA service 调用 p50 **14.126 秒**、p95 **26.799 秒**；包含该调用内部的检索、生成和支持校验，排除外部诊断检索与共享导入/索引。
- 9 个 Claim 回答 p50 **22.283 秒**、p95 **44.519 秒**；样本少，p95 是该样本最大值。
- 共享导入 3.986 秒、索引 47.050 秒；全流程含下游验证约 672.528 秒。未做长文档性能优化。
- 主 20 题全部 cold、cache_hit=false。重复 s01、s06、s16 均 warm/cache_hit=true、answer ID 不变，各约 7.8–8.4 毫秒，**新增真实模型调用为 0**；warm 不计入 cold QA 指标。

对相同 20 个问题，4B 的离线 gold-gated FakeLLM 会答 15、拒答 5，s27 命中 gold、s19 正确拒答。真实模型显著不同：s27 选错来源，s19 替换字段回答，Claim 出现分类及范围问题。真实 semantic judge 对 conflict/compatible 明显优于仅识别特定措辞的 RuleSemanticFakeLLM，但无法因此推断整条真实下游链可靠。

## 失败归属及验收建议

**已知系统事实：** QA 模型输入仅含 citation alias 和 quote，没有结构化文件/页码元数据；支持校验输入是 Claim 与引用，不含原问题及目标事实槽位。s27/s19 均被接受为 validated。Claim 的确定性分类会识别日期/金额表达，但没有全面约束 other/person/action 或无金额值的 amount。这些是本次代码及运行证据确认的防护边界，未修改它们。

**模型行为：** Qwen 选错 s27 的文档；在 s19 用转账字段替代到账字段；对 s06 过度拆分并失去主体，部分 action/person 标 other。s27 的“低清扫描”描述缺乏唯一文档标识，也没有出现在该 OCR 文本中，模型如何利用元数据需要后续受控定位；不能凭本轮结果保证单一原因或特定修复一定有效。

**Provider / 网络：** 本次无故障；不能估计长期可靠性。

**待人工确认：** s38 额外引用的事件关联、两个 uncertain gold 的口径、用户或独立人工签字。没有调用额外模型充当评测 judge。

**建议暂不正式结束第 9 阶段。** s19 已违反本阶段无证据拒答边界，s27 的来源错误也未被拦截；应先单独决定如何处理问题字段与来源范围验证，以及 Claim 分类/范围问题，再做针对性复验。本轮到报告和提交为止，未开始修复或第 10 阶段。

## 质量检查

本轮仅新增显式 smoke 观察工具、使用 MockTransport 的 6 个离线测试、合成结果摘要及文档。CI 不调用云 API，业务文件无 diff。

- `uv run pytest -q`：**148 passed**，65.52 秒；1 条现有 Starlette/AnyIO 弃用警告。
- `uv run ruff check .`：通过。
- `uv run mypy --strict src`：通过，63 个源文件。
- 原始结果、报告及新工具按配置中的实际 credential 检查：0 处命中。
