# RAG Eval Suite — Workspace & Cross-Harness Instructions

This repository implements the **RAG Eval Suite** multi-harness plugin (compatible with **DeepSeek Harness (`dsh`)**, **Antigravity**, **Claude Code**, and **OpenAI Codex**).

## Mandatory Operating Rules
- **Understand-First**: Always run `rag_eval_inspect` (or `python3 -m rag_eval inspect`) and confirm `rag_profile.json` via `rag_eval_confirm` before generating test data or running backtests. Never test a RAG system blindly without understanding its architecture, KB schema, and observability.
- **Core Scope Pruning**: For large or complex RAG architectures/corpora, evaluate complexity via `rag_eval_scope` and recommend/apply `core` (or `smoke`) pruning with an explicit Tested vs Deferred scope disclosure.
- **Rigor, Validity & Privacy**:
  - Use pure Python stdlib core (`rag_eval/`), keep `workers <= 2` by default, and never fabricate unobservable retrieval metrics (`unobservable`).
  - Reference metrics without thresholds emit `info` (never `pass`); small-sample borderline misses emit `low_sample_warn`, while severe breaches (`>= 20%` off target) retain hard `fail` so critical defects are never masked.
  - `--llm-gen` and `--llm-judge` are strictly opt-in (off by default); warn users before enabling on confidential/intranet corpora because `--llm-gen` sends query + 160-char evidence snippets to the configured LLM API.
  - Always deliver both a directly browsable `report.html` and a downloadable `report.md` with P0-P3 root-cause grades and actionable fixes; regenerating a historical `run_id` never clobbers `latest_report.*` unless `--update-latest` is passed.

## Key Entrypoints (10 Unified Tools Across MCP & DSH Cordis)
- Universal Skill: `skills/rag-eval-suite/SKILL.md`
- CLI: `python3 -m rag_eval --help` (supports `--json` across all subcommands including `status` and `manual-eval`; `run --json` includes full `diagnosis`, plus `--detail` for `case_evaluations`; quick showcase: `python3 -m rag_eval demo`)
- Stdio MCP Server: `python3 -m rag_eval.mcp_server` (10 `rag_eval_*` tools)
- Lightweight Web Studio: `python3 -m rag_eval serve --workdir .rag_eval --idle-timeout 1800`
- DSH Cordis Plugin Bundle: `package.json` (`exports`/`icon`/`locale/*`), `cordis.patch.yml` (Loader `insert:` dialect), `dsh/index.mjs` (`defineTool` + property-map DSL + 10 matching `rag_eval_*` tools)
- Multi-platform Installer: `python3 scripts/install.py --target all --dry-run`
