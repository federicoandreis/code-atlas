"""Platform integration — writes injection shims for coding assistants."""

from __future__ import annotations

from pathlib import Path

from .reporter import Reporter
from .store import AtlasStore

SUPPORTED_PLATFORMS = ("little-coder", "claude", "opencode", "cursor", "cline", "generic")


class Installer:
    def __init__(self, store: AtlasStore, atlas_dir: Path) -> None:
        self.store = store
        self.atlas_dir = atlas_dir
        self.reporter = Reporter(store)

    def install(self, platform: str, target_dir: Path | None = None) -> list[str]:
        """Install atlas integration for the given platform. Returns list of written paths."""
        if platform == "all":
            written = []
            for p in SUPPORTED_PLATFORMS[:-1]:  # skip "generic"
                try:
                    written.extend(self.install(p, target_dir))
                except Exception:
                    pass
            return written

        dispatch = {
            "little-coder": self._install_little_coder,
            "claude": self._install_claude,
            "opencode": self._install_opencode,
            "cursor": self._install_cursor,
            "cline": self._install_cline,
            "generic": self._install_generic,
        }
        fn = dispatch.get(platform)
        if fn is None:
            raise ValueError(f"Unknown platform '{platform}'. Supported: {', '.join(SUPPORTED_PLATFORMS)}")
        return fn(target_dir or Path.cwd())

    # ------------------------------------------------------------------ #
    # little-coder (pi framework)                                         #
    # ------------------------------------------------------------------ #

    def _install_little_coder(self, base: Path) -> list[str]:
        written = []
        skills_dir = base / "skills" / "knowledge"

        # Write per-project skill cards + index
        self.reporter.write_little_coder_cards(skills_dir)
        written.append(str(skills_dir / "atlas-index.md"))
        for s in self.store.all_project_summaries():
            from .reporter import _slug
            written.append(str(skills_dir / f"atlas-{_slug(s.project_name)}.md"))

        # Append to AGENTS.md (pi auto-discovers it)
        agents_md = base / "AGENTS.md"
        snippet = self.reporter.write_agents_md_snippet(agents_md)
        _append_or_create(agents_md, snippet, marker="## Project Atlas")
        written.append(str(agents_md))

        return written

    # ------------------------------------------------------------------ #
    # Claude Code                                                          #
    # ------------------------------------------------------------------ #

    def _install_claude(self, base: Path) -> list[str]:
        atlas_md = self.atlas_dir / "PROJECTS_ATLAS.md"
        claude_md = base / "CLAUDE.md"
        snippet = (
            f"\n## Project Atlas\n\n"
            f"Always read `{atlas_md}` at the start of each session to understand "
            f"the full project portfolio and avoid reinventing prior work.\n"
        )
        _append_or_create(claude_md, snippet, marker="## Project Atlas")
        return [str(claude_md)]

    # ------------------------------------------------------------------ #
    # OpenCode                                                             #
    # ------------------------------------------------------------------ #

    def _install_opencode(self, base: Path) -> list[str]:
        written = []
        atlas_md = self.atlas_dir / "PROJECTS_ATLAS.md"

        agents_md = base / "agents.md"
        snippet = (
            f"\n## Project Atlas\n\n"
            f"Read `{atlas_md}` at session start for full project portfolio context.\n"
        )
        _append_or_create(agents_md, snippet, marker="## Project Atlas")
        written.append(str(agents_md))

        plugin_dir = base / ".opencode" / "plugins"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        plugin_path = plugin_dir / "atlas.js"
        plugin_path.write_text(_opencode_plugin(atlas_md), encoding="utf-8")
        written.append(str(plugin_path))

        return written

    # ------------------------------------------------------------------ #
    # Cursor                                                               #
    # ------------------------------------------------------------------ #

    def _install_cursor(self, base: Path) -> list[str]:
        atlas_md = self.atlas_dir / "PROJECTS_ATLAS.md"
        rules_path = base / ".cursorrules"
        snippet = (
            f"\n# Project Atlas\n\n"
            f"Read `{atlas_md}` at the start of each session "
            f"to understand the full project portfolio and identify reuse opportunities.\n"
        )
        _append_or_create(rules_path, snippet, marker="# Project Atlas")
        return [str(rules_path)]

    # ------------------------------------------------------------------ #
    # Cline / RooCode                                                      #
    # ------------------------------------------------------------------ #

    def _install_cline(self, base: Path) -> list[str]:
        atlas_md = self.atlas_dir / "PROJECTS_ATLAS.md"
        rules_path = base / ".clinerules"
        snippet = (
            f"\n# Project Atlas\n\n"
            f"Read `{atlas_md}` at the start of each session "
            f"to understand the full project portfolio and identify reuse opportunities.\n"
        )
        _append_or_create(rules_path, snippet, marker="# Project Atlas")
        return [str(rules_path)]

    # ------------------------------------------------------------------ #
    # Generic fallback — drop PROJECTS_ATLAS.md in project root           #
    # ------------------------------------------------------------------ #

    def _install_generic(self, base: Path) -> list[str]:
        target = base / "PROJECTS_ATLAS.md"
        source = self.atlas_dir / "PROJECTS_ATLAS.md"
        if source.exists():
            target.write_bytes(source.read_bytes())
        else:
            self.reporter.write_projects_atlas(target)
        return [str(target)]


# ------------------------------------------------------------------ #
# Helpers                                                             #
# ------------------------------------------------------------------ #

def _append_or_create(path: Path, content: str, marker: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if marker in existing:
            return  # already installed
        path.write_text(existing.rstrip() + "\n\n" + content + "\n", encoding="utf-8")
    else:
        path.write_text(content + "\n", encoding="utf-8")


def _opencode_plugin(atlas_md: Path) -> str:
    return f"""// atlas.js — code-atlas pre-tool injection for OpenCode
// Auto-generated by `atlas install --platform opencode`

export default {{
  name: "atlas",
  version: "1",
  hooks: {{
    preToolCall: async (ctx) => {{
      if (!ctx.atlasInjected) {{
        ctx.atlasInjected = true;
        return {{
          appendToContext: `\\n[atlas] Project atlas available at {atlas_md}. Read it before browsing files.\\n`
        }};
      }}
    }}
  }}
}};
"""
