"""Phase 3: Report generation — PROJECTS_ATLAS.md and platform-specific outputs."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .models import ProjectSummary
from .store import AtlasStore


class Reporter:
    def __init__(self, store: AtlasStore) -> None:
        self.store = store

    def write_projects_atlas(self, output_path: Path) -> None:
        summaries = self.store.all_project_summaries()
        key_files = {s.project_name: self.store.get_key_files_for_project(s.project_name)
                     for s in summaries}
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(_render_atlas(summaries, key_files), encoding="utf-8")

    def write_graph_json(self, output_path: Path) -> None:
        summaries = self.store.all_project_summaries()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        graph = _build_graph_json(summaries)
        output_path.write_text(json.dumps(graph, indent=2), encoding="utf-8")

    def write_little_coder_cards(self, skills_knowledge_dir: Path) -> None:
        """Write per-project skill cards for little-coder's knowledge-inject extension."""
        summaries = self.store.all_project_summaries()
        skills_knowledge_dir.mkdir(parents=True, exist_ok=True)

        # Write per-project cards
        for s in summaries:
            key_files = self.store.get_key_files_for_project(s.project_name)
            card_path = skills_knowledge_dir / f"atlas-{_slug(s.project_name)}.md"
            card_path.write_text(_render_skill_card(s, key_files), encoding="utf-8")

        # Write the index card (lightweight, always relevant)
        index_path = skills_knowledge_dir / "atlas-index.md"
        index_path.write_text(_render_index_card(summaries), encoding="utf-8")

    def write_agents_md_snippet(self, agents_md_path: Path) -> str:
        """Return the AGENTS.md snippet for little-coder / pi. Caller decides whether to append."""
        summaries = self.store.all_project_summaries()
        snippet = _render_agents_md_snippet(summaries)
        return snippet


# ------------------------------------------------------------------ #
# Renderers                                                           #
# ------------------------------------------------------------------ #

def _render_atlas(summaries: list[ProjectSummary], key_files: dict[str, list[tuple[str, str]]] | None = None) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# Projects Atlas",
        f"",
        f"Generated: {now} | Projects: {len(summaries)}",
        f"",
        f"## Quick Index",
        f"",
        f"| Project | Language | Purpose | Key Technologies | Last enriched |",
        f"|---------|----------|---------|-----------------|---------------|",
    ]
    for s in summaries:
        lang = s.tech_stack[0] if s.tech_stack else "—"
        tech = ", ".join(s.tech_stack[:4])
        date = s.summarized_at.strftime("%Y-%m-%d")
        lines.append(f"| {s.project_name} | {lang} | {s.one_liner} | {tech} | {date} |")

    # Cross-project patterns
    lines += ["", "## Cross-Project Patterns", ""]
    tech_to_projects = _invert_tech(summaries)
    for tech, projects in sorted(tech_to_projects.items(), key=lambda x: -len(x[1])):
        if len(projects) > 1:
            lines.append(f"- **{tech}**: {', '.join(projects)}")

    # Per-project details
    lines += ["", "## Per-Project Summaries", ""]
    for s in summaries:
        lines += [
            f"### {s.project_name}",
            f"",
            f"**Location:** `{s.root}`  ",
            f"**Purpose:** {s.description or s.one_liner}",
            f"",
        ]
        if s.tech_stack:
            lines.append(f"**Stack:** {', '.join(s.tech_stack)}")
        if s.key_patterns:
            lines.append(f"**Patterns:** {', '.join(s.key_patterns)}")
        if s.reuse_hints:
            lines.append(f"**Reuse from here:** {', '.join(s.reuse_hints)}")
        files = (key_files or {}).get(s.project_name, [])
        if files:
            lines.append("**Key files:**")
            for rel, summary in files:
                lines.append(f"- `{rel}` — {summary}")
        lines.append("")

    # Reuse opportunities
    lines += ["## Reuse Opportunities", ""]
    for s in summaries:
        if s.reuse_hints:
            for hint in s.reuse_hints:
                lines.append(f"- **{s.project_name}**: {hint}")
    lines.append("")

    return "\n".join(lines)


def _render_skill_card(s: ProjectSummary, key_files: list[tuple[str, str]] | None = None) -> str:
    lines = [
        f"## Project Reference: {s.project_name}",
        f"",
        f"**Location:** `{s.root}`",
        f"**Purpose:** {s.one_liner}",
        f"",
        s.description,
        f"",
    ]
    if s.tech_stack:
        lines.append(f"**Stack:** {', '.join(s.tech_stack)}")
    if s.key_patterns:
        lines.append(f"**Patterns:** {', '.join(s.key_patterns)}")
    if s.reuse_hints:
        lines += [f"**Reuse from here:**"]
        for hint in s.reuse_hints:
            lines.append(f"- {hint}")
    if key_files:
        lines.append(f"**Key files:**")
        for rel, summary in key_files:
            lines.append(f"- `{rel}` — {summary}")
    lines += [
        f"",
        f"**Keywords:** {' '.join(s.keywords)}",
    ]
    return "\n".join(lines)


def _render_index_card(summaries: list[ProjectSummary]) -> str:
    now = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"## Atlas Index — {len(summaries)} projects ({now})",
        f"",
        f"Detailed skill cards: `atlas-<project-name>.md` in this directory.",
        f"",
        f"| Project | One-liner |",
        f"|---------|-----------|",
    ]
    for s in summaries:
        lines.append(f"| {s.project_name} | {s.one_liner} |")

    lines += [
        f"",
        f"**Keywords:** {' '.join(set(kw for s in summaries for kw in s.keywords[:5]))}",
    ]
    return "\n".join(lines)


def _render_agents_md_snippet(summaries: list[ProjectSummary]) -> str:
    names = ", ".join(s.project_name for s in summaries)
    return f"""
# Project Atlas

This workspace has a code atlas with {len(summaries)} known projects: {names}.

Per-project skill cards are in `skills/knowledge/atlas-*.md` and will be injected
automatically when relevant to your current task. For a full overview, read
`skills/knowledge/atlas-index.md`.

When starting a new implementation task, check if a prior project has already solved
a similar problem before writing new code.
""".strip()


def _build_graph_json(summaries: list[ProjectSummary]) -> dict:
    nodes = []
    edges = []
    tech_nodes: dict[str, int] = {}
    node_id = 0

    project_ids = {}
    for s in summaries:
        project_ids[s.project_name] = node_id
        nodes.append({"id": node_id, "label": s.project_name, "type": "project",
                       "root": s.root, "one_liner": s.one_liner})
        node_id += 1

    for s in summaries:
        for tech in s.tech_stack:
            if tech not in tech_nodes:
                tech_nodes[tech] = node_id
                nodes.append({"id": node_id, "label": tech, "type": "technology"})
                node_id += 1
            edges.append({
                "source": project_ids[s.project_name],
                "target": tech_nodes[tech],
                "type": "uses",
            })

    return {"nodes": nodes, "edges": edges, "generated_at": datetime.now().isoformat()}


def _invert_tech(summaries: list[ProjectSummary]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for s in summaries:
        for tech in s.tech_stack:
            result.setdefault(tech, []).append(s.project_name)
    return result


def _slug(name: str) -> str:
    return name.lower().replace(" ", "-").replace("_", "-")
