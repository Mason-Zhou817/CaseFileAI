# Claim coverage and conflict candidates

## Qwen Claim extraction smoke test (2026-09-23)

The configured `qwen3.7-plus` was called with synthetic materials only. Two successful,
persisted Grounded QA answers came from the existing integration fixture's FakeLLM QA flow;
the third was its validated multi-fact `GroundedAnswer` fixture. Qwen performed **Claim
extraction and Claim support checks**, not Grounded QA generation. Each elapsed time includes
all Claim extraction and support calls, with no performance tuning.

The first run exposed two errors: the amount Claim was labeled `date`, and it inserted
`人民币` despite that word being absent from the answer. The multi-fact answer split into
two Claims, but the amount half was also labeled `date`. Initial elapsed times were 17,213 ms
(amount), 14,562 ms (date), and 23,663 ms (multi-fact), respectively. The answer's authorized
evidence IDs were used; no evidence ID was fabricated in the observed results. Extraction v2
now requires an extractive text span
and deterministically classifies explicit dates and currency amounts. A model response that
adds answer-external words is rejected. Automated tests cover both failures.

After the fix:

| Validated answer | Qwen Claims | Result | Elapsed |
| --- | --- | --- | ---: |
| `交易金额为450000元。` | `交易金额为450000元` (`amount`) | One fact, no added fact, legal answer evidence | 22,372 ms |
| `交易日期为2021年3月5日。` | `交易日期为2021年3月5日` (`date`) | One fact, no added fact, legal answer evidence | 18,440 ms |
| `交易金额为450000元，日期为2021年3月5日。` | `交易金额为450000元` (`amount`); `日期为2021年3月5日` (`date`) | Two facts split, no added fact, both use the answer's legal evidence | 29,155 ms |

All three extraction calls completed; there were no post-fix provider, schema, support, or
evidence-ID failures. This is a small smoke test, not a quality estimate across case files.

## Coverage definition

The scope is one **successfully validated Grounded QA answer**, not all facts in a case file.
Grounded QA reconstructs displayed answer text by joining its validated `AnswerClaim.text`
values. Each such source claim is the factual source span for coverage. An extracted Claim
counts as covering characters in its source claim only when:

1. its `answer_id` and `source_answer_claim_id` match the validated answer;
2. every attached evidence ID resolves to a citation belonging to that exact source claim;
3. its text, excluding punctuation and whitespace, appears contiguously in that source text.

The deterministic algorithm marks the matched source characters, then emits the remaining
verbatim fragments with offsets relative to `AnswerClaim.text`. `ClaimCoverageResult` carries
the answer ID, extraction/model versions, per-source covered Claim IDs and uncovered fragments,
and per-Claim evidence citations, validity flag and issue. `complete` means all non-punctuation
source characters are marked and no Claim has an issue. Empty extraction leaves the source
uncovered. An invalid citation or a non-extractive Claim covers nothing.

This deliberately conservative surface rule may flag omitted connective words as uncovered
even when no fact is lost. It cannot prove semantic equivalence or that Qwen selected every
possible atomic proposition. The exact source fragment and links make those cases reviewable.

## Candidate generation

The repository first selects Claims through `Question.case_id → Answer → Claim`, restricted to
one extraction/model version. The service checks each Claim's answer and evidence coverage
lineage again and excludes invalid records. It indexes Claims by factual kind (`date`,
`amount`, `action`, `person`) and shared subject/topic cues (`receipt`, `payment`, `contract`,
`investment`, or a shared informative token). `other` Claims are excluded. Only Claims in a
shared bucket become pairs. Each pair uses sorted stable Claim IDs as its key, so input order,
duplicate source rows, and multiple shared cues cannot create duplicate candidates. Self pairs
and identical assertions are skipped. Each candidate includes its case ID, both full Claims,
both sides' citations and the shared topic that explains why comparison is worthwhile.

This is a recall/precision tradeoff. Broad topic cues can admit unrelated events in the same
case, while different wording without a shared cue can miss a useful pair. Later contradiction
detection must recheck event identity, temporal scope, source reliability and actual truth
conditions; a candidate itself carries no contradiction verdict.
