# ADR 003: Chinese keyword retrieval with an FTS5 shadow column

- Status: accepted
- Date: 2026-09-22

## Context

SQLite FTS5 `unicode61` handles Unicode character classes but does not provide Chinese word segmentation. Raw continuous Chinese sentences failed the Day 1 probe for isolated person-name, amount, and date queries.

## Decision

Keep FTS5 and its `bm25()` scorer, but index a deterministic shadow string containing:

- Jieba word segmentation;
- Chinese character bigrams for recall;
- normalized date tokens such as `date_2021_03_05`;
- normalized amount tokens such as `amount_500000`.

The original chunk text remains authoritative for display and citation. Generated query tokens are quoted before being passed to `MATCH`.

## Consequences

- No custom SQLite extension or external keyword-search service is required for MVP.
- The tokenization version must participate in index invalidation and cache keys.
- Bigram expansion increases index size and may reduce precision; vector retrieval and later fusion/reranking must compensate.
