"""Application composition helpers kept outside interface adapters."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine

from casefile_ai.application.services.case_lifecycle import CaseLifecycleService
from casefile_ai.application.services.cases import DefaultCaseService
from casefile_ai.application.services.claim_coverage import DefaultClaimCoverageService
from casefile_ai.application.services.claims import DefaultClaimExtractionService
from casefile_ai.application.services.conflict_candidates import DefaultConflictCandidateService
from casefile_ai.application.services.contradictions import DefaultContradictionService
from casefile_ai.application.services.data_management import DataManagementService
from casefile_ai.application.services.documents import DefaultIngestionService
from casefile_ai.application.services.feedback import FeedbackService
from casefile_ai.application.services.materials import MaterialsService
from casefile_ai.application.services.qa import DefaultQAService
from casefile_ai.application.services.retrieval import DefaultRetrievalService
from casefile_ai.application.services.system_status import SystemStatusService, directory_status
from casefile_ai.application.services.timeline import DefaultTimelineService
from casefile_ai.application.services.workspace import CaseWorkspaceService
from casefile_ai.config import Settings
from casefile_ai.infrastructure.analysis_reader import SqliteAnalysisReader
from casefile_ai.infrastructure.backup_storage import SqliteBackupStorage
from casefile_ai.infrastructure.db.claims import SqlAlchemyClaimRepository
from casefile_ai.infrastructure.db.contradictions import SqlAlchemyContradictionRepository
from casefile_ai.infrastructure.db.documents import SqlAlchemyDocumentRepository
from casefile_ai.infrastructure.db.feedback import SqlAlchemyFeedbackRepository
from casefile_ai.infrastructure.db.materials import SqlAlchemyMaterialRepository
from casefile_ai.infrastructure.db.migration import expected_revision, migrate_database
from casefile_ai.infrastructure.db.qa import SqlAlchemyQARepository
from casefile_ai.infrastructure.db.repositories import SqlAlchemyCaseRepository
from casefile_ai.infrastructure.db.session import build_engine, build_session_factory
from casefile_ai.infrastructure.llm.disabled import DisabledLLMProvider
from casefile_ai.infrastructure.llm.errors import ProviderError
from casefile_ai.infrastructure.llm.local_embeddings import (
    LocalSentenceTransformerEmbeddingProvider,
)
from casefile_ai.infrastructure.llm.openai_compatible import OpenAICompatibleLLMProvider
from casefile_ai.infrastructure.retrieval.repository import SqliteChunkRepository
from casefile_ai.infrastructure.safe_logging import SafeLogging, safe_event


@dataclass(frozen=True, slots=True)
class CaseApplication:
    service: DefaultCaseService
    engine: Engine


def build_case_application(database_url: str) -> CaseApplication:
    engine = build_engine(database_url)
    repository = SqlAlchemyCaseRepository(build_session_factory(engine))
    return CaseApplication(service=DefaultCaseService(repository), engine=engine)


def prepare_local_storage(settings: Settings) -> None:
    settings.ensure_local_directories()
    if not directory_status(settings.data_dir).writable:
        raise OSError("data_directory_unwritable")
    migrate_database(settings)


@dataclass(slots=True)
class ApplicationRuntime:
    settings: Settings
    cases: CaseApplication
    ingestion: DefaultIngestionService
    retrieval: DefaultRetrievalService
    qa: DefaultQAService
    workspace: CaseWorkspaceService
    embeddings: LocalSentenceTransformerEmbeddingProvider
    llm: OpenAICompatibleLLMProvider | DisabledLLMProvider
    diagnostics: SystemStatusService
    materials: MaterialsService
    lifecycle: CaseLifecycleService
    data_management: DataManagementService
    feedback: FeedbackService
    _closed: bool = False

    def require_model(self) -> None:
        if not self.settings.llm_configured:
            raise ProviderError("model_not_configured")
        if not self.settings.llm_enabled:
            raise ProviderError("model_disabled")

    def require_semantic_model(self, case_id: str) -> None:
        if any(
            item.left_claim.kind in {"action", "person"}
            for item in self.workspace.candidates(case_id)
        ):
            self.require_model()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for close_resource in (self.llm.close, self.embeddings.close, self.cases.engine.dispose):
            try:
                close_resource()
            except Exception:
                safe_event("shutdown", "failed", level=40)


def build_application_runtime(settings: Settings, logs: SafeLogging) -> ApplicationRuntime:
    """Production composition available to every adapter, with no factual rules."""
    cases = build_case_application(settings.database_url)
    llm: OpenAICompatibleLLMProvider | DisabledLLMProvider | None = None
    embeddings: LocalSentenceTransformerEmbeddingProvider | None = None
    try:
        factory = build_session_factory(cases.engine)
        lifecycle = CaseLifecycleService(SqlAlchemyCaseRepository(factory))
        management_root = settings.app_home / "data-management"
        data_management = DataManagementService(
            management_root,
            SqliteBackupStorage(settings, cases.engine),
            SqliteAnalysisReader(cases.engine, management_root / "export-staging"),
            expected_revision(settings) or "",
        )
        ingestion = DefaultIngestionService(
            SqlAlchemyDocumentRepository(factory), settings.data_dir
        )
        embeddings = LocalSentenceTransformerEmbeddingProvider(
            model=settings.embedding_model,
            revision=settings.embedding_revision,
            device=settings.embedding_device,
            query_instruction=settings.embedding_query_instruction,
        )
        materials = MaterialsService(
            SqlAlchemyMaterialRepository(factory), embeddings.model_version
        )
        retrieval = DefaultRetrievalService(
            SqliteChunkRepository(factory), embeddings, index_monitor=materials.monitor
        )
        if settings.llm_configured and settings.llm_enabled:
            llm = OpenAICompatibleLLMProvider(
                base_url=settings.llm_base_url or "",
                api_key=settings.llm_api_key or "",
                model=settings.llm_model or "",
                structured_output_mode=settings.llm_structured_output_mode,
                max_validation_attempts=settings.llm_max_validation_attempts,
                timeout_seconds=settings.llm_timeout_seconds,
            )
        else:
            llm = DisabledLLMProvider(configured=settings.llm_configured, model=settings.llm_model)
        answers = SqlAlchemyQARepository(factory)
        claims = SqlAlchemyClaimRepository(factory)
        results = SqlAlchemyContradictionRepository(factory)
        candidates = DefaultConflictCandidateService(answers, claims)
        qa = DefaultQAService(answers, retrieval, llm)
        workspace = CaseWorkspaceService(
            cases.service,
            answers,
            claims,
            results,
            DefaultClaimExtractionService(answers, claims, llm),
            DefaultClaimCoverageService(answers, claims),
            candidates,
            DefaultContradictionService(candidates, results, llm),
            DefaultTimelineService(answers, claims, results),
            llm.model_version,
        )
        diagnostics = SystemStatusService(
            settings,
            cases.engine,
            embeddings=embeddings,
            provider=llm if isinstance(llm, OpenAICompatibleLLMProvider) else None,
            logs=logs,
        )
        return ApplicationRuntime(
            settings,
            cases,
            ingestion,
            retrieval,
            qa,
            workspace,
            embeddings,
            llm,
            diagnostics,
            materials,
            lifecycle,
            data_management,
            FeedbackService(SqlAlchemyFeedbackRepository(factory)),
        )
    except BaseException:
        if llm is not None:
            llm.close()
        if embeddings is not None:
            embeddings.close()
        cases.engine.dispose()
        raise
