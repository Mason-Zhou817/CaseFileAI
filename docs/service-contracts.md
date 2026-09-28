# Application service contracts

Interfaces may serialize these operations differently, but GUI/API, CLI, and MCP must call the same application services.

## CaseService

```text
create_case(name, description?) -> CaseSummary
list_cases() -> list[CaseSummary]
get_case(case_id) -> CaseSummary
```

## IngestionService

```text
import_document(case_id, path) -> DocumentSummary
get_document_page(document_id, page_number) -> PageView
```

The service hashes content, selects text extraction or OCR per page, persists page provenance, chunks changed pages, and schedules only invalidated downstream analysis.

## RetrievalService

```text
search_case_evidence(case_id, query, limit=10) -> list[SearchHit]
```

The service performs Chinese-aware keyword retrieval, vector retrieval, deterministic fusion, and provenance-preserving result construction.

## QAService

```text
ask_case(case_id, question, retrieval_limit=5) -> GroundedAnswer
```

The service retrieves evidence, requests structured model output, validates all citations, retries or abstains on invalid grounding, persists the answer, and returns only validated citations.

## ClaimService

```text
extract_answer_claims(answer_id) -> tuple[ExtractedClaim, ...]
```

The current foundation accepts only a persisted, successfully validated Grounded QA answer.
It splits each answer claim into minimal factual statements, checks support again against that
claim's cited evidence, and persists links to the exact `AnswerCitation` rows. The v2 extraction
contract additionally requires Claim text to be an extractive span of its source answer claim.
Document-driven extraction is not implemented yet.

## ClaimCoverageService

```text
evaluate(answer_id, extraction_version, model_version) -> ClaimCoverageResult
```

The result contains per-source-answer-claim covered Claim IDs and verbatim uncovered spans,
plus per-Claim citation evidence and validity issues. `complete` requires all non-punctuation
characters of every validated QA claim to be covered by valid extracted Claims. It is a
conservative answer-to-Claim coverage check, not semantic proof of every proposition.

## ConflictCandidateService

```text
generate(case_id, extraction_version, model_version) -> tuple[ConflictCandidate, ...]
```

It returns case-scoped, distinct, same-kind Claim pairs sharing a subject/topic cue, with
both sides' validated AnswerCitation evidence. It does not assign a contradiction verdict.

## ConflictService

```text
detect_conflicts(case_id, changed_claim_ids?) -> ConflictDetectionResult
```

The initial conflict taxonomy is date, amount, action-presence, and subject/person.

## TimelineService

```text
build_timeline(case_id, changed_claim_ids?) -> TimelineResult
```

## FeedbackService

```text
submit_feedback(case_id, target_type, target_id, rating?, corrected_value?, reason?)
```

## Failure contract

- Missing IDs produce typed not-found errors.
- Unsupported or unsafe files are rejected before parsing.
- Model timeouts are retriable and observable.
- Insufficient evidence is a successful abstention, not an internal error.
- Invalid citations are never silently exposed to the caller.
