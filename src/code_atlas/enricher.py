"""Phase 2: LLM enrichment — local Qwen3 via llama.cpp, batched, cached by hash."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import httpx

from .config import AtlasConfig
from .models import FileSummary, ProjectSnapshot, ProjectSummary
from .store import AtlasStore

# Keep output tokens tight: index + one short sentence per file.
# max_tokens is set to batch_size * _OUTPUT_TOKENS_PER_FILE at call time.
_OUTPUT_TOKENS_PER_FILE = 35

_FILE_BATCH_PROMPT = """\
For each file, output one SHORT sentence (max 15 words) describing what it does. \
Return a JSON array: [{{"index":0,"summary":"..."}}, ...]. No explanation, no markdown.

PROJECT: {project_name}

{files}"""

_PROJECT_PROMPT = """\
You are a code documentation assistant. Given the following information about a software project, produce a structured JSON summary.

PROJECT: {project_name}
LOCATION: {root}
LANGUAGES: {languages}
DEPENDENCIES: {dependencies}
README (truncated):
{readme}

FILE SUMMARIES:
{file_summaries}

Return a JSON object with exactly these fields:
- "one_liner": one sentence (max 120 chars) describing what the project does
- "description": 2-3 sentences expanding on purpose, audience, and approach
- "tech_stack": list of key technologies (framework names, databases, major libs)
- "key_patterns": list of notable architectural/design patterns used
- "reuse_hints": list of specific things from this project reusable in other projects
- "keywords": list of 10-20 keywords for search/matching (technologies, concepts, patterns)

Return ONLY the JSON object."""


class Enricher:
    def __init__(self, config: AtlasConfig, store: AtlasStore) -> None:
        self.config = config
        self.store = store
        self._client = httpx.Client(timeout=config.llm.timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Enricher":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def enrich_project(self, snapshot: ProjectSnapshot) -> ProjectSummary:
        file_summaries = self._enrich_files(snapshot)
        return self._enrich_project(snapshot, file_summaries)

    # ------------------------------------------------------------------ #
    # File-level enrichment                                                #
    # ------------------------------------------------------------------ #

    def _enrich_files(self, snapshot: ProjectSnapshot) -> list[FileSummary]:
        results: list[FileSummary] = []
        to_summarize = []

        # Build symbol lookup from snapshot (tree-sitter already parsed these)
        symbols_by_file: dict[str, list[str]] = {}
        for sym in snapshot.symbols:
            symbols_by_file.setdefault(sym.file_path, []).append(f"{sym.kind}:{sym.name}")

        for rec in snapshot.files:
            # Auto-classify trivial files — no LLM needed
            stub = _trivial_summary(rec.path)
            if stub:
                fs = FileSummary(
                    file_path=str(rec.path),
                    project_name=snapshot.name,
                    sha256=rec.sha256,
                    summary=stub,
                )
                self.store.upsert_file_summary(fs)
                results.append(fs)
                continue

            cached = self.store.get_file_summary(str(rec.path), rec.sha256)
            if cached:
                results.append(FileSummary(
                    file_path=str(rec.path),
                    project_name=snapshot.name,
                    sha256=rec.sha256,
                    summary=cached,
                ))
            else:
                to_summarize.append((rec, symbols_by_file.get(str(rec.path), [])))

        if not to_summarize or not self.config.llm.enabled:
            return results

        batch_size = self.config.llm.batch_size
        total = len(to_summarize)
        for i in range(0, total, batch_size):
            batch = to_summarize[i:i + batch_size]
            t0 = time.monotonic()
            summaries = self._summarize_file_batch(batch, snapshot.name)
            elapsed = time.monotonic() - t0
            batch_end = min(i + batch_size, total)
            print(f"  batch {i+1}-{batch_end}/{total}  {elapsed:.0f}s", flush=True)
            for (rec, _), summary_text in zip(batch, summaries):
                fs = FileSummary(
                    file_path=str(rec.path),
                    project_name=snapshot.name,
                    sha256=rec.sha256,
                    summary=summary_text,
                )
                self.store.upsert_file_summary(fs)
                results.append(fs)

        return results

    def _summarize_file_batch(self, batch: list[tuple], project_name: str) -> list[str]:
        parts = []
        for idx, (rec, symbols) in enumerate(batch):
            # Use tree-sitter symbols if available, otherwise fall back to code head
            if symbols:
                context = "symbols: " + ", ".join(symbols[:20])
                head = _read_head(rec.path, 5)  # just first 5 lines (imports/docstring)
                file_text = f"{context}\n{head}".strip()
            else:
                file_text = _read_head(rec.path, 15)
            parts.append(f"FILE {idx} ({rec.relative_path}):\n{file_text}")

        prompt = _FILE_BATCH_PROMPT.format(
            project_name=project_name,
            files="\n\n".join(parts),
        )
        max_tokens = len(batch) * _OUTPUT_TOKENS_PER_FILE
        raw = self._call_llm(prompt, max_tokens=max_tokens)
        parsed = _extract_json(raw)
        if not isinstance(parsed, list):
            return ["(summary unavailable)"] * len(batch)

        by_index = {item.get("index", i): item.get("summary", "") for i, item in enumerate(parsed)}
        return [by_index.get(i, "(summary unavailable)") for i in range(len(batch))]

    # ------------------------------------------------------------------ #
    # Project-level enrichment                                             #
    # ------------------------------------------------------------------ #

    def _enrich_project(
        self,
        snapshot: ProjectSnapshot,
        file_summaries: list[FileSummary],
    ) -> ProjectSummary:
        if not self.config.llm.enabled:
            return _stub_summary(snapshot)

        fs_text = "\n".join(
            f"- {Path(fs.file_path).name}: {fs.summary}"
            for fs in file_summaries[:40]  # cap to avoid token overflow
        )
        lang_str = ", ".join(
            f"{lang} ({lines} lines)"
            for lang, lines in sorted(snapshot.languages.items(), key=lambda x: -x[1])[:5]
        )
        dep_str = ", ".join(
            pkg
            for pkgs in snapshot.dependencies.values()
            for pkg in pkgs[:15]
        )

        prompt = _PROJECT_PROMPT.format(
            project_name=snapshot.name,
            root=str(snapshot.root),
            languages=lang_str or "unknown",
            dependencies=dep_str or "none detected",
            readme=snapshot.readme_text[:2000] or "(no README)",
            file_summaries=fs_text or "(no file summaries)",
        )
        raw = self._call_llm(prompt)
        data = _extract_json(raw)
        if not isinstance(data, dict):
            return _stub_summary(snapshot)

        summary = ProjectSummary(
            project_name=snapshot.name,
            root=str(snapshot.root),
            one_liner=data.get("one_liner", snapshot.name),
            description=data.get("description", ""),
            tech_stack=data.get("tech_stack", []),
            key_patterns=data.get("key_patterns", []),
            reuse_hints=data.get("reuse_hints", []),
            keywords=data.get("keywords", []),
        )
        self.store.upsert_project_summary(summary)
        return summary

    # ------------------------------------------------------------------ #
    # LLM call                                                             #
    # ------------------------------------------------------------------ #

    def _call_llm(self, prompt: str, max_tokens: int | None = None) -> str:
        payload: dict = {
            "model": self.config.llm.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        for attempt in range(self.config.llm.max_retries):
            try:
                resp = self._client.post(
                    f"{self.config.llm.base_url}/chat/completions",
                    json=payload,
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"]
            except Exception as exc:
                if attempt == self.config.llm.max_retries - 1:
                    raise RuntimeError(f"LLM call failed after {self.config.llm.max_retries} attempts: {exc}") from exc
        return ""


# ------------------------------------------------------------------ #
# Helpers                                                             #
# ------------------------------------------------------------------ #

def _trivial_summary(path: Path) -> str | None:
    """Return a pre-baked summary for files that don't need LLM, or None if LLM is needed."""
    name = path.name
    stem = path.stem

    # Empty or near-empty __init__.py
    if name == "__init__.py":
        try:
            content = path.read_text(encoding="utf-8", errors="replace").strip()
            non_comment = [l for l in content.splitlines()
                           if l.strip() and not l.strip().startswith("#")]
            if len(non_comment) <= 5:
                return f"Package init for {path.parent.name}."
        except OSError:
            return f"Package init for {path.parent.name}."

    # Test files
    if stem.startswith("test_") or stem.endswith("_test") or "tests" in path.parts:
        return f"Tests for {stem.removeprefix('test_').removesuffix('_test')}."

    # Database migrations
    if "migration" in str(path).lower() or "migrate" in name.lower():
        return f"Database migration: {stem}."

    # Config / setup files that carry no logic
    if name in ("setup.cfg", "setup.py", "conftest.py", ".env.example",
                "Makefile", "Dockerfile", "docker-compose.yml"):
        return f"Project {name} configuration."

    return None


def _read_head(path: Path, lines: int = 15) -> str:
    try:
        if path.suffix == ".ipynb":
            return _read_notebook_head(path, lines)
        text = path.read_text(encoding="utf-8", errors="replace")
        return "\n".join(text.splitlines()[:lines])
    except OSError:
        return ""


def _read_notebook_head(path: Path, lines: int = 15) -> str:
    """Extract the first code cell(s) from a Jupyter notebook instead of the raw JSON."""
    try:
        nb = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        code_lines: list[str] = []
        for cell in nb.get("cells", []):
            if cell.get("cell_type") == "code":
                src = cell.get("source", [])
                if isinstance(src, list):
                    code_lines.extend(src)
                else:
                    code_lines.extend(src.splitlines(keepends=True))
            if len(code_lines) >= lines:
                break
        return "".join(code_lines[:lines]).strip() or "(empty notebook)"
    except Exception:
        return ""


def _extract_json(text: str) -> dict | list | None:
    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"```\s*$", "", text.strip(), flags=re.MULTILINE)
    # Find first { or [
    for start_char, end_char in (("{", "}"), ("[", "]")):
        start = text.find(start_char)
        if start != -1:
            end = text.rfind(end_char)
            if end != -1:
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    pass
    return None


def _stub_summary(snapshot: ProjectSnapshot) -> ProjectSummary:
    langs = list(snapshot.languages.keys())
    deps = [pkg for pkgs in snapshot.dependencies.values() for pkg in pkgs[:5]]
    return ProjectSummary(
        project_name=snapshot.name,
        root=str(snapshot.root),
        one_liner=f"{snapshot.name} — {', '.join(langs[:3]) or 'unknown language'} project",
        description="(LLM enrichment disabled or unavailable)",
        tech_stack=deps[:10],
        key_patterns=[],
        reuse_hints=[],
        keywords=langs + deps[:8],
    )
