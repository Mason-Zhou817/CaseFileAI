"""Real Windows lifecycle, source delivery and wheel isolation; synthetic data only."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from casefile_ai import __version__
from casefile_ai.application.services.model_preparation import prepare_local_models
from casefile_ai.config import Settings, configuration_file
from casefile_ai.interfaces.windows.launcher import Instance, LauncherError, WindowsLauncher
from casefile_ai.interfaces.windows.process_identity import process_identity

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows delivery requires Windows")
ROOT = Path(__file__).resolve().parents[2]
SECRET = "synthetic-config-secret-never-log-task4"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture
def launcher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for key in list(os.environ):
        if key.startswith("CASEFILE_"):
            monkeypatch.delenv(key)
    home = tmp_path / "用户 数据"
    home.mkdir()
    config = home / "config.env"
    config.write_text(
        f"CASEFILE_LLM_ENABLED=false\nCASEFILE_LLM_API_KEY={SECRET}\n", encoding="utf-8"
    )
    monkeypatch.setenv("CASEFILE_PRODUCT_MODE", "1")
    monkeypatch.setenv("CASEFILE_APP_HOME", str(home))
    monkeypatch.setenv("CASEFILE_ENV_FILE", str(config))
    monkeypatch.setenv("PYTHONUTF8", "1")
    service = WindowsLauncher(
        Settings(), port=free_port(), timeout=15, browser=Mock(return_value=True)
    )
    yield service
    item = service.read()
    if item is not None and service.alive(item) and service.identified(item):
        service.stop()


def test_product_config_and_paths_ignore_cwd_and_developer_env(launcher, monkeypatch, tmp_path):
    other = tmp_path / "无关 目录"
    other.mkdir()
    (other / ".env").write_text("CASEFILE_LLM_ENABLED=true", encoding="utf-8")
    monkeypatch.chdir(other)
    assert configuration_file() == launcher.settings.app_home / "config.env"
    assert not launcher.settings.llm_enabled
    assert launcher.settings.data_dir == launcher.settings.app_home / "data"
    assert not launcher.settings.data_dir.is_relative_to(ROOT)


def test_browser_is_opened_after_health_and_identity_and_repeated_start_reuses(launcher):
    calls = []

    def browser(url):
        item = launcher.read()
        assert item is not None and launcher.alive(item) and launcher.identified(item)
        calls.append(url)
        return True

    launcher.browser = browser
    url = launcher.start()
    item = launcher.read()
    assert item is not None
    assert launcher.start() == url
    assert launcher.start() == url
    assert launcher.read() == item
    assert calls == [url] * 3
    with httpx.Client(trust_env=False) as client:
        assert client.get(url + "health").json() == {"status": "ok"}
        assert client.get(url).status_code == 200
        assert client.get(url + "assets/workspace.js").status_code == 200
        assert client.get(url + "assets/app.css").status_code == 200
        public = client.get(url + "api/system/identity").json()
        assert set(public) == {"application", "version", "instance_id", "pid"}
        assert item.control_token not in json.dumps(public)


def test_concurrent_start_serializes_migration_and_creates_one_instance(launcher):
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: launcher.start(open_browser=False), range(2)))
    assert results[0] == results[1]
    assert launcher.read() is not None and launcher.identified(launcher.read())


def test_graceful_stop_preserves_data_restart_and_other_python(launcher):
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        url = launcher.start(open_browser=False)
        with httpx.Client(trust_env=False) as client:
            case = client.post(url + "api/cases", json={"name": "合成持久案件"}).json()
        before = launcher.read()
        launcher.stop()
        assert launcher.read() is None
        assert before is not None and not launcher.alive(before)
        assert other.poll() is None
        launcher.start(open_browser=False)
        assert launcher.read().instance_id != before.instance_id
        with httpx.Client(trust_env=False) as client:
            assert client.get(url + "api/cases").json()["cases"][0]["case_id"] == case["case_id"]
        assert other.poll() is None
    finally:
        other.terminate()
        other.wait(timeout=10)


@pytest.mark.parametrize("stale", ["dead", "pid_reuse", "malformed"])
def test_stale_runtime_recovers_without_killing_other_process(launcher, stale):
    launcher.runtime.mkdir(parents=True)
    if stale == "malformed":
        launcher.metadata.write_text("broken", encoding="utf-8")
    else:
        item = Instance(
            instance_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            pid=os.getpid(),
            process_created="incorrect-creation-time",
            executable=sys.executable,
            port=launcher.port,
            started_at="2026-09-27T00:00:00+00:00",
            control_token="x" * 43,
        )
        if stale == "dead":
            item.pid = 2147483647
        launcher.write(item)
    launcher.start(open_browser=False)
    assert launcher.read().pid != os.getpid()
    assert launcher.identified(launcher.read())


def test_abrupt_shutdown_stale_metadata_then_restart(launcher):
    launcher.start(open_browser=False)
    previous = launcher.read()
    assert previous is not None and launcher.alive(previous)
    # Simulates a crash of this test-owned server only; product Stop never uses kill.
    os.kill(previous.pid, signal.SIGTERM)
    deadline = time.monotonic() + 10
    while launcher.alive(previous) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not launcher.alive(previous)
    assert launcher.metadata.exists()
    launcher.start(open_browser=False)
    assert launcher.read().instance_id != previous.instance_id


def test_other_listener_is_not_treated_as_casefile_and_browser_stays_closed(launcher):
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", launcher.port))
        occupied.listen()
        with pytest.raises(LauncherError, match="port_in_use"):
            launcher.start()
        launcher.browser.assert_not_called()
        launcher.stop()
        assert occupied.getsockname()[1] == launcher.port


def test_pid_reuse_stop_does_not_send_shutdown_to_other_python(launcher, monkeypatch):
    observed = process_identity(os.getpid())
    assert observed is not None
    item = Instance(
        instance_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        pid=os.getpid(),
        process_created="wrong",
        executable=observed.executable,
        port=launcher.port,
        started_at="2026-09-27T00:00:00+00:00",
        control_token="x" * 43,
    )
    launcher.write(item)
    request = Mock(return_value=None)
    monkeypatch.setattr(launcher, "request", request)
    launcher.stop()
    assert all(call.args[0] == "GET" for call in request.call_args_list)
    assert process_identity(os.getpid()) == observed


def test_matching_pid_with_wrong_identity_is_not_stopped(launcher, monkeypatch):
    observed = process_identity(os.getpid())
    item = Instance(
        instance_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        pid=os.getpid(),
        process_created=observed.created,
        executable=observed.executable,
        port=launcher.port,
        started_at="2026-09-27T00:00:00+00:00",
        control_token="x" * 43,
    )
    launcher.write(item)
    request = Mock(return_value=None)
    monkeypatch.setattr(launcher, "request", request)
    with pytest.raises(LauncherError, match="identity_unverified"):
        launcher.stop()
    assert all(call.args[0] == "GET" for call in request.call_args_list)


def test_shutdown_requires_private_control_token_and_rejects_browser_origin(launcher):
    url = launcher.start(open_browser=False)
    item = launcher.read()
    with httpx.Client(trust_env=False) as client:
        endpoint = url + "api/system/shutdown"
        assert client.post(endpoint).status_code == 403
        assert client.post(endpoint, headers={"X-CaseFile-Control": "wrong"}).status_code == 403
        assert (
            client.post(
                endpoint,
                headers={"X-CaseFile-Control": item.control_token, "Origin": "http://127.0.0.1"},
            ).status_code
            == 403
        )
    assert launcher.alive(item)


def test_stop_timeout_never_force_kills(launcher, monkeypatch):
    launcher.start(open_browser=False)
    item = launcher.read()
    original = launcher.request
    monkeypatch.setattr(
        launcher,
        "request",
        lambda method, path, **options: (
            {"status": "stopping"} if method == "POST" else original(method, path, **options)
        ),
    )
    launcher.timeout = 0.1
    with pytest.raises(LauncherError, match="stop_timeout"):
        launcher.stop()
    assert launcher.alive(item)
    monkeypatch.setattr(launcher, "request", original)
    launcher.timeout = 15


def test_metadata_logs_no_config_secret_and_only_loopback(launcher):
    url = launcher.start(open_browser=False)
    item = launcher.read()
    assert url.startswith("http://127.0.0.1:")
    addresses = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            f"@(Get-NetTCPConnection -LocalPort {item.port} -State Listen).LocalAddress "
            "| ConvertTo-Json -Compress",
        ],
        capture_output=True,
        encoding="utf-8",
        timeout=15,
    )
    assert addresses.returncode == 0 and json.loads(addresses.stdout) == "127.0.0.1"
    metadata = launcher.metadata.read_text(encoding="utf-8")
    assert SECRET not in metadata
    assert set(json.loads(metadata)) == set(Instance.model_fields) - {"control_token"}
    assert item.control_token not in metadata
    launcher.stop()
    text = "".join(
        path.read_text(encoding="utf-8") for path in launcher.settings.log_dir.glob("*.log")
    )
    assert SECRET not in text and item.control_token not in text
    for line in (
        (launcher.settings.log_dir / "launcher.log").read_text(encoding="utf-8").splitlines()
    ):
        assert set(json.loads(line)) == {"time", "stage", "result", "pid", "port"}


def test_models_not_prepared_notice_and_failure_preserves_existing_data(
    launcher, monkeypatch, capsys
):
    marker = launcher.settings.app_home / "synthetic-existing-data"
    marker.write_bytes(b"preserve")
    monkeypatch.setattr("casefile_ai.interfaces.windows.launcher.embedding_cached", lambda _: False)
    monkeypatch.setattr(
        "casefile_ai.interfaces.windows.launcher.ocr_models_available", lambda: False
    )
    launcher.model_notice()
    assert "未准备" in capsys.readouterr().out
    monkeypatch.setattr(
        "casefile_ai.application.services.model_preparation.LocalSentenceTransformerEmbeddingProvider.embed_query",
        Mock(side_effect=OSError(SECRET)),
    )
    with pytest.raises(OSError):
        prepare_local_models(launcher.settings, print)
    assert marker.read_bytes() == b"preserve"


def run(arguments, *, cwd, env, timeout=120):
    result = subprocess.run(
        arguments,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def test_start_failure_does_not_open_browser_and_cleans_reservation(launcher, monkeypatch):
    monkeypatch.setattr(
        "casefile_ai.interfaces.windows.launcher.subprocess.Popen",
        Mock(side_effect=OSError(SECRET)),
    )
    with pytest.raises(LauncherError, match="start_failed"):
        launcher.start()
    launcher.browser.assert_not_called()
    assert not launcher.metadata.exists()
    assert not list(launcher.runtime.glob("control-*.token"))


def test_missing_dependencies_report_setup_without_launching(launcher, monkeypatch):
    monkeypatch.setattr(
        "casefile_ai.interfaces.windows.launcher.dependencies_available", lambda *_: False
    )
    with pytest.raises(LauncherError, match="environment_missing"):
        launcher.start()
    launcher.browser.assert_not_called()


def test_product_refuses_data_inside_installation(launcher, monkeypatch):
    monkeypatch.setenv("CASEFILE_INSTALL_DIR", str(launcher.settings.app_home))
    with pytest.raises(LauncherError, match="data_in_installation"):
        launcher.start()


def test_old_managed_version_requires_stop_not_second_server(launcher, monkeypatch):
    observed = process_identity(os.getpid())
    item = Instance(
        version="0.0.9",
        instance_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        pid=os.getpid(),
        process_created=observed.created,
        executable=observed.executable,
        port=launcher.port,
        started_at="2026-09-27T00:00:00+00:00",
        control_token="x" * 43,
    )
    launcher.write(item)
    monkeypatch.setattr(launcher, "identified", lambda _: True)
    with pytest.raises(LauncherError, match="old_instance"):
        launcher.start()
    launcher.browser.assert_not_called()
    launcher.cleanup(item.instance_id)


def test_model_cli_failure_returns_nonzero_safe_category_and_preserves_data(
    launcher, monkeypatch, capsys
):
    from casefile_ai.interfaces.windows.launcher import main

    marker = launcher.settings.app_home / "keep.txt"
    marker.write_bytes(b"keep")
    monkeypatch.setattr(sys, "argv", ["launcher", "prepare-models", "--port", str(launcher.port)])
    monkeypatch.setattr(
        "casefile_ai.interfaces.windows.launcher.prepare_local_models",
        Mock(side_effect=OSError(SECRET)),
    )
    assert main() == 1
    assert SECRET not in capsys.readouterr().out
    assert marker.read_bytes() == b"keep"
    log = (launcher.settings.log_dir / "launcher.log").read_text(encoding="utf-8")
    assert SECRET not in log and "prepare_failed" in log


def test_developer_configuration_remains_source_based(launcher, monkeypatch, tmp_path):
    monkeypatch.delenv("CASEFILE_PRODUCT_MODE")
    monkeypatch.delenv("CASEFILE_ENV_FILE")
    monkeypatch.chdir(tmp_path)
    assert configuration_file() == ROOT / ".env"


def test_relative_product_config_discovery_has_stable_base(launcher, monkeypatch, tmp_path):
    monkeypatch.setenv("CASEFILE_ENV_FILE", "自定义 配置.env")
    monkeypatch.chdir(tmp_path)
    assert configuration_file() == launcher.settings.app_home / "自定义 配置.env"
    monkeypatch.setenv("CASEFILE_APP_HOME", "relative-home")
    expected = Path(os.environ["LOCALAPPDATA"]) / "relative-home" / "自定义 配置.env"
    assert configuration_file() == expected


def test_missing_uv_setup_fails_with_official_instructions_and_safe_log(tmp_path):
    root = tmp_path / "缺 uv 程序"
    (root / "scripts").mkdir(parents=True)
    for name in ("windows.ps1", "windows-runtime.json"):
        shutil.copyfile(ROOT / "scripts" / name, root / "scripts" / name)
    env = {key: value for key, value in os.environ.items() if not key.startswith("CASEFILE_")}
    env.update(
        CASEFILE_APP_HOME=str(tmp_path / "用户"),
        USERPROFILE=str(tmp_path / "无工具用户"),
        PATH=str(Path(os.environ["SystemRoot"]) / "System32"),
        HF_TOKEN=SECRET,
    )
    result = subprocess.run(
        [
            shutil.which("powershell.exe"),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(root / "scripts/windows.ps1"),
            "-Action",
            "Setup",
            "-NoShortcut",
            "-Port",
            str(free_port()),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        encoding="utf-8",
        timeout=15,
    )
    assert result.returncode == 1
    assert "https://github.com/astral-sh/uv/releases/tag/0.11.21" in result.stdout
    assert "阶段：uv" in result.stdout
    assert SECRET not in result.stdout + result.stderr
    assert SECRET not in (tmp_path / "用户/logs/launcher.log").read_text(encoding="utf-8")


def test_source_delivery_whitelist_excludes_user_data_and_secrets(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "delivery_builder", ROOT / "scripts/build_windows_delivery.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    archive_path = module.build(tmp_path / "delivery.zip")
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        assert "CaseFileAI/Start CaseFile AI.cmd" in names
        assert not any(
            "/.env" in name or "/.runtime/" in name or "/data/" in name or name.endswith(".log")
            for name in names
        )
        manifest = json.loads(archive.read("CaseFileAI/delivery-manifest.json"))
        for item in manifest["files"]:
            assert (
                hashlib.sha256(archive.read("CaseFileAI/" + item["path"])).hexdigest()
                == item["sha256"]
            )


def test_windows_scripts_quote_paths_and_do_not_change_global_execution_policy():
    script = (ROOT / "scripts/windows.ps1").read_text(encoding="utf-8-sig")
    assert "Invoke-Expression" not in script and "Set-ExecutionPolicy" not in script
    assert "taskkill" not in script.lower()
    assert (ROOT / "scripts/windows.ps1").read_bytes().startswith(b"\xef\xbb\xbf")
    for action in ("Start", "Stop", "Setup", "Prepare Models"):
        wrapper = (ROOT / f"{action} CaseFile AI.cmd").read_text(encoding="utf-8")
        assert '"%~dp0scripts\\windows.ps1"' in wrapper
        assert "-ExecutionPolicy Bypass" in wrapper


@pytest.fixture(scope="module")
def delivery(tmp_path_factory):
    base = tmp_path_factory.mktemp("windows-isolation")
    installation = base / "案件分析 CaseFile AI"
    installation.mkdir()
    for name in ("src", "migrations"):
        shutil.copytree(
            ROOT / name, installation / name, ignore=shutil.ignore_patterns("__pycache__")
        )
    (installation / "scripts").mkdir()
    for name in (
        "pyproject.toml",
        "uv.lock",
        "README.md",
        "config.env.example",
        "Setup CaseFile AI.cmd",
        "Start CaseFile AI.cmd",
        "Stop CaseFile AI.cmd",
        "Prepare Models CaseFile AI.cmd",
        "scripts/windows.ps1",
        "scripts/windows-runtime.json",
    ):
        shutil.copyfile(ROOT / name, installation / name)
    unrelated = base / "非仓库 工作目录"
    unrelated.mkdir()
    home = base / "用户 数据 中文"
    env = {key: value for key, value in os.environ.items() if not key.startswith("CASEFILE_")}
    env.update(CASEFILE_APP_HOME=str(home), CASEFILE_NO_PAUSE="1", PYTHONUTF8="1")
    powershell = shutil.which("powershell.exe")
    script = installation / "scripts/windows.ps1"
    command = [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    run(command + ["-Action", "Setup", "-NoShortcut"], cwd=unrelated, env=env)
    return installation, home, unrelated, env, command


def test_setup_repeat_preserves_user_data_config_and_locked_dependencies(delivery):
    installation, home, unrelated, env, command = delivery
    marker = home / "data/synthetic-marker.txt"
    marker.write_bytes(b"keep across updates")
    config = home / "config.env"
    config.write_text(
        config.read_text(encoding="utf-8") + f"\nCASEFILE_LLM_API_KEY={SECRET}\n", encoding="utf-8"
    )
    digest = hashlib.sha256(config.read_bytes()).hexdigest()
    source = installation / "src/casefile_ai/__init__.py"
    source.write_text(
        source.read_text(encoding="utf-8") + "\n# DELIVERY_UPDATE_TEST\n", encoding="utf-8"
    )
    run(command + ["-Action", "Setup", "-NoShortcut"], cwd=unrelated, env=env)
    assert marker.read_bytes() == b"keep across updates"
    assert hashlib.sha256(config.read_bytes()).hexdigest() == digest
    assert not (installation / "data").exists()
    installed = installation / ".runtime/Lib/site-packages/casefile_ai/__init__.py"
    assert "DELIVERY_UPDATE_TEST" in installed.read_text(encoding="utf-8")
    assert SECRET not in (home / "logs/launcher.log").read_text(encoding="utf-8")
    python = installation / ".runtime/Scripts/python.exe"
    result = run(
        [
            str(python),
            "-I",
            "-c",
            "import sys,importlib.metadata as m; print(sys.version_info[:3]); "
            "print([m.version(p) for p in ['torch','rapidocr','sentence-transformers']])",
        ],
        cwd=unrelated,
        env=env,
    )
    assert "(3, 12, 13)" in result.stdout and "2.14.0" in result.stdout and "3.9.2" in result.stdout


def test_wheel_contains_resources_and_runs_cli_from_isolated_environment(delivery, tmp_path):
    installation, home, unrelated, env, _ = delivery
    uv = shutil.which("uv")
    run([uv, "build", "--wheel", "--out-dir", str(tmp_path)], cwd=ROOT, env=env)
    wheel = next(tmp_path.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert "casefile_ai/resources/migrations/env.py" in names
        assert (
            len(
                [name for name in names if "/migrations/versions/" in name and name.endswith(".py")]
            )
            == len(list((ROOT / "migrations/versions").glob("*.py")))
        )
        assert all(
            f"casefile_ai/interfaces/web/{name}" in names
            for name in ("index.html", "workspace.js", "app.css")
        )
    python = installation / ".runtime/Scripts/python.exe"
    run(
        [uv, "pip", "install", "--python", str(python), "--no-deps", "--reinstall", str(wheel)],
        cwd=unrelated,
        env=env,
    )
    port = free_port()
    serve_env = {
        **env,
        "CASEFILE_PRODUCT_MODE": "1",
        "CASEFILE_APP_HOME": str(tmp_path / "wheel 用户 数据"),
    }
    # The built wheel is installed into the clean setup venv, not the developer venv.
    server = subprocess.Popen(
        [str(installation / ".runtime/Scripts/casefile-ai.exe"), "serve", "--port", str(port)],
        cwd=unrelated,
        env=serve_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    identity = None
    try:
        with httpx.Client(trust_env=False, timeout=1) as client:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    identity = client.get(f"http://127.0.0.1:{port}/api/system/identity").json()
                    break
                except (httpx.HTTPError, ValueError):
                    time.sleep(0.2)
            assert identity and identity["application"] == "casefile-ai"
            base = f"http://127.0.0.1:{port}"
            assert client.get(base + "/health").json() == {"status": "ok"}
            assert client.get(base + "/").status_code == 200
            assert all(
                client.get(base + "/assets/" + name).status_code == 200
                for name in ("workspace.js", "app.css")
            )
            status = client.get(base + "/api/system/status").json()
            assert status["database"]["schema_status"] == "current"
            assert status["application"]["version"] == __version__
    finally:
        if identity:
            # Only the PID returned by this test's freshly spawned CLI is disposable.
            os.kill(identity["pid"], signal.SIGTERM)
        server.wait(timeout=10)


def test_actual_cmd_wrapper_start_stop_from_nonrepository_cwd(delivery):
    installation, home, unrelated, env, _ = delivery
    port = free_port()
    powershell = shutil.which("powershell.exe")

    def command(action):
        path = str(installation / f"{action} CaseFile AI.cmd").replace("'", "''")
        arguments = " -NoBrowser" + (f" -Port {port}" if action == "Start" else "")
        return [powershell, "-NoProfile", "-Command", f"& '{path}'{arguments}"]

    try:
        run(command("Start"), cwd=unrelated, env=env)
        with httpx.Client(trust_env=False) as client:
            assert client.get(f"http://127.0.0.1:{port}/health").json() == {"status": "ok"}
        assert (home / "runtime/instance.json").exists()
    finally:
        run(command("Stop"), cwd=unrelated, env=env)
    assert not (home / "runtime/instance.json").exists()
    events = [
        json.loads(line)
        for line in (home / "logs/launcher.log").read_text(encoding="utf-8").splitlines()
    ]
    assert any(
        event["stage"] == "stop" and event["result"] == "ok" and event["port"] == port
        for event in events
    )


def test_setup_relative_config_is_anchored_to_user_directory(delivery):
    _, home, unrelated, env, command = delivery
    custom_env = {**env, "CASEFILE_ENV_FILE": "自定义 配置.env"}
    run(command + ["-Action", "Setup", "-NoShortcut"], cwd=unrelated, env=custom_env)
    assert (home / "自定义 配置.env").is_file()
    assert not (unrelated / "自定义 配置.env").exists()
