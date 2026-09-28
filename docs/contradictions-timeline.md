# Candidate-bound contradictions and Timeline

## Scope and identity

`DefaultContradictionService.detect_case(case_id, extraction_version, model_version)` consumes only `DefaultConflictCandidateService.generate(...)` output. It does not compare all Claims. A candidate's UUIDv5 identity is derived from case, ordered Claim IDs, and Claim extraction/model versions. The result UUIDv5 additionally includes detector, prompt, and semantic model versions. Repeating detection with the same versions returns the saved result.

The persisted `ContradictionResult` contains result/case/candidate IDs, ordered Claim IDs, type, three-way verdict, a reason and machine-readable basis code, both complete `AnswerCitation` sets, detector/prompt/model versions, and source Claim extraction/model versions. Each citation carries its evidence ID, original QA Claim ID, document/version/page IDs, filename, page number, and original quote. The trace is `Case → Candidate → Claim → validated answer citation → Evidence → document page and quote`. The model receives only Claim text and evidence quotes; it has no output fields for IDs.

## Decision rules

| Type | Detector | Conservative boundary |
| --- | --- | --- |
| Date/time | Deterministic parsing of ISO/Chinese year, month, day and clock time. Compare known components only when the remaining event/slot text matches. | Invalid, vague, multiple, or unaligned dates return `uncertain`. A partial date compatible with an exact date is `non_contradiction`. |
| Amount | Decimal parsing, comma removal, `千/万/亿` conversion, and CNY/USD currency normalization. Compare only matching event/slot text and currency. | Different fields/events, multiple numbers, and missing/mismatched currency on one side return `uncertain`. No currency is inferred. |
| Action | Qwen structured output supplies same-event/slot flags and separate occurrence states. | A contradiction requires opposite explicit occurrence states. Missing/malformed output returns `uncertain`. |
| Person | Qwen structured output supplies same-event/slot flags and source identities. | A contradiction requires two different literal identities in the corresponding Claims and an explicit exclusivity cue in at least one Claim (`唯一`, `仅有`, `只有`, `独自`, `仅由`). Containment and very similar names stay `uncertain` as possible aliases/OCR variation. Plural-compatible roles remain `uncertain`. Different roles/events are `non_contradiction`. |

`uncertain` is persisted as its own verdict and is never promoted to contradiction. This intentionally favors precision over recall. Matching event text is deliberately strict; paraphrases can remain uncertain until later semantic alignment is validated. Candidate generation can still miss relevant pairs.

## Backend validation

Before model invocation, the service checks case, distinct Claim IDs, matching factual kinds and source versions, complete evidence ID sets, source QA Claim IDs, and visible page provenance. The persistence adapter independently checks both Claims and their validated answers belong to the requested case, matches the result kind and stable IDs, and joins every citation to `ClaimEvidence → AnswerCitation → Evidence → Chunk → Page → DocumentVersion → Document`. It checks the quote against the page slice and rejects any added or cross-case citation. The database enforces foreign keys and result type/verdict constraints.

The semantic output schema forbids extra fields and requires all status/identity fields. The backend revalidates it after the provider returns it. Invalid output returns `uncertain` with the `model_output_invalid` basis but is not cached, so a later retry can succeed. An unavailable model is treated the same way. The model cannot choose or replace evidence IDs.

## Timeline

`DefaultTimelineService.build(...)` reads the case's validated Claims and their coverage-checked citations. It emits one stable event per Claim, preserving the Claim's text as the fact; no summary text or new case fact is generated. The event has case, Claim, source extraction/model versions, all citations, linked contradiction result IDs, and `exact`, `partial`, or `unknown` date status. A single valid date in a Claim is normalized; year-only and year-month are partial, vague/multiple/invalid dates are unknown. Unknown dates sort last. Events on the same day remain separate.

This is a basic Claim timeline: it does not infer a date for an undated action from another Claim or merge duplicate real-world events. Doing either without a grounded event link would create unsupported chronology.
