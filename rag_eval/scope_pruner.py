"""Phase 2: Complexity Profiler and Core Scope Pruning Engine.

When a RAG system or knowledge base is overly large or complex, automatically
prunes to a high-signal core subset (representative KB files, core test categories,
and core evaluation methods) while generating an explicit Tested vs Deferred
disclosure statement for rigorous reporting.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import CATEGORY_SPECS, RAGProfile, ScopePlan
from .resource_guard import read_json, safe_worker_count, write_json_atomic


ALL_METHODS = [
    "retrieval_hit_rate",
    "context_quality",
    "answer_accuracy",
    "hallucination_audit",
    "abstention_guard",
    "citation_check",
    "latency_efficiency",
]

CORE_METHODS = [
    "retrieval_hit_rate",
    "answer_accuracy",
    "hallucination_audit",
    "abstention_guard",
    "latency_efficiency",
]

SMOKE_METHODS = [
    "retrieval_hit_rate",
    "answer_accuracy",
    "hallucination_audit",
    "abstention_guard",
]

SMOKE_CATEGORIES = [
    "single_hop_fact",
    "numerical_table",
    "unanswerable_oos",
    "terminology_jargon",
]

CORE_CATEGORIES = [
    "single_hop_fact",
    "multi_hop_reasoning",
    "numerical_table",
    "temporal_version",
    "unanswerable_oos",
    "terminology_jargon",
    "adversarial_distractor",
]


def _stratified_select_files(
    inventory: List[Dict[str, Any]],
    limit: int,
    core_paths: Optional[List[str]] = None,
) -> List[str]:
    """Select representative high-signal KB files across directories and doc traits."""
    if not inventory:
        return []
    if len(inventory) <= limit and not core_paths:
        return [f["path"] for f in inventory]

    selected: List[str] = []
    seen: set[str] = set()

    # 1. Explicit user-priority core paths come first
    if core_paths:
        norm_prefixes = [str(Path(p).expanduser().resolve()) for p in core_paths]
        for item in inventory:
            if any(item["path"].startswith(pref) or pref in item["path"] for pref in norm_prefixes):
                if item["path"] not in seen:
                    selected.append(item["path"])
                    seen.add(item["path"])
                    if len(selected) >= limit:
                        return selected

    # 2. Prioritize information-dense files with tables or temporal markers across subdirs
    by_dir: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in inventory:
        if item["path"] in seen:
            continue
        parent = str(Path(item["path"]).parent)
        by_dir[parent].append(item)

    # Sort each bucket by richness score (tables + dates + moderate length)
    def richness(doc: Dict[str, Any]) -> float:
        score = 0.0
        if doc.get("has_table"):
            score += 3.0
        if doc.get("has_dates"):
            score += 2.0
        chars = int(doc.get("char_count", 0))
        if 300 <= chars <= 40000:
            score += 2.0
        elif chars > 40000:
            score += 1.0
        return score

    for bucket in by_dir.values():
        bucket.sort(key=lambda d: (-richness(d), d["path"]))

    # Round-robin across directories for broad domain coverage
    dir_keys = sorted(by_dir.keys())
    idx = 0
    while len(selected) < limit and dir_keys:
        next_keys: List[str] = []
        for dk in dir_keys:
            bucket = by_dir[dk]
            if idx < len(bucket) and len(selected) < limit:
                path = bucket[idx]["path"]
                if path not in seen:
                    selected.append(path)
                    seen.add(path)
                if idx + 1 < len(bucket):
                    next_keys.append(dk)
        dir_keys = next_keys
        idx += 1

    return selected


def plan_evaluation_scope(
    profile: RAGProfile,
    requested_mode: str = "auto",  # auto | smoke | core | full
    max_cases: Optional[int] = None,
    core_paths: Optional[List[str]] = None,
    avg_latency_sec: float = 2.0,
) -> ScopePlan:
    """Assess RAG & corpus complexity and produce a pruned or full ScopePlan."""
    stats = profile.corpus_stats or {}
    inventory: List[Dict[str, Any]] = list(stats.get("file_inventory") or [])
    total_files = int(stats.get("readable_text_files", len(inventory)))
    total_mb = float(stats.get("total_size_mb", 0.0))
    binary_count = int(stats.get("binary_files_count", 0))

    stages = profile.pipeline_stages or {}
    complexity_score = 0
    reasons: List[str] = []

    if total_files > 200:
        complexity_score += 35
        reasons.append(f"知识库文档规模较大（{total_files} 份可读文档），全量覆盖耗时且高开销")
    elif total_files > 40:
        complexity_score += 20
        reasons.append(f"知识库包含 {total_files} 份文档，适合分层抽样核心文档")

    if total_mb > 30.0:
        complexity_score += 25
        reasons.append(f"语料体积较大（{total_mb:.1f} MB），精简核心可避免内存与索引压力")
    elif total_mb > 5.0:
        complexity_score += 10

    if binary_count > 10:
        complexity_score += 15
        reasons.append(f"含 {binary_count} 份二进制文档（PDF/Office），解析链路复杂")

    if stages.get("retrieval", {}).get("hybrid_suspected"):
        complexity_score += 10
        reasons.append("存在混合检索（向量 + 词法/BM25）架构")
    if stages.get("reranking", {}).get("enabled"):
        complexity_score += 10
        reasons.append("包含独立重排（Reranker）阶段")
    if stages.get("query_understanding", {}).get("query_rewrite_or_hyde"):
        complexity_score += 12
        reasons.append("包含查询改写 / HyDE / 多子查询分解模块")
    if stages.get("query_understanding", {}).get("multi_kb_routing"):
        complexity_score += 15
        reasons.append("包含多知识库路由架构")
    if any("GraphRAG" in x for x in profile.detected_stack.get("retrievers_and_stores", [])):
        complexity_score += 15
        reasons.append("包含 GraphRAG / 知识图谱链路")

    if complexity_score >= 60:
        complexity_level = "extreme"
        recommended_mode = "core"
    elif complexity_score >= 30:
        complexity_level = "high"
        recommended_mode = "core"
    elif complexity_score >= 15:
        complexity_level = "medium"
        recommended_mode = "core"
    else:
        complexity_level = "low"
        recommended_mode = "full"

    if not reasons:
        reasons.append("当前系统与语料规模适中，支持快速全分类评测；也可按需切换 smoke/core 模式。")

    mode = recommended_mode if requested_mode == "auto" else requested_mode
    if mode not in {"smoke", "core", "full"}:
        mode = recommended_mode

    # Adapt categories, case count, KB file cap, and methods to selected mode
    if mode == "smoke":
        selected_categories = list(SMOKE_CATEGORIES)
        default_cases = 16
        file_cap = 8
        raw_methods = list(SMOKE_METHODS)
    elif mode == "core":
        selected_categories = list(CORE_CATEGORIES)
        default_cases = 35
        file_cap = 20
        raw_methods = list(CORE_METHODS)
    else:
        selected_categories = list(CATEGORY_SPECS.keys())
        default_cases = 60
        file_cap = 60
        raw_methods = list(ALL_METHODS)

    # Filter out categories that have no corpus signal unless specifically needed
    if stats.get("docs_with_tables", 0) == 0 and "numerical_table" in selected_categories and total_files > 0:
        # Keep if numbers exist; dataset_gen handles fallback cleanly, so keep category active
        pass

    # Filter methods strictly by observability (rigor!)
    selected_methods: List[str] = []
    deferred_methods: List[str] = []
    for m in ALL_METHODS:
        if m not in raw_methods:
            deferred_methods.append(f"{m} (scope_pruned:{mode})")
            continue
        if m in {"retrieval_hit_rate", "context_quality"} and not profile.observability.retrieved_docs:
            deferred_methods.append(f"{m} (unobservable:retrieved_docs=false)")
            continue
        if m == "citation_check" and not profile.observability.citations and mode != "full":
            deferred_methods.append(f"{m} (citations_not_enabled)")
            continue
        selected_methods.append(m)

    target_cases = int(max_cases) if max_cases else default_cases
    # Cap target cases reasonably for small KBs while still allowing comprehensive tests
    if total_files > 0:
        target_cases = max(8, min(target_cases, 200))

    selected_files = _stratified_select_files(inventory, limit=file_cap, core_paths=core_paths)
    deferred_files_count = max(0, total_files - len(selected_files))
    deferred_categories = [c for c in CATEGORY_SPECS if c not in selected_categories]

    workers = safe_worker_count(2)
    est_duration = round((target_cases * max(0.2, avg_latency_sec)) / max(1, workers), 1)

    if mode == "full" and deferred_files_count == 0 and not deferred_categories:
        disclosure = (
            f"本次采用【全量模式 (full)】，覆盖全部 {len(selected_files)} 份可读核心文档与 "
            f"{len(selected_categories)} 个测试分类，执行 {len(selected_methods)} 项评测方法。"
        )
    else:
        disclosure = (
            f"为控制测试复杂度与资源消耗，本次采用【精简核心模式 ({mode})】"
            f"（系统复杂度评估：{complexity_level}，得分 {complexity_score}/100）："
            f"精选 {len(selected_files)}/{total_files} 份高价值代表性文档，"
            f"聚焦 {len(selected_categories)}/{len(CATEGORY_SPECS)} 个核心测试分类（约 {target_cases} 条用例）及 "
            f"{len(selected_methods)} 项核心评测方法；"
            f"暂缓 {deferred_files_count} 份边缘文档与 {len(deferred_categories)} 个扩展分类"
            f"（{', '.join(deferred_categories) if deferred_categories else '无'}）。"
            f"结论仅代表当前核心闭环表现，待核心 P0/P1 问题收敛后可切换 `--scope full` 扩展全量验证。"
        )

    return ScopePlan(
        profile_mode=mode,
        recommended_mode=recommended_mode,
        complexity_level=complexity_level,
        complexity_score=complexity_score,
        complexity_reasons=reasons,
        selected_categories=selected_categories,
        deferred_categories=deferred_categories,
        target_case_count=target_cases,
        selected_methods=selected_methods,
        deferred_methods=deferred_methods,
        selected_kb_files=selected_files,
        total_kb_files=total_files,
        deferred_kb_files_count=deferred_files_count,
        estimated_duration_sec=est_duration,
        max_workers=workers,
        disclosure_statement=disclosure,
    )


def save_scope_plan(workdir: Path, plan: ScopePlan) -> Path:
    path = workdir / "scope_plan.json"
    write_json_atomic(path, plan.to_dict())
    return path


def load_scope_plan(workdir: Path) -> Optional[ScopePlan]:
    path = workdir / "scope_plan.json"
    if not path.exists():
        return None
    return ScopePlan.from_dict(read_json(path))
