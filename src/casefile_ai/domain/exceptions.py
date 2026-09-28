"""Typed domain/application errors mapped consistently by interfaces."""


class CaseFileError(Exception):
    """Base error for expected application failures."""


class InvalidCaseError(CaseFileError):
    """Raised when case input violates an application invariant."""


class CaseNotFoundError(CaseFileError):
    def __init__(self, case_id: str) -> None:
        self.case_id = case_id
        super().__init__(f"case not found: {case_id}")
