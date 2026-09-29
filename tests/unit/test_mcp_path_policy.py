"""MCP import allowlist boundaries independent of PDF parsing."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from casefile_ai.application.document_contracts import InvalidDocumentError
from casefile_ai.interfaces.mcp.server import allowed_pdf, import_roots_from_environment


def test_import_disabled_without_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CASEFILE_MCP_IMPORT_ROOTS", raising=False)
    assert import_roots_from_environment() == ()
    with pytest.raises(PermissionError):
        allowed_pdf(str(tmp_path / "sample.pdf"), ())


def test_import_rejects_traversal_unsupported_and_oversized(tmp_path: Path) -> None:
    root = tmp_path / "imports"
    root.mkdir()
    valid = root / "sample.pdf"
    valid.write_bytes(b"%PDF")
    assert allowed_pdf(str(valid), (root,)) == valid
    with pytest.raises(PermissionError):
        allowed_pdf(str(root / ".." / "imports" / "sample.pdf"), (root,))
    unsupported = root / "secret.env"
    unsupported.write_text("API_KEY=fixture-secret")
    with pytest.raises(InvalidDocumentError):
        allowed_pdf(str(unsupported), (root,))
    large = root / "large.pdf"
    with large.open("wb") as stream:
        stream.truncate(50 * 1024 * 1024 + 1)
    with pytest.raises(InvalidDocumentError):
        allowed_pdf(str(large), (root,))
    with pytest.raises(PermissionError):
        allowed_pdf(r"\\server\share\sample.pdf", (root,))


def test_symlink_cannot_escape_root(tmp_path: Path) -> None:
    root = tmp_path / "imports"
    root.mkdir()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF")
    link = root / "linked.pdf"
    try:
        os.symlink(outside, link)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc.__class__.__name__}")
    with pytest.raises(PermissionError):
        allowed_pdf(str(link), (root,))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction test")
def test_junction_cannot_escape_root(tmp_path: Path) -> None:
    root = tmp_path / "imports"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sample.pdf").write_bytes(b"%PDF")
    junction = root / "linked"
    try:
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        pytest.skip(f"junction creation unavailable: {exc.returncode}")
    try:
        with pytest.raises(PermissionError):
            allowed_pdf(str(junction / "sample.pdf"), (root,))
    finally:
        junction.rmdir()
