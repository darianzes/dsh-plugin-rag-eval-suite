---
name: rag-eval-suite
description: >-
  Understand-first RAG evaluation, categorized test dataset generation, single/batch/manual
  backtesting (hit rate, recall, MRR, accuracy, faithfulness, hallucination rate, abstention),
  P0-P3 root-cause severity grading with remediation plans, and interactive HTML + downloadable
  Markdown reporting. Works across Antigravity, Claude Code, OpenAI Codex, and DeepSeek Harness (dsh).
---

# RAG Eval Suite — 严谨的 RAG 评测、回测与根因诊断指南

本 Skill 用于指导 Agent（Antigravity / Claude Code / Codex / DeepSeek Harness `dsh`）对任意 RAG 系统执行**理解先行、指标严谨、低资源占用**的测试数据生成、单条/批量/手动回测、P0-P3 根因分级与报告生成。

MCP 与 DSH Cordis 适配器均暴露完全一致的 **10 个工具**：
`rag_eval_inspect` · `rag_eval_confirm` · `rag_eval_scope` · `rag_eval_generate_dataset` · `rag_eval_view_dataset` · `rag_eval_list_methods` · `rag_eval_run` · `rag_eval_manual_eval` · `rag_eval_generate_report` · `rag_eval_status`

---

## 核心铁律（严禁违反）

1. **严禁在不理解系统的情况下直接测试（Understand-First Gate）**
   - 先执行 `rag_eval_inspect`（或 `python3 -m rag_eval inspect`）扫描目标 RAG 的架构、知识库语料、调用接口与可观测性（不传 `--kb` 时会自动探测常见子目录，未命中则回退检查项目根目录并生成非阻断 `UNK-KB-AUTO` 请用户确认）。
   - 若存在阻断性未知项（Blocking Unknowns），**必须向用户澄清**并调用 `rag_eval_confirm` 显式确认后方可进入测试阶段。
   - **严禁编造不可观测指标**：若目标 RAG 不暴露召回片段，必须将 `retrieved_docs=false`，让 `Hit@K / Recall@K / MRR` 保持 `unobservable`。
2. **大范围/复杂 RAG 自动精简核心闭环（Core Scope Pruning）**
   - 当语料文档过多或流水线包含多路混合检索/重排/多知识库/GraphRAG 时，调用 `rag_eval_scope`（`auto` / `core` / `smoke`）精简为核心高价值文档与核心分类，并在报告中**明确披露已测核心范围与暂缓范围**。
3. **测试数据可独立查看、隐私合规可控**
   - 生成测试集默认采用纯本地确定性实体锚定模式（无外传）。
   - 可选开启 `--llm-gen` / `use_llm_gen=true` 润色自然问句，但**会向配置的外部 LLM API 发送问句与证据前 160 字**——对内网或涉密语料**必须先确认合规再开启**（默认关闭）。
   - 生成后先用 `rag_eval_view_dataset` 按 10 大分类向用户展示覆盖情况与平衡度预警。
4. **严谨状态解读与效度诚实**
   - 指标状态包含 7 种：`pass`、`warn`、`low_sample_warn`（小样本温和偏离）、`fail`、`info`（无阈值参考指标如 `Hit@1` / `P50`）、`unobservable`、`insufficient_data`。
   - 若指标**严重偏离目标阈值 ≥ 20%**（如盲区拒答率 50% vs 目标 85%），即使样本量小（N < 10）也保留 `fail` 硬警示，绝不掩盖高危缺陷。
   - 自动抽取的 `must_include_facts` 适用于同一系统的纵向回归；跨系统/跨模型横向对比应叠加 `--llm-judge` 或人工独立标注标准答案。
5. **严控 CPU 与内存占用**
   - 纯 Python 标准库轻量执行，默认并发 `workers <= 2`；优先交付静态独立 `report.html` + `report.md`（0 MB 后台占用），按需启动带 `--idle-timeout` 的 `serve` 服务。

---

## 标准 6 阶段工作流

### 阶段 1：理解目标 RAG 系统（Understand-First）
- **Tool**: `rag_eval_inspect(project_path=..., kb_paths=[...])`
- **CLI**: `python3 -m rag_eval inspect --project <dir> --kb <kb_dir> --json`
- 向用户汇报识别结果与 `blocking_unknowns`，确认后解锁：
  - **Tool**: `rag_eval_confirm(adapter_config=..., observability_overrides=...)`
  - **CLI**: `python3 -m rag_eval confirm --adapter http --endpoint http://localhost:8000/rag --obs-retrieved true`

### 阶段 2：复杂度评估与核心范围精简（Scope Pruning）
- **Tool**: `rag_eval_scope(profile="auto")`
- **CLI**: `python3 -m rag_eval scope --profile auto`（可选 `smoke` / `core` / `full`）

### 阶段 3：生成并独立查看分类测试数据
- 生成：
  - **Tool**: `rag_eval_generate_dataset(use_llm_gen=False)`
  - **CLI**: `python3 -m rag_eval generate`（如需自然问句润色且语料允许出境，加 `--llm-gen`）
- 独立分类查看与导出：
  - **Tool**: `rag_eval_view_dataset(category=..., core_only=..., export_markdown=...)`
  - **CLI**: `dataset summary` / `dataset list --category unanswerable_oos` / `dataset show TC-001` / `dataset export -o test_dataset.md`

### 阶段 4：选择测试方法（单独 / 批量 / 手动）并回测
- 查看方法清单：`rag_eval_list_methods` / `python3 -m rag_eval methods`
- 支持的 7 大模块化方法：`retrieval_hit_rate`、`context_quality`、`answer_accuracy`、`hallucination_audit`、`abstention_guard`、`citation_check`、`latency_efficiency`。
- 典型执行命令：
  - 单独执行某方法：`python3 -m rag_eval run --methods hallucination_audit --json`（`--json` 默认包含 `metrics`、`category_breakdown` 与完整 `diagnosis.issues`；加 `--detail` 附带全量 `case_evaluations`）
  - 单条用例调试：`python3 -m rag_eval run --case-id TC-001 --json --detail`
  - 核心集批量回测：`python3 -m rag_eval run --core-only --methods all --workers 2`
  - 离线手动快照评测：`python3 -m rag_eval run --adapter offline --responses manual_outputs.jsonl`
  - 免配置单条手动演练：`rag_eval_manual_eval` / `python3 -m rag_eval manual-eval --json --query "..." --answer "..." --chunk "..."`
  - 工作区状态与内存：`rag_eval_status` / `python3 -m rag_eval status --json`

### 阶段 5：P0-P3 问题原因分级与解决方案
回测自动按 6 层链路输出 **P0 阻断 / P1 严重 / P2 中等 / P3 优化** 原因，每项含受影响用例 ID、占比、诊断置信度、**短期止血 (Quick Wins)**、**架构修复 (Structural Fixes)** 与 **回归验证命令**。

### 阶段 6：浏览交互页面与下载 Markdown 报告
- 静态单文件页面（0 MB 常驻，内置「下载 Markdown 报告 (.md)」）：`.rag_eval/reports/latest_report.html` 与 `.rag_eval/reports/latest_report.md`
- 重导出历史 `run_id` 默认仅写入 `reports/<run_id>/` 而**不覆盖** `latest_report.*`；如需覆盖传 `--update-latest`（或 MCP/DSH `update_latest=true`）。
- 实时交互控制台：`python3 -m rag_eval serve --workdir .rag_eval --port 8765 --idle-timeout 1800`

---

## 参考文档
- 指标数学公式、状态机与效度说明：[metrics-formulas.md](./references/metrics-formulas.md)
- 10 大测试数据分类设计规范：[taxonomy-categories.md](./references/taxonomy-categories.md)
- P0-P3 根因分级与链路归因标准：[root-cause-rubric.md](./references/root-cause-rubric.md)
