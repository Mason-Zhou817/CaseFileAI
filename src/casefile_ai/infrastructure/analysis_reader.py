"""Read every persisted analysis scope from one database snapshot, without an LLM."""

from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from sqlalchemy import Engine, select

from casefile_ai import __version__
from casefile_ai.application.data_contracts import AnalysisExport, ExportDocument
from casefile_ai.application.services.case_lifecycle import CaseLifecycleService
from casefile_ai.application.services.claim_coverage import DefaultClaimCoverageService
from casefile_ai.application.services.conflict_candidates import DefaultConflictCandidateService
from casefile_ai.application.services.feedback import FeedbackService
from casefile_ai.application.services.timeline import DefaultTimelineService
from casefile_ai.infrastructure.data_snapshot import snapshot_database
from casefile_ai.infrastructure.db.claims import SqlAlchemyClaimRepository
from casefile_ai.infrastructure.db.contradictions import SqlAlchemyContradictionRepository
from casefile_ai.infrastructure.db.documents import SqlAlchemyDocumentRepository
from casefile_ai.infrastructure.db.feedback import SqlAlchemyFeedbackRepository
from casefile_ai.infrastructure.db.models import AnswerModel, ClaimModel, PageModel, QuestionModel
from casefile_ai.infrastructure.db.qa import SqlAlchemyQARepository
from casefile_ai.infrastructure.db.repositories import SqlAlchemyCaseRepository
from casefile_ai.infrastructure.db.session import build_engine, build_session_factory, session_scope

PRIVACY_NOTICE = "导出的分析报告可能包含卷宗原文片段和个人/商业信息，请妥善保存。"


class SqliteAnalysisReader:
    def __init__(self, engine: Engine, staging_root: Path) -> None:
        self._engine = engine
        self._root = staging_root

    def read(self, case_id: str) -> AnalysisExport:
        self._root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=self._root) as temp:
            database = Path(temp) / "analysis.db"
            snapshot_database(self._engine, database)
            engine = build_engine(f"sqlite:///{database.as_posix()}")
            try:
                return self._read(engine, case_id)
            finally:
                engine.dispose()

    @staticmethod
    def _read(engine: Engine, case_id: str) -> AnalysisExport:
        factory = build_session_factory(engine)
        case = CaseLifecycleService(SqlAlchemyCaseRepository(factory)).get_case(case_id)
        documents_repo = SqlAlchemyDocumentRepository(factory)
        answers_repo = SqlAlchemyQARepository(factory)
        claims_repo = SqlAlchemyClaimRepository(factory)
        contradictions_repo = SqlAlchemyContradictionRepository(factory)
        documents: list[ExportDocument] = []
        versions_by_id: dict[str, tuple[int, int]] = {}
        for latest in documents_repo.list_documents(case_id):
            versions = documents_repo.versions(latest.document_id)
            for version in versions:
                versions_by_id[version.document_version_id] = (
                    version.version_number,
                    latest.version_number,
                )
            with session_scope(factory) as session:
                pages = [
                    dict(
                        page_id=page.page_id,
                        document_version_id=page.document_version_id,
                        page_number=page.page_number,
                        ocr_used=page.ocr_used,
                        text_sha256=page.text_hash,
                    )
                    for page in session.scalars(
                        select(PageModel)
                        .where(
                            PageModel.document_version_id.in_(
                                [v.document_version_id for v in versions]
                            )
                        )
                        .order_by(PageModel.document_version_id, PageModel.page_number)
                    )
                ]
            documents.append(
                ExportDocument(
                    document_id=latest.document_id,
                    filename=latest.filename,
                    current_version=latest.version_number,
                    versions=versions,
                    pages=pages,
                )
            )

        def cite(value: dict[str, Any]) -> dict[str, Any]:
            number, latest_number = versions_by_id[value["document_version_id"]]
            return {
                **value,
                "version_number": number,
                "current_version": latest_number,
                "is_current_version": number == latest_number,
            }

        answers = list(answers_repo.list_case_answers(case_id))
        answer_by_id = {answer.answer_id: answer for answer in answers}
        answer_exports = []
        for answer in answers:
            value = asdict(answer)
            value["status"] = "insufficient_evidence" if answer.abstained else "answered"
            value["citations"] = [cite(asdict(c)) for c in answer.citations]
            answer_exports.append(value)
        with session_scope(factory) as session:
            scopes = sorted(
                set(
                    session.execute(
                        select(ClaimModel.extraction_version, ClaimModel.model_version)
                        .join(AnswerModel, AnswerModel.answer_id == ClaimModel.answer_id)
                        .join(QuestionModel, QuestionModel.question_id == AnswerModel.question_id)
                        .where(QuestionModel.case_id == case_id)
                    ).all()
                )
            )
        claims: list[dict[str, Any]] = []
        coverage: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        timeline: list[dict[str, Any]] = []
        for extraction_version, model_version in scopes:
            scope_claims = claims_repo.list_case_claims(case_id, extraction_version, model_version)
            for item in scope_claims:
                value = asdict(item.claim)
                answer = answer_by_id[item.claim.answer_id]
                value["sources"] = [
                    cite(asdict(c))
                    for c in answer.citations
                    if c.evidence_id in item.claim.evidence_ids
                    and c.claim_id == item.claim.source_answer_claim_id
                ]
                claims.append(value)
            for answer_id in sorted({item.claim.answer_id for item in scope_claims}):
                result = DefaultClaimCoverageService(answers_repo, claims_repo).evaluate(
                    answer_id, extraction_version, model_version
                )
                value = asdict(result)
                for claim in value["claims"]:
                    claim["evidence"] = [cite(c) for c in claim["evidence"]]
                coverage.append(value)
            for candidate in DefaultConflictCandidateService(answers_repo, claims_repo).generate(
                case_id, extraction_version, model_version
            ):
                value = {**asdict(candidate), "candidate_id": candidate.candidate_id}
                for side in ("left", "right"):
                    value[side + "_evidence"] = [cite(c) for c in value[side + "_evidence"]]
                candidates.append(value)
            for event in DefaultTimelineService(
                answers_repo, claims_repo, contradictions_repo
            ).build(case_id, extraction_version, model_version):
                value = asdict(event)
                value["evidence"] = [cite(c) for c in value["evidence"]]
                timeline.append(value)
        contradictions = []
        for conflict_result in contradictions_repo.list_case_results(case_id):
            value = asdict(conflict_result)
            for side in ("left", "right"):
                value[side + "_evidence"] = [cite(c) for c in value[side + "_evidence"]]
            contradictions.append(value)
        return AnalysisExport(
            metadata={
                "export_version": 1,
                "generated_at": datetime.now(UTC).isoformat(),
                "application_version": __version__,
                "scope": "all_persisted_analysis_versions",
                "derived_sections": ["coverage", "candidates", "timeline"],
            },
            case=case,
            documents=documents,
            answers=answer_exports,
            claims=claims,
            coverage=coverage,
            candidates=candidates,
            contradictions=contradictions,
            timeline=timeline,
            feedback_summary=FeedbackService(
                SqlAlchemyFeedbackRepository(factory)
            ).get_feedback_summary(case_id),
            notices=[
                PRIVACY_NOTICE,
                "这是 CaseFile AI 当前已保存分析结果，不代表系统已穷尽卷宗所有事实。",
                "没有检测结果不等于材料不存在矛盾。临时语义失败未保存，不进入导出。",
                "Coverage、候选和时间线由已有 service 从已保存结果计算；不重新调用模型。",
            ],
        )
