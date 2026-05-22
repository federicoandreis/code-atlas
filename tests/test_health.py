"""Tests for atlas health checks."""

import hashlib
from datetime import datetime
from pathlib import Path

import yaml
from typer.testing import CliRunner

from code_atlas.cli import app
from code_atlas.models import FileRecord, ProjectSnapshot, ProjectSummary
from code_atlas.store import AtlasStore

runner = CliRunner()


def _setup(tmp_path: Path) -> tuple[AtlasStore, Path]:
    """Write an atlas.yaml pointing atlas_dir to tmp_path. Return (store, config_path)."""
    config_path = tmp_path / "atlas.yaml"
    config_path.write_text(
        yaml.dump({"atlas_dir": str(tmp_path).replace("\\", "/")})
    )
    store = AtlasStore(tmp_path / "atlas.db")
    return store, config_path


def _invoke(args: list[str], config_path: Path):
    return runner.invoke(app, args + ["--config", str(config_path)])


def _make_file_record(path: Path, project_root: Path) -> FileRecord:
    content = path.read_bytes()
    return FileRecord(
        path=path, project_root=project_root, language="python",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        last_modified=datetime.now(),
    )


def test_health_clean(tmp_path):
    """No issues when every project has a real path, files, and a summary."""
    store, cfg = _setup(tmp_path)

    real_dir = tmp_path / "myproject"
    real_dir.mkdir()
    py_file = real_dir / "main.py"
    py_file.write_text("def main(): pass\n")
    store.upsert_snapshot(ProjectSnapshot(
        root=real_dir, name="myproject",
        files=[_make_file_record(py_file, real_dir)],
    ))
    store.upsert_project_summary(ProjectSummary(
        project_name="myproject", root=str(real_dir),
        one_liner="A project.", description="",
    ))

    result = _invoke(["health"], cfg)
    assert result.exit_code == 0
    assert "healthy" in result.output.lower()


def test_health_dead_path(tmp_path):
    """Dead path detected and flagged as ERROR."""
    store, cfg = _setup(tmp_path)

    ghost_dir = tmp_path / "ghost"  # intentionally not created
    store.upsert_snapshot(ProjectSnapshot(root=ghost_dir, name="ghost"))

    result = _invoke(["health"], cfg)
    assert result.exit_code == 0
    assert "path not found" in result.output.lower()
    assert "ERROR" in result.output


def test_health_fix_removes_dead(tmp_path):
    """--fix removes dead-path entries from the DB."""
    store, cfg = _setup(tmp_path)

    ghost_dir = tmp_path / "ghost"
    store.upsert_snapshot(ProjectSnapshot(root=ghost_dir, name="ghost"))

    result = _invoke(["health", "--fix"], cfg)
    assert result.exit_code == 0
    assert store.get_project_roots() == []


def test_health_no_summary(tmp_path):
    """Scanned but un-enriched project flagged as WARN."""
    store, cfg = _setup(tmp_path)

    real_dir = tmp_path / "unenriched"
    real_dir.mkdir()
    store.upsert_snapshot(ProjectSnapshot(root=real_dir, name="unenriched"))
    # no project summary upserted

    result = _invoke(["health"], cfg)
    assert result.exit_code == 0
    assert "WARN" in result.output
    assert "not enriched" in result.output


def test_health_similar_names(tmp_path):
    """Similar project names flagged as INFO."""
    store, cfg = _setup(tmp_path)

    for name, dirname in (("ragsistant", "r1"), ("ragsistant-v2", "r2")):
        d = tmp_path / dirname
        d.mkdir()
        store.upsert_snapshot(ProjectSnapshot(root=d, name=name))
        store.upsert_project_summary(ProjectSummary(
            project_name=name, root=str(d), one_liner=".", description="",
        ))

    result = _invoke(["health"], cfg)
    assert result.exit_code == 0
    assert "similar" in result.output.lower()
