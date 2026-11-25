# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.2] - 2026-11-25

### Added
- Source repository metadata (`repository` / `homepage` / `bugs`) pointing at
  https://github.com/darianzes/dsh-plugin-rag-eval-suite.

### Fixed
- CHANGELOG release comparison links now target the npm version pages instead of
  placeholder GitHub release URLs.

## [0.2.1] - 2026-11-25

### Added
- DeepSeek Harness binding now registers the full 10-tool surface (`inspect`, `confirm`,
  `scope`, `generate_dataset`, `view_dataset`, `list_methods`, `run`, `manual_eval`,
  `generate_report`, `status`) with DeepSeek Harness `defineTool`-compatible parameter and
  output schemas (`additionalProperties` declared explicitly, open object roots).
- `rag_eval_view_dataset` gained `export_markdown` for one-shot Markdown dataset export.
- `rag_eval_list_methods` now returns `{ "methods": [...], "count": N }`.
- `low_sample_warn` metric status with a hard-fail margin, so small samples are downgraded to a
  warning instead of a false "warning" hiding a real failure.
- `run --json --detail` emits full `case_evaluations`; `run --json` now carries `diagnosis` and
  `category_breakdown`.
- `report --run-id` no longer overwrites `latest_report.*` unless `--update-latest` is passed.
- `status --json` and `manual-eval --json` flags.
- Deterministic anchored dataset generator: concise expected answers, entity-anchored question
  templates, and optional `--llm-gen` query rewriting (never required, always downgraded safely).
- Cross-harness manifests shipped in the package: `mcp.json`, `.mcp.json`, `.claude-plugin/`,
  `.codex-plugin/`, `commands/`, `rules/`, `examples/sample_kb/`, `LICENSE`, `CHANGELOG.md`.
- DSH bundle metadata: `exports`, `icon`, `locale/{en,zh}.json`, `dsh.bundle.patch`.

### Changed
- `cordis.patch.yml` uses the loader `insert` dialect so the bundle mounts correctly.
- `install_bundle` guidance in `scripts/install.py` now recommends installing the tarball/bundle
  instead of hand-editing profile manifests.
- `prepack` prunes `__pycache__` so published tarballs contain no `.pyc` artifacts.

### Fixed
- Knowledge-base auto-discovery now falls back to the project root when `--kb` is omitted.
- `demo` reports real `resource_stats` instead of zeros.
- Demo/test dataset expected answers are concise facts rather than whole chunks.

## [0.2.0] - 2026-11-25

### Added
- Understand-First gate (`inspect` → `confirm`), producing `rag_profile.json` with blocking
  unknowns, readiness score, and observability flags.
- Core scope pruning (`smoke` / `core` / `full`) with explicit tested-vs-deferred disclosure.
- 10-category, corpus-grounded test dataset generation.
- 7 modular test methods: retrieval hit rate, context quality, answer accuracy, hallucination
  audit, abstention guard, citation check, latency efficiency.
- P0–P3 six-stage root-cause diagnosis with quick wins, structural fixes, and verification plans.
- Zero-CDN single-file HTML report plus downloadable Markdown report, and a lightweight
  stdlib-only web studio (`rag_eval serve`).
- Adapters for HTTP, Python callable, CLI command, offline snapshot, and a built-in demo RAG.
- MCP stdio server exposing the same tool surface.
- Cross-harness installers for Antigravity, Claude Code, OpenAI Codex, and DeepSeek Harness.

[0.2.2]: https://www.npmjs.com/package/dsh-plugin-rag-eval-suite/v/0.2.2
[0.2.1]: https://www.npmjs.com/package/dsh-plugin-rag-eval-suite/v/0.2.1
[0.2.0]: https://www.npmjs.com/package/dsh-plugin-rag-eval-suite/v/0.2.0
