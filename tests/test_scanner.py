"""Tests for the static scanner — no LLM, no network required."""

import tempfile
from pathlib import Path

from code_atlas.config import AtlasConfig
from code_atlas.scanner import ProjectScanner, _sha256, _dep_name


def _make_project(tmp_path: Path) -> Path:
    root = tmp_path / "myproject"
    root.mkdir()
    (root / "README.md").write_text("# My Project\nDoes something useful.")
    (root / "main.py").write_text(
        "import os\n\ndef hello():\n    pass\n\nclass Greeter:\n    pass\n"
    )
    (root / "pyproject.toml").write_bytes(
        b'[project]\nname = "myproject"\ndependencies = ["httpx>=0.27", "typer"]\n'
    )
    (root / ".git").mkdir()  # fake git dir — scanner won't crash
    return root


def test_scanner_collects_files(tmp_path):
    root = _make_project(tmp_path)
    cfg = AtlasConfig(projects=[str(root)])
    scanner = ProjectScanner(cfg)
    snapshot = scanner.scan(root)

    paths = [r.relative_path for r in snapshot.files]
    assert "main.py" in paths


def test_scanner_ignores_git_dir(tmp_path):
    root = _make_project(tmp_path)
    cfg = AtlasConfig(projects=[str(root)])
    scanner = ProjectScanner(cfg)
    snapshot = scanner.scan(root)

    for rec in snapshot.files:
        assert ".git" not in rec.relative_path


def test_scanner_reads_readme(tmp_path):
    root = _make_project(tmp_path)
    cfg = AtlasConfig(projects=[str(root)])
    snapshot = ProjectScanner(cfg).scan(root)
    assert "My Project" in snapshot.readme_text


def test_scanner_reads_pyproject_deps(tmp_path):
    root = _make_project(tmp_path)
    cfg = AtlasConfig(projects=[str(root)])
    snapshot = ProjectScanner(cfg).scan(root)
    deps = snapshot.dependencies.get("pyproject.toml", [])
    assert "httpx" in deps
    assert "typer" in deps


def test_sha256_stable(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("hello")
    h1 = _sha256(f)
    h2 = _sha256(f)
    assert h1 == h2
    assert len(h1) == 64


def test_dep_name_strips_version():
    assert _dep_name("httpx>=0.27") == "httpx"
    assert _dep_name("typer[all]>=0.12") == "typer"
    assert _dep_name("requests") == "requests"
