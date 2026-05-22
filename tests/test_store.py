"""Tests for the SQLite store — idempotency and caching."""

import tempfile
from datetime import datetime
from pathlib import Path

from code_atlas.models import FileSummary, ProjectSnapshot, ProjectSummary
from code_atlas.store import AtlasStore


def _make_store(tmp_path: Path) -> AtlasStore:
    return AtlasStore(tmp_path / "test.db")


def test_upsert_snapshot_idempotent(tmp_path):
    store = _make_store(tmp_path)
    root = tmp_path / "fake_project"
    root.mkdir()
    snap = ProjectSnapshot(root=root, name="test")
    store.upsert_snapshot(snap)
    store.upsert_snapshot(snap)  # second upsert should not raise
    roots = store.get_project_roots()
    assert roots.count(str(root)) == 1


def test_file_summary_cache(tmp_path):
    store = _make_store(tmp_path)
    fs = FileSummary(
        file_path="/fake/project/main.py",
        project_name="test",
        sha256="abc123",
        summary="Does the main thing.",
    )
    store.upsert_file_summary(fs)

    result = store.get_file_summary("/fake/project/main.py", "abc123")
    assert result == "Does the main thing."

    # Different hash → cache miss
    assert store.get_file_summary("/fake/project/main.py", "differenthash") is None


def test_project_summary_roundtrip(tmp_path):
    store = _make_store(tmp_path)
    ps = ProjectSummary(
        project_name="myproject",
        root="/fake/project",
        one_liner="A test project.",
        description="Does nothing useful.",
        tech_stack=["Python", "SQLite"],
        key_patterns=["singleton"],
        reuse_hints=["the SQLite pattern"],
        keywords=["python", "sqlite", "test"],
    )
    store.upsert_project_summary(ps)
    retrieved = store.get_project_summary("myproject")

    assert retrieved is not None
    assert retrieved.one_liner == "A test project."
    assert "Python" in retrieved.tech_stack
    assert "python" in retrieved.keywords


def test_all_project_summaries(tmp_path):
    store = _make_store(tmp_path)
    for name in ("alpha", "beta", "gamma"):
        store.upsert_project_summary(ProjectSummary(
            project_name=name,
            root=f"/fake/{name}",
            one_liner=f"{name} project",
            description="",
        ))
    summaries = store.all_project_summaries()
    names = [s.project_name for s in summaries]
    assert set(names) == {"alpha", "beta", "gamma"}
