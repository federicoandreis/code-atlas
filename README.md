# code-atlas

> Fully local code project knowledge base for AI coding assistants.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/code-atlas)](https://pypi.org/project/code-atlas/)

---

**The problem:** Every time you open a new coding session, your AI assistant asks the same questions — what's in this codebase? what libraries do you use? have you solved this before? It wastes tokens and time rediscovering what you already built.

**The fix:** code-atlas scans your projects once, generates compact summaries with a local LLM, and writes a single `PROJECTS_ATLAS.md` that coding assistants read at session start. Your whole portfolio, one page, zero cloud.

```
$ atlas query "authentication middleware"

┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Project          ┃ Match       ┃ Purpose                                  ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ user-api         │ 4 terms     │ FastAPI service for user auth and         │
│                  │             │ session management                        │
│ auth-middleware  │ 3 terms     │ Reusable JWT auth layer for Python APIs   │
│ admin-dashboard  │ 2 terms     │ React admin panel with role-based access  │
└──────────────────┴─────────────┴───────────────────────────────────────────┘
```

---

## How it works

Three passes, run once, results cached forever:

```
Pass 1 — Static analysis            no LLM, instant
  ├─ tree-sitter          → functions, classes, imports
  ├─ manifest files       → pyproject.toml / package.json / go.mod / Cargo.toml / …
  ├─ git log              → remote URL, commit count, last activity date
  └─ README               → raw text for context

Pass 2 — LLM enrichment             local, batched, SHA-256 cached
  ├─ file summaries       → "what does this module do?" (15 words, batched 8/call)
  └─ project summary      → one-liner · description · tech stack · reuse hints
     Unchanged files are NEVER re-summarised — hash-keyed cache in SQLite

Pass 3 — Output                     free, instant
  ├─ ~/.atlas/PROJECTS_ATLAS.md     inject into any coding assistant
  ├─ ~/.atlas/graph.json            machine-readable project graph
  └─ platform shims                 CLAUDE.md / .cursorrules / AGENTS.md / …
```

Everything lives in `~/.atlas/` — one SQLite file, no Docker, no Neo4j, no cloud.

---

## Prerequisites

- **Python ≥ 3.11**
- **A local LLM server** — any OpenAI-compatible endpoint works. [llama.cpp](https://github.com/ggerganov/llama.cpp) is recommended:

  ```bash
  llama-server -m your-model.gguf --port 8080 -ngl 99
  ```

  The default config points to `http://127.0.0.1:8080/v1`. Any model that follows instructions will produce good results; 7B+ is recommended for project summaries.

  > **No LLM?** Set `llm.enabled: false` in `atlas.yaml` and code-atlas will generate stub summaries from static data only (tech stack, languages, dependencies).

- **git** on PATH — optional, used for cloning remote repos and reading commit metadata.

---

## Install

```bash
pip install code-atlas
```

From source:

```bash
git clone https://github.com/yourusername/code-atlas
cd code-atlas
pip install -e .
```

---

## Quick start

```bash
# 1. Initialise
atlas init                                  # creates ~/.atlas/atlas.yaml

# 2. Add your projects — pick any input style
atlas add ~/dev/my-api                      # local directory
atlas add https://github.com/owner/repo    # clone a single repo
atlas add https://github.com/yourusername  # browse & pick from a whole account
atlas add ~/dev --discover                  # auto-detect all sub-projects

# 3. Start your LLM server, then enrich
atlas enrich                                # batched, ~30s for a typical project

# 4. Generate the atlas
atlas report                                # writes PROJECTS_ATLAS.md + graph.json

# 5. Wire into your coding assistant (run from any project directory)
atlas install claude                        # appends a snippet to CLAUDE.md
atlas install cursor                        # appends to .cursorrules
atlas install opencode                      # writes agents.md + JS plugin
```

After step 5, your coding assistant will read the atlas at session start automatically.

---

## Walkthrough: adding a GitHub account

```bash
$ atlas add https://github.com/yourusername

Fetching repos for yourusername...

                 github.com/yourusername — 18 repos
┏━━━┳━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ # ┃ Name              ┃ Description                          ┃ Updated    ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ 1 │ user-api          │ FastAPI user management service       │ 2025-04-12 │
│ 2 │ data-pipeline     │ ETL pipeline for analytics            │ 2025-03-28 │
│ 3 │ ml-experiments    │ Jupyter notebooks for model research  │ 2025-02-10 │
│ …                                                                          │
└───┴───────────────────┴──────────────────────────────────────┴────────────┘

Enter numbers to add (e.g. 1,3,5-8), 'all', or Enter to cancel: 1-3

Cloning 3 repo(s)...
  ✓ user-api
  ✓ data-pipeline
  ✓ ml-experiments

✓ user-api: 24 files, python
  Enrich estimate: 18 files → 3 batch(es) → ~42s at 30 t/s
✓ data-pipeline: 11 files, python
  Enrich estimate: 8 files → 1 batch(es) → ~19s at 30 t/s
✓ ml-experiments: 34 files, jupyter, python
  Enrich estimate: 22 files → 3 batch(es) → ~56s at 30 t/s

✓ Registered 3 project(s) in ~/.atlas/atlas.yaml
Run atlas enrich to add LLM summaries, then atlas report.
```

---

## Walkthrough: discovering local projects

```bash
$ atlas add ~/dev --discover

          Projects found in /home/you/dev
┏━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Name              ┃ Detected by  ┃ Source files (top 2 levels) ┃
┡━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ user-api          │ git repo     │ 19 .py  3 .yaml             │
│ admin-dashboard   │ package.json │ 42 .tsx  8 .ts  2 .json     │
│ data-pipeline     │ pyproject... │ 11 .py                      │
│ old-prototype     │ 4 source ... │ 4 .py                       │
└───────────────────┴──────────────┴─────────────────────────────┘

Add all 4 project(s)? [y/N]: y
```

Use `--dry-run` to preview without committing, `--yes` to skip the prompt in scripts.

---

## What the atlas looks like

`atlas show my-api` in the terminal:

```
╭─ user-api  ~/dev/user-api ────────────────────────────────────────────────╮
│ FastAPI service for user authentication and session management             │
│                                                                            │
│ REST API providing registration, login, JWT refresh, and OAuth2 flow      │
│ for downstream services. Designed as a standalone microservice; other      │
│ apps call it via HTTP rather than embedding auth logic directly.           │
│                                                                            │
│ Stack:    FastAPI, SQLAlchemy, PostgreSQL, Redis, Pydantic, httpx          │
│ Patterns: dependency injection, repository pattern, async handlers         │
│ Reuse:  • JWT middleware reusable in any FastAPI project                   │
│         • Rate-limiter decorator (Redis-backed, ~50 lines)                 │
│         • Generic pagination helper for SQLAlchemy queries                 │
│                                                                            │
│ Keywords: fastapi python auth jwt oauth2 postgresql redis microservice     │
╰────────────────────────────────────────────────────────────────────────────╯
```

And the generated `PROJECTS_ATLAS.md` fragment that your assistant reads:

```markdown
## user-api
FastAPI service for user authentication and session management.
Stack: FastAPI · SQLAlchemy · PostgreSQL · Redis
Reuse: JWT middleware · Redis rate-limiter · SQLAlchemy pagination helper
Keywords: fastapi python auth jwt oauth2 postgresql redis
Key files: src/auth/middleware.py · src/core/pagination.py · src/users/router.py
```

---

## Command reference

### Project management

| Command | Description |
|---------|-------------|
| `atlas add <path\|url>` | Register, scan, and optionally enrich a project |
| `atlas add <path> --discover` | Auto-detect sub-projects one level deep |
| `atlas add <gh-account-url>` | Browse all public repos, select by number/range/`all` |
| `atlas add <url> --dry-run` | Preview what would be added without cloning |
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
| `atlas show` | All project cards in the terminal |
| `atlas show <name>` | One project card |
| `atlas show <name> --files` | Include per-file summaries |
| `atlas status` | Atlas location, DB path, indexed project list |
| `atlas health` | Diagnose dead paths, missing summaries, duplicate remotes |
| `atlas health --fix` | Auto-remove dead-path entries |

### Setup and maintenance

| Command | Description |
|---------|-------------|
| `atlas init` | Create example `~/.atlas/atlas.yaml` |
| `atlas install <platform>` | Write coding assistant integration files |
| `atlas reset <name>` | Delete cached LLM summaries (force re-enrichment) |
| `atlas clear` | Wipe the database (atlas.yaml kept) |
| `atlas clear --repos` | Also delete cloned repos + purge atlas.yaml entries |

### `atlas add` flags

| Flag | Description |
|------|-------------|
| `--discover` | Scan one level deep; prompt before adding each batch |
| `--yes` / `-y` | Skip all confirmation prompts |
| `--dry-run` | Preview without writing anything |
| `--enrich` | Run enrichment immediately after scan |
| `--no-llm` | Stub summaries only (no LLM call) |
| `--shallow` | Shallow clone — default for URL inputs |
| `--include-forks` | Include forked repos (GitHub account mode) |
| `--force` | Add even if no code files found |

---

## Configuration

`atlas init` writes `~/.atlas/atlas.yaml`:

```yaml
projects:
  - ~/dev/my-api
  - ~/dev/admin-dashboard
  - ~/dev/data-pipeline

atlas_dir: ~/.atlas

llm:
  base_url: http://127.0.0.1:8080/v1
  model: your-model-name
  timeout: 120
  batch_size: 8        # files per LLM call — raise for faster GPUs
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

Config is resolved in this order:
1. `ATLAS_CONFIG` environment variable
2. `./atlas.yaml` in the current directory
3. `~/.atlas/atlas.yaml` (default)

### Supported languages

Python, TypeScript, JavaScript (+ JSX/TSX), Go, Rust, Java, Ruby, C/C++, C#, Kotlin, R, Scala, Swift, Lua, Jupyter notebooks (`.ipynb`), Quarto (`.qmd`), R Markdown (`.Rmd`), LaTeX (`.tex`).

---

## Platform integrations

Run `atlas install <platform>` from the root of any project that should load the atlas.

| Platform | Files written |
|----------|--------------|
| `claude` | Appends a `## Project Atlas` section to `CLAUDE.md` |
| `little-coder` | `skills/knowledge/atlas-*.md` skill cards + `AGENTS.md` section |
| `opencode` | `agents.md` section + `.opencode/plugins/atlas.js` pre-tool hook |
| `cursor` | Appends to `.cursorrules` |
| `cline` | Appends to `.clinerules` |
| `generic` | Copies `PROJECTS_ATLAS.md` into the target directory |
| `all` | All of the above (except generic) |

Each integration tells the assistant to read `PROJECTS_ATLAS.md` at session start. The file is ~50–200 tokens per project, so injecting it is cheap.

---

## Atlas health

`atlas health` checks for common issues and prints a structured report:

```
[ERROR] old-prototype: path not found (/home/you/dev/old-prototype)
        → atlas remove old-prototype
[WARN]  data-pipeline: scanned but not enriched
        → atlas enrich --project data-pipeline
[INFO]  Similar names: 'user-api' and 'user-api-v2' (similarity 83%)

3 issue(s): 1 error, 1 warning, 1 info

Run with --fix to auto-remove dead paths.
```

`--fix` automatically removes dead-path entries. Other issues require manual action.

---

## Tips

**Estimate before enriching** — `atlas add` prints a per-project time estimate based on file count and batch size. Adjust `llm.batch_size` up if your GPU is fast.

**Incremental updates** — `atlas update` only re-enriches files whose SHA-256 hash has changed. Safe to run daily from a cron job or git hook.

**Bad summaries?** — `atlas reset <name>` clears only the LLM summaries for one project; scan data and file records are kept. Then re-run `atlas enrich`.

**No LLM available** — `atlas enrich --no-llm` generates stub summaries from the static scan data (languages, dependencies, file names). Useful for a quick index without spinning up a server.

**GitHub token** — set `GITHUB_TOKEN` to raise the API rate limit from 60 to 5 000 requests/hour when browsing GitHub accounts.

---

## License

MIT
