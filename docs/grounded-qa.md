# Grounded case-file QA and citation validation

## Request path

`DefaultQAService` is the single application-layer workflow used by interface adapters:

```text
question
→ current-source/index fingerprint
→ hybrid retrieval
→ persisted chunk evidence
→ structured answer
→ hard citation validation
→ claim support validation
→ persisted answer and citations
```

`POST /api/cases/{case_id}/ask` accepts `question` and `retrieval_limit` (1–20). The
response contains `answered` or `insufficient_evidence`, backend-generated claims and
citations, and per-stage run metadata.

## Evidence and source coordinates

One complete page-local chunk is one evidence record. Its deterministic `evidence_id` is
derived from the immutable `chunk_id`. `start_offset` and `end_offset` are Python string
offsets into the persisted page text; they are best-effort display coordinates, not PDF glyph
coordinates. Source display metadata is always resolved by the backend through:

```text
evidence → chunk → page → document_version → document
```

The model receives only the question plus a bounded list of per-round `citation_id` aliases
(`E1`, `E2`, ...) and verbatim quotes. The structured answer schema limits citations to those
aliases. The backend maps each selected alias back to its stable `evidence_id` before hard
validation, persistence, or API output. Aliases are never stored as evidence identifiers.
The model does not author filenames, page numbers, document IDs, or evidence IDs.

## Validation and abstention

Hard validation rejects the whole generated answer when any core claim lacks citations, an
alias is absent from the current round, a mapped evidence ID is cross-case, or its persisted source
chain/filename/page/quote cannot be resolved. The service never keeps a conclusion after
merely deleting an invalid citation.

Each hard-valid claim receives a second structured support decision: `supported`,
`partially_supported`, or `unsupported`. The current conservative policy accepts only
`supported`; both other results become `insufficient_evidence`. This is a model judgment that
reduces risk, not a proof of factual correctness.

The displayed answer is rebuilt from validated claim text. The model's free-form `answer`
field is never displayed directly, so it cannot add uncited conclusions. Empty retrieval,
incomplete indexing, model-declared insufficiency, hard validation failure, and semantic
validation failure have distinct refusal reasons. Provider failures return HTTP 503 and are
not mislabeled as evidence insufficiency.

## Persistence and cache invalidation

Migration `20260923_0004` adds `evidence`, `questions`, `answers`, and `answer_citations`.
Historical answers keep their original versioned citation lineage across restarts.

The answer cache key covers the case, normalized question, retrieval limit, latest document
content/version fingerprint, live index fingerprint, model, prompt, retrieval, embedding, and
chunking versions. A latest-document update or an incomplete-to-complete index transition
therefore cannot reuse the previous answer. Stored run metadata includes all these effective
versions, offered evidence IDs, validation result/refusal reason, and retrieval/generation/
validation/total latency. Credentials and raw provider errors are not persisted or returned.
