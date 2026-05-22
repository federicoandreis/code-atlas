# code-atlas

Fully local code project knowledge base for coding assistant injection.

Scans your coding projects once (tree-sitter, no LLM), then uses a local LLM to generate compact, cached summaries. Produces a token-efficient `PROJECTS_ATLAS.md` and per-project skill cards that coding assistants can consume at session start — so you stop pointing tools at the same files over and over.

**Why?** Coding assistants burn context re-discovering your stack on every session. code-atlas gives them a one-page answer: *what each project does, what it's built with, and what's worth reusing.*

---

## How it works

Three passes, run once and cached:

```
Pass 1 — Static analysis  (free, no LLM)
  tree-sitter → functions, classes, imports
  pyproject.toml / package.json / go.mod / Cargo.toml / … → dependencies
  Git log → remote URL, commit count, last activity
  README → raw text

Pass 2 — LLM enrichment  (local, batched, cached by SHA-256)
  File summaries   "what does this module do?" (15-word max, batched 8 at a time)
  Project summary  one-liner, description, tech stack, patterns, reuse hints, keywords
  Unchanged files are never re-summarised — hash-keyed cache in SQLite

Pass 3 — Output generation  (free)
  ~/.atlas/PROJECTS_ATLAS.md   token-efficient cross-project map
  ~/.atlas/graph.json           machine-readable graph
  per-platform integration shims for little-coder, Claude Code, OpenCode, Cursor, Cline
```

Everything lives in `~/.atlas/` — one SQLite database, no Docker, no Neo4j.

---

## Prerequisites

- Python ≥ 3.11
- A local OpenAI-compatible LLM endpoint, e.g. [llama.cpp](https://github.com/ggerganov/llama.cpp) server:

  ```bash
  llama-server -m qwen3-30b-a3b.gguf --port 8080 -ngl 99
  ```

  Any model works. The default config points to `http://127.0.0.1:8080/v1`.  
  Set `llm.enabled: false` in `atlas.yaml` to skip enrichment entirely and use stub summaries.

- (Optional) `git` on PATH — for pulling remote repos and reading git metadata.

---

## Install

```bash
pip install code-atlas
```

Or from source:

```bash
git clone https://github.com/federicoandreis/code-atlas
cd code-atlas
pip install -e .
```

---

## Quick start

```bash
# 1. Create config
atlas init                              # writes ~/.atlas/atlas.yaml

# 2. Add projects
atlas add ~/projects/my-app             # local path
atlas add https://github.com/owner/repo # clone a single repo
atlas add https://github.com/myusername # browse + select from whole account
atlas add ~/projects --discover         # auto-detect all sub-projects

# 3. Enrich (start your local LLM first)
atlas enrich

# 4. Generate outputs
atlas report

# 5. Wire into your coding assistant
atlas install claude                    # appends to CLAUDE.md
atlas install little-coder             # writes skills/knowledge/atlas-*.md
atlas install opencode                 # writes agents.md + .opencode/plugins/atlas.js
```

---

## Commands

### Adding and managing projects

| Command | Description |
|---------|-------------|
| `atlas add <path\|url>` | Register, scan, and optionally enrich a project |
| `atlas add <path> --discover` | Auto-detect sub-projects one level deep |
| `atlas add <gh-url> --dry-run` | Preview what would be added without cloning |
| `atlas add <gh-account-url>` | Browse all public repos, select by number/range/all |
| `atlas remove <name>` | Remove a project from the atlas (DB + config) |
| `atlas update` | Incremental re-scan + re-enrich + re-report for all projects |

### Analysis pipeline

| Command | Description |
|---------|-------------|
| `atlas scan` | Re-scan projects already in atlas.yaml (no LLM) |
| `atlas enrich` | LLM pass — batched, skips unchanged files |
| `atlas enrich -p <name>` | Enrich a specific project only |
| `atlas report` | Write `PROJECTS_ATLAS.md` + `graph.json` |

### Querying and inspection

| Command | Description |
|---------|-------------|
| `atlas query <question>` | Keyword search across all enriched projects |
| `atlas show` | Show all project cards in the terminal |
| `atlas show <name>` | Show one project card |
| `atlas show <name> --files` | Include per-file summaries |
| `atlas status` | Atlas location, DB size, indexed project list |
| `atlas health` | Diagnose dead paths, missing summaries, duplicates |
| `atlas health --fix` | Auto-remove dead-path entries |

### Setup and maintenance

| Command | Description |
|---------|-------------|
| `atlas init` | Create example `~/.atlas/atlas.yaml` |
| `atlas install <platform>` | Write coding assistant integration files |
| `atlas reset <name>` | Delete cached LLM summaries (force re-enrichment) |
| `atlas clear` | Wipe the entire database (atlas.yaml kept) |
| `atlas clear --repos` | Also delete cloned repos + purge atlas.yaml entries |

### `atlas add` flags

| Flag | Description |
|------|-------------|
| `--discover` | Scan one level deep; prompt to add all detected sub-projects |
| `--yes` / `-y` | Skip confirmation (for scripting) |
| `--dry-run` | Preview without writing anything |
| `--enrich` | Run LLM enrichment immediately after scan |
| `--no-llm` | Produce stub summaries only |
| `--shallow` | Shallow clone (depth=1) — default for URL mode |
| `--include-forks` | Include forked repos (GitHub account mode) |
| `--force` | Add a project even if no code files are found |

---

## Configuration

`atlas init` creates `~/.atlas/atlas.yaml`. Edit it directly:

```yaml
projects:
  - G:/Development/my-app
  - ~/projects/ragsistant

atlas_dir: ~/.atlas   # where the DB and outputs live

llm:
  base_url: http://127.0.0.1:8080/v1
  model: qwen3-30b-a3b
  timeout: 120
  batch_size: 8        # files per LLM call
  enabled: true        # set false to use stub summaries

ignore_patterns:
  - .git
  - __pycache__
  - node_modules
  - .venv
  - dist
  - build
  - "*.egg-info"
```

Config is discovered in order: `ATLAS_CONFIG` env var → `./atlas.yaml` → `~/.atlas/atlas.yaml`.

### Supported file types

Python, TypeScript, JavaScript, Go, Rust, Java, Ruby, C/C++, C#, Kotlin, R, Scala, Swift, Lua, Jupyter notebooks (`.ipynb`), Quarto (`.qmd`), R Markdown (`.Rmd`), LaTeX (`.tex`).

---

## Platform integrations

| Platform | What gets written |
|----------|------------------|
| `little-coder` | `skills/knowledge/atlas-*.md` skill cards + `AGENTS.md` section |
| `claude` | `CLAUDE.md` section pointing at `PROJECTS_ATLAS.md` |
| `opencode` | `agents.md` section + `.opencode/plugins/atlas.js` pre-tool hook |
| `cursor` | `.cursorrules` section |
| `cline` | `.clinerules` section |
| `generic` | Copies `PROJECTS_ATLAS.md` into the target directory |
| `all` | All of the above (except generic) |

Run `atlas install <platform>` from the root of any project that should load the atlas.

---

## GitHub account mode

Point `atlas add` at a GitHub account URL to browse all public repos:

```bash
atlas add https://github.com/federicoandreis
```

A table is shown; enter numbers, ranges, or `all` to select which repos to clone and add. Repos with no code files are skipped automatically (use `--force` to override). Set `GITHUB_TOKEN` for higher rate limits.

```bash
# Scripting: add all non-fork repos without prompts
atlas add https://github.com/federicoandreis --yes
```

---

## Tips

- **First run estimate** — after `atlas add`, the enrich estimate is printed: `18 files → 3 batch(es) → ~42s at 30 t/s`. Adjust `llm.batch_size` to tune throughput.
- **Incremental updates** — `atlas update` only re-enriches files whose SHA-256 has changed. Run it from a cron job or git post-merge hook.
- **Reset a project** — `atlas reset <name>` clears only the LLM summaries; scan data is kept. Useful when the LLM produced bad output.
- **Offline / no LLM** — `atlas enrich --no-llm` generates stub summaries from static data (tech stack, languages) without calling the LLM.
- **Windows** — code-atlas handles Windows path separators and cp1252 console encoding automatically.

---

## License

MIT
