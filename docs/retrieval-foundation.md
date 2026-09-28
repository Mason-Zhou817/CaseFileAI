# Retrieval foundation

## Data chain

The indexed lineage is:

```text
case → document → document_version → page → chunk → keyword/vector index
```

Each chunk persists `chunk_id`, `case_id`, `page_id`, zero-based `chunk_index`, exact
text, SHA-256 text hash, start/end page offsets, and `chunking_version`. Chunk IDs are
deterministic UUIDv5 values over the immutable page ID, versioned chunking inputs,
offsets and text hash. A chunk never crosses a page.

Default searches include only the latest version of each document. Chunks and vectors
for older document versions remain stored and resolvable by stable ID, preserving old
references after a newer document version is indexed.

## Chunking

Version: `page-char-v1-target500-overlap80-boundary100`.

- Target maximum: 500 Python characters.
- Boundary preference: search backward through the final 100 characters for newline,
  Chinese full stop, question mark, exclamation mark, or semicolon.
- Maximum overlap: 80 characters.
- Text is sliced directly from the page without normalization, so offsets satisfy
  `chunk.text == page.text[start_offset:end_offset]`.
- Empty pages create no chunks.

The four-page synthetic fixture is short enough to create one chunk per page. Its
average is 99.25 characters and actual overlap is therefore zero. Long-page automated
tests exercise multiple chunks and the configured overlap.

## Keyword retrieval

SQLite FTS5 stores a shadow search representation, while the original chunk text remains
authoritative. Tokenization version `jieba-bigram-date-amount-v1` uses:

- Jieba segmentation;
- Chinese bigrams;
- normalized date tokens, for example `date_2021_03_05`;
- normalized amount tokens, so `45万元` matches `450000元`.

Queries use safely quoted generated tokens and FTS5 `bm25()`. The public score is the
negated SQLite raw score so larger is better. The FTS row records `case_id` and the SQL
query also joins the source lineage and latest document version.

## Vector retrieval

The frozen local `BAAI/bge-small-zh-v1.5` provider creates normalized 512-dimensional
vectors. Each `(chunk_id, embedding_version)` is stored as a little-endian float32 SQLite
BLOB with its dimension. Search loads the case's current-version vectors into NumPy and
computes cosine similarity. This is appropriate for the MVP corpus size and does not
require a vector service.

## Hybrid retrieval

Hybrid mode retrieves up to `max(20, 4 × requested_limit)` candidates from each channel,
capped at 200, then applies Reciprocal Rank Fusion with `k=60`:

```text
RRF(chunk) = Σ 1 / (60 + channel_rank)
```

There is no query rewrite or reranker. Results report final rank and score plus channel
scores/ranks and the contributing retrieval sources.

## API

```text
POST /api/cases/{case_id}/index
GET  /api/cases/{case_id}/search?q=...&mode=bm25|vector|hybrid&limit=10
```

Indexing processes all unindexed page versions, while repeated runs reuse stable chunks
and existing embeddings. Search is case-scoped and returns `chunk_id`, `page_id`,
`document_id`, `document_version_id`, `page_number`, filename, exact chunk text, rank,
final score and per-channel ranking fields.

## Seed evaluation

The existing native and scanned PDF fixtures produce four chunks. Four relevant queries
cover person, exact date, normalized amount and a synonymous failed-payment expression;
one unrelated rental query is a negative example.

| Mode | Recall@1 | Recall@3 |
| --- | ---: | ---: |
| BM25 | 0.75 | 1.00 |
| Vector | 0.25 | 1.00 |
| Hybrid RRF | 1.00 | 1.00 |

BM25 returns no result for the unrelated query. Vector and hybrid modes still rank
candidates; retrieval alone does not decide whether evidence is sufficient. A later QA
stage must abstain when grounding is inadequate.

The original four-chunk local-model probe was an internal smoke test, not a representative legal retrieval benchmark. Its exploratory script and raw result are not included in this public source snapshot. For reproducible public evaluation, see the [MVP evaluation](evaluation/mvp.md) and its committed synthetic case set.

No Qwen call, generated answer, claim extraction or conflict detection is part of this
stage.
