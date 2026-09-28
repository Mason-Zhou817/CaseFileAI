# Data model and stable identifiers

## Identifier policy

- Aggregate roots and user-created records use persisted UUIDv4 identifiers.
- Derived source records use deterministic UUIDv5 identifiers when their complete identity is known.
- Reprocessing with a different content or algorithm version creates new immutable derived records.
- Historical answers retain their original citation lineage.
- A filename is display metadata and never an identity or cache key.

Required public identifiers:

```text
case_id
document_id
page_id
chunk_id
claim_id
evidence_id
conflict_id
```

## Core relationships

```text
Case
 └─ Document
     └─ DocumentVersion (content_sha256, parser_version)
         └─ Page
             └─ Chunk (chunking_version, exact page offsets)
                 ├─ ChunkEmbedding (embedding_version, float32 vector)
                 ├─ FTS5 shadow tokens (tokenization_version)
                 └─ Evidence (verbatim source span)
                     └─ ClaimEvidence ─ Claim

Answer ─ AnswerCitation ─ Evidence ─ Chunk ─ Page ─ DocumentVersion ─ Document

Answer ─ Claim (atomic fact; source_answer_claim_id, kind, extraction/model version)
           └─ ClaimEvidence ─ AnswerCitation ─ Evidence

Conflict ─ left Claim
         └ right Claim
```

## Versioned processing

`analysis_runs` records the effective parser, chunker, model, prompt, embedding, and task configuration. `artifact_cache` stores the derived payload and links it to the producing run.

The cache key is a canonical hash over all relevant values:

```text
source content hash
+ parser version
+ chunking version
+ model version
+ prompt version
+ embedding version
+ normalized task configuration
```

## Citation invariant

For every accepted citation:

```text
answer
→ answer_citation
→ evidence
→ chunk
→ page
→ document_version
→ document
```

`Evidence.quote` must be found verbatim in the resolved page text after the documented whitespace normalization. Offsets are nullable and best-effort.

For the current chunk-evidence implementation, the quote is the unmodified complete chunk and
the non-null offsets use the persisted page text's Python string coordinate system. Citation
display metadata is resolved from the immutable source chain rather than accepted from a model.

An extracted Claim is a minimal factual proposition that can independently be supported,
refuted, or compared. It is distinct from the QA answer's `AnswerClaim`, which may contain
multiple facts. Its stable UUIDv5 ID includes the answer, source answer-claim, extraction/model
versions, fact kind, normalized text, and real evidence IDs. `ClaimEvidence` points to existing
validated answer-citation rows; model-side aliases are never persisted as evidence IDs.

## Frozen versus migratable

Frozen on Day 1:

- identity semantics;
- immutable document and processing versions;
- citation lineage;
- cache-version inputs;
- service boundaries.

Migratable later:

- display metadata;
- optional confidence fields;
- additional status or diagnostic columns;
- indexes and performance-oriented denormalization.
