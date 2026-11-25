# RAG Eval Suite — Always-On Rigor & Resource Discipline Rules

When evaluating, generating test data for, or diagnosing any RAG system using `rag-eval-suite`:

1. **Understand-First Gate (严禁盲测)**: Never generate test datasets or execute RAG backtests before inspecting the target RAG architecture, knowledge base corpus, invocation interface, and intermediate observability (`retrieved_docs`, `citations`). Always produce and confirm `rag_profile.json` first; resolve all blocking unknowns with the user rather than guessing.
2. **No Fabricated Metrics (严谨性与可观测诚实)**: If the target RAG does not expose intermediate retrieved chunks, mark retrieval metrics (`Hit@K`, `Recall@K`, `MRR`) as `unobservable` instead of guessing or conflating them with end-to-end answer accuracy. Always report sample size `N` and Wilson 95% confidence intervals.
3. **Core Scope Pruning for Complex RAGs (复杂系统精简核心)**: When a RAG system or knowledge base is large or complex, proactively run scope profiling (`smoke` / `core` / `full`), prune to the high-signal core path, and explicitly state **Tested Core Scope vs Deferred Scope** in the report.
4. **Categorized Test Data Visibility**: Always allow the user to inspect generated test data separately by category before or alongside execution.
5. **Low CPU & Memory Footprint**: Keep worker concurrency conservative (`workers <= 2` by default), never load heavy local ML embedding/LLM frameworks into the evaluation process, and prefer static standalone `report.html` + downloadable `report.md` (0 MB background RAM) over leaving idle servers running.
