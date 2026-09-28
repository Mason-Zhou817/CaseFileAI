"""Build the source + locked-environment Windows delivery without user data."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tomllib
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(destination: Path | None = None) -> Path:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    destination = destination or ROOT / "dist" / f"CaseFileAI-{version}-windows.zip"
    paths = [
        ROOT / name
        for name in (
            "pyproject.toml",
            "uv.lock",
            "README.md",
            "THIRD_PARTY_NOTICES.md",
            "config.env.example",
            "Setup CaseFile AI.cmd",
            "Start CaseFile AI.cmd",
            "Stop CaseFile AI.cmd",
            "Prepare Models CaseFile AI.cmd",
            "scripts/windows.ps1",
            "scripts/windows-runtime.json",
            "docs/windows-installation.md",
            "docs/user-guide-zh.md",
            "docs/stage10-task5-acceptance.md",
            "docs/development.md",
            "docs/requirements.md",
            "docs/architecture.md",
            "docs/data-model.md",
            "docs/service-contracts.md",
            "docs/grounded-qa.md",
            "docs/claim-coverage-candidates.md",
            "docs/contradictions-timeline.md",
            "docs/stage10-task2-acceptance.md",
            "docs/data-management.md",
            "docs/stage10-task4-acceptance.md",
            "docs/assets/github/01-material-index.png",
            "docs/assets/github/02-grounded-qa.png",
            "docs/assets/github/03-claim-coverage.png",
            "docs/assets/github/04-contradiction.png",
            "docs/assets/github/05-timeline.png",
            "docs/assets/github/06-backup-export.png",
        )
    ]
    for directory in (ROOT / "src/casefile_ai", ROOT / "migrations"):
        paths.extend(
            path
            for path in directory.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix in {".py", ".html", ".js", ".css", ".mako"}
        )
    selected = {path.relative_to(ROOT).as_posix(): path for path in paths}
    for name in ("RELEASE_NOTES.md", "docs/stage10-task6-acceptance.md", "LICENSE", "NOTICE"):
        if (ROOT / name).is_file():
            selected[name] = ROOT / name
    wheel = ROOT / "dist" / f"casefile_ai-{version}-py3-none-any.whl"
    if wheel.is_file():
        selected["app/" + wheel.name] = wheel
    entries = sorted(selected.items())
    manifest = {
        "application_version": version,
        "built_at": datetime.now(UTC).isoformat(),
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "runtime": json.loads((ROOT / "scripts/windows-runtime.json").read_text(encoding="utf-8")),
        "files": [
            {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for name, path in entries
        ],
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, path in entries:
                archive.write(path, "CaseFileAI/" + name)
            archive.writestr("CaseFileAI/delivery-manifest.json", json.dumps(manifest, indent=2))
        with zipfile.ZipFile(temporary) as archive:
            assert archive.testzip() is None
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


if __name__ == "__main__":
    print(build())
