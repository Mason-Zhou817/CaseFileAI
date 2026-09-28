"""Run actual Windows scripts/HTTP in isolated Chinese paths using synthetic PDFs."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import tempfile
import tomllib
import zipfile
from pathlib import Path
from uuid import uuid4

import httpx
import pymupdf

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    base = Path(tempfile.gettempdir()) / ("CaseFileAI 任务4 " + str(uuid4()))
    installation = base / "案件分析 CaseFile AI"
    unrelated = base / "非仓库 工作目录"
    home = base / "用户 数据 中文"
    unrelated.mkdir(parents=True)
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    with zipfile.ZipFile(ROOT / f"dist/CaseFileAI-{version}-windows.zip") as archive:
        archive.extractall(base / "release")  # Only our locally built, allowlisted delivery.
    source_directory = base / "release/CaseFileAI"
    assert source_directory.resolve().is_relative_to(base.resolve())
    assert installation.resolve().is_relative_to(base.resolve())
    source_directory.rename(installation)
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("CASEFILE_")
    }
    environment.update(CASEFILE_APP_HOME=str(home), CASEFILE_NO_PAUSE="1", PYTHONUTF8="1")
    url = "http://127.0.0.1:8000/"

    def operation(action: str, *, browser: bool = False, expected: int = 0) -> str:
        name = {"Models": "Prepare Models"}.get(action, action)
        wrapper = installation / (name + " CaseFile AI.cmd")
        command = "& '" + str(wrapper).replace("'", "''") + "' -NoShortcut"
        if not browser:
            command += " -NoBrowser"
        arguments = [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            command,
        ]
        print("实际 Windows：" + action, flush=True)
        result = subprocess.run(
            arguments,
            cwd=unrelated,
            env=environment,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=240,
        )
        assert result.returncode == expected, result.stdout + result.stderr
        return result.stdout

    def record() -> dict:
        return json.loads((home / "runtime/instance.json").read_text(encoding="utf-8"))

    def check_stopped() -> None:
        assert not (home / "runtime/instance.json").exists()
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", 8000)) != 0

    operation("Setup")
    operation("Models")
    metadata = None
    try:
        operation("Start", browser=True)
        metadata = record()
        assert "control_token" not in metadata
        with httpx.Client(trust_env=False, timeout=120) as client:
            assert client.get(url + "health").json() == {"status": "ok"}
            assert client.get(url).status_code == 200
            status = client.get(url + "api/system/status").json()
            assert not status["qa_provider"]["enabled"] and not status["qa_provider"]["configured"]
            assert status["embedding"]["model_cached"] and status["ocr"]["model_available"]
            case = client.post(url + "api/cases", json={"name": "合成 Windows 持久案件"}).json()
            operation("Start", browser=True)
            operation("Start", browser=True)
            assert record() == metadata
            operation("Stop")
            check_stopped()
            marker = home / "data/keep-through-update.txt"
            marker.write_bytes(b"synthetic-preserved-data")
            configuration_hash = hashlib.sha256((home / "config.env").read_bytes()).hexdigest()
            operation("Setup")
            assert marker.read_bytes() == b"synthetic-preserved-data"
            assert (
                hashlib.sha256((home / "config.env").read_bytes()).hexdigest() == configuration_hash
            )
            operation("Start")
            cases = client.get(url + "api/cases").json()["cases"]
            assert cases[0]["case_id"] == case["case_id"]
            pdf = base / "合成收款材料.pdf"
            with pymupdf.open() as document:  # type: ignore[no-untyped-call]
                page = document.new_page()
                page.insert_text(
                    (50, 80),
                    "合成收款凭证：2026年9月27日收到工程款500000元。",
                    fontname="china-s",
                    fontsize=14,
                )
                document.save(pdf)
            with pdf.open("rb") as source:
                response = client.post(
                    url + f"api/cases/{case['case_id']}/documents",
                    files={"file": (pdf.name, source, "application/pdf")},
                )
            assert response.status_code == 200, response.text
            document_result = response.json()
            response = client.post(url + f"api/cases/{case['case_id']}/index")
            assert response.status_code == 200, response.text
            hits = client.get(
                url + f"api/cases/{case['case_id']}/search",
                params={"q": "收到工程款", "mode": "hybrid"},
            ).json()
            assert hits
            operation("Stop")
            check_stopped()
            operation("Start")
            documents = client.get(url + f"api/cases/{case['case_id']}/documents").json()
            assert documents[0]["document_id"] == document_result["document_id"]
            assert (
                client.get(url + f"api/cases/{case['case_id']}/materials").json()["documents"][0][
                    "status"
                ]
                == "ready"
            )
            operation("Stop")
            check_stopped()
            stale = dict(metadata, pid=2147483647, process_created="0", executable="stale")
            (home / "runtime/instance.json").write_text(json.dumps(stale), encoding="utf-8")
            operation("Start")
            assert record()["instance_id"] != stale["instance_id"]
            operation("Stop")
            check_stopped()
            with socket.socket() as other:
                other.bind(("127.0.0.1", 8000))
                other.listen()
                text = operation("Start", expected=1)
                assert "端口 8000 已被其他程序占用" in text
                assert not (home / "runtime/instance.json").exists()
            operation("Start")
            assert client.get(url + "health").status_code == 200
            operation("Stop")
            check_stopped()
        logs = [
            json.loads(line)
            for line in (home / "logs/launcher.log").read_text(encoding="utf-8").splitlines()
        ]
        assert any(event["stage"] == "browser" and event["result"] == "ok" for event in logs)
        # Read only matching window presence, never unrelated window titles/content.
        window = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "@(Get-Process | Where-Object { $_.MainWindowTitle -match 'CaseFile AI' }).Count",
            ],
            capture_output=True,
            encoding="utf-8",
            timeout=15,
        )
        result = {
            "outcome": "passed",
            "installation": str(installation),
            "user_data": str(home),
            "url": url,
            "browser_dispatch_accepted": True,
            "casefile_browser_window_seen": int(window.stdout.strip() or "0") > 0,
            "case_id": case["case_id"],
            "document_id": document_result["document_id"],
            "isolation": "current Windows; fresh venv; Chinese/space paths; "
            "not another physical machine",
            "checked": [
                "setup",
                "models",
                "ready-before-browser",
                "repeat-start",
                "stop",
                "update-preservation",
                "restart",
                "native-pdf-import",
                "real-bge-index",
                "hybrid-search",
                "material-preservation",
                "stale-metadata",
                "port8000-conflict",
            ],
        }
        report = base / "result.json"
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(report, flush=True)
    finally:
        if (home / "runtime/instance.json").exists():
            operation("Stop")


if __name__ == "__main__":
    main()
