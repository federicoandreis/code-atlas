# code-atlas

> Fully local code project knowledge base for AI coding assistants.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

**The problem:** Every time you open a new coding session, your AI assistant asks the same questions — what's in this codebase? what libraries do you use? have you solved this before? It wastes tokens and time re-discovering what you already built.

**The fix:** code-atlas scans your projects once, generates compact summaries with a local LLM, and writes a single `PROJECTS_ATLAS.md` that your coding assistant reads at session start. Your whole portfolio, one page, zero cloud.

---

## How it works

Three passes, run once and cached:

```
Pass 1 — Static analysis            no LLM, instant
  ├─ tree-sitter          → functions, classes, imports
  ├─ manifest files       → pyproject.toml / package.json / go.mod / Cargo.toml / …
  ├─ git log              → remote URL, commit count, last activity
  └─ README               → raw text for context

Pass 2 — LLM enrichment             local, batched, SHA-256 cached
  ├─ file summaries       → one sentence per file, batched 8 at a time
  └─ project summary      → one-liner · description · tech stack · reuse hints
     Unchanged files are NEVER re-summarised — hash-keyed cache in SQLite

Pass 3 — Output                     free, instant
  ├─ ~/.atlas/PROJECTS_ATLAS.md     inject into any coding assistant
  ├─ ~/.atlas/graph.json            machine-readable project graph
  └─ platform shims                 AGENTS.md skill cards (little-coder) + others
```

Everything lives in `~/.atlas/` — one SQLite file, no Docker, no Neo4j, no cloud.

---

## Prerequisites

- **Python ≥ 3.11**
- **A local OpenAI-compatible LLM server.** [llama.cpp](https://github.com/ggerganov/llama.cpp) is recommended:

  ```bash
  llama-server -m your-model.gguf --port 8080 -ngl 99
  ```

  The default config points to `http://127.0.0.1:8080/v1`. Any instruction-following model works; 7B+ gives the best summaries.

  > **No LLM?** Set `llm.enabled: false` in `atlas.yaml` to generate stub summaries from static data only (languages, dependencies, file names) — no server needed.

- **git** on PATH — optional, used for cloning remote repos and reading commit metadata.

---

## Install

Clone and install in editable mode:

```bash
git clone https://github.com/yourusername/code-atlas
cd code-atlas
pip install -e .
```

---

## Walkthrough

A complete example from a clean slate to a working atlas.

### 1. Create the config

```bash
atlas init
```

Creates `~/.atlas/atlas.yaml` with defaults. Edit it to adjust your LLM endpoint, batch size, or ignore patterns — or leave it as-is and use `atlas add` to manage projects.

### 2. Add your projects

**From a local directory:**

```bash
atlas add ~/dev/my-api
```

```
✓ my-api: 24 files, python
  Enrich estimate: 18 files → 3 batch(es) → ~42s at 30 t/s
✓ Registered 1 project(s) in ~/.atlas/atlas.yaml
Run atlas enrich to add LLM summaries, then atlas report.
```

**From a parent folder — detect all sub-projects at once:**

```bash
atlas add ~/dev --discover
```

```
          Projects found in /home/you/dev
┏━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Name              ┃ Detected by  ┃ Source files (top 2 levels) ┃
┡━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ my-api            │ git repo     │ 19 .py  3 .yaml             │
│ admin-dashboard   │ package.json │ 42 .tsx  8 .ts              │
│ data-pipeline     │ pyproject... │ 11 .py                      │
└───────────────────┴──────────────┴─────────────────────────────┘

Add all 3 project(s)? [y/N]: y
```

Use `--dry-run` to preview without registering anything. Use `--yes` to skip the prompt in scripts.

**From a GitHub repo or account:**

```bash
atlas add https://github.com/owner/some-repo      # clone a single repo
atlas add https://github.com/yourusername         # browse and pick from a whole account
```

For the account form, a numbered table of repos is shown; enter `1,3,5-8` or `all` to select which ones to clone and register.

### 3. Enrich

Start your LLM server, then:

```bash
atlas enrich
```

```
Enriching my-api...
  batch 1-8/18  14s
  batch 9-16/18  13s
  batch 17-18/18  5s
  ✓ FastAPI service handling user authentication and session management.
Enriching data-pipeline...
  batch 1-8/11  12s
  ✓ ETL pipeline that ingests CSV exports and writes to a PostgreSQL warehouse.

Enrichment complete. Run atlas report to generate outputs.
```

Enrichment is incremental — only files whose SHA-256 has changed since the last run are re-processed. Re-running `atlas enrich` on an already-indexed project is fast.

### 4. Generate the atlas

```bash
atlas report
```

```
✓ /home/you/.atlas/PROJECTS_ATLAS.md
✓ /home/you/.atlas/graph.json

2 projects in atlas.
```

### 5. Inspect results

```bash
atlas show my-api
```

```
╭─ my-api  ~/dev/my-api ────────────────────────────────────────────────────╮
│ FastAPI service handling user authentication and session management        │
│                                                                            │
│ REST API providing registration, login, JWT refresh, and OAuth2 flow.     │
│ Designed as a standalone microservice; downstream services call it via     │
│ HTTP rather than embedding auth logic directly.                            │
│                                                                            │
│ Stack:    FastAPI, SQLAlchemy, PostgreSQL, Redis, Pydantic                 │
│ Patterns: dependency injection, repository pattern, async handlers         │
│ Reuse:  • JWT middleware (reusable in any FastAPI project)                 │
│         • Redis-backed rate-limiter decorator (~50 lines)                  │
│                                                                            │
│ Keywords: fastapi python auth jwt postgresql redis microservice            │
╰────────────────────────────────────────────────────────────────────────────╯
```

`atlas show` with no argument lists all projects. Add `--files` to include per-file summaries.

### 6. Check atlas health

```bash
atlas health
```

```
[WARN]  data-pipeline: scanned but not enriched
        → atlas enrich --project data-pipeline
[INFO]  Similar names: 'my-api' and 'my-api-v2' (similarity 83%)

2 issue(s): 0 errors, 1 warning, 1 info

Run with --fix to auto-remove dead paths.
```

`--fix` automatically removes entries whose paths no longer exist on disk. Other issues require manual action.

### 7. Wire into your coding assistant

```bash
# From the root of any project where you want the atlas available:
atlas install little-coder
```

This writes per-project skill cards to `skills/knowledge/atlas-*.md` and appends a `## Project Atlas` section to `AGENTS.md`. little-coder's knowledge-inject extension picks them up automatically.

---

## Command reference

### Project management

| Command | Description |
|---------|-------------|
| `atlas add <path>` | Register and scan a local project |
| `atlas add <path> --discover` | Auto-detect sub-projects one level deep |
| `atlas add <git-url>` | Clone a repo and register it |
| `atlas add <github-account-url>` | Browse public repos, select by number/range/`all` |
| `atlas add ... --dry-run` | Preview without writing anything |
| `atlas add ... --enrich` | Scan + enrich in one step |
| `atlas remove <name>` | Remove from atlas (DB + atlas.yaml entry) |
| `atlas update` | Incremental re-scan + re-enrich for all changed files |

### Analysis pipeline

| Command | Description |
|---------|-------------|
| `atlas scan` | Re-scan projects already in atlas.yaml (no LLM) |
| `atlas enrich` | LLM pass — batched, skips unchanged files |
| `atlas enrich -p <name>` | Enrich one project only |
| `atlas report` | Write `PROJECTS_ATLAS.md` + `graph.json` |

### Query and inspect

| Command | Description |
|---------|-------------|
| `atlas query <words>` | Keyword search across all enriched projects |
| `atlas show` | All project cards |
| `atlas show <name>` | One project card |
| `atlas show <name> --files` | Include per-file summaries |
| `atlas status` | Atlas location, DB path, indexed project list |
| `atlas health` | Diagnose dead paths, missing summaries, duplicates |
| `atlas health --fix` | Auto-remove dead-path entries |

### Maintenance

| Command | Description |
|---------|-------------|
| `atlas init` | Create example `~/.atlas/atlas.yaml` |
| `atlas install <platform>` | Write coding assistant integration files |
| `atlas reset <name>` | Delete cached LLM summaries (force re-enrichment next run) |
| `atlas clear` | Wipe the database (atlas.yaml kept) |
| `atlas clear --repos` | Also delete managed clones + purge atlas.yaml entries |

### `atlas add` flags

| Flag | Description |
|------|-------------|
| `--discover` | Scan one level deep; show detected projects and prompt to add |
| `--yes` / `-y` | Skip confirmation prompts (scripting) |
| `--dry-run` | Preview without writing anything |
| `--enrich` | Run LLM enrichment immediately after scan |
| `--no-llm` | Generate stub summaries only |
| `--shallow` | Shallow clone depth=1 (default for URL inputs) |
| `--include-forks` | Include forked repos in GitHub account mode |
| `--force` | Register even if no code files are found |

---

## Configuration

`atlas init` creates `~/.atlas/atlas.yaml`:

```yaml
projects:
  - ~/dev/my-api
  - ~/dev/data-pipeline

atlas_dir: ~/.atlas

llm:
  base_url: http://127.0.0.1:8080/v1
  model: your-model-name
  timeout: 120
  batch_size: 8        # files per LLM call — increase for faster hardware
  enabled: true

ignore_patterns:
  - .git
  - __pycache__
  - node_modules
  - .venv
  - dist
  - build
  - "*.egg-info"
```

Config resolution order: `ATLAS_CONFIG` env var → `./atlas.yaml` → `~/.atlas/atlas.yaml`.

### Supported languages

Python, TypeScript, JavaScript (JSX/TSX), Go, Rust, Java, Ruby, C/C++, C#, Kotlin, R, Scala, Swift, Lua, Jupyter notebooks (`.ipynb`), Quarto (`.qmd`), R Markdown (`.Rmd`), LaTeX (`.tex`).

---

## Coding assistant integration

### little-coder (tested)

```bash
atlas install little-coder
```

Writes to the current directory:

- `skills/knowledge/atlas-index.md` — lightweight index of all projects
- `skills/knowledge/atlas-<project>.md` — full skill card per project
- `AGENTS.md` — appends a `## Project Atlas` section

little-coder's knowledge-inject extension picks up the skill cards automatically and injects them when relevant to the current task.

### Other platforms (basic, untested)

The following integrations write the appropriate config snippet pointing at `PROJECTS_ATLAS.md`. They have not been tested against the actual tools.

| Platform | Files written |
|----------|--------------|
| `claude` | Appends `## Project Atlas` section to `CLAUDE.md` |
| `opencode` | `agents.md` section + `.opencode/plugins/atlas.js` |
| `cursor` | Appends to `.cursorrules` |
| `cline` | Appends to `.clinerules` |
| `generic` | Copies `PROJECTS_ATLAS.md` into the target directory |

PRs with real-world testing notes are welcome.

---

## License

MIT
