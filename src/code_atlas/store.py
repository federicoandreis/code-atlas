"""SQLite store — snapshots, summary cache, project records."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .models import FileSummary, ProjectSnapshot, ProjectSummary


class AtlasStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            _migrate(conn)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------ #
    # Project snapshots                                                    #
    # ------------------------------------------------------------------ #

    def upsert_snapshot(self, snapshot: ProjectSnapshot) -> None:
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO projects (name, root, languages, dependencies,
                    git_last_commit, git_commit_count, git_remote_url, scanned_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(root) DO UPDATE SET
                    name=excluded.name,
                    languages=excluded.languages,
                    dependencies=excluded.dependencies,
                    git_last_commit=excluded.git_last_commit,
                    git_commit_count=excluded.git_commit_count,
                    git_remote_url=excluded.git_remote_url,
                    scanned_at=excluded.scanned_at
            """, (
                snapshot.name,
                str(snapshot.root),
                json.dumps(snapshot.languages),
                json.dumps(snapshot.dependencies),
                snapshot.git_last_commit.isoformat() if snapshot.git_last_commit else None,
                snapshot.git_commit_count,
                snapshot.git_remote_url,
                snapshot.scanned_at.isoformat(),
            ))
            # Replace file records for this project
            conn.execute("DELETE FROM files WHERE project_root = ?", (str(snapshot.root),))
            conn.executemany("""
                INSERT INTO files (project_root, relative_path, language, size_bytes, sha256, last_modified)
                VALUES (?, ?, ?, ?, ?, ?)
            """, [
                (str(snapshot.root), rec.relative_path, rec.language,
                 rec.size_bytes, rec.sha256, rec.last_modified.isoformat())
                for rec in snapshot.files
            ])

    def get_project_roots(self) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute("SELECT root FROM projects ORDER BY name").fetchall()
            return [r["root"] for r in rows]

    def remove_project(self, project_root: str) -> bool:
        """Remove all data for a project. Returns True if it existed."""
        with self._conn() as conn:
            existed = conn.execute(
                "SELECT 1 FROM projects WHERE root = ?", (project_root,)
            ).fetchone() is not None
            conn.execute("DELETE FROM projects WHERE root = ?", (project_root,))
            conn.execute("DELETE FROM files WHERE project_root = ?", (project_root,))
            conn.execute("DELETE FROM file_summaries WHERE project_name = "
                         "(SELECT name FROM projects WHERE root = ?) "
                         "OR file_path LIKE ?", (project_root, project_root + "%"))
            conn.execute("DELETE FROM project_summaries WHERE root = ?", (project_root,))
        return existed

    def remove_project_by_name(self, name: str) -> str | None:
        """Remove by project name. Returns root path if found, else None."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT root FROM projects WHERE name = ?", (name,)
            ).fetchone()
            if not row:
                return None
            root = row["root"]
            conn.execute("DELETE FROM projects WHERE name = ?", (name,))
            conn.execute("DELETE FROM files WHERE project_root = ?", (root,))
            conn.execute("DELETE FROM file_summaries WHERE project_name = ?", (name,))
            conn.execute("DELETE FROM project_summaries WHERE project_name = ?", (name,))
        return root

    def reset_summaries(self, project_root: str) -> int:
        """Delete cached LLM summaries for a project, forcing re-enrichment. Returns count deleted."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT name FROM projects WHERE root = ?", (project_root,)
            ).fetchone()
            name = row["name"] if row else None
            n = conn.execute(
                "SELECT COUNT(*) FROM file_summaries WHERE project_name = ?", (name,)
            ).fetchone()[0] if name else 0
            if name:
                conn.execute("DELETE FROM file_summaries WHERE project_name = ?", (name,))
                conn.execute("DELETE FROM project_summaries WHERE project_name = ?", (name,))
        return n

    def clear_all(self) -> None:
        """Wipe the entire database."""
        with self._conn() as conn:
            conn.executescript("""
                DELETE FROM project_summaries;
                DELETE FROM file_summaries;
                DELETE FROM files;
                DELETE FROM projects;
            """)

    def get_file_hashes(self, project_root: str) -> dict[str, str]:
        """Return {relative_path: sha256} for a project."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT relative_path, sha256 FROM files WHERE project_root = ?",
                (project_root,),
            ).fetchall()
            return {r["relative_path"]: r["sha256"] for r in rows}

    # ------------------------------------------------------------------ #
    # Summary cache                                                        #
    # ------------------------------------------------------------------ #

    def get_file_summary(self, file_path: str, sha256: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT summary FROM file_summaries WHERE file_path = ? AND sha256 = ?",
                (file_path, sha256),
            ).fetchone()
            return row["summary"] if row else None

    def upsert_file_summary(self, summary: FileSummary) -> None:
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO file_summaries (file_path, project_name, sha256, summary, summarized_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(file_path, sha256) DO UPDATE SET
                    summary=excluded.summary,
                    summarized_at=excluded.summarized_at
            """, (
                summary.file_path,
                summary.project_name,
                summary.sha256,
                summary.summary,
                summary.summarized_at.isoformat(),
            ))

    def get_project_summary(self, project_name: str) -> ProjectSummary | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM project_summaries WHERE project_name = ?",
                (project_name,),
            ).fetchone()
            if not row:
                return None
            return ProjectSummary(
                project_name=row["project_name"],
                root=row["root"],
                one_liner=row["one_liner"],
                description=row["description"],
                tech_stack=json.loads(row["tech_stack"]),
                key_patterns=json.loads(row["key_patterns"]),
                reuse_hints=json.loads(row["reuse_hints"]),
                keywords=json.loads(row["keywords"]),
                summarized_at=datetime.fromisoformat(row["summarized_at"]),
            )

    def upsert_project_summary(self, summary: ProjectSummary) -> None:
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO project_summaries
                    (project_name, root, one_liner, description,
                     tech_stack, key_patterns, reuse_hints, keywords, summarized_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_name) DO UPDATE SET
                    root=excluded.root,
                    one_liner=excluded.one_liner,
                    description=excluded.description,
                    tech_stack=excluded.tech_stack,
                    key_patterns=excluded.key_patterns,
                    reuse_hints=excluded.reuse_hints,
                    keywords=excluded.keywords,
                    summarized_at=excluded.summarized_at
            """, (
                summary.project_name,
                summary.root,
                summary.one_liner,
                summary.description,
                json.dumps(summary.tech_stack),
                json.dumps(summary.key_patterns),
                json.dumps(summary.reuse_hints),
                json.dumps(summary.keywords),
                summary.summarized_at.isoformat(),
            ))

    def all_project_summaries(self) -> list[ProjectSummary]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM project_summaries ORDER BY project_name"
            ).fetchall()
            return [ProjectSummary(
                project_name=r["project_name"],
                root=r["root"],
                one_liner=r["one_liner"],
                description=r["description"],
                tech_stack=json.loads(r["tech_stack"]),
                key_patterns=json.loads(r["key_patterns"]),
                reuse_hints=json.loads(r["reuse_hints"]),
                keywords=json.loads(r["keywords"]),
                summarized_at=datetime.fromisoformat(r["summarized_at"]),
            ) for r in rows]

    def get_key_files_for_project(
        self, project_name: str, n: int = 8
    ) -> list[tuple[str, str]]:
        """Return (relative_path, summary) for the N most significant files.

        Ranked by size_bytes descending. Trivially auto-classified files
        (tests, __init__, migrations, config) are deprioritised — they tell
        an agent nothing useful about where reusable logic lives.
        """
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT f.relative_path, fs.summary, f.size_bytes
                FROM files f
                JOIN file_summaries fs
                  ON f.project_root = (SELECT root FROM projects WHERE name = ?)
                 AND f.relative_path LIKE '%' || REPLACE(fs.file_path, '\\', '/') || '%'
                  OR fs.file_path LIKE '%' || f.relative_path || '%'
                WHERE fs.project_name = ?
                ORDER BY f.size_bytes DESC
            """, (project_name, project_name)).fetchall()

        # Simpler join via Python — file_path in summaries is absolute,
        # relative_path in files is relative; match by suffix
        with self._conn() as conn:
            frows = conn.execute(
                "SELECT relative_path, size_bytes FROM files WHERE project_root = "
                "(SELECT root FROM projects WHERE name = ?)",
                (project_name,),
            ).fetchall()
            srows = conn.execute(
                "SELECT file_path, summary FROM file_summaries WHERE project_name = ?",
                (project_name,),
            ).fetchall()

        # Build lookup: normalised suffix → (relative_path, size_bytes)
        file_by_suffix: dict[str, tuple[str, int]] = {}
        for r in frows:
            norm = r["relative_path"].replace("\\", "/")
            file_by_suffix[norm] = (r["relative_path"], r["size_bytes"])

        _STUB_PREFIXES = (
            "Tests for", "Package init", "Database migration",
            "Project ", "(summary unavailable)",
        )

        scored: list[tuple[int, str, str]] = []
        for s in srows:
            if any(s["summary"].startswith(p) for p in _STUB_PREFIXES):
                continue
            # Match absolute file_path → relative_path by longest common suffix
            abs_norm = s["file_path"].replace("\\", "/")
            match_rel = None
            match_size = 0
            for suffix, (rel, size) in file_by_suffix.items():
                if abs_norm.endswith(suffix) or abs_norm.endswith("/" + suffix):
                    match_rel = rel
                    match_size = size
                    break
            if match_rel:
                scored.append((match_size, match_rel, s["summary"]))

        scored.sort(key=lambda x: -x[0])
        return [(rel, summary) for _, rel, summary in scored[:n]]

    def get_file_summaries_for_project(self, project_name: str) -> list[FileSummary]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM file_summaries WHERE project_name = ?",
                (project_name,),
            ).fetchall()
            return [FileSummary(
                file_path=r["file_path"],
                project_name=r["project_name"],
                sha256=r["sha256"],
                summary=r["summary"],
                summarized_at=datetime.fromisoformat(r["summarized_at"]),
            ) for r in rows]


def _migrate(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            root TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            languages TEXT NOT NULL DEFAULT '{}',
            dependencies TEXT NOT NULL DEFAULT '{}',
            git_last_commit TEXT,
            git_commit_count INTEGER DEFAULT 0,
            git_remote_url TEXT DEFAULT '',
            scanned_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_root TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            language TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            last_modified TEXT NOT NULL,
            UNIQUE(project_root, relative_path)
        );

        CREATE TABLE IF NOT EXISTS file_summaries (
            file_path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            project_name TEXT NOT NULL,
            summary TEXT NOT NULL,
            summarized_at TEXT NOT NULL,
            PRIMARY KEY (file_path, sha256)
        );

        CREATE TABLE IF NOT EXISTS project_summaries (
            project_name TEXT PRIMARY KEY,
            root TEXT NOT NULL,
            one_liner TEXT NOT NULL,
            description TEXT NOT NULL,
            tech_stack TEXT NOT NULL DEFAULT '[]',
            key_patterns TEXT NOT NULL DEFAULT '[]',
            reuse_hints TEXT NOT NULL DEFAULT '[]',
            keywords TEXT NOT NULL DEFAULT '[]',
            summarized_at TEXT NOT NULL
        );
    """)
