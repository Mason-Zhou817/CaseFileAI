# MVP v1 downstream evaluation

This wholly synthetic set extends the question-level baseline into Claim, Conflict
Candidate, Contradiction, and Timeline. The original eight-document/30-question
baseline remains unchanged. The downstream case adds one three-page disputed
transaction PDF and uses a separate 16-question file.

Files: [case set](../../evals/case_sets/mvp_v1/downstream_case_set.json),
[questions](../../evals/case_sets/mvp_v1/downstream_questions.jsonl),
[gold](../../evals/case_sets/mvp_v1/downstream_gold.json), and
[baseline summary](baselines/mvp-v1-downstream-2026-09-25.json).

## Run

From the repository root, with the pinned BGE model already cached:

```powershell
uv run python evals/case_sets/mvp_v1/run_downstream_baseline.py --output .eval-results/mvp-v1-downstream-2026-09-25
```

The wrapper forces Hugging Face offline mode. It calls the same isolated runner
used by the question baseline, then calls the existing Claim extraction,
candidate generation, contradiction, and timeline application services before
the temporary database is removed. The output directory contains the usual
`results.jsonl` and `summary.json` plus `downstream_results.jsonl` and
`downstream_summary.json`. Only the compact downstream summary is committed.
No cloud API or real case material is used.

## Gold contract

`downstream_gold.json` has three arrays. Unknown references, duplicate keys,
invalid enum values, absent PDF pages, and missing source snippets fail before
the run starts.

- `claims`: `claim_key`, `question_id`, `category` (`amount`, `date`, `person`,
  `action`), `expected_text_any`, a single corresponding `normalized` value,
  and `source` (`doc_key`, one-based `page`, short `snippet`).
- `conflict_pairs`: `pair_key`, `claim_a`, `claim_b`, `expected_relation`
  (`contradiction`, `non_contradiction`, `uncertain`), `contradiction_type`, and
  `note`. The pair is unordered for matching.
- `timeline_events`: `event_key`, `claim_key`, `expected_date` in ISO year,
  year-month, or full-date form, and `date_precision` (`partial` or `exact`).

There are 16 expected claims: three amount, seven date, three person, and three
action. Eight annotated pairs include one clear contradiction in each of the
four types, plus a compatible or uncertain control for each type. Six timeline
events include five exact dates, one month-precision date, two events on the
same day, and sources in different PDFs.

## Matching and denominators

Claim fact matching uses the existing amount/date normalizers; person and
payment-action claims use the annotated identity or action value. It does not
require exact generated wording. Source document, page, and snippet are checked
against the Claim's **validated answer citation chain**, using document version
IDs. Source accuracy denominators contain only fact-matched claims. A fact may
therefore match while its source score fails.

`candidate.pair_recall` counts gold contradiction pairs whose **both** claims
matched their facts and sources. `candidate.claim_pairs_ready` gives that
denominator; an upstream Claim failure never masquerades as a candidate miss.
`contradiction.decision_accuracy_given_candidate` counts all annotated
relations whose pair entered the candidate set. A missing or wrong judge result
fails that check. `end_to_end_contradiction_recall` uses every gold
contradiction pair as its denominator, including upstream losses. Uncertain and
compatible controls are checked for decision accuracy but never counted as
contradictions found.

Timeline event recall uses every annotated event. Date and source accuracy count
only events that were emitted. Order accuracy compares pairs of emitted gold
events with different expected dates; same-day pairs are excluded because their
relative order has no evidentiary meaning. Every rate reports numerator,
denominator, and value; zero denominators have `value: null`.

Each downstream result records whether its source document was present,
parsed, chunked, retrieved, used as QA evidence, cited in a validated answer,
matched to a Claim, paired as a candidate, judged, or emitted in Timeline.
Stages irrelevant to that gold record are `null`. `first_failure_stage` is the
first failed applicable stage; a failed upstream stage propagates to dependent
pairs/events. The summary counts gold records by first failure stage. The
reported stage is a location for investigation, not proof of the root cause.

## Provider boundaries and interpretation

QA uses a gold-gated FakeLLM: it answers only when all annotated evidence is
present. Claim extraction uses a separate extractive FakeLLM that emits only
answer substrings linked to a matching quote. Both still pass through real QA
citation validation and Claim source/support validation and persistence. The
real candidate generator handles all four types. The real deterministic judge
handles amount/date; action/person use a small rule-based semantic provider,
after which the real contradiction service enforces its safeguards. The real
Timeline service builds and orders events.

The committed offline baseline scores 16/16 Claim facts and sources, 4/4
candidate recall, 8/8 decisions given candidate, 4/4 end-to-end contradiction
recall, and 6/6 Timeline events, with 14/14 comparable event pairs in order.
There are no observed stage losses in this controlled run. These full scores
do **not** estimate real model extraction or semantic judgment reliability.
The runner's local BGE and OCR paths are real; deterministic CI tests also use
an offline hash embedding so tests never download a model or call Qwen.

## Claim quality (4D)

The Claim extractor now requires a minimal self-contained fact, with category based
on its primary predicate. Ordered extraction from the same AnswerClaim may preserve
shared subjects across split facts; Coverage uses the same provenance positions.
The existing support call jointly checks support, atomicity, scope, and category.
It adds no call stage. QA citation validation and downstream decision rules are unchanged.
The deterministic Claim provider supplies scripted quality flags, so its baseline
does not measure a real model's semantic quality.

The fixed 14-sample real Qwen Claim observation, metric definitions, first-run outputs,
strict source discrepancies, and closing assessment are in the
[4D report](claim-quality-4d-2026-09-26.md). Automatic unknowns require review;
returned-Claim quality is reported alongside missing required facts. Direct source
inspection in that report is not independent human acceptance.
