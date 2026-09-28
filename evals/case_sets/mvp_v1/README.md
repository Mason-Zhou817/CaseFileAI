# MVP v1 synthetic scenario set

This set is wholly synthetic. The names, companies, amounts, dates, events, and
documents were written for evaluation; none came from a user case file. The eight
PDFs and their relative paths are pinned by SHA-256 in `case_set.json`.

| Document key | Pages | Scenario |
| --- | ---: | --- |
| `bank` | 2 | Native-text payment record; the amount appears on pages 1 and 2; one exact duplicate upload is requested |
| `lease` | 2 | Contract parties, lease dates, rent, and delivery duty |
| `clear_scan` | 1 | Image-only receipt with date, amount, and Chinese name |
| `low_scan` | 1 | Image-only lower-resolution check sheet with slight blur, amount, date, name, and payment state |
| `table` | 2 | Native-text table with line-wrapped amount/date and adjacent records with different values |
| `long` | 12 | Distinct page records; gold facts on pages 2, 7, and 12 |
| `order_v1` | 1 | Initial purchase-order amount and date |
| `order_v2` | 1 | Updated amount and date; imported as version 2 of `order_v1` |

The 30 manually authored questions comprise **27 answerable** and **3 abstain**
cases. Tags overlap; their counts are:

| Tag | Count | Tag | Count |
| --- | ---: | --- | ---: |
| `text_pdf` | 11 | `ocr` | 8 |
| `ocr_noise` | 5 | `table` | 4 |
| `long_doc` | 3 | `version` | 3 |
| `amount` | 11 | `date` | 11 |
| `person` | 6 | `action` | 5 |
| `abstain` | 3 | `multi_evidence` | 2 |
| `same_fact_multi_page` | 2 | `paraphrase` | 3 |
| `citation_page` | 12 | | |

Every answerable item has a document key, one-based page, and short gold quote.
The two `multi_evidence` items require both pages. CI parses the actual PDFs,
checks each gold page and text, and verifies that scan pages use OCR. The date
in table question `q20` is intentionally split between two extracted lines; its
gold is checked after whitespace normalization. Other gold quotes are exact
substrings of extracted page text. The low-resolution image visibly contains
an `O` in a non-gold hint line; the 2026-09-24 OCR run read it as `0`. That is
an observed OCR character error, while the annotated date, amount, and name
were recovered on this machine.

## Run the offline baseline

From the repository root, with the pinned local BGE model already cached:

```powershell
uv run python evals/case_sets/mvp_v1/run_baseline.py --output .eval-results/mvp-v1-2026-09-24
```

This wrapper uses the production ingestion, indexing, retrieval, Evidence, QA,
and citation-validation services. It forces Hugging Face offline mode and injects
`GoldEvidenceFakeLLM`; `case_set.json` also forbids cloud QA. The FakeLLM returns
the annotated answer only when **all** gold snippets appear in the evidence sent
to QA. It can therefore measure retrieval and citation plumbing, but its answer
status and answer-match rates do **not** estimate real-model QA accuracy or false
abstention. No Qwen or other cloud API is called.

The run writes `results.jsonl` and `summary.json` to the local, Git-ignored
output directory. The committed [baseline summary](../../../docs/evaluation/baselines/mvp-v1-2026-09-24.json)
is a snapshot of one offline run. Latency depends on hardware and model cache
state. On that run, strict retrieval hit@5 was **27/27**, correct abstain **3/3**,
document/page/snippet citation checks **29/29**, and there were no runner failures.
All tagged answerable groups met their gold evidence in top 5; there is no
measured retrieval miss or citation page mismatch in this initial set. The
observed `O`/`0` OCR error did not affect an annotated fact. QA model errors
remain unmeasured by this FakeLLM baseline.

## Versions, duplicates, and regeneration

`bank.repeat_uploads = 1` exercises exact-content deduplication. `order_v2` has
`version_of = "order_v1"`, so the runner imports both under one document ID,
searches the latest version, and checks that old pages remain addressable. A
separate integration test validates that an answer cited from v1 retains its
original citation after v2 is indexed. The ordinary question set evaluates
current-version retrieval; its old-version question is an abstain case because
the current document does not state the old amount.

The PDFs can be regenerated with:

```powershell
uv run python evals/case_sets/mvp_v1/generate_documents.py
```

The generator uses a local Chinese TrueType font. Set `CASEFILE_EVAL_CJK_FONT`
if the system font cannot be found. Generated PDFs are byte-stable with the
same font and runtime; if regeneration changes bytes, review the render and
update the pinned hashes and gold labels deliberately. No external data or
image service is used.

## Downstream gold extension

The separate [downstream evaluation](../../../docs/evaluation/downstream.md)
adds a ninth, three-page synthetic PDF with disputed statements. It leaves the
original eight-document/30-question case set and its baseline unchanged.
The extension contains 16 Claim questions, eight conflict pairs, and six
Timeline events. Use `run_downstream_baseline.py` to generate the downstream
reports in a temporary case, fully offline.
