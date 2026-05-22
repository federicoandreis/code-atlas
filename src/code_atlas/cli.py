"""code-atlas CLI — atlas scan / enrich / report / install / query."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Annotated, Optional

# Force UTF-8 on Windows where the default console encoding (cp1252) can't
# render Rich's unicode checkmarks and arrows.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

import typer
from rich import print as rprint
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.text import Text

from .config import AtlasConfig, add_project_to_config, load_config, remove_projects_from_config, write_example_config
from .enricher import Enricher, _trivial_summary
from .installer import SUPPORTED_PLATFORMS, Installer
from .reporter import Reporter
from .scanner import ProjectScanner
from .store import AtlasStore

app = typer.Typer(
    name="atlas",
    help="Fully local code project knowledge base for coding assistant injection.",
    no_args_is_help=True,
)


def main() -> None:
    """Entry point — wraps app() to catch Ctrl+C cleanly."""
    try:
        app()
    except KeyboardInterrupt:
        rprint("\n[yellow]Interrupted.[/yellow]")
        sys.exit(130)


_PROJECT_MANIFESTS = {
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
    "package.json", "Cargo.toml", "go.mod", "Gemfile", "pom.xml",
    "build.gradle", "DESCRIPTION", "CMakeLists.txt",
}


def _detect_project_signal(path: Path, cfg: AtlasConfig) -> str | None:
    """Return a human-readable detection reason if path looks like a project root, else None."""
    if (path / ".git").is_dir():
        return "git repo"
    for m in _PROJECT_MANIFESTS:
        if (path / m).exists():
            return m
    src_files = [f for f in path.iterdir()
                 if f.is_file() and f.suffix in cfg.include_extensions]
    if len(src_files) >= 3:
        return f"{len(src_files)} source files"
    return None


def _is_project_root(path: Path, cfg: AtlasConfig) -> bool:
    return _detect_project_signal(path, cfg) is not None


def _shallow_src_count(path: Path, cfg: AtlasConfig) -> dict[str, int]:
    """Count source files by extension in the top two levels (fast, no full scan)."""
    counts: dict[str, int] = {}
    for f in path.rglob("*"):
        if f.is_file() and f.suffix in cfg.include_extensions:
            # Only go 2 levels deep to keep it fast
            try:
                depth = len(f.relative_to(path).parts)
            except ValueError:
                continue
            if depth <= 2:
                counts[f.suffix] = counts.get(f.suffix, 0) + 1
    return counts


def _discover_projects(parent: Path, cfg: AtlasConfig) -> list[tuple[Path, str]]:
    """Return (subdir, detection_signal) for immediate subdirectories that look like project roots."""
    ignore = set(cfg.ignore_patterns)
    candidates = []
    for sub in sorted(parent.iterdir()):
        if not sub.is_dir():
            continue
        if sub.name.startswith("."):
            continue
        if sub.name in ignore:
            continue
        signal = _detect_project_signal(sub, cfg)
        if signal:
            candidates.append((sub, signal))
    return candidates


def _is_github_account_url(s: str) -> bool:
    """True for https://github.com/username — one path segment, no .git suffix."""
    from urllib.parse import urlparse
    try:
        p = urlparse(s)
        if p.netloc.lower().lstrip("www.") != "github.com":
            return False
        parts = [x for x in p.path.strip("/").split("/") if x]
        return len(parts) == 1 and not parts[0].endswith(".git")
    except Exception:
        return False


def _github_username_from_url(s: str) -> str:
    from urllib.parse import urlparse
    return [x for x in urlparse(s).path.strip("/").split("/") if x][0]


def _fetch_github_repos(username: str, include_forks: bool = False) -> list[dict]:
    """Fetch public repos for a GitHub user or org via the REST API."""
    import os
    import httpx

    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"

    repos: list[dict] = []
    # Try user endpoint first; fall back to org endpoint
    for kind in ("users", "orgs"):
        url = f"https://api.github.com/{kind}/{username}/repos"
        page = 1
        found = False
        while True:
            resp = httpx.get(url, params={"per_page": 100, "page": page, "sort": "updated"},
                             headers=headers, timeout=30)
            if resp.status_code == 404:
                break
            if resp.status_code == 403:
                raise RuntimeError(
                    "GitHub rate limit hit. Set GITHUB_TOKEN env var to increase quota."
                )
            resp.raise_for_status()
            data = resp.json()
            if not data:
                found = True
                break
            repos.extend(data)
            found = True
            if len(data) < 100:
                break
            page += 1
        if found:
            break

    if not include_forks:
        repos = [r for r in repos if not r.get("fork", False)]

    return repos


def _parse_repo_selection(text: str, count: int) -> list[int] | None:
    """Parse '1,3,5-8' or 'all' into sorted 0-based indices. Returns None to cancel."""
    text = text.strip().lower()
    if not text or text in ("q", "quit", "n", "cancel"):
        return None
    if text == "all":
        return list(range(count))
    indices: set[int] = set()
    for part in text.replace(" ", "").split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            indices.update(range(int(a) - 1, int(b)))
        else:
            indices.add(int(part) - 1)
    return sorted(i for i in indices if 0 <= i < count)


def _is_git_url(s: str) -> bool:
    if _is_github_account_url(s):
        return False
    return (
        s.startswith("https://")
        or s.startswith("http://")
        or s.startswith("git@")
        or s.startswith("git://")
        or (s.endswith(".git") and "/" in s)
    )


def _clone_or_update(url: str, repos_dir: Path, shallow: bool = True) -> Path:
    try:
        import git as gitmod
    except ImportError:
        rprint("[red]gitpython is required for URL support.[/red] Run: pip install gitpython")
        raise typer.Exit(1)

    # Derive a directory name from the URL: owner-repo
    clean = url.rstrip("/").removesuffix(".git")
    parts = clean.replace(":", "/").split("/")
    slug = "-".join(parts[-2:]) if len(parts) >= 2 else parts[-1]
    dest = repos_dir / slug
    repos_dir.mkdir(parents=True, exist_ok=True)

    if dest.exists():
        rprint(f"[dim]Already cloned — pulling latest...[/dim]")
        try:
            repo = gitmod.Repo(dest)
            repo.remotes.origin.pull()
            rprint(f"[green]✓[/green] Pulled latest from {url}")
        except Exception as exc:
            rprint(f"[yellow]Pull failed (using existing):[/yellow] {exc}")
    else:
        rprint(f"Cloning [bold]{url}[/bold] → {dest}")
        kwargs: dict = {"depth": 1} if shallow else {}
        try:
            gitmod.Repo.clone_from(url, dest, **kwargs)
        except gitmod.GitCommandError as exc:
            rprint(f"[red]Clone failed:[/red] {exc}")
            raise typer.Exit(1)

    return dest


def _pull_if_remote(root: Path) -> None:
    try:
        import git as gitmod
        repo = gitmod.Repo(root)
        if repo.remotes:
            repo.remotes.origin.pull()
            rprint(f"  [dim]Pulled latest for {root.name}[/dim]")
    except Exception as exc:
        rprint(f"  [yellow]Pull skipped ({root.name}):[/yellow] {exc}")


def _rmtree(path: Path) -> None:
    """Delete a directory tree, handling Windows read-only files in .git directories."""
    import shutil
    import stat

    def _on_error(func, fpath, _exc_info):
        try:
            os.chmod(fpath, stat.S_IWRITE)
            func(fpath)
        except Exception:
            pass

    shutil.rmtree(path, onerror=_on_error)


_TPS = 30          # reference throughput (tokens/second)
_TOKENS_PER_FILE = 35   # max output tokens per file in a batch
_PROJECT_SUMMARY_TOKENS = 400  # estimated output tokens for the project-level call


def _enrich_estimate(snapshot, batch_size: int = 8) -> str:
    """Return a human-readable LLM time estimate for first-time enrichment of a snapshot."""
    llm_files = sum(1 for rec in snapshot.files if not _trivial_summary(rec.path))
    if llm_files == 0:
        return "no LLM calls needed (all files auto-classified)"
    n_batches = (llm_files + batch_size - 1) // batch_size
    total_tokens = n_batches * batch_size * _TOKENS_PER_FILE + _PROJECT_SUMMARY_TOKENS
    seconds = total_tokens / _TPS
    if seconds < 60:
        time_str = f"~{int(seconds)}s"
    elif seconds < 3600:
        m, s = divmod(int(seconds), 60)
        time_str = f"~{m}m {s}s"
    else:
        time_str = f"~{seconds/3600:.1f}h"
    return f"{llm_files} files → {n_batches} batch(es) → {time_str} at {_TPS} t/s"


def _get_config(config_path: Optional[Path]) -> AtlasConfig:
    return load_config(config_path)


def _find_config_path(config_path: Optional[Path]) -> Optional[Path]:
    from .config import _find_config
    return config_path or _find_config()


def _resolve_project_root(project: str, store: AtlasStore) -> Optional[str]:
    """Resolve a name or path string to a stored project root, or None."""
    summaries = store.all_project_summaries()
    match = next((s for s in summaries if s.project_name == project), None)
    if match:
        return match.root
    matches = [s for s in summaries if project.lower() in s.project_name.lower()]
    if len(matches) == 1:
        return matches[0].root
    root_str = str(Path(project).expanduser().resolve())
    if root_str in store.get_project_roots():
        return root_str
    return None


def _get_store(cfg: AtlasConfig) -> AtlasStore:
    cfg.atlas_path.mkdir(parents=True, exist_ok=True)
    return AtlasStore(cfg.db_path)


# ------------------------------------------------------------------ #
# atlas init                                                           #
# ------------------------------------------------------------------ #

@app.command()
def init(
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
) -> None:
    """Create an example atlas.yaml config in the default location."""
    target = config or Path.home() / ".atlas" / "atlas.yaml"
    if target.exists():
        rprint(f"[yellow]Config already exists:[/yellow] {target}")
        raise typer.Exit(1)
    write_example_config(target)
    rprint(f"[green]Created config:[/green] {target}")
    rprint("Edit it to add your project paths, then run [bold]atlas scan[/bold].")


# ------------------------------------------------------------------ #
# atlas add                                                            #
# ------------------------------------------------------------------ #

@app.command()
def add(
    path: Annotated[str, typer.Argument(
        help="Local path, a Git repo URL, or a GitHub account URL (https://github.com/username)")],
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
    enrich_now: Annotated[bool, typer.Option("--enrich/--no-enrich",
        help="Run LLM enrichment immediately after scanning")] = False,
    no_llm: Annotated[bool, typer.Option("--no-llm")] = False,
    shallow: Annotated[bool, typer.Option("--shallow",
        help="Shallow clone (depth=1) for large repos")] = True,
    discover: Annotated[bool, typer.Option("--discover",
        help="Scan one level deep and add all project subdirectories found")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y",
        help="Skip confirmation prompt (for --discover)")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run",
        help="Preview what would be added without changing anything")] = False,
    include_forks: Annotated[bool, typer.Option("--include-forks",
        help="Include forked repositories (GitHub account mode)")] = False,
    force: Annotated[bool, typer.Option("--force",
        help="Add projects even if no code files are found (empty or non-code repos)")] = False,
) -> None:
    """Add a project to the atlas: register it, scan it, optionally enrich it.

    Three input modes:

        atlas add G:\\\\Development\\\\ragsistant                # local path
        atlas add https://github.com/owner/repo                  # single repo
        atlas add https://github.com/federicoandreis             # whole account
        atlas add G:\\\\Development --discover                   # parent folder
        atlas add G:\\\\Development --discover --dry-run         # preview only
    """
    from rich.console import Console
    cfg = _get_config(config)

    # ── GitHub account URL ────────────────────────────────────────────
    if _is_github_account_url(path):
        username = _github_username_from_url(path)
        rprint(f"Fetching repos for [bold]{username}[/bold]...")
        try:
            repos = _fetch_github_repos(username, include_forks=include_forks)
        except Exception as exc:
            rprint(f"[red]GitHub API error:[/red] {exc}")
            raise typer.Exit(1)

        if not repos:
            rprint(f"[yellow]No public repos found for {username}[/yellow]")
            raise typer.Exit(1)

        table = Table(title=f"github.com/{username} — {len(repos)} repos", show_lines=False)
        table.add_column("#", justify="right", style="dim")
        table.add_column("Name", style="bold")
        table.add_column("Description")
        table.add_column("Lang")
        table.add_column("Stars", justify="right")
        table.add_column("Updated")
        for i, r in enumerate(repos, 1):
            desc = (r.get("description") or "")[:55]
            if len(r.get("description") or "") > 55:
                desc += "…"
            updated = (r.get("updated_at") or "")[:10]
            table.add_row(
                str(i), r["name"], desc,
                r.get("language") or "—",
                str(r.get("stargazers_count", 0)),
                updated,
            )
        Console().print(table)

        if dry_run:
            rprint(f"\n[dim]dry-run: {len(repos)} repos shown. Re-run without --dry-run and enter selection to proceed.[/dim]")
            return

        raw = typer.prompt(
            "\nEnter numbers to add (e.g. 1,3,5-8), 'all', or Enter to cancel",
            default="",
        )
        selected = _parse_repo_selection(raw, len(repos))
        if selected is None:
            rprint("[yellow]Cancelled.[/yellow]")
            raise typer.Exit()

        urls_to_clone = [repos[i]["clone_url"] for i in selected]
        rprint(f"\nCloning {len(urls_to_clone)} repo(s)...")
        roots_to_add = []
        for url in urls_to_clone:
            try:
                root = _clone_or_update(url, cfg.atlas_path / "repos", shallow=shallow)
                roots_to_add.append(root)
                rprint(f"  [green]✓[/green] {root.name}")
            except Exception as exc:
                rprint(f"  [red]failed[/red] {url}: {exc}")

    # ── Single Git repo URL ───────────────────────────────────────────
    elif _is_git_url(path):
        if dry_run:
            rprint(f"[dim]dry-run:[/dim] would clone {path}")
            return
        root = _clone_or_update(path, cfg.atlas_path / "repos", shallow=shallow)
        rprint(f"[green]✓[/green] Repository ready at [dim]{root}[/dim]")
        roots_to_add = [root]

    # ── Local path ───────────────────────────────────────────────────
    else:
        root = Path(path).expanduser().resolve()
        if not root.exists():
            rprint(f"[red]Path not found:[/red] {root}")
            raise typer.Exit(1)
        if not root.is_dir():
            rprint(f"[red]Not a directory:[/red] {root}")
            raise typer.Exit(1)

        if discover or dry_run:
            candidates = _discover_projects(root, cfg)
            if not candidates:
                rprint(f"[yellow]No projects detected in[/yellow] {root}")
                rprint("Expected subdirectories with .git, a manifest file (pyproject.toml, package.json, etc.), or ≥3 source files.")
                raise typer.Exit(1)

            table = Table(title=f"Projects found in {root}", show_lines=False)
            table.add_column("Name", style="bold")
            table.add_column("Detected by")
            table.add_column("Source files (top 2 levels)", justify="right")
            for sub, signal in candidates:
                counts = _shallow_src_count(sub, cfg)
                file_summary = "  ".join(
                    f"{v} {k}" for k, v in sorted(counts.items(), key=lambda x: -x[1])[:3]
                ) or "—"
                table.add_row(sub.name, signal, file_summary)
            Console().print(table)

            if dry_run:
                rprint(f"\n[dim]{len(candidates)} project(s) would be added. Re-run without --dry-run to proceed.[/dim]")
                return

            if not yes:
                typer.confirm(f"\nAdd all {len(candidates)} project(s)?", abort=True)
            roots_to_add = [sub for sub, _ in candidates]
        else:
            roots_to_add = [root]

    # ── Scan + register ──────────────────────────────────────────────
    store = _get_store(cfg)
    scanner = ProjectScanner(cfg)
    config_path = None
    added = 0
    skipped: list[str] = []

    for project_root in roots_to_add:
        snapshot = scanner.scan(project_root)

        if len(snapshot.files) == 0 and not force:
            rprint(
                f"[yellow]⊘[/yellow] [bold]{project_root.name}[/bold]: "
                f"no code files found — skipped "
                f"[dim](use --force to add anyway)[/dim]"
            )
            skipped.append(project_root.name)
            continue

        config_path = add_project_to_config(str(project_root), config)
        store.upsert_snapshot(snapshot)
        added += 1
        langs = ', '.join(list(snapshot.languages.keys())[:3]) or 'unknown'
        rprint(
            f"[green]✓[/green] [bold]{project_root.name}[/bold]: "
            f"{len(snapshot.files)} files, {langs}"
        )
        if not enrich_now:
            rprint(f"  [dim]Enrich estimate: {_enrich_estimate(snapshot, cfg.llm.batch_size)}[/dim]")

        if enrich_now:
            if no_llm:
                cfg.llm.enabled = False
            with Enricher(cfg, store) as enricher:
                summary, _ = enricher.enrich_project(snapshot)
            rprint(f"     [green]✓[/green] {summary.one_liner}")

    if config_path:
        rprint(f"\n[green]✓[/green] Registered {added} project(s) in {config_path}")
    if skipped:
        rprint(f"[dim]Skipped {len(skipped)}: {', '.join(skipped)}[/dim]")

    if added == 0:
        return
    if enrich_now:
        rprint(f"Run [bold]atlas report[/bold] to regenerate PROJECTS_ATLAS.md.")
    else:
        rprint(f"Run [bold]atlas enrich[/bold] to add LLM summaries, then [bold]atlas report[/bold].")


# ------------------------------------------------------------------ #
# atlas remove                                                         #
# ------------------------------------------------------------------ #

@app.command()
def remove(
    project: Annotated[str, typer.Argument(help="Project name or path to remove")],
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation")] = False,
) -> None:
    """Remove a project from the atlas (scan data, summaries, and config entry)."""
    cfg = _get_config(config)
    store = _get_store(cfg)

    # Resolve: try as name first, then as path
    summaries = store.all_project_summaries()
    match = next((s for s in summaries if s.project_name == project), None)
    if match is None:
        # Try partial name match
        matches = [s for s in summaries if project.lower() in s.project_name.lower()]
        if len(matches) == 1:
            match = matches[0]
        elif len(matches) > 1:
            rprint(f"[yellow]Ambiguous:[/yellow] {', '.join(m.project_name for m in matches)}")
            raise typer.Exit(1)
    if match is None:
        # Try treating argument as a path
        root_str = str(Path(project).expanduser().resolve())
        roots = store.get_project_roots()
        if root_str in roots:
            match_root = root_str
            match_name = Path(root_str).name
        else:
            rprint(f"[red]Not found in atlas:[/red] {project}")
            rprint("Run [bold]atlas status[/bold] to see what's indexed.")
            raise typer.Exit(1)
    else:
        match_root = match.root
        match_name = match.project_name

    if not yes:
        typer.confirm(f"Remove '{match_name}' ({match_root}) from atlas?", abort=True)

    store.remove_project(match_root)

    # Also remove from atlas.yaml if present
    config_path = _find_config_path(config)
    if config_path and config_path.exists():
        import yaml as _yaml
        with open(config_path) as f:
            data = _yaml.safe_load(f) or {}
        projects = data.get("projects", [])
        before = len(projects)
        data["projects"] = [p for p in projects if str(Path(p).expanduser().resolve()) != match_root]
        if len(data["projects"]) < before:
            with open(config_path, "w") as f:
                _yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    rprint(f"[green]✓[/green] Removed [bold]{match_name}[/bold] from atlas.")


# ------------------------------------------------------------------ #
# atlas reset                                                          #
# ------------------------------------------------------------------ #

@app.command()
def reset(
    project: Annotated[str, typer.Argument(help="Project name or path")],
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
) -> None:
    """Delete cached LLM summaries for a project, forcing full re-enrichment on next run.

    Scan data and file records are kept. Use this when summaries are stale or wrong.
    """
    cfg = _get_config(config)
    store = _get_store(cfg)

    root_str = _resolve_project_root(project, store)
    if root_str is None:
        rprint(f"[red]Not found in atlas:[/red] {project}")
        raise typer.Exit(1)

    name = Path(root_str).name
    if not yes:
        typer.confirm(f"Delete LLM summaries for '{name}'? (scan data kept)", abort=True)

    n = store.reset_summaries(root_str)
    rprint(f"[green]✓[/green] Cleared {n} cached summaries for [bold]{name}[/bold].")
    rprint("Run [bold]atlas enrich[/bold] to regenerate.")


# ------------------------------------------------------------------ #
# atlas clear                                                          #
# ------------------------------------------------------------------ #

@app.command()
def clear(
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
    repos: Annotated[bool, typer.Option("--repos",
        help="Also delete managed clones (~/.atlas/repos/) and remove entries from atlas.yaml"
    )] = False,
) -> None:
    """Wipe the entire atlas database.

    By default only clears the database (atlas.yaml is kept).
    Use --repos to also delete cloned repos on disk and purge atlas.yaml entries.
    """
    cfg = _get_config(config)
    store = _get_store(cfg)

    all_roots = store.get_project_roots()
    repos_dir = cfg.atlas_path / "repos"

    if repos:
        managed = [r for r in all_roots
                   if Path(r).parent == repos_dir or repos_dir in Path(r).parents]
        local = [r for r in all_roots if r not in managed]
        n_managed = len(managed)
        n_local = len(local)
        disk_exists = repos_dir.exists()

        if not all_roots and not disk_exists:
            rprint("[dim]Atlas is already empty.[/dim]")
            return

        if not yes:
            parts = [f"{len(all_roots)} project(s) from the database"]
            if disk_exists:
                parts.append(f"cloned repos in {repos_dir}")
            if managed:
                parts.append(f"{n_managed} managed project(s) from atlas.yaml")
            typer.confirm(
                f"This will remove: {', '.join(parts)}.\n"
                f"Local projects ({n_local}) will be removed from the DB but kept on disk.\n"
                f"Proceed?",
                abort=True,
            )

        store.clear_all()
        rprint("[green]✓[/green] Atlas database cleared.")

        if disk_exists:
            _rmtree(repos_dir)
            rprint(f"[green]✓[/green] Deleted {repos_dir}")

        if managed:
            cfg_path, n = remove_projects_from_config(managed, config)
            rprint(f"[green]✓[/green] Removed {n} managed project(s) from {cfg_path}")

        rprint("Run [bold]atlas add <path>[/bold] to start fresh.")
        return

    # Default: DB only
    if not all_roots:
        rprint("[dim]Atlas is already empty.[/dim]")
        return

    if not yes:
        typer.confirm(
            f"Wipe ALL atlas data ({len(all_roots)} projects)? "
            f"atlas.yaml and cloned repos are untouched.",
            abort=True,
        )

    store.clear_all()
    rprint("[green]✓[/green] Atlas database cleared.")
    rprint("Run [bold]atlas add <path>[/bold] to start fresh.")


# ------------------------------------------------------------------ #
# atlas scan                                                           #
# ------------------------------------------------------------------ #

@app.command()
def scan(
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
    project: Annotated[Optional[list[str]], typer.Option("--project", "-p",
        help="Scan specific project path(s) instead of config list")] = None,
) -> None:
    """Re-scan projects already registered in atlas.yaml. No LLM.

    Prefer [bold]atlas add[/bold] for new projects and [bold]atlas update[/bold] for incremental
    refresh. Use scan only if you manually edited atlas.yaml and want to refresh scan data
    without running enrichment.
    """
    cfg = _get_config(config)
    store = _get_store(cfg)
    scanner = ProjectScanner(cfg)

    roots: list[Path] = []
    if project:
        roots = [Path(p).resolve() for p in project]
    elif cfg.projects:
        roots = [Path(p).expanduser().resolve() for p in cfg.projects]
    else:
        rprint("[red]No projects configured.[/red] Run [bold]atlas init[/bold] first.")
        raise typer.Exit(1)

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), transient=True) as progress:
        for root in roots:
            if not root.exists():
                rprint(f"[yellow]Skipping (not found):[/yellow] {root}")
                continue
            task = progress.add_task(f"Scanning {root.name}...")
            snapshot = scanner.scan(root)
            store.upsert_snapshot(snapshot)
            progress.remove_task(task)
            rprint(
                f"[green]✓[/green] {snapshot.name}: "
                f"{len(snapshot.files)} files, "
                f"{', '.join(list(snapshot.languages.keys())[:3])}"
            )

    rprint(f"\n[bold]Scan complete.[/bold] Run [bold]atlas enrich[/bold] to add LLM summaries.")


# ------------------------------------------------------------------ #
# atlas enrich                                                         #
# ------------------------------------------------------------------ #

@app.command()
def enrich(
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
    project: Annotated[Optional[list[str]], typer.Option("--project", "-p",
        help="Enrich specific project(s) by name or path. Repeatable. Default: all.")] = None,
    no_llm: Annotated[bool, typer.Option("--no-llm",
        help="Skip LLM; generate stub summaries from static data only")] = False,
) -> None:
    """Pass 2: LLM enrichment — batched, cached by file hash. Skips unchanged files."""
    cfg = _get_config(config)
    if no_llm:
        cfg.llm.enabled = False
    store = _get_store(cfg)
    scanner = ProjectScanner(cfg)

    db_roots = store.get_project_roots()
    if not db_roots:
        rprint("[red]No projects scanned yet.[/red] Run [bold]atlas add <path>[/bold] first.")
        raise typer.Exit(1)

    if project:
        resolved = []
        for p in project:
            root_str = _resolve_project_root(p, store)
            if root_str is None:
                rprint(f"[red]Project not found:[/red] {p}")
                raise typer.Exit(1)
            resolved.append(root_str)
        db_roots = resolved

    try:
        with Enricher(cfg, store) as enricher:
            for root_str in db_roots:
                root = Path(root_str)
                if not root.exists():
                    rprint(f"[yellow]Skipping (not found):[/yellow] {root_str}")
                    continue
                snapshot = scanner.scan(root)
                summary, was_new = enricher.enrich_project(snapshot)
                if was_new:
                    rprint(f"[green]✓[/green] [bold]{root.name}[/bold]: {summary.one_liner}")
                else:
                    rprint(f"  [dim]{root.name}: up to date[/dim]")
    except KeyboardInterrupt:
        rprint("\n[yellow]Enrichment interrupted. Cached progress is saved.[/yellow]")
        raise typer.Exit(130)

    rprint(f"\n[bold]Enrichment complete.[/bold] Run [bold]atlas report[/bold] to generate outputs.")


# ------------------------------------------------------------------ #
# atlas report                                                         #
# ------------------------------------------------------------------ #

@app.command()
def report(
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
) -> None:
    """Pass 3: generate PROJECTS_ATLAS.md and graph.json from cached data."""
    cfg = _get_config(config)
    store = _get_store(cfg)
    reporter = Reporter(store)

    atlas_md = cfg.atlas_md_path
    reporter.write_projects_atlas(atlas_md)
    rprint(f"[green]✓[/green] {atlas_md}")

    graph_json = cfg.atlas_path / "graph.json"
    reporter.write_graph_json(graph_json)
    rprint(f"[green]✓[/green] {graph_json}")

    summaries = store.all_project_summaries()
    rprint(f"\n[bold]{len(summaries)} projects[/bold] in atlas.")


# ------------------------------------------------------------------ #
# atlas install                                                        #
# ------------------------------------------------------------------ #

@app.command()
def install(
    platform: Annotated[str, typer.Argument(
        help=f"Target platform: {', '.join(SUPPORTED_PLATFORMS)}, or 'all'"
    )],
    target: Annotated[Optional[Path], typer.Option("--target", "-t",
        help="Target directory (default: current directory)")] = None,
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
) -> None:
    """Install coding assistant integration for a platform."""
    cfg = _get_config(config)
    store = _get_store(cfg)
    installer = Installer(store, cfg.atlas_path)

    base = (target or Path.cwd()).resolve()
    rprint(f"Installing [bold]atlas → {platform}[/bold] in {base}")

    written = installer.install(platform, base)
    for path in written:
        rprint(f"  [green]✓[/green] {path}")

    rprint(f"\n[bold]Done.[/bold] {len(written)} file(s) written.")


# ------------------------------------------------------------------ #
# atlas update                                                         #
# ------------------------------------------------------------------ #

@app.command()
def update(
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
) -> None:
    """Incremental update: re-scan + re-enrich only changed files, then regenerate reports."""
    cfg = _get_config(config)
    store = _get_store(cfg)
    scanner = ProjectScanner(cfg)

    roots = [Path(r) for r in store.get_project_roots()]
    if not roots:
        rprint("[yellow]No projects in atlas yet.[/yellow] Run [bold]atlas add <path>[/bold] first.")
        raise typer.Exit()
    changed_projects = 0

    repos_dir = cfg.atlas_path / "repos"

    try:
        with Enricher(cfg, store) as enricher:
            for root in roots:
                if not root.exists():
                    rprint(f"[yellow]Skipping (not found):[/yellow] {root}")
                    continue
                # Pull latest if this is a managed remote clone
                if repos_dir in root.parents or root.parent == repos_dir:
                    _pull_if_remote(root)
                old_hashes = store.get_file_hashes(str(root))
                snapshot = scanner.scan(root)
                new_hashes = {r.relative_path: r.sha256 for r in snapshot.files}
                changed = [
                    r for r in snapshot.files
                    if old_hashes.get(r.relative_path) != new_hashes.get(r.relative_path)
                ]
                if changed:
                    store.upsert_snapshot(snapshot)
                    enricher.enrich_project(snapshot)
                    changed_projects += 1
                    rprint(f"[green]✓[/green] {root.name}: {len(changed)} file(s) changed")
                else:
                    rprint(f"  {root.name}: no changes")
    except KeyboardInterrupt:
        rprint("\n[yellow]Update interrupted. Cached progress is saved.[/yellow]")
        raise typer.Exit(130)

    if changed_projects:
        reporter = Reporter(store)
        reporter.write_projects_atlas(cfg.atlas_md_path)
        rprint(f"\n[green]✓[/green] Atlas updated.")
    else:
        rprint("\nNo changes detected.")


# ------------------------------------------------------------------ #
# atlas health                                                         #
# ------------------------------------------------------------------ #

@app.command()
def health(
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
    fix: Annotated[bool, typer.Option("--fix",
        help="Auto-remove dead paths (safe). All other issues require manual action.")] = False,
) -> None:
    """Diagnose atlas health: dead paths, missing summaries, duplicates, similar content."""
    import difflib
    from rich.console import Console

    cfg = _get_config(config)
    store = _get_store(cfg)

    with store._conn() as conn:
        projects = conn.execute(
            "SELECT name, root, git_remote_url, git_commit_count FROM projects"
        ).fetchall()
        file_counts = {
            r["project_root"]: r["cnt"]
            for r in conn.execute(
                "SELECT project_root, COUNT(*) as cnt FROM files GROUP BY project_root"
            ).fetchall()
        }

    all_summaries = store.all_project_summaries()
    summarized = {s.project_name for s in all_summaries}
    # keywords + tech_stack from each enriched card, used for content-similarity check
    summary_terms: dict[str, set[str]] = {
        s.project_name: set(s.keywords) | set(s.tech_stack)
        for s in all_summaries
    }

    if not projects:
        rprint("[yellow]No projects in atlas.[/yellow]")
        raise typer.Exit()

    # ── Collect issues ────────────────────────────────────────────────
    errors:  list[tuple[str, str, str | None]] = []  # (kind, message, fix_cmd)
    warns:   list[tuple[str, str, str | None]] = []
    infos:   list[tuple[str, str, str | None]] = []

    dead = []
    for p in projects:
        root = p["root"]
        name = p["name"]

        # Dead path
        if not Path(root).exists():
            errors.append(("dead_path", f"{name}: path not found ({root})", f"atlas remove {name}"))
            dead.append(name)
            continue

        # No files recorded
        if file_counts.get(root, 0) == 0:
            warns.append(("empty", f"{name}: 0 files recorded — scan may have failed",
                          f"atlas add {root}"))

        # Scanned but not enriched
        if name not in summarized:
            warns.append(("no_summary", f"{name}: scanned but not enriched",
                          f"atlas enrich --project {name}"))

    # Duplicate git remotes
    remote_map: dict[str, list[str]] = {}
    for p in projects:
        raw = (p["git_remote_url"] or "").strip()
        if not raw:
            continue
        norm = _normalize_remote(raw)
        remote_map.setdefault(norm, []).append(p["name"])
    for norm, names in remote_map.items():
        if len(names) > 1:
            warns.append((
                "duplicate_remote",
                f"Same git remote shared by: {', '.join(names)}\n"
                f"  ({norm})\n"
                f"  Likely the same project cloned to multiple locations.",
                f"atlas remove {names[-1]}",
            ))

    # Possibly duplicate projects — compare card content and prefix-stripped names.
    # Name-only comparison is unreliable when all projects share a common prefix
    # (e.g. all cloned from the same GitHub account as "username-reponame").
    project_names = [p["name"] for p in projects]
    common_pfx = _common_name_prefix(project_names)
    seen_pairs: set[frozenset] = set()
    for i, a in enumerate(project_names):
        for b in project_names[i + 1:]:
            pair = frozenset({a, b})
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            reasons: list[str] = []

            # Name similarity — only on the part after the common prefix,
            # and only when both stripped names are long enough to be meaningful.
            a_stripped = a[len(common_pfx):] if a.lower().startswith(common_pfx) else a
            b_stripped = b[len(common_pfx):] if b.lower().startswith(common_pfx) else b
            if len(a_stripped) >= 5 and len(b_stripped) >= 5:
                name_sim = difflib.SequenceMatcher(
                    None, a_stripped.lower(), b_stripped.lower()
                ).ratio()
                if name_sim >= 0.80:
                    reasons.append(f"name similarity {name_sim:.0%}")

            # Content similarity — Jaccard on keywords + tech_stack from enriched cards.
            a_terms = summary_terms.get(a, set())
            b_terms = summary_terms.get(b, set())
            if a_terms and b_terms:
                shared = a_terms & b_terms
                jaccard = len(shared) / len(a_terms | b_terms)
                if jaccard >= 0.55 and len(shared) >= 7:
                    reasons.append(
                        f"content overlap {jaccard:.0%} ({len(shared)} shared terms)"
                    )

            if reasons:
                infos.append((
                    "similar_project",
                    f"Possibly duplicate: '{a}' and '{b}' ({', '.join(reasons)})",
                    None,
                ))

    # ── Render report ─────────────────────────────────────────────────
    con = Console()
    total = len(errors) + len(warns) + len(infos)

    if total == 0:
        rprint("[green]✓ Atlas looks healthy.[/green] No issues found.")
        return

    severity_color = {"error": "red", "warn": "yellow", "info": "dim"}

    for severity, items in (("error", errors), ("warn", warns), ("info", infos)):
        for kind, msg, fix_cmd in items:
            label = f"[{severity_color[severity]}][{severity.upper()}][/{severity_color[severity]}]"
            rprint(f"{label} {msg}")
            if fix_cmd:
                rprint(f"       [dim]→ {fix_cmd}[/dim]")
    rprint()

    n_err, n_warn, n_info = len(errors), len(warns), len(infos)
    rprint(
        f"[bold]{total} issue(s):[/bold] "
        f"[red]{n_err} error[/red], "
        f"[yellow]{n_warn} warning[/yellow], "
        f"[dim]{n_info} info[/dim]"
    )

    # ── Auto-fix ──────────────────────────────────────────────────────
    if fix:
        if not dead:
            rprint("\n[dim]--fix: nothing safe to auto-remove.[/dim]")
            return
        rprint(f"\n[bold]--fix:[/bold] removing {len(dead)} dead project(s)...")
        for name in dead:
            removed = store.remove_project_by_name(name)
            if removed:
                rprint(f"  [green]✓[/green] removed {name}")
        rprint("[dim]Re-run atlas health to check remaining issues.[/dim]")
    elif errors:
        rprint("\n[dim]Run with --fix to auto-remove dead paths.[/dim]")


def _common_name_prefix(names: list[str]) -> str:
    """Longest separator-aligned prefix shared by all project names (case-insensitive).

    Used to strip e.g. 'username-' before comparing GitHub-cloned project names,
    so that 'alice-foo' and 'alice-bar' aren't flagged as similar due to the shared owner prefix.
    """
    if len(names) < 2:
        return ""
    raw = os.path.commonprefix([n.lower() for n in names])
    for sep in ("-", "_", "."):
        idx = raw.rfind(sep)
        if idx >= 0:
            return raw[:idx + 1]
    return ""


def _normalize_remote(url: str) -> str:
    """Normalise a git remote URL so ssh and https variants compare equal."""
    url = url.strip().rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    # git@github.com:user/repo → https://github.com/user/repo
    if url.startswith("git@github.com:"):
        url = "https://github.com/" + url[len("git@github.com:"):]
    return url.lower()


# ------------------------------------------------------------------ #
# atlas query                                                          #
# ------------------------------------------------------------------ #

@app.command()
def query(
    question: Annotated[str, typer.Argument(help="What to search for")],
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
) -> None:
    """Search the atlas without opening a full session."""
    cfg = _get_config(config)
    store = _get_store(cfg)
    summaries = store.all_project_summaries()

    q_lower = question.lower()
    results = []
    for s in summaries:
        score = 0
        text = " ".join([s.one_liner, s.description] + s.tech_stack + s.keywords + s.key_patterns)
        for word in q_lower.split():
            if word in text.lower():
                score += 1
        if score > 0:
            results.append((score, s))

    results.sort(key=lambda x: -x[0])

    if not results:
        rprint(f"[yellow]No projects matched:[/yellow] {question}")
        raise typer.Exit()

    table = Table(title=f'Results for "{question}"')
    table.add_column("Project", style="bold")
    table.add_column("Match")
    table.add_column("Purpose")
    table.add_column("Reuse hints")

    for score, s in results[:5]:
        table.add_row(
            s.project_name,
            f"{score} terms",
            s.one_liner,
            "; ".join(s.reuse_hints[:2]) or "—",
        )
    rprint(table)


# ------------------------------------------------------------------ #
# atlas show                                                           #
# ------------------------------------------------------------------ #

@app.command()
def show(
    project: Annotated[Optional[str], typer.Argument(
        help="Project name to show (omit to list all)")] = None,
    files: Annotated[bool, typer.Option("--files", "-f",
        help="Also show individual file summaries")] = False,
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
) -> None:
    """Inspect enriched project data in the terminal."""
    cfg = _get_config(config)
    store = _get_store(cfg)

    if project is None:
        summaries = store.all_project_summaries()
        if not summaries:
            rprint("[yellow]No projects enriched yet.[/yellow] Run [bold]atlas enrich[/bold] first.")
            raise typer.Exit()
        for s in summaries:
            rprint(Panel(
                f"[bold]{s.one_liner}[/bold]\n\n"
                f"{s.description}\n\n"
                f"[cyan]Stack:[/cyan] {', '.join(s.tech_stack) or '—'}\n"
                f"[cyan]Patterns:[/cyan] {', '.join(s.key_patterns) or '—'}\n"
                f"[cyan]Reuse:[/cyan] {'; '.join(s.reuse_hints) or '—'}\n"
                f"[dim]Keywords: {' '.join(s.keywords[:12])}[/dim]",
                title=f"[bold green]{s.project_name}[/bold green]  [dim]{s.root}[/dim]",
                expand=False,
            ))
        return

    summary = store.get_project_summary(project)
    if summary is None:
        # Try partial match
        all_summaries = store.all_project_summaries()
        matches = [s for s in all_summaries if project.lower() in s.project_name.lower()]
        if len(matches) == 1:
            summary = matches[0]
        elif len(matches) > 1:
            rprint(f"[yellow]Ambiguous:[/yellow] {', '.join(m.project_name for m in matches)}")
            raise typer.Exit(1)
        else:
            rprint(f"[red]Project not found:[/red] {project}")
            raise typer.Exit(1)

    rprint(Panel(
        f"[bold]{summary.one_liner}[/bold]\n\n"
        f"{summary.description}\n\n"
        f"[cyan]Stack:[/cyan]    {', '.join(summary.tech_stack) or '—'}\n"
        f"[cyan]Patterns:[/cyan] {', '.join(summary.key_patterns) or '—'}\n"
        f"[cyan]Reuse:[/cyan]    {chr(10).join('• ' + h for h in summary.reuse_hints) or '—'}\n\n"
        f"[dim]Keywords: {' '.join(summary.keywords)}[/dim]",
        title=f"[bold green]{summary.project_name}[/bold green]  [dim]{summary.root}[/dim]",
    ))

    if files:
        file_summaries = store.get_file_summaries_for_project(summary.project_name)
        if not file_summaries:
            rprint("[dim]No file summaries cached.[/dim]")
            return
        table = Table(title=f"File summaries ({len(file_summaries)} files)", show_lines=False)
        table.add_column("File", style="dim", max_width=50)
        table.add_column("Summary")
        for fs in sorted(file_summaries, key=lambda x: x.file_path):
            fname = Path(fs.file_path).name
            table.add_row(fname, fs.summary)
        rprint(table)


# ------------------------------------------------------------------ #
# atlas status                                                         #
# ------------------------------------------------------------------ #

@app.command()
def status(
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
) -> None:
    """Show atlas status — projects indexed, last update, atlas location."""
    cfg = _get_config(config)
    store = _get_store(cfg)
    summaries = store.all_project_summaries()

    rprint(f"[bold]Atlas directory:[/bold] {cfg.atlas_path}")
    rprint(f"[bold]Database:[/bold] {cfg.db_path} ({'exists' if cfg.db_path.exists() else 'missing'})")
    rprint(f"[bold]PROJECTS_ATLAS.md:[/bold] {cfg.atlas_md_path} ({'exists' if cfg.atlas_md_path.exists() else 'missing'})")
    rprint(f"[bold]Projects indexed:[/bold] {len(summaries)}")

    if summaries:
        table = Table()
        table.add_column("Project", style="bold")
        table.add_column("One-liner")
        table.add_column("Last enriched")
        for s in summaries:
            table.add_row(s.project_name, s.one_liner[:60], s.summarized_at.strftime("%Y-%m-%d"))
        rprint(table)
