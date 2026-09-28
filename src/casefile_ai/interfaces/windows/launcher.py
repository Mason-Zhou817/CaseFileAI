"""Local launcher with process-bound identity and authenticated graceful shutdown."""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import time
import webbrowser
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from casefile_ai import __version__
from casefile_ai.application.bootstrap import prepare_local_storage
from casefile_ai.application.services.model_preparation import prepare_local_models
from casefile_ai.application.services.system_status import (
    dependencies_available,
    embedding_cached,
    ocr_models_available,
)
from casefile_ai.config import Settings
from casefile_ai.infrastructure.db.migration import expected_revision
from casefile_ai.infrastructure.safe_logging import SafeLogging
from casefile_ai.interfaces.api.main import create_app
from casefile_ai.interfaces.windows.process_identity import process_identity

MESSAGES = {
    "environment_missing": "运行环境未准备，请先双击 Setup CaseFile AI.cmd。",
    "configuration_invalid": "用户配置无效，请检查 config.env；不会输出配置内容。",
    "data_in_installation": "数据、数据库或日志位于程序目录内，请改用独立用户目录。",
    "operation_busy": "另一个安装、启动或停止操作尚未完成，请稍后重试。",
    "port_in_use": "端口 {port} 已被其他程序占用。请停止占用程序后重试，不会自动切换端口。",
    "old_instance": "已有不同版本的 CaseFile AI 正在运行，请先用原入口停止，再重新 Setup。",
    "unmanaged_instance": "该实例不由当前用户目录管理，请使用它的原入口或终端停止；不会强杀进程。",
    "identity_unverified": "实例身份无法验证；未向进程发送停止命令，请查看系统状态或原终端。",
    "start_failed": "CaseFile AI 未能启动。请检查数据权限、数据库与应用安全日志。",
    "stop_timeout": "服务仍在处理请求，尚未退出；请等待后再次停止，不会默认强杀。",
    "prepare_failed": "本地模型准备失败，请检查网络、缓存权限和空间后重新运行模型准备。",
    "setup_failed": "应用自检失败，请检查用户目录权限、数据库和迁移资源。",
    "unexpected_failure": "操作失败，请查看安全日志并重试。",
}
LAUNCHER_MODULE = "casefile_ai.interfaces.windows.launcher"


class LauncherError(RuntimeError):
    pass


class Instance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    application: str = "casefile-ai"
    version: str = __version__
    instance_id: str = Field(pattern=r"^[a-f0-9-]{36}$")
    pid: int = Field(ge=0)
    process_created: str
    executable: str
    port: int = Field(ge=1024, le=65535)
    started_at: str
    control_token: str = Field(default="", exclude=True, repr=False)


def launcher_event(root: Path, stage: str, result: str, *, pid: int = 0, port: int = 8000) -> None:
    # Closed stage/result codes only; never serialize exceptions, settings or HTTP bodies.
    stages = {"start", "stop", "serve", "setup", "models", "browser", "stale"}
    results = {"ok", "reused", "not_running", "warning", *MESSAGES}
    event = {
        "time": datetime.now(UTC).isoformat(),
        "stage": stage if stage in stages else "start",
        "result": result if result in results else "unexpected_failure",
        "pid": pid,
        "port": port,
    }
    root.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        root / "launcher.log", maxBytes=2 * 1024**2, backupCount=3, encoding="utf-8"
    )
    try:
        record = logging.LogRecord(
            "launcher", logging.INFO, "", 0, json.dumps(event, ensure_ascii=False), (), None
        )
        handler.emit(record)
    finally:
        handler.close()


@contextmanager
def runtime_lock(runtime: Path, timeout: float = 90) -> Iterator[None]:
    import msvcrt

    runtime.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    stream = None
    while stream is None:
        try:
            stream = (runtime / "launcher.lock").open("a+b")
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            if stream is not None:
                stream.close()
                stream = None
            if time.monotonic() >= deadline:
                raise LauncherError("operation_busy") from None
            time.sleep(0.1)
    try:
        yield
    finally:
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        stream.close()


class WindowsLauncher:
    def __init__(
        self,
        settings: Settings,
        *,
        port: int = 8000,
        browser: Callable[[str], bool] = webbrowser.open,
        timeout: float = 90,
    ) -> None:
        self.settings = settings
        self.port = port
        self.browser = browser
        self.timeout = timeout
        self.runtime = settings.app_home / "runtime"
        self.metadata = self.runtime / "instance.json"

    def event(self, stage: str, result: str, pid: int = 0) -> None:
        try:
            launcher_event(self.settings.log_dir, stage, result, pid=pid, port=self.port)
        except OSError:
            print("启动器安全日志无法写入，请检查日志目录权限。")

    def read(self) -> Instance | None:
        try:
            if self.metadata.is_symlink() or self.metadata.stat().st_size > 8192:
                return None
            item = Instance.model_validate_json(self.metadata.read_bytes())
            private = self.runtime / ("control-" + item.instance_id + ".token")
            if private.is_file() and not private.is_symlink() and private.stat().st_size <= 128:
                item.control_token = private.read_text(encoding="ascii")
            return item
        except (OSError, ValueError):
            return None

    def write(self, item: Instance) -> None:
        self.runtime.mkdir(parents=True, exist_ok=True)
        if item.control_token:
            private = self.runtime / ("control-" + item.instance_id + ".token")
            private.write_text(item.control_token, encoding="ascii")
        temporary = self.runtime / (item.instance_id + ".tmp")
        temporary.write_text(item.model_dump_json(), encoding="utf-8")
        temporary.replace(self.metadata)

    def cleanup(self, instance_id: str) -> None:
        item = self.read()
        if item is not None and item.instance_id == instance_id:
            self.metadata.unlink(missing_ok=True)
            (self.runtime / ("control-" + instance_id + ".token")).unlink(missing_ok=True)

    @staticmethod
    def alive(item: Instance) -> bool:
        observed = process_identity(item.pid)
        return observed is not None and (
            observed.created == item.process_created and observed.executable == item.executable
        )

    def request(self, method: str, path: str, *, item: Instance | None = None) -> Any:
        port = item.port if item is not None else self.port
        headers = {"X-CaseFile-Control": item.control_token} if method == "POST" and item else {}
        try:
            with httpx.Client(timeout=1, trust_env=False) as client:
                response = client.request(method, f"http://127.0.0.1:{port}{path}", headers=headers)
                if response.status_code == 200:
                    return response.json()
        except (httpx.HTTPError, ValueError):
            pass
        return None

    def identified(self, item: Instance) -> bool:
        identity = self.request("GET", "/api/system/identity", item=item)
        return (
            isinstance(identity, dict)
            and identity
            == {
                "application": "casefile-ai",
                "version": item.version,
                "instance_id": item.instance_id,
                "pid": item.pid,
            }
            and self.request("GET", "/health", item=item) == {"status": "ok"}
        )

    def occupied(self) -> bool:
        with socket.socket() as probe:
            probe.settimeout(0.5)
            return probe.connect_ex(("127.0.0.1", self.port)) == 0

    def open_browser(self, port: int) -> str:
        url = f"http://127.0.0.1:{port}/"
        try:
            opened = self.browser(url)
        except (OSError, webbrowser.Error):
            opened = False
        if not opened:
            self.event("browser", "warning")
            print("默认浏览器未能打开，请手动访问：" + url)
        else:
            self.event("browser", "ok")
        return url

    def preflight(self) -> None:
        if sys.version_info[:2] != (3, 12) or not dependencies_available(
            "rapidocr", "onnxruntime", "sentence_transformers", "torch"
        ):
            raise LauncherError("environment_missing")
        installation = os.environ.get("CASEFILE_INSTALL_DIR")
        if installation:
            root = Path(installation).resolve()
            for path in (
                self.settings.app_home,
                self.settings.data_dir,
                self.settings.database_path,
                self.settings.log_dir,
            ):
                if path is not None and path.is_relative_to(root):
                    raise LauncherError("data_in_installation")
        if expected_revision(self.settings) is None:
            raise LauncherError("environment_missing")

    def start(self, *, open_browser: bool = True) -> str:
        self.preflight()
        with runtime_lock(self.runtime):
            item = self.read()
            if item is not None and self.alive(item):
                self.port = item.port
                if not self.identified(item):
                    raise LauncherError("identity_unverified")
                if item.version != __version__:
                    raise LauncherError("old_instance")
                self.event("start", "reused", item.pid)
                return (
                    self.open_browser(item.port)
                    if open_browser
                    else f"http://127.0.0.1:{item.port}/"
                )
            if self.metadata.exists():
                if item is not None:
                    self.cleanup(item.instance_id)
                else:
                    self.metadata.unlink()
                self.event("stale", "ok")
            if self.occupied():
                identity = self.request("GET", "/api/system/identity")
                if isinstance(identity, dict) and identity.get("application") == "casefile-ai":
                    if identity.get("version") != __version__:
                        raise LauncherError("old_instance")
                    if self.request("GET", "/health") == {"status": "ok"}:
                        print("发现其他入口启动的实例；关闭页面不会停服务，停止时请使用它的原入口。")
                        self.event("start", "reused")
                        return (
                            self.open_browser(self.port)
                            if open_browser
                            else f"http://127.0.0.1:{self.port}/"
                        )
                raise LauncherError("port_in_use")
            item = Instance(
                instance_id=str(uuid4()),
                pid=0,
                process_created="",
                executable="",
                port=self.port,
                started_at=datetime.now(UTC).isoformat(),
                control_token=secrets.token_urlsafe(32),
            )
            self.write(item)
            environment = os.environ.copy()
            # Credentials stay in the inherited environment/config, never on the command line.
            for name in ("PYTHONPATH", "PYTHONHOME"):
                environment.pop(name, None)
            try:
                subprocess.Popen(
                    [
                        sys.executable,
                        "-I",
                        "-X",
                        "utf8",
                        "-m",
                        LAUNCHER_MODULE,
                        "serve",
                        "--instance-id",
                        item.instance_id,
                    ],
                    cwd=self.settings.app_home,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
                )
            except OSError:
                self.cleanup(item.instance_id)
                raise LauncherError("start_failed") from None
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                running = self.read()
                if running is None:
                    raise LauncherError("start_failed")
                if running.instance_id != item.instance_id:
                    raise LauncherError("identity_unverified")
                if running.pid and not self.alive(running):
                    self.cleanup(running.instance_id)
                    raise LauncherError("start_failed")
                if running.pid and self.identified(running):
                    self.event("start", "ok", running.pid)
                    print("CaseFile AI 已启动。关闭浏览器不会停服务，请使用 Stop CaseFile AI.cmd。")
                    self.model_notice()
                    return (
                        self.open_browser(running.port)
                        if open_browser
                        else f"http://127.0.0.1:{running.port}/"
                    )
                time.sleep(0.2)
            # An unverified process is never killed, even on startup timeout.
            raise LauncherError("start_failed")

    def stop(self) -> None:
        with runtime_lock(self.runtime):
            item = self.read()
            if item is None or not self.alive(item):
                if item is not None:
                    self.cleanup(item.instance_id)
                else:
                    self.metadata.unlink(missing_ok=True)
                identity = self.request("GET", "/api/system/identity")
                if isinstance(identity, dict) and identity.get("application") == "casefile-ai":
                    raise LauncherError("unmanaged_instance")
                self.event("stop", "not_running")
                print("没有当前入口管理的运行实例；未停止其他程序。")
                return
            self.port = item.port
            if not self.identified(item):
                raise LauncherError("identity_unverified")
            if self.request("POST", "/api/system/shutdown", item=item) != {"status": "stopping"}:
                raise LauncherError("identity_unverified")
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                if not self.alive(item):
                    self.cleanup(item.instance_id)
                    self.event("stop", "ok", item.pid)
                    print("CaseFile AI 已正常停止，案件数据已保留。")
                    return
                time.sleep(0.2)
            raise LauncherError("stop_timeout")

    def model_notice(self) -> None:
        print(
            "本地向量模型："
            + (
                "已准备"
                if embedding_cached(self.settings)
                else "未准备，请运行 Prepare Models CaseFile AI.cmd"
            )
        )
        print(
            "OCR 模型："
            + (
                "已准备"
                if ocr_models_available()
                else "未准备，请运行 Prepare Models CaseFile AI.cmd"
            )
        )

    def setup_check(self) -> None:
        self.preflight()
        item = self.read()
        if item is not None and self.alive(item):
            raise LauncherError("operation_busy")
        try:
            prepare_local_storage(self.settings)
        except Exception:
            raise LauncherError("setup_failed") from None
        self.model_notice()
        self.event("setup", "ok")
        print("应用自检通过。无需云模型凭据；首次分析前请准备本地模型。")

    def serve(self, instance_id: str) -> None:
        import uvicorn

        item = self.read()
        observed = process_identity(os.getpid())
        if item is None or item.instance_id != instance_id or item.pid != 0 or observed is None:
            raise LauncherError("identity_unverified")
        item.pid, item.process_created, item.executable = (
            os.getpid(),
            observed.created,
            observed.executable,
        )
        self.write(item)
        logs = SafeLogging(self.settings.log_dir, self.settings.log_level, console=False)
        try:
            prepare_local_storage(self.settings)
            application = create_app(
                settings=self.settings, log_manager=logs, instance_id=instance_id
            )
            server = uvicorn.Server(
                uvicorn.Config(
                    application, host="127.0.0.1", port=item.port, access_log=False, log_config=None
                )
            )

            @application.post("/api/system/shutdown", include_in_schema=False)
            async def shutdown(request: Request) -> JSONResponse:
                if (
                    request.client is None
                    or request.client.host != "127.0.0.1"
                    or request.headers.get("origin") is not None
                    or not hmac.compare_digest(
                        request.headers.get("x-casefile-control", ""), item.control_token
                    )
                ):
                    return JSONResponse({"detail": "无权执行本地停止操作。"}, status_code=403)
                server.should_exit = True
                return JSONResponse({"status": "stopping"})

            server.run()
        finally:
            logs.close()
            self.cleanup(instance_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="CaseFile AI Windows local launcher")
    parser.add_argument(
        "action", choices=["start", "stop", "serve", "setup-check", "prepare-models"]
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--instance-id", default="")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    os.environ["CASEFILE_PRODUCT_MODE"] = "1"
    try:
        settings = Settings()
    except Exception:
        print(MESSAGES["configuration_invalid"])
        return 1
    launcher = WindowsLauncher(settings, port=args.port)
    try:
        if not 1024 <= args.port <= 65535:
            raise LauncherError("configuration_invalid")
        if args.action == "start":
            launcher.start(open_browser=not args.no_browser)
        elif args.action == "stop":
            launcher.stop()
        elif args.action == "serve":
            launcher.serve(args.instance_id)
        elif args.action == "setup-check":
            with closing(SafeLogging(settings.log_dir, settings.log_level, console=False)):
                launcher.setup_check()
        else:
            with (
                runtime_lock(launcher.runtime),
                closing(SafeLogging(settings.log_dir, settings.log_level, console=False)),
            ):
                launcher.preflight()
                try:
                    prepare_local_models(settings, print)
                except Exception:
                    raise LauncherError("prepare_failed") from None
                launcher.event("models", "ok")
        return 0
    except Exception as error:
        category = (
            str(error)
            if isinstance(error, LauncherError) and str(error) in MESSAGES
            else "unexpected_failure"
        )
        stage = {"setup-check": "setup", "prepare-models": "models"}.get(args.action, args.action)
        launcher.event(stage, category)
        print(MESSAGES[category].format(port=launcher.port))
        print(
            f"端口：{launcher.port}；日志目录：{settings.log_dir}；"
            "诊断入口：Start 的系统状态面板。"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
