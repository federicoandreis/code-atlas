"""Phase 1: Static project scanner — no LLM, no network, fully local."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from .config import AtlasConfig
from .models import CodeSymbol, FileRecord, ProjectSnapshot

try:
    import git
    HAS_GIT = True
except ImportError:
    HAS_GIT = False

try:
    from tree_sitter_languages import get_language, get_parser
    HAS_TREE_SITTER = True
except ImportError:
    HAS_TREE_SITTER = False

# Map file extension → tree-sitter language name
EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".cs": "c_sharp",
    ".kt": "kotlin",
    ".r": "r",
    ".R": "r",
    ".scala": "scala",
    ".swift": "swift",
    ".lua": "lua",
}

# Dependency manifest files and how to read them
DEPENDENCY_FILES = {
    "pyproject.toml": "_parse_pyproject",
    "requirements.txt": "_parse_requirements",
    "package.json": "_parse_package_json",
    "Cargo.toml": "_parse_cargo_toml",
    "go.mod": "_parse_go_mod",
    "Gemfile": "_parse_gemfile",
    "pom.xml": "_parse_pom",
    "build.gradle": "_parse_gradle",
    "DESCRIPTION": "_parse_r_description",
}


class ProjectScanner:
    def __init__(self, config: AtlasConfig) -> None:
        self.config = config

    def scan(self, project_root: Path) -> ProjectSnapshot:
        root = project_root.resolve()
        snapshot = ProjectSnapshot(root=root, name=root.name)

        self._collect_files(snapshot)
        self._read_git_metadata(snapshot)
        self._read_readme(snapshot)
        self._read_dependencies(snapshot)
        if HAS_TREE_SITTER:
            self._extract_symbols(snapshot)

        return snapshot

    # ------------------------------------------------------------------ #
    # File collection                                                      #
    # ------------------------------------------------------------------ #

    def _collect_files(self, snapshot: ProjectSnapshot) -> None:
        lang_lines: dict[str, int] = {}
        for path in snapshot.root.rglob("*"):
            if not path.is_file():
                continue
            if self._is_ignored(path, snapshot.root):
                continue
            ext = path.suffix.lower()
            if ext not in self.config.include_extensions:
                continue
            try:
                size = path.stat().st_size
                if size > self.config.max_file_size_kb * 1024:
                    continue
                mtime = datetime.fromtimestamp(path.stat().st_mtime)
                sha = _sha256(path)
                lang = EXT_TO_LANG.get(path.suffix, path.suffix.lstrip("."))
                rec = FileRecord(
                    path=path,
                    project_root=snapshot.root,
                    language=lang,
                    size_bytes=size,
                    sha256=sha,
                    last_modified=mtime,
                )
                snapshot.files.append(rec)
                lines = _count_lines(path)
                lang_lines[lang] = lang_lines.get(lang, 0) + lines
            except (OSError, PermissionError):
                continue
        snapshot.languages = lang_lines

    def _is_ignored(self, path: Path, root: Path) -> bool:
        try:
            rel = path.relative_to(root)
        except ValueError:
            return False
        parts = rel.parts
        for pattern in self.config.ignore_patterns:
            for part in parts:
                if fnmatch.fnmatch(part, pattern):
                    return True
        return False

    # ------------------------------------------------------------------ #
    # Git metadata                                                         #
    # ------------------------------------------------------------------ #

    def _read_git_metadata(self, snapshot: ProjectSnapshot) -> None:
        if not HAS_GIT:
            return
        try:
            with git.Repo(snapshot.root, search_parent_directories=True) as repo:
                try:
                    last = repo.head.commit
                    snapshot.git_last_commit = datetime.fromtimestamp(last.committed_date)
                    snapshot.git_commit_count = sum(1 for _ in repo.iter_commits())
                except Exception:
                    pass
                try:
                    remote = repo.remotes[0].url if repo.remotes else ""
                    snapshot.git_remote_url = remote
                except Exception:
                    pass
        except (git.InvalidGitRepositoryError, git.NoSuchPathError, Exception):
            pass

    # ------------------------------------------------------------------ #
    # README                                                               #
    # ------------------------------------------------------------------ #

    def _read_readme(self, snapshot: ProjectSnapshot) -> None:
        for name in ("README.md", "readme.md", "README.rst", "README.txt", "README"):
            readme = snapshot.root / name
            if readme.exists():
                try:
                    text = readme.read_text(encoding="utf-8", errors="replace")
                    snapshot.readme_text = text[:8000]  # cap at 8K chars
                except OSError:
                    pass
                break

    # ------------------------------------------------------------------ #
    # Dependency manifests                                                 #
    # ------------------------------------------------------------------ #

    def _read_dependencies(self, snapshot: ProjectSnapshot) -> None:
        for filename, parser_name in DEPENDENCY_FILES.items():
            manifest = snapshot.root / filename
            if manifest.exists():
                parser = getattr(self, parser_name, None)
                if parser:
                    try:
                        packages = parser(manifest)
                        if packages:
                            snapshot.dependencies[filename] = packages
                    except Exception:
                        pass

    def _parse_pyproject(self, path: Path) -> list[str]:
        try:
            import tomllib  # 3.11+
        except ImportError:
            return []
        with open(path, "rb") as f:
            data = tomllib.load(f)
        deps = data.get("project", {}).get("dependencies", [])
        return [_dep_name(d) for d in deps if isinstance(d, str)]

    def _parse_requirements(self, path: Path) -> list[str]:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        pkgs = []
        for line in lines:
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("-"):
                pkgs.append(_dep_name(line))
        return pkgs

    def _parse_package_json(self, path: Path) -> list[str]:
        data = json.loads(path.read_text(encoding="utf-8"))
        deps = {}
        deps.update(data.get("dependencies", {}))
        deps.update(data.get("devDependencies", {}))
        return list(deps.keys())

    def _parse_cargo_toml(self, path: Path) -> list[str]:
        try:
            import tomllib
        except ImportError:
            return []
        with open(path, "rb") as f:
            data = tomllib.load(f)
        return list(data.get("dependencies", {}).keys())

    def _parse_go_mod(self, path: Path) -> list[str]:
        text = path.read_text(encoding="utf-8", errors="replace")
        return re.findall(r"^\s+(\S+)\s+v", text, re.MULTILINE)

    def _parse_gemfile(self, path: Path) -> list[str]:
        text = path.read_text(encoding="utf-8", errors="replace")
        return re.findall(r"gem\s+['\"](\S+)['\"]", text)

    def _parse_pom(self, path: Path) -> list[str]:
        text = path.read_text(encoding="utf-8", errors="replace")
        return re.findall(r"<artifactId>([^<]+)</artifactId>", text)

    def _parse_gradle(self, path: Path) -> list[str]:
        text = path.read_text(encoding="utf-8", errors="replace")
        return re.findall(r"""['"]([\w.\-:]+):[\w.\-]+['"]\s*""", text)

    def _parse_r_description(self, path: Path) -> list[str]:
        text = path.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^Imports:\s*(.*?)(?=^\w|\Z)", text, re.MULTILINE | re.DOTALL)
        if not m:
            return []
        return [p.strip().rstrip(",") for p in m.group(1).split("\n") if p.strip()]

    # ------------------------------------------------------------------ #
    # Symbol extraction via tree-sitter                                   #
    # ------------------------------------------------------------------ #

    def _extract_symbols(self, snapshot: ProjectSnapshot) -> None:
        for rec in snapshot.files:
            lang_name = EXT_TO_LANG.get(rec.path.suffix)
            if not lang_name:
                continue
            try:
                symbols = _parse_symbols(rec.path, lang_name)
                snapshot.symbols.extend(symbols)
            except Exception:
                continue


# ------------------------------------------------------------------ #
# Module-level helpers                                                #
# ------------------------------------------------------------------ #

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _count_lines(path: Path) -> int:
    try:
        return sum(1 for _ in open(path, "rb"))
    except OSError:
        return 0


def _dep_name(dep: str) -> str:
    return re.split(r"[>=<!;\[]", dep.strip())[0].strip()


_SYMBOL_QUERIES: dict[str, str] = {
    "python": """
        (function_definition name: (identifier) @fn)
        (class_definition name: (identifier) @cls)
        (import_statement name: (dotted_name) @imp)
        (import_from_statement module_name: (dotted_name) @imp)
    """,
    "typescript": """
        (function_declaration name: (identifier) @fn)
        (class_declaration name: (type_identifier) @cls)
        (import_statement source: (string) @imp)
        (export_statement declaration: (function_declaration name: (identifier) @fn))
    """,
    "javascript": """
        (function_declaration name: (identifier) @fn)
        (class_declaration name: (identifier) @cls)
        (import_statement source: (string) @imp)
    """,
    "go": """
        (function_declaration name: (identifier) @fn)
        (type_declaration (type_spec name: (type_identifier) @cls))
        (import_spec path: (interpreted_string_literal) @imp)
    """,
    "rust": """
        (function_item name: (identifier) @fn)
        (struct_item name: (type_identifier) @cls)
        (use_declaration argument: (scoped_identifier) @imp)
    """,
}


def _parse_symbols(path: Path, lang_name: str) -> list[CodeSymbol]:
    if lang_name not in _SYMBOL_QUERIES:
        return []
    try:
        language = get_language(lang_name)
        parser = get_parser(lang_name)
        source = path.read_bytes()
        tree = parser.parse(source)
        query = language.query(_SYMBOL_QUERIES[lang_name])
        captures = query.captures(tree.root_node)
    except Exception:
        return []

    symbols = []
    for node, capture_name in captures:
        kind = {"fn": "function", "cls": "class", "imp": "import"}.get(capture_name, capture_name)
        symbols.append(CodeSymbol(
            name=node.text.decode("utf-8", errors="replace").strip('"\''),
            kind=kind,
            file_path=str(path),
            line=node.start_point[0] + 1,
        ))
    return symbols
