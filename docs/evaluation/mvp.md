# MVP evaluation

## Post-MVP case-set runner

The case-set runner imports local PDFs into a newly migrated temporary SQLite
database and temporary data directory. It calls the existing ingestion, indexing,
retrieval, and grounded QA services, then removes the temporary case and database.
It writes reports only to the requested local output directory. It does not change
retrieval ranking, QA prompts, citation validation, or abstention rules.

Prepare a case-set JSON file like [`evals/case_set.example.json`](../../evals/case_set.example.json).
`name`, `allow_cloud_model`, and a nonempty `documents` list are required. Every
document has a unique `doc_key` and a PDF `path` relative to the case-set file;
`expected_sha256` is optional. Duplicate PDF bytes under different keys are rejected
because document identity would be ambiguous after case-scoped deduplication.
For version scenarios, `version_of` names an earlier `doc_key` and imports the PDF
as a new version of that document. `repeat_uploads` repeats a PDF upload and checks
the existing deduplication result. The summary records these document checks.
`retrieval_k` defaults to 5 and must be between 1 and 20. Set
`allow_cloud_model: true` **only for synthetic or de-identified material**. With
`false`, the CLI rejects cloud QA before importing anything; automated tests can
inject a local/test provider through `run_evaluation`. This gate does not establish
that material is safe to disclose. The operator must make that determination.

Write one JSON object per line in a questions file, as in
[`evals/questions.example.jsonl`](../../evals/questions.example.jsonl). Each line
requires a unique `id`, `question`, `expected_status`, `expected_answer_any`, and
`gold_evidence`; `tags` is optional. An `answered` item requires at least one
accepted answer string and one gold `{doc_key, page, snippet}` record. An
`insufficient_evidence` item requires both arrays to be empty. Unknown keys,
unknown document keys, blank lines, missing PDFs, bad hashes, and malformed JSON
stop the run before creating reports. Gold page numbers are allowed to be wrong so
that citation/page mismatches remain measurable.

From the repository root, with the local embedding model and QA provider configured:

```powershell
uv run python scripts/evaluate_case_set.py --case-set evals/case_set.example.json --questions evals/questions.example.jsonl --output .eval-results/example
```

The example PDFs are checked-in synthetic material. The example explicitly allows
the configured cloud model. Do not use real sensitive case files with a public
model API. If the case set disallows cloud QA, supply a verified local/test provider
through the Python runner API; the CLI currently has no local LLM adapter.

`results.jsonl` contains one row per input question, including failed questions.
`retrieval_results` lists the top `k` chunks with source document, version, page,
and exact text. For answerable questions, a gold record is hit when a retrieved
chunk has the same `doc_key` and page and contains the gold snippet after Unicode
NFKC, case, whitespace, and punctuation normalization. `retrieval_any_hit` needs
one gold record; `retrieval_all_required_hit` and the `retrieval_hit` alias require
every gold record. Negative questions have `null` hit fields. These are retrieval
measurements, not permission to answer.

`actual_status` is `answered` only when grounded QA returns a validated answer;
otherwise the service returns `insufficient_evidence`. `abstain_correct` checks
that status against the expected answerability. `answer_match` uses deterministic
normalized equality or inclusion of one permitted answer fact. Nonmatching text
is `null` with `manual_review_required: true`, rather than an automatic claim of
semantic error. `answer_match_method` records the rule. The evaluation matcher uses
no LLM judge. The application QA service separately uses structured claim-support
and question-alignment validation before releasing a validated answer.

Each citation records document and version IDs, page, Evidence ID, and original
text. `document_match` checks the gold document, `page_match` the gold document
and page, and `snippet_match` the full gold document/page/snippet tuple. Snippet
accuracy is lexical and does not prove semantic support; the service's own citation
and model support/alignment validation still apply. Incorrect gold labels can therefore
lower evaluation accuracy without changing a validated QA answer.

`summary.json` includes each rate as `{numerator, denominator, value}`; `value`
is `null` if the denominator is zero. Status accuracy covers all questions, with
execution failures counted incorrect. Correct abstain rate covers negative
questions. Retrieval rates cover answerable questions whose retrieval completed;
negative questions and retrieval errors are excluded. Citation rates cover
returned citations for answerable questions; missing citations are visible through
status and failure counts. Answer match rate covers only comparisons resolved by
the deterministic rules; the manual-review count reports unresolved comparisons.
`failure_count` counts rows with a `failure_stage` of import, index, retrieval, QA,
validation, or evaluation. `failure_reason` preserves a short error or mismatch code.

Since phase 9 task 4C, `alignment_validation_failed` is a validation-stage result.
Its reason distinguishes a requested-field mismatch, source-scope mismatch, an
unrelated answer, and uncertainty. A rejected candidate produces no final claims
or citations. On a negative question, that can simultaneously be a correct final
abstention and a validation rejection; these measurements should not be merged.
Alignment provider/protocol errors remain QA execution failures, do not become
validated answers, and are not cached. See the
[4C diagnosis and bounded real regression](qa-alignment-4c-2026-09-26.md).

Every invocation starts from a fresh case and database. A repeated question within
one set can still hit the QA cache: per-row `run_mode` is `warm` in that case and
`cold` otherwise, based on the observed `cache_hit` flag. The embedding model may
remain warm in process. There is no separate warm-run replay mode yet.
Per-row `import_ms` and `index_ms` repeat the
shared run measurements. `retrieval_ms` measures the evaluation's search call;
`qa_ms` measures `ask_case`, including its own search and any model calls.
`total_ms` covers the per-question retrieval, QA, and scoring, excluding shared
import/index. Summary p50/p95 use the nearest-rank method on available per-question
totals; `run_total_ms` includes migration and processing but excludes report writing.
Unavailable stage timings are `null`. Input hashes and model versions are recorded
so runs can be compared against the same material and configuration.

The first [MVP v1 synthetic scenario set](../../evals/case_sets/mvp_v1/README.md)
contains eight PDFs and 30 annotated questions. Its committed
[2026-09-24 offline baseline](baselines/mvp-v1-2026-09-24.json) uses local BGE
and a gold-gated FakeLLM; it measures retrieval and citation wiring, not real
model QA reliability.

The [MVP v1 downstream evaluation](downstream.md) adds labelled Claim,
candidate, contradiction, and timeline stages using a separate synthetic
case and a deterministic, fully offline provider boundary.

Run deterministic and FakeLLM evaluation with `uv run pytest -q`. Run the isolated
real-model smoke with `uv run python -m scripts.evaluate_live_mvp` after configuring
Qwen in `.env`. The live script imports only a checked-in synthetic PDF into a
temporary database. It reports schema acceptance, external-fact checks, citation
lineage, backend verdict, failure category, and latency in `live-qwen-mvp.json`.
Set `CASEFILE_EVAL_QA_ONLY=1` and `CASEFILE_EVAL_REPORT` to run one isolated QA
attempt without overwriting the full report. When Qwen QA does not yield a validated
answer, the full script marks its Claim source as a FakeLLM answer that passed the
same persisted citation chain, then calls real Qwen for Claim extraction. Those
paths are reported separately.

| Area | Deterministic / FakeLLM evaluation |
| --- | --- |
| Grounded QA | Answerable and unanswerable, cache/index invalidation, citation allowlist, support refusal, temporary model failure in `test_qa_service.py`; persisted lineage in `test_qa_persistence.py` |
| Claim | Single and multiple atomic facts, no answer-external fact, citation binding, invalid/foreign evidence in `test_claim_extraction.py` |
| Coverage | Complete, incomplete and illegal Evidence in `test_claim_coverage_candidates.py` |
| Contradiction | Date, amount, action, person, explicit non-contradiction, uncertain, and different-event cases in `test_contradictions_timeline.py`; DB lineage in `test_contradiction_persistence.py` |
| Timeline | Exact, partial, unknown and provenance in `test_contradictions_timeline.py` |
| Integrated workflow | Fresh Case → PDF import → index → search → QA → Claim → Coverage → candidate → contradiction → Timeline → GUI HTTP assets, plus Case isolation and page quote checks in `test_workspace_e2e.py` |

The real Qwen smoke is observational: a small sample cannot estimate reliability.
An answerable question can abstain or fail hard citation validation. Such a run is
recorded as a refusal, not counted as a successful answer. A contradiction model's
JSON verdict is recorded separately from the backend verdict; the backend may
conservatively return `uncertain`. A transient model failure must not erase prior
validated answers, Claims, or saved contradiction decisions.

## 2026-09-23 live observations

`qwen3.7-plus` was called only with the checked-in synthetic case. The first full
run had one schema-valid `insufficient_evidence` response and two HTTP 503 model
failures for three answerable phrasings (24.24, 35.79, 46.11 seconds). The
validated FakeLLM fallback then allowed a separate real Qwen Claim extraction:
one schema-valid Claim, valid Evidence, complete Coverage, no answer-external
fact, 19.67 seconds. The real semantic action contradiction output was schema
valid; the backend verdict was `contradiction`, reason code
`model_incompatible_same_slot`, with both Evidence sets unchanged, 10.57 seconds.
See [full run](live-qwen-mvp.json).

A separate QA retry with the exact synthetic question succeeded: a validated
answer with one legal citation, supported Claim, matching source amount, and
page quote lineage, 15.05 seconds. Real Qwen Claim extraction on that Qwen answer
also succeeded with one Claim, valid Evidence and complete Coverage, 26.86
seconds. See [QA retry](live-qwen-qa-retry.json). `outside_fact_detected` is a
targeted check against the known synthetic amount; it is not a general factuality
proof. Earlier abstentions and 503s remain part of the evaluation record.

Risks retained for future work: candidate miss, different events admitted as
candidates, strict event slots leaving true conflicts uncertain, OCR and alias
generalization, small Qwen sample, and Timeline not inferring unknown dates.
