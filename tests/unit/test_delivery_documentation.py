"""Check user instructions against live contracts and the distributed document graph."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import tomllib
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from casefile_ai.application.services.documents import MAX_PAGES, MAX_PDF_BYTES
from casefile_ai.application.services.materials import LABELS
from casefile_ai.config import Settings, configuration_file

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs/user-guide-zh.md"
EDITED_DOCS = (
    ROOT / "README.md",
    GUIDE,
    ROOT / "docs/development.md",
    ROOT / "docs/windows-installation.md",
    ROOT / "docs/data-management.md",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def local_links(text: str) -> list[tuple[str, str]]:
    # This document set uses ordinary inline links, not reference-style Markdown.
    prose = re.sub(r"```[^\n]*\n.*?```", "", text, flags=re.S)
    links = []
    for target in re.findall(r"\[[^\]]+\]\(([^)\s]+)\)", prose):
        parts = urlsplit(target)
        if not parts.scheme and not parts.netloc:
            links.append((unquote(parts.path), unquote(parts.fragment)))
    return links


def anchors(text: str) -> set[str]:
    explicit = set(re.findall(r'<a id="([^"]+)">', text))
    for title in re.findall(r"^#{1,6} (.+)$", text, flags=re.M):
        title = re.sub(r"[^\w\s-]", "", title.lower()).strip().replace(" ", "-")
        explicit.add(title)
    return explicit


def test_manual_entrypoints_versions_and_import_limits_match_implementation() -> None:
    guide = read(GUIDE)
    runtime = json.loads(read(ROOT / "scripts/windows-runtime.json"))
    version = tomllib.loads(read(ROOT / "pyproject.toml"))["project"]["version"]
    assert f"CaseFileAI-{version}-windows.zip" in guide
    assert f"**{version}**" in guide
    assert runtime["python"] in guide and runtime["uv"] in guide
    mentioned = set(re.findall(r"(?:Setup|Start|Stop|Prepare Models)[\w ]*\.cmd", guide))
    assert mentioned == {
        "Setup CaseFile AI.cmd",
        "Start CaseFile AI.cmd",
        "Stop CaseFile AI.cmd",
        "Prepare Models CaseFile AI.cmd",
    }
    assert all((ROOT / name).is_file() for name in mentioned)
    assert f"**{MAX_PDF_BYTES // (1024 * 1024)} MiB**" in guide
    assert f"**1–{MAX_PAGES} 页**" in guide


def test_documented_configuration_names_are_real_settings() -> None:
    supported = {"CASEFILE_" + name.upper() for name in Settings.model_fields}
    entry_options = {
        "CASEFILE_ENV_FILE": read(ROOT / "src/casefile_ai/config.py"),
        "CASEFILE_NO_PAUSE": read(ROOT / "Setup CaseFile AI.cmd"),
    }
    assert all(name in source for name, source in entry_options.items())
    supported.update(entry_options)
    for path in EDITED_DOCS:
        names = set(re.findall(r"\bCASEFILE_[A-Z_]+\b", read(path)))
        assert names <= supported, f"Unknown configuration in {path.name}: {names - supported}"
    guide = read(GUIDE)
    for field in ("ENABLED", "BASE_URL", "MODEL", "API_KEY"):
        assert f"CASEFILE_LLM_{field}=" in guide
    assert "CASEFILE_LLM_ENABLED=true" in guide
    assert "CASEFILE_LLM_ENABLED=false" in guide


def test_windows_default_paths_and_opt_in_match_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in tuple(os.environ):
        if name.startswith("CASEFILE_"):
            monkeypatch.delenv(name)
    monkeypatch.setattr("casefile_ai.config.sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setenv("CASEFILE_PRODUCT_MODE", "1")
    settings = Settings(_env_file=None)
    home = tmp_path / "Local/CaseFileAI"
    assert settings.app_home == home
    assert settings.data_dir == home / "data"
    assert settings.log_dir == home / "logs"
    assert settings.database_path == home / "data/casefile-ai.db"
    assert configuration_file() == home / "config.env"
    assert not settings.llm_enabled and not settings.llm_configured
    guide = read(GUIDE)
    for suffix in (
        "config.env",
        "data",
        "data\\casefile-ai.db",
        "data\\originals",
        "logs",
        "runtime",
        "data-management",
    ):
        assert "%LOCALAPPDATA%\\CaseFileAI\\" + suffix in guide
    assert not home.exists(), "Documentation checks must not create user storage"


def test_readme_and_manual_links_and_all_chapter_anchors_resolve() -> None:
    assert GUIDE.is_file()
    assert "docs/user-guide-zh.md" in read(ROOT / "README.md")
    guide = read(GUIDE)
    assert {f"guide{number:02}" for number in range(1, 34)} <= anchors(guide)
    headings = re.findall(r"^## (\d+)\. ", guide, flags=re.M)
    assert headings == [str(number) for number in range(1, 34)]
    for path in EDITED_DOCS:
        for relative, fragment in local_links(read(path)):
            target = (path.parent / relative).resolve() if relative else path
            assert target.is_file(), f"Broken local link in {path.name}: {relative}"
            if fragment:
                assert fragment in anchors(read(target)), f"Broken anchor: {path.name}#{fragment}"


def test_instructions_use_current_gui_labels_and_document_visible_limits() -> None:
    guide = read(GUIDE)
    ui = read(ROOT / "src/casefile_ai/interfaces/web/index.html") + read(
        ROOT / "src/casefile_ai/interfaces/web/workspace.js"
    )
    for label in (
        "材料与索引",
        "提问与引用",
        "事实与覆盖检查",
        "候选与矛盾",
        "时间线",
        "创建案件",
        "保存名称",
        "归档案件",
        "显示已归档案件",
        "恢复归档",
        "添加新版本",
        "查看历史版本",
        "抽取事实",
        "查看覆盖检查",
        "运行四类矛盾检测",
        "创建完整备份",
        "确认创建新副本",
        "刷新系统状态",
    ):
        assert label in guide and label in ui, f"GUI label no longer matches: {label}"
    assert all(label in guide for label in LABELS.values())
    assert "v1（非当前版本）" in guide
    assert "系统状态" in guide and "技术详情" in guide
    assert "Windows 产品入口实际应改 `config.env`" in guide


def test_manual_keeps_local_cloud_and_analysis_boundaries_explicit() -> None:
    guide = read(GUIDE)
    privacy = guide.split("## 29. 隐私与云模型边界", 1)[1].split('<a id="guide30">', 1)[0]
    for operation in ("OCR", "BGE", "Coverage", "Timeline", "QA", "Claim", "对齐", "语义"):
        assert operation in privacy
    for boundary in (
        "系统不会自动脱敏",
        "真实敏感卷宗不得发送到公共模型 API",
        "Coverage 不代表完整案件覆盖",
        "当前没有检测到矛盾 ≠ 案件不存在矛盾",
        "引用合法 ≠ 法律结论正确",
        "分析导出 ≠ 完整备份",
        "备份没有加密",
        "当前版本不支持 GUI 永久删除",
        "仅填写凭据 ≠ 自动启用",
    ):
        assert boundary in guide, f"Missing product boundary: {boundary}"
    assert "所有内容永不离开电脑" not in privacy


def test_document_examples_contain_only_placeholder_credentials() -> None:
    for path in EDITED_DOCS:
        content = read(path)
        assignments = re.findall(r"^\s*#?\s*CASEFILE_LLM_API_KEY\s*=\s*(.*)$", content, re.M)
        assert all(value.strip() in {"", "YOUR_API_KEY"} for value in assignments), path.name
        assert not re.search(r"\bsk-[A-Za-z0-9_-]{16,}\b", content), path.name
    assert "https://MODEL_ENDPOINT.invalid/v1" in read(GUIDE)


def test_windows_archive_contains_manual_and_resolvable_document_graph(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "documentation_delivery", ROOT / "scripts/build_windows_delivery.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.build(tmp_path / "documentation.zip")
    with zipfile.ZipFile(result) as archive:
        names = set(archive.namelist())
        assert "CaseFileAI/docs/user-guide-zh.md" in names
        manifest = json.loads(archive.read("CaseFileAI/delivery-manifest.json"))
        for item in manifest["files"]:
            assert (
                hashlib.sha256(archive.read("CaseFileAI/" + item["path"])).hexdigest()
                == item["sha256"]
            )
        for name in sorted(names):
            if not name.endswith(".md"):
                continue
            content = archive.read(name).decode("utf-8-sig")
            for relative, fragment in local_links(content):
                target = (
                    os.path.normpath(str(Path(name).parent / relative)).replace("\\", "/")
                    if relative
                    else name
                )
                assert target in names, f"Missing delivery link: {name} -> {target}"
                if fragment:
                    assert fragment in anchors(archive.read(target).decode("utf-8-sig"))
        assert not any(Path(name).name in {".env", "config.env"} for name in names)
