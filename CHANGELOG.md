# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.1] - 2026-11-27

### Docs
- README 新增「发版（一键发布）」小节：`release.sh` 七步闸门表、五种调用方式（全流程 / 语义化自增 /
  `--dry-run` / `--skip-publish` / `--otp`）、前置条件、产物位置，以及把发版方法论复用到新插件的方式。
- 目录结构补充 `skills/dsh-plugin-release/` 与 `scripts/release.sh`，避免使用者漏看发版工具链。

### Verified
- 在 DSH 桌面端以 npm 0.3.0 实体安装（非 `link:`）完成端到端验收：10 个 `rag_eval_*` 工具由
  `defineTool` 正常注册，`inspect → confirm → scope → generate → run` 全链路跑通并产出 HTML/Markdown 报告。

## [0.3.0] - 2026-11-25

### Added
- `scripts/release.sh` —— 一键发版：三道闸门（单元测试 / DSH 工具契约 / tarball 解包冒烟）→
  六处清单版本同步 → `npm publish` → 等待 registry 生效 → commit/tag/push → `gh release create`。
  支持 `--dry-run`、`--skip-publish`、`--skip-git`、`--otp`、`--notes`。
- `skills/dsh-plugin-release/` —— 把「DSH 插件从本地能跑到 npm 可装」沉淀为可复用 Skill：
  bundle 契约、`defineTool` 属性映射与 `additionalProperties` 硬规则、打包卫生、
  版本矩阵、发布流水线与 20 条真实踩坑诊断。
- `skills/dsh-plugin-release/scripts/probe-define-tools.mjs` —— G2 契约探测器：
  校验 `inject` 形态、工具规格层级 `additionalProperties`、`output.schema` + `render`，
  并可用 DSH 真实 `defineTool` 编译（`--real`）。
- `skills/dsh-plugin-release/references/` —— 发版清单与踩坑手册。

### Changed
- `scripts/install.py` 现在安装 `skills/` 下的**全部** Skill（此前只安装单一 Skill），
  并同步校验每个 Skill 的 `SKILL.md`。

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

[0.3.1]: https://www.npmjs.com/package/dsh-plugin-rag-eval-suite/v/0.3.1
[0.3.0]: https://www.npmjs.com/package/dsh-plugin-rag-eval-suite/v/0.3.0
[0.2.2]: https://www.npmjs.com/package/dsh-plugin-rag-eval-suite/v/0.2.2
[0.2.1]: https://www.npmjs.com/package/dsh-plugin-rag-eval-suite/v/0.2.1
[0.2.0]: https://www.npmjs.com/package/dsh-plugin-rag-eval-suite/v/0.2.0
