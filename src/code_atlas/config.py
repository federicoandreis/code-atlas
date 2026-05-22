"""Atlas configuration — loaded from atlas.yaml, written to ~/.atlas/ by default."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_ATLAS_DIR = Path.home() / ".atlas"
DEFAULT_CONFIG_PATH = DEFAULT_ATLAS_DIR / "atlas.yaml"


@dataclass
class LLMConfig:
    base_url: str = "http://127.0.0.1:8080/v1"
    model: str = "qwen3-30b-a3b"
    timeout: int = 120
    max_retries: int = 3
    batch_size: int = 8
    enabled: bool = True


@dataclass
class AtlasConfig:
    projects: list[str] = field(default_factory=list)
    atlas_dir: str = str(DEFAULT_ATLAS_DIR)
    llm: LLMConfig = field(default_factory=LLMConfig)
    ignore_patterns: list[str] = field(default_factory=lambda: [
        ".git", "__pycache__", "node_modules", ".venv", "venv",
        "dist", "build", "*.egg-info", ".pytest_cache",
    ])
    max_file_size_kb: int = 500
    include_extensions: list[str] = field(default_factory=lambda: [
        ".py", ".ts", ".js", ".tsx", ".jsx", ".go", ".rs",
        ".java", ".rb", ".c", ".cpp", ".h", ".cs", ".kt",
        ".r", ".R", ".scala", ".swift", ".lua",
        # Data science / academic formats
        ".Rmd", ".rmd", ".qmd",   # R/Quarto markdown (contain executable code)
        ".ipynb",                  # Jupyter notebooks
        ".tex",                    # LaTeX (code + markup)
    ])

    @property
    def atlas_path(self) -> Path:
        return Path(self.atlas_dir)

    @property
    def db_path(self) -> Path:
        return self.atlas_path / "atlas.db"

    @property
    def atlas_md_path(self) -> Path:
        return self.atlas_path / "PROJECTS_ATLAS.md"


def load_config(path: Path | None = None) -> AtlasConfig:
    candidate = path or _find_config()
    if candidate is None or not candidate.exists():
        return AtlasConfig()
    with open(candidate) as f:
        data = yaml.safe_load(f) or {}
    cfg = AtlasConfig()
    if "projects" in data:
        cfg.projects = [str(p) for p in data["projects"]]
    if "atlas_dir" in data:
        cfg.atlas_dir = data["atlas_dir"]
    if "llm" in data:
        llm = data["llm"]
        cfg.llm = LLMConfig(
            base_url=llm.get("base_url", cfg.llm.base_url),
            model=llm.get("model", cfg.llm.model),
            timeout=llm.get("timeout", cfg.llm.timeout),
            max_retries=llm.get("max_retries", cfg.llm.max_retries),
            batch_size=llm.get("batch_size", cfg.llm.batch_size),
            enabled=llm.get("enabled", cfg.llm.enabled),
        )
    if "ignore_patterns" in data:
        cfg.ignore_patterns = data["ignore_patterns"]
    if "max_file_size_kb" in data:
        cfg.max_file_size_kb = data["max_file_size_kb"]
    return cfg


def _find_config() -> Path | None:
    # 1. ATLAS_CONFIG env var
    if env := os.environ.get("ATLAS_CONFIG"):
        return Path(env)
    # 2. Current working dir
    cwd_cfg = Path.cwd() / "atlas.yaml"
    if cwd_cfg.exists():
        return cwd_cfg
    # 3. Default user location
    return DEFAULT_CONFIG_PATH


def add_project_to_config(project_path: str, config_path: Path | None = None) -> Path:
    """Add a project path to atlas.yaml, creating it if needed. Returns the config path used."""
    target = config_path or _find_config() or DEFAULT_CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        with open(target) as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}

    projects: list[str] = data.get("projects", [])
    normalized = str(Path(project_path).expanduser().resolve())
    if normalized not in projects:
        projects.append(normalized)
        data["projects"] = projects
        with open(target, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    return target


def remove_projects_from_config(
    project_paths: list[str], config_path: Path | None = None
) -> tuple[Path, int]:
    """Remove project paths from atlas.yaml. Returns (config_path, n_removed)."""
    target = config_path or _find_config() or DEFAULT_CONFIG_PATH
    if not target.exists():
        return target, 0
    with open(target) as f:
        data = yaml.safe_load(f) or {}
    existing: list[str] = data.get("projects", [])
    to_remove = {str(Path(p).expanduser().resolve()) for p in project_paths}
    updated = [p for p in existing if str(Path(p).expanduser().resolve()) not in to_remove]
    n_removed = len(existing) - len(updated)
    if n_removed:
        data["projects"] = updated
        with open(target, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    return target, n_removed


def write_example_config(path: Path) -> None:
    example = {
        "projects": [
            "G:/Development/local-badger",
            "~/projects/ragsistant",
        ],
        "atlas_dir": str(DEFAULT_ATLAS_DIR),
        "llm": {
            "base_url": "http://127.0.0.1:8080/v1",
            "model": "qwen3-30b-a3b",
            "timeout": 120,
            "batch_size": 8,
            "enabled": True,
        },
        "ignore_patterns": [
            ".git", "__pycache__", "node_modules", ".venv",
            "venv", "dist", "build", "*.egg-info",
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(example, f, default_flow_style=False, sort_keys=False)
