"""Domain models for code-atlas."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class FileRecord:
    path: Path
    project_root: Path
    language: str
    size_bytes: int
    sha256: str
    last_modified: datetime

    @property
    def relative_path(self) -> str:
        try:
            return str(self.path.relative_to(self.project_root))
        except ValueError:
            return str(self.path)


@dataclass
class CodeSymbol:
    name: str
    kind: str  # "function" | "class" | "method" | "import" | "export"
    file_path: str
    line: int


@dataclass
class ProjectSnapshot:
    root: Path
    name: str
    languages: dict[str, int] = field(default_factory=dict)   # lang -> line count
    files: list[FileRecord] = field(default_factory=list)
    symbols: list[CodeSymbol] = field(default_factory=list)
    dependencies: dict[str, list[str]] = field(default_factory=dict)  # dep file -> packages
    readme_text: str = ""
    git_last_commit: datetime | None = None
    git_commit_count: int = 0
    git_remote_url: str = ""
    scanned_at: datetime = field(default_factory=datetime.now)


@dataclass
class FileSummary:
    file_path: str
    project_name: str
    sha256: str
    summary: str
    summarized_at: datetime = field(default_factory=datetime.now)


@dataclass
class ProjectSummary:
    project_name: str
    root: str
    one_liner: str
    description: str
    tech_stack: list[str] = field(default_factory=list)
    key_patterns: list[str] = field(default_factory=list)
    reuse_hints: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    summarized_at: datetime = field(default_factory=datetime.now)
