"""Run the dependency-free JS behavioral suite as part of normal project checks."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_frontend_behavior_suite() -> None:
    executable = shutil.which("node")
    if executable is None:
        pytest.skip("Node is required only for the lightweight GUI behavior checks")
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [executable, "--test", str(root / "tests/frontend/workspace.test.cjs")],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
