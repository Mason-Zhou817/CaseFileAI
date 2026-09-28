"""One finite QA alignment observation on pinned synthetic inputs, explicit cloud opt-in."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from run_final_smoke import (
    HERE,
    ObservedProvider,
    ObservedTransport,
    provider_summary,
    select_questions,
    write_json,
)

from casefile_ai.config import Settings
from casefile_ai.evaluation.runner import (
    EvaluationContext,
    EvaluationInputError,
    _percentile,
    run_evaluation,
)
from casefile_ai.infrastructure.llm.local_embeddings import (
    LocalSentenceTransformerEmbeddingProvider,
)
from casefile_ai.infrastructure.llm.openai_compatible import OpenAICompatibleLLMProvider

# Fixed before execution, independent of observed outcomes. No automatic rerun.
SUBSET = ("s19", "s27", "s01", "s02", "s06", "s09", "s13", "s16", "s17")


class UsageTransport(ObservedTransport):
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = super().handle_request(request)
        # Existing application provider does not expose usage. Observe numeric fields
        # only at the transport boundary; never store headers or raw response bodies.
        try:
            usage = response.json().get("usage", {})
            self.calls[-1]["tokens"] = {
                key: value for key, value in usage.items()
                if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
                and isinstance(value, int) and not isinstance(value, bool)
            }
        except (ValueError, AttributeError):
            self.calls[-1]["tokens"] = {}
        return response


class ContextProvider(ObservedProvider):
    def generate_structured(self, *, system_prompt: str, user_prompt: str,
                            response_model: type[Any]) -> Any:
        payload = json.loads(user_prompt)
        # Only preflight-pinned synthetic evidence can reach this observer.
        with (self.output / "contexts.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"schema": response_model.__name__, "input": payload},
                                    ensure_ascii=False) + "\n")
        return super().generate_structured(system_prompt=system_prompt, user_prompt=user_prompt,
                                           response_model=response_model)


def observation_summary(rows: list[dict[str, Any]], calls: list[dict[str, Any]],
                        http_calls: list[dict[str, Any]]) -> dict[str, Any]:
    alignment = [call for call in calls if call["schema"] == "ModelAnswerAlignment"]
    rejected = [row["id"] for row in rows
                if row["validation_status"] == "alignment_validation_failed"]
    positive = [row for row in rows if row["expected_status"] == "answered"]
    negative = [row for row in rows if row["expected_status"] == "insufficient_evidence"]
    latencies = [row["latency_ms"]["qa_ms"] for row in rows
                 if row["latency_ms"]["qa_ms"] is not None]
    by_id = {}
    for row in rows:
        observed = [call for call in calls if call["scope"] == row["id"]]
        by_id[row["id"]] = {
            "structured_calls": len(observed),
            "http_calls": sum(call["scope"] == row["id"] for call in http_calls),
            "by_schema": dict(Counter(call["schema"] for call in observed)),
            "alignment": [call["output"] for call in observed
                          if call["schema"] == "ModelAnswerAlignment"],
            "qa_ms": row["latency_ms"]["qa_ms"],
        }
    token_fields = ("prompt_tokens", "completion_tokens", "total_tokens")
    return {
        "total": len(rows), "positive": len(positive), "negative": len(negative),
        "answered": sum(row["actual_status"] == "answered" for row in rows),
        "correct_abstain_ids": [row["id"] for row in negative
                                if row["actual_status"] == "insufficient_evidence"],
        "false_answer_ids": [row["id"] for row in negative if row["actual_status"] == "answered"],
        "positive_abstain_ids": [row["id"] for row in positive
                                 if row["actual_status"] == "insufficient_evidence"],
        "validation_failure_ids": [row["id"] for row in rows
                                   if row["failure_stage"] == "validation"],
        "alignment_rejected_ids": rejected,
        "alignment_calls": len(alignment),
        "mean_added_structured_alignment_calls": len(alignment) / len(rows) if rows else None,
        "by_question": by_id,
        "qa_latency_ms": {"p50": _percentile(latencies, .5), "p95": _percentile(latencies, .95)},
        "tokens": {key: sum(call.get("tokens", {}).get(key, 0) for call in http_calls)
                   for key in token_fields},
        "token_observed_http_calls": sum(bool(call.get("tokens")) for call in http_calls),
        "cost": None,
        "cost_note": "provider returns token usage but no charge/pricing; no invented cost",
        "review_status": "pending direct review of requested facts and selected sources",
    }


def run(output: Path) -> dict[str, Any]:
    # Reuse the existing hash, path, cloud boundary and question hash preflight.
    available = {question.id: question for question in select_questions()}
    selected = [available[key] for key in SUBSET]
    settings = Settings()
    if not all((settings.llm_base_url, settings.llm_api_key, settings.llm_model)):
        raise EvaluationInputError("Qwen URL, key and model must be configured")
    if "qwen" not in (settings.llm_model or "").lower():
        raise EvaluationInputError("configured model is not Qwen")
    if not settings.embedding_model:
        raise EvaluationInputError("local embedding model must be configured")
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise EvaluationInputError("use an empty output directory to preserve first observations")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    embeddings = LocalSentenceTransformerEmbeddingProvider(
        model=settings.embedding_model, revision=settings.embedding_revision,
        device=settings.embedding_device, query_instruction=settings.embedding_query_instruction,
    )
    transport = UsageTransport(httpx.HTTPTransport(retries=0))
    rows: list[dict[str, Any]] = []
    with httpx.Client(timeout=60.0, headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                      transport=transport) as client:
        llm = ContextProvider(OpenAICompatibleLLMProvider(
            base_url=settings.llm_base_url or "", api_key=settings.llm_api_key or "",
            model=settings.llm_model or "", client=client,
            structured_output_mode=settings.llm_structured_output_mode,
            max_validation_attempts=settings.llm_max_validation_attempts,
        ), transport, selected, output)

        def after_qa(context: EvaluationContext) -> None:
            rows.extend(context.rows)
            write_json(output / "http_calls.json", transport.calls)

        with tempfile.TemporaryDirectory(prefix="casefile-alignment-smoke-") as directory:
            questions = Path(directory) / "questions.jsonl"
            questions.write_text(
                "".join(question.model_dump_json() + "\n" for question in selected),
                encoding="utf-8",
            )
            summary = run_evaluation(HERE / "real_model_case_set.json", questions,
                                     output / "runner", llm=llm, embeddings=embeddings,
                                     settings=settings, on_complete=after_qa)
        summary.update(
            run_date=datetime.now(UTC).date().isoformat(), subset=SUBSET,
            model=settings.llm_model, structured_output_mode=settings.llm_structured_output_mode,
            max_validation_attempts=settings.llm_max_validation_attempts,
            synthetic_pinned_materials=True, database_isolation="fresh temporary case/database",
            run_policy="one cold application run per predefined question; no agent rerun",
            provider=provider_summary(transport.calls),
            alignment_regression=observation_summary(rows, llm.outputs, transport.calls),
        )
        write_json(output / "summary.json", summary)
        (output / "results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n"
                                                     for row in rows), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-model", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = run(args.output)
    print(json.dumps({"provider": summary["provider"],
                      "alignment": summary["alignment_regression"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
