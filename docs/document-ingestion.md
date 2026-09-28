# Document ingestion and page persistence

The persisted chain is `cases → documents → document_versions → pages`.
All IDs are stable UUIDs. A page belongs to one immutable document version; its
one-based number corresponds to the original PDF. UTF-8 page text has its own SHA-256.

## HTTP API

| Method | Path | Behavior |
| --- | --- | --- |
| POST | `/api/cases/{case_id}/documents` | Multipart `file`; optional `document_id` updates a known document |
| GET | `/api/cases/{case_id}/documents` | Latest version of each document |
| GET | `/api/documents/{document_id}` | Document identity, latest version and full version history |
| GET | `/api/documents/{document_id}/pages/{page_number}` | Page text and all provenance IDs |
| GET | `/api/documents/{document_id}/original` | Download the preserved original PDF |

Page and original endpoints accept `document_version_id` as a query parameter.
Omitting it selects the latest version. Persist the returned version ID and page ID
for citations; a latest-version URL alone is not an immutable citation.

Page responses contain `case_id`, `document_id`, `document_version_id`, `page_id`,
`page_number`, `filename`, `text`, `text_hash`, and `ocr_used`.
All HTTP business operations delegate to `DefaultIngestionService`; existing
`import_document(case_id, path)` and `get_document_page(document_id, page_number)`
remain available for the frozen CLI/MCP contracts. Extended methods expose versions.

## Content and versions

- SHA-256 is computed while copying the upload in 1 MiB blocks.
- With no `document_id`, identical content already in this case returns the existing
  document/version with `duplicate=true`, regardless of filename. Different content
  creates a new document even if filenames match.
- With `document_id`, different content creates the next version of that document;
  an already imported hash returns its historical version without changing latest.
- A document from another case cannot be used as the update target.
- Deduplication is case-scoped, not global across cases.
- Each version's original is stored as `data/originals/{document_version_id}.pdf`.
  User filenames never determine storage paths. Source files are not changed.
- SQLite serializes the duplicate check and version insertion. Version and page
  rows commit together. Normal parser/database failures clean up this import's files.
  A forced process termination between file creation and commit can leave an orphan
  file; automatic crash-orphan reconciliation is not implemented in this milestone.

## PDF and OCR selection

PyMuPDF extracts native text per page. At least ten alphanumeric characters (including
Chinese characters) selects native extraction. Whitespace, punctuation and replacement
glyphs do not count. Pages below this threshold are rasterized for local RapidOCR.
If OCR returns no text, any short native text is retained. `ocr_used` indicates that
OCR was invoked, not that its output is necessarily accurate.

This threshold is an MVP heuristic. A page with enough native text plus a scanned region
does not receive additional OCR for the scanned region. Character-level highlighting,
mixed-region extraction and OCR confidence review are outside this stage.

Import currently accepts unencrypted PDFs up to 50 MiB and 500 pages. The size limit
is enforced in the ingestion service after multipart spooling; it is not an HTTP
request-body firewall. Invalid inputs return 422; missing cases/documents/versions/pages
return 404. Model APIs and embeddings are not used.

## Migration and verification

Migration `20260923_0002` adds documents, document_versions and pages with foreign keys,
unique version/hash and version/page constraints. Existing cases are preserved.

```powershell
uv run alembic upgrade head
uv run pytest -q
```

The real two-page native fixture preserves both page numbers and expected Chinese
facts without OCR. The two-page scanned bank fixture invokes OCR and recovers the
amount and payment-status evidence. Integration tests cover original-byte equality,
page hashes, version history, application restart, duplicate/concurrent imports,
cross-case isolation, rejected inputs, parser/database rollback, migration downgrade,
and ORM/migration schema agreement.

No chunking, RAG, claim extraction or conflict detection is implemented here.
