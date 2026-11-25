"""Execution engine supporting single-case, single-method, category-slice, batch, and manual evaluation.

Enforces the Understand-First Gate, bounded CPU/RAM concurrency, incremental JSONL
checkpointing, observability honesty, and automatic P0-P3 root-cause diagnosis.
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .adapters import BaseRAGAdapter, build_adapter
from .dataset_gen import filter_dataset, load_dataset
from .diagnostics import diagnose_run
from .inspector import check_understanding_gate, load_profile
from .llm_judge import LLMJudge
from .methods import CASE_EVALUATORS, METHOD_SPECS, aggregate_metrics
from .models import CATEGORY_SPECS, RAGProfile, RAGResponse, RetrievedChunk, ScopePlan, TestCase, utc_now_iso
from .resource_guard import (
    ResourceTracker,
    append_jsonl,
    iter_jsonl,
    read_json,
    safe_worker_count,
    write_json_atomic,
)
from .scope_pruner import load_scope_plan


def _resolve_methods(
    requested: Optional[List[str]],
    profile: RAGProfile,
    scope_plan: Optional[ScopePlan],
    allow_observed_override: bool = False,
) -> Tuple[List[str], Dict[str, str]]:
    if not requested or requested == ["all"]:
        base = list(scope_plan.selected_methods) if scope_plan else list(METHOD_SPECS.keys())
    else:
        base = [m.strip() for m in requested if m.strip() in METHOD_SPECS]
        unknown = [m for m in requested if m.strip() and m.strip() not in METHOD_SPECS and m.strip() != "all"]
        if unknown:
            raise ValueError(f"未知的测试方法: {', '.join(unknown)}。可选: {', '.join(METHOD_SPECS.keys())}")

    executed: List[str] = []
    skipped: Dict[str, str] = {}
    for mid in base:
        spec = METHOD_SPECS[mid]
        if spec.requires_retrieved_docs and not profile.observability.retrieved_docs and not allow_observed_override:
            skipped[mid] = "RAG 档案标记 retrieved_docs=false（不可观测召回片段），严禁编造命中率指标"
            continue
        executed.append(mid)

    return executed, skipped


def _compute_category_breakdown(case_evals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for ce in case_evals:
        grouped[ce["category"]].append(ce)

    rows: List[Dict[str, Any]] = []
    for cat_id, spec in CATEGORY_SPECS.items():
        bucket = grouped.get(cat_id)
        if not bucket:
            continue

        def avg_score(method_id: str, key: str) -> Optional[float]:
            vals = [
                float(c["method_scores"][method_id][key])
                for c in bucket
                if c["method_scores"].get(method_id, {}).get("applicable")
                and c["method_scores"][method_id].get(key) is not None
            ]
            return round(sum(vals) / len(vals), 4) if vals else None

        hall_flags = [
            1.0 if c["method_scores"]["hallucination_audit"].get("hallucinated") else 0.0
            for c in bucket
            if c["method_scores"].get("hallucination_audit", {}).get("applicable")
        ]
        hall_rate = round(sum(hall_flags) / len(hall_flags), 4) if hall_flags else None
        passed_cnt = sum(1 for c in bucket if c["passed"])
        rows.append(
            {
                "category": cat_id,
                "zh_name": spec["zh_name"],
                "total": len(bucket),
                "passed": passed_cnt,
                "pass_rate": round(passed_cnt / len(bucket), 4),
                "hit_at_k": avg_score("retrieval_hit_rate", "hit_at_k"),
                "recall_at_k": avg_score("retrieval_hit_rate", "recall_at_k"),
                "answer_accuracy": avg_score("answer_accuracy", "score"),
                "hallucination_rate": hall_rate,
                "faithfulness": avg_score("hallucination_audit", "faithfulness"),
            }
        )
    return rows


def _evaluate_single_case(
    case: TestCase,
    resp: RAGResponse,
    methods: List[str],
    judge: Optional[LLMJudge] = None,
) -> Dict[str, Any]:
    judge_res: Optional[Dict[str, Any]] = None
    if judge and judge.is_available and not resp.error and (
        "answer_accuracy" in methods or "hallucination_audit" in methods
    ):
        active_chunks = resp.reranked_chunks if resp.reranked_chunks else resp.retrieved_chunks
        judge_res = judge.evaluate_case(
            query=case.query,
            expected_answer=case.expected_answer,
            actual_answer=resp.answer,
            context_snippets=[c.text for c in active_chunks],
            should_abstain=case.should_abstain,
        )

    method_scores: Dict[str, Any] = {}
    for mid in methods:
        fn = CASE_EVALUATORS[mid]
        method_scores[mid] = fn(case, resp, judge_res)

    failure_tags: List[str] = []
    if resp.error:
        failure_tags.append("adapter_error")
    ret = method_scores.get("retrieval_hit_rate", {})
    if ret.get("applicable"):
        if ret.get("rerank_cutoff_loss"):
            failure_tags.append("rerank_cutoff_loss")
        elif ret.get("hit_at_k", 1.0) < 1.0:
            failure_tags.append("retrieval_miss")
        elif ret.get("recall_at_k", 1.0) < 1.0:
            failure_tags.append("partial_recall")

    acc = method_scores.get("answer_accuracy", {})
    if acc.get("applicable") and not acc.get("passed"):
        failure_tags.append("low_accuracy")
        if acc.get("numeric_accuracy") is not None and acc["numeric_accuracy"] < 0.8:
            failure_tags.append("numeric_drift")

    hal = method_scores.get("hallucination_audit", {})
    if hal.get("applicable") and hal.get("hallucinated"):
        failure_tags.append("hallucination")
        for ht in hal.get("hallucination_types", []):
            if ht not in failure_tags:
                failure_tags.append(ht)

    abst = method_scores.get("abstention_guard", {})
    if abst.get("applicable"):
        if abst.get("oos_fabrication") and "oos_fabrication" not in failure_tags:
            failure_tags.append("oos_fabrication")
        if abst.get("false_refusal"):
            failure_tags.append("false_refusal")

    cit = method_scores.get("citation_check", {})
    if cit.get("applicable") and cit.get("citation_accuracy", 1.0) < 0.75:
        failure_tags.append("citation_mismatch")

    critical_failures = {
        "adapter_error",
        "retrieval_miss",
        "low_accuracy",
        "hallucination",
        "oos_fabrication",
        "false_refusal",
    }
    passed = not any(tag in critical_failures for tag in failure_tags)

    return {
        "case_id": case.id,
        "category": case.category,
        "difficulty": case.difficulty,
        "core_priority": case.core_priority,
        "should_abstain": case.should_abstain,
        "query": case.query,
        "expected_answer": case.expected_answer,
        "expected_doc_ids": case.expected_doc_ids,
        "must_include_facts": case.must_include_facts,
        "must_not_claim": case.must_not_claim,
        "response": resp.to_dict(),
        "method_scores": method_scores,
        "judge_result": judge_res,
        "failure_tags": failure_tags,
        "passed": passed,
    }


def run_evaluation(
    workdir: Path,
    methods: Optional[List[str]] = None,
    case_ids: Optional[List[str]] = None,
    categories: Optional[List[str]] = None,
    core_only: bool = False,
    adapter_override: Optional[Dict[str, Any]] = None,
    workers: Optional[int] = None,
    use_llm_judge: bool = False,
    force_unconfirmed: bool = False,
    resume_run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run single-case, single-method, category-slice, or batch RAG backtest."""
    tracker = ResourceTracker()
    profile = load_profile(workdir)
    _, gate_warnings = check_understanding_gate(profile, force_unconfirmed=force_unconfirmed)
    assert profile is not None

    scope_plan = load_scope_plan(workdir)
    all_cases, dataset_meta = load_dataset(workdir)
    if not all_cases:
        raise ValueError("未找到测试数据集（datasets/test_dataset.jsonl）。请先运行 `rag-eval generate` 生成测试集。")

    # Filter target test cases
    selected_cases = all_cases
    if case_ids:
        selected_cases = filter_dataset(selected_cases, case_ids=case_ids)
    if categories:
        cat_set = {c.strip() for c in categories if c.strip()}
        selected_cases = [c for c in selected_cases if c.category in cat_set]
    if core_only:
        selected_cases = [c for c in selected_cases if c.core_priority == 1]

    if not selected_cases:
        raise ValueError("过滤条件下没有匹配的测试用例，请检查 --case-id / --categories / --core-only 参数。")

    # Determine execution mode label
    if case_ids and len(selected_cases) == 1:
        exec_mode = "single_case"
    elif methods and len(methods) == 1 and methods[0] != "all":
        exec_mode = "single_method"
    elif categories:
        exec_mode = "category_slice"
    elif core_only:
        exec_mode = "core_batch"
    else:
        exec_mode = "batch"

    # Build target RAG adapter
    ad_cfg = dict(profile.adapter_config or {})
    if adapter_override:
        ad_cfg.update({k: v for k, v in adapter_override.items() if v is not None})
    ad_type = str(ad_cfg.get("type", "")).strip()
    if not ad_type:
        raise ValueError("未指定目标 RAG 适配器类型，请通过 --adapter 或 `rag-eval confirm` 配置。")

    kb_files = (
        scope_plan.selected_kb_files
        if scope_plan and scope_plan.selected_kb_files
        else [f["path"] for f in profile.corpus_stats.get("file_inventory", [])]
    )
    kb_root = profile.kb_paths[0] if profile.kb_paths else None
    adapter: BaseRAGAdapter = build_adapter(ad_type, ad_cfg, kb_files=kb_files, kb_root=kb_root, tracker=tracker)

    # Resolve methods respecting observability
    methods_exec, methods_skipped = _resolve_methods(
        methods,
        profile,
        scope_plan,
        allow_observed_override=(ad_type == "demo"),
    )
    if not methods_exec:
        raise ValueError("当前可观测性配置下没有可执行的测试方法：" + str(methods_skipped))

    # Optional LLM judge
    judge = LLMJudge() if use_llm_judge else None
    judge_active = bool(judge and judge.is_available)
    if use_llm_judge and not judge_active:
        gate_warnings.append(
            "已启用 --llm-judge 但未检测到 API Key（DEEPSEEK_API_KEY / OPENAI_API_KEY / RAG_EVAL_JUDGE_API_KEY），已自动回退为纯确定性严谨评测。"
        )

    # Prepare run folder & incremental checkpointing (unique suffix avoids same-second collisions)
    run_id = resume_run_id or (
        "run_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    )
    run_dir = workdir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = run_dir / "responses.jsonl"

    cached_responses: Dict[str, RAGResponse] = {}
    if checkpoint_path.exists():
        for row in iter_jsonl(checkpoint_path):
            r = RAGResponse.from_dict(row)
            if r.case_id:
                cached_responses[r.case_id] = r

    worker_cnt = safe_worker_count(workers if workers is not None else (scope_plan.max_workers if scope_plan else 2))
    pending_cases = [c for c in selected_cases if c.id not in cached_responses]

    # Execute with bounded worker pool & memory checks
    if pending_cases:
        if worker_cnt <= 1 or len(pending_cases) <= 1 or ad_type == "offline":
            for c in pending_cases:
                tracker.check_memory()
                resp = adapter.query(c)
                cached_responses[c.id] = resp
                append_jsonl(checkpoint_path, resp.to_dict())
        else:
            with ThreadPoolExecutor(max_workers=worker_cnt) as pool:
                fut_map = {pool.submit(adapter.query, c): c for c in pending_cases}
                for fut in as_completed(fut_map):
                    c = fut_map[fut]
                    try:
                        resp = fut.result()
                    except Exception as exc:
                        resp = RAGResponse(case_id=c.id, answer="", error=f"Worker exception: {exc}")
                    cached_responses[c.id] = resp
                    append_jsonl(checkpoint_path, resp.to_dict())
                    tracker.check_memory()

    # Score every case cleanly in deterministic order
    case_evals: List[Dict[str, Any]] = []
    for c in selected_cases:
        resp = cached_responses[c.id]
        ce = _evaluate_single_case(c, resp, methods_exec, judge=judge if judge_active else None)
        case_evals.append(ce)

    metrics = aggregate_metrics(
        case_evals=case_evals,
        methods_executed=methods_exec,
        methods_skipped=methods_skipped,
        thresholds=profile.thresholds,
        judge_used=judge_active,
    )
    category_breakdown = _compute_category_breakdown(case_evals)
    diagnosis = diagnose_run(case_evals, metrics, profile)

    result: Dict[str, Any] = {
        "run_id": run_id,
        "created_at": utc_now_iso(),
        "execution_mode": exec_mode,
        "adapter_type": ad_type,
        "judge_mode": f"hybrid ({judge.model})" if judge_active and judge else "deterministic",
        "gate_warnings": gate_warnings,
        "unverified_warning": any("UNVERIFIED_UNDERSTANDING" in w for w in gate_warnings),
        "profile_snapshot": {
            "system_name": profile.system_name,
            "architecture_summary": profile.architecture_summary,
            "observability": profile.observability.to_dict(),
            "readiness_score": profile.readiness_score,
            "confirmed": profile.confirmed,
            "confirmed_at": profile.confirmed_at,
        },
        "scope_plan": scope_plan.to_dict() if scope_plan else None,
        "dataset_meta": {
            "total_dataset_cases": len(all_cases),
            "executed_cases": len(selected_cases),
            "core_executed": sum(1 for c in selected_cases if c.core_priority == 1),
        },
        "methods_requested": methods or ["all"],
        "methods_executed": methods_exec,
        "methods_skipped": methods_skipped,
        "metrics": [m.to_dict() for m in metrics],
        "category_breakdown": category_breakdown,
        "diagnosis": diagnosis,
        "case_evaluations": case_evals,
        "resource_stats": tracker.snapshot(workers_used=worker_cnt),
    }

    write_json_atomic(run_dir / "eval_result.json", result)
    write_json_atomic(workdir / "latest_run.json", {"run_id": run_id, "created_at": result["created_at"]})
    return result


def evaluate_manual_input(
    query: str,
    answer: str,
    expected_answer: str = "",
    retrieved_chunks: Optional[List[Dict[str, Any]]] = None,
    expected_doc_ids: Optional[List[str]] = None,
    must_include_facts: Optional[List[str]] = None,
    must_not_claim: Optional[List[str]] = None,
    expected_numbers: Optional[List[str]] = None,
    should_abstain: bool = False,
    citations: Optional[List[str]] = None,
    methods: Optional[List[str]] = None,
    category: str = "single_hop_fact",
) -> Dict[str, Any]:
    """Zero-setup manual single-case evaluator for interactive / ad-hoc RAG inspection."""
    tc = TestCase(
        id="MANUAL-001",
        category=category,
        difficulty="medium",
        core_priority=1,
        query=query,
        expected_answer=expected_answer,
        expected_doc_ids=list(expected_doc_ids or []),
        must_include_facts=list(must_include_facts or []),
        must_not_claim=list(must_not_claim or []),
        expected_numbers=list(expected_numbers or []),
        should_abstain=should_abstain,
    )
    chunks = [
        RetrievedChunk.from_dict(ch) if isinstance(ch, dict) else RetrievedChunk(doc_id=f"manual_{i+1}", text=str(ch), rank=i + 1)
        for i, ch in enumerate(retrieved_chunks or [])
    ]
    resp = RAGResponse(
        case_id=tc.id,
        answer=answer,
        retrieved_chunks=chunks,
        citations=list(citations or []),
        latency_ms=0.0,
    )
    chosen = methods if (methods and methods != ["all"]) else [
        m for m in METHOD_SPECS
        if (not METHOD_SPECS[m].requires_retrieved_docs or bool(chunks))
    ]
    chosen = [m for m in chosen if m in CASE_EVALUATORS]
    ce = _evaluate_single_case(tc, resp, chosen, judge=None)
    return {
        "mode": "manual_single_eval",
        "methods_executed": chosen,
        "case_evaluation": ce,
    }


def load_run_result(workdir: Path, run_id: Optional[str] = None) -> Dict[str, Any]:
    if not run_id:
        latest = workdir / "latest_run.json"
        if not latest.exists():
            raise FileNotFoundError("尚未找到历史评测记录，请先运行 `rag-eval run`。")
        run_id = read_json(latest)["run_id"]
    path = workdir / "runs" / run_id / "eval_result.json"
    if not path.exists():
        raise FileNotFoundError(f"未找到评测结果文件: {path}")
    return read_json(path)


def list_runs(workdir: Path) -> List[Dict[str, Any]]:
    runs_dir = workdir / "runs"
    if not runs_dir.exists():
        return []
    out: List[Dict[str, Any]] = []
    for sub in sorted(runs_dir.iterdir(), reverse=True):
        res_file = sub / "eval_result.json"
        if res_file.exists():
            data = read_json(res_file)
            out.append(
                {
                    "run_id": data.get("run_id", sub.name),
                    "created_at": data.get("created_at", ""),
                    "execution_mode": data.get("execution_mode", ""),
                    "adapter_type": data.get("adapter_type", ""),
                    "executed_cases": data.get("dataset_meta", {}).get("executed_cases", 0),
                    "overall_grade": data.get("diagnosis", {}).get("overall_grade", ""),
                    "severity_counts": data.get("diagnosis", {}).get("severity_counts", {}),
                }
            )
    return out
