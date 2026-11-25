"""Zero-dependency Stdio MCP Server (JSON-RPC 2.0) for RAG Eval Suite.

Compatible with Antigravity, Claude Code, OpenAI Codex, and DeepSeek Harness (dsh).
Uses pure Python stdlib (<25 MB RSS) and supports newline-delimited JSON stdio transport.
"""

from __future__ import annotations

import importlib
import json
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .dataset_gen import (
    export_dataset_markdown,
    filter_dataset,
    generate_test_dataset,
    load_dataset,
    save_dataset,
    summarize_dataset,
)
from .inspector import (
    UnderstandingGateError,
    confirm_profile,
    inspect_rag_system,
    load_profile,
    save_profile,
)
from .methods import METHOD_SPECS
from .reporter import generate_reports
from .resource_guard import get_peak_rss_mb
from .runner import evaluate_manual_input, list_runs, run_evaluation
from .scope_pruner import load_scope_plan, plan_evaluation_scope, save_scope_plan

SUPPORTED_PROTOCOLS = ["2025-03-26", "2024-11-05"]

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "rag_eval_inspect",
        "description": "Phase 1 (Understand-First): Inspect target RAG codebase & knowledge base to discover architecture, observability, and blocking unknowns before any testing.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_path": {"type": "string", "description": "Target RAG codebase directory"},
                "kb_paths": {"type": "array", "items": {"type": "string"}, "description": "Knowledge base directory/file paths"},
                "system_name": {"type": "string"},
                "workdir": {"type": "string", "default": ".rag_eval"},
                "adapter_config": {"type": "object"},
                "observability_overrides": {"type": "object"},
            },
            "required": ["project_path"],
        },
    },
    {
        "name": "rag_eval_confirm",
        "description": "Resolve blocking unknowns and explicitly confirm the RAG understanding profile to unlock test generation and backtesting.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workdir": {"type": "string", "default": ".rag_eval"},
                "adapter_config": {"type": "object", "description": "e.g. {'type':'http','endpoint':'http://localhost:8000/rag'} or {'type':'offline','responses_file':'...'} or {'type':'demo'}"},
                "observability_overrides": {"type": "object", "description": "e.g. {'retrieved_docs': true, 'citations': true}"},
                "resolutions": {"type": "object", "description": "Map of unknown ID -> resolution note"},
                "architecture_note": {"type": "string"},
                "confirmed_by": {"type": "string", "default": "user"},
            },
        },
    },
    {
        "name": "rag_eval_scope",
        "description": "Phase 2: Profile RAG complexity and recommend/apply Core Scope Pruning (smoke / core / full) with an explicit Tested vs Deferred disclosure.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workdir": {"type": "string", "default": ".rag_eval"},
                "profile": {"type": "string", "enum": ["auto", "smoke", "core", "full"], "default": "auto"},
                "max_cases": {"type": "integer"},
                "core_paths": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    {
        "name": "rag_eval_generate_dataset",
        "description": "Phase 3: Generate categorized, corpus-grounded RAG test dataset (enforces Understand-First Gate).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workdir": {"type": "string", "default": ".rag_eval"},
                "scope": {"type": "string", "enum": ["smoke", "core", "full"]},
                "max_cases": {"type": "integer"},
                "use_llm_gen": {"type": "boolean", "default": False},
                "force_unconfirmed": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "rag_eval_view_dataset",
        "description": "View generated test data separately with category distribution, coverage balance warnings, and filters (category / difficulty / core_only / search).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workdir": {"type": "string", "default": ".rag_eval"},
                "category": {"type": "string"},
                "difficulty": {"type": "string"},
                "core_only": {"type": "boolean", "default": False},
                "search": {"type": "string"},
                "limit": {"type": "integer", "default": 30},
                "export_markdown": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "rag_eval_list_methods",
        "description": "List all modular RAG test methods, their metrics, and required observability flags.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "rag_eval_run",
        "description": "Phase 4-6: Execute single test method, single case, category slice, core subset, full batch, or offline manual snapshot, and produce P0-P3 root-cause diagnosis + HTML/MD reports.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workdir": {"type": "string", "default": ".rag_eval"},
                "methods": {"type": "array", "items": {"type": "string"}, "description": "Method IDs or ['all']"},
                "case_ids": {"type": "array", "items": {"type": "string"}},
                "categories": {"type": "array", "items": {"type": "string"}},
                "core_only": {"type": "boolean", "default": False},
                "adapter_override": {"type": "object"},
                "workers": {"type": "integer", "default": 2},
                "use_llm_judge": {"type": "boolean", "default": False},
                "include_cases": {"type": "boolean", "default": False, "description": "Include full case_evaluations drilldown array in tool output"},
                "force_unconfirmed": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "rag_eval_manual_eval",
        "description": "Run instant ad-hoc manual single-case evaluation on a pasted query, retrieved chunks, actual answer, and expected answer (no live RAG needed).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "answer": {"type": "string"},
                "expected_answer": {"type": "string", "default": ""},
                "retrieved_chunks": {"type": "array", "items": {"type": "object"}},
                "expected_doc_ids": {"type": "array", "items": {"type": "string"}},
                "must_include_facts": {"type": "array", "items": {"type": "string"}},
                "must_not_claim": {"type": "array", "items": {"type": "string"}},
                "should_abstain": {"type": "boolean", "default": False},
                "methods": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["query", "answer"],
        },
    },
    {
        "name": "rag_eval_generate_report",
        "description": "Generate standalone browsable HTML analysis page and downloadable Markdown (.md) report for a completed run.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workdir": {"type": "string", "default": ".rag_eval"},
                "run_id": {"type": "string"},
                "update_latest": {"type": "boolean"},
            },
        },
    },
    {
        "name": "rag_eval_status",
        "description": "Check RAG evaluation workspace status, understanding confirmation state, dataset counts, recent runs, and low-footprint memory usage (RSS MB).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workdir": {"type": "string", "default": ".rag_eval"},
            },
        },
    },
]


_PKG_DIR = Path(__file__).resolve().parent
_MODULE_ORDER = [
    "rag_eval.models",
    "rag_eval.resource_guard",
    "rag_eval.inspector",
    "rag_eval.scope_pruner",
    "rag_eval.dataset_gen",
    "rag_eval.adapters",
    "rag_eval.llm_judge",
    "rag_eval.methods",
    "rag_eval.diagnostics",
    "rag_eval.runner",
    "rag_eval.web.studio_template",
    "rag_eval.reporter",
]


def _max_pkg_mtime_ns() -> int:
    latest = 0
    for p in _PKG_DIR.rglob("*.py"):
        if p.name == "mcp_server.py":
            continue
        try:
            mt = p.stat().st_mtime_ns
            if mt > latest:
                latest = mt
        except OSError:
            pass
    return latest


_LAST_MTIME_NS = _max_pkg_mtime_ns()


def _hot_reload_if_changed() -> None:
    global _LAST_MTIME_NS
    global export_dataset_markdown, filter_dataset, generate_test_dataset, load_dataset, save_dataset, summarize_dataset
    global UnderstandingGateError, confirm_profile, inspect_rag_system, load_profile, save_profile
    global METHOD_SPECS, generate_reports, evaluate_manual_input, list_runs, run_evaluation
    global load_scope_plan, plan_evaluation_scope, save_scope_plan

    cur = _max_pkg_mtime_ns()
    if cur <= _LAST_MTIME_NS:
        return
    for mod_name in _MODULE_ORDER:
        mod = sys.modules.get(mod_name)
        if mod is not None:
            importlib.reload(mod)
    from .dataset_gen import (
        export_dataset_markdown as _edm,
        filter_dataset as _fd,
        generate_test_dataset as _gtd,
        load_dataset as _ld,
        save_dataset as _sd,
        summarize_dataset as _sumd,
    )
    from .inspector import (
        UnderstandingGateError as _uge,
        confirm_profile as _cp,
        inspect_rag_system as _irs,
        load_profile as _lp,
        save_profile as _sp,
    )
    from .methods import METHOD_SPECS as _ms
    from .reporter import generate_reports as _gr
    from .runner import evaluate_manual_input as _emi, list_runs as _lr, run_evaluation as _re
    from .scope_pruner import load_scope_plan as _lsp, plan_evaluation_scope as _pes, save_scope_plan as _ssp

    export_dataset_markdown, filter_dataset, generate_test_dataset = _edm, _fd, _gtd
    load_dataset, save_dataset, summarize_dataset = _ld, _sd, _sumd
    UnderstandingGateError, confirm_profile, inspect_rag_system, load_profile, save_profile = _uge, _cp, _irs, _lp, _sp
    METHOD_SPECS, generate_reports = _ms, _gr
    evaluate_manual_input, list_runs, run_evaluation = _emi, _lr, _re
    load_scope_plan, plan_evaluation_scope, save_scope_plan = _lsp, _pes, _ssp
    _LAST_MTIME_NS = cur


def _wd(args: Dict[str, Any]) -> Path:
    return Path(args.get("workdir") or ".rag_eval").expanduser().resolve()


def dispatch_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    _hot_reload_if_changed()
    if name == "rag_eval_inspect":
        workdir = _wd(args)
        profile, res_stats = inspect_rag_system(
            project_path=args["project_path"],
            kb_paths=args.get("kb_paths"),
            system_name=args.get("system_name"),
            adapter_config=args.get("adapter_config"),
            observability_overrides=args.get("observability_overrides"),
        )
        out_path = save_profile(workdir, profile)
        return {
            "profile_path": str(out_path),
            "readiness_score": profile.readiness_score,
            "confirmed": profile.confirmed,
            "architecture_summary": profile.architecture_summary,
            "blocking_unknowns": [u.to_dict() for u in profile.blocking_unknowns()],
            "non_blocking_unknowns": [u.to_dict() for u in profile.unknowns if not u.blocking],
            "corpus_summary": {k: v for k, v in profile.corpus_stats.items() if k != "file_inventory"},
            "resource_stats": res_stats,
        }

    if name == "rag_eval_confirm":
        workdir = _wd(args)
        profile = load_profile(workdir)
        if not profile:
            raise FileNotFoundError(f"Profile not found at {workdir / 'rag_profile.json'}. Call rag_eval_inspect first.")
        updated = confirm_profile(
            profile,
            confirmed_by=str(args.get("confirmed_by") or "user"),
            resolutions=args.get("resolutions"),
            adapter_config=args.get("adapter_config"),
            observability_overrides=args.get("observability_overrides"),
            architecture_note=args.get("architecture_note"),
        )
        out_path = save_profile(workdir, updated)
        return {
            "confirmed": updated.confirmed,
            "readiness_score": updated.readiness_score,
            "profile_path": str(out_path),
        }

    if name == "rag_eval_scope":
        workdir = _wd(args)
        profile = load_profile(workdir)
        if not profile:
            raise FileNotFoundError("Profile not found. Call rag_eval_inspect first.")
        plan = plan_evaluation_scope(
            profile,
            requested_mode=str(args.get("profile") or "auto"),
            max_cases=args.get("max_cases"),
            core_paths=args.get("core_paths"),
        )
        out_path = save_scope_plan(workdir, plan)
        return {"scope_path": str(out_path), "scope_plan": plan.to_dict()}

    if name == "rag_eval_generate_dataset":
        workdir = _wd(args)
        profile = load_profile(workdir)
        if not profile:
            raise UnderstandingGateError("No RAG profile found. Run rag_eval_inspect and rag_eval_confirm first.")
        plan = load_scope_plan(workdir)
        if plan is None or args.get("scope") or args.get("max_cases"):
            plan = plan_evaluation_scope(
                profile,
                requested_mode=str(args.get("scope") or (plan.profile_mode if plan else "auto")),
                max_cases=args.get("max_cases"),
            )
            save_scope_plan(workdir, plan)
        cases, meta = generate_test_dataset(
            profile,
            plan,
            force_unconfirmed=bool(args.get("force_unconfirmed", False)),
            use_llm_gen=bool(args.get("use_llm_gen", False)),
        )
        ds_path, meta_path = save_dataset(workdir, cases, meta)
        return {"dataset_path": str(ds_path), "meta_path": str(meta_path), "meta": meta}

    if name == "rag_eval_view_dataset":
        workdir = _wd(args)
        cases, _ = load_dataset(workdir)
        filtered = filter_dataset(
            cases,
            category=args.get("category"),
            difficulty=args.get("difficulty"),
            core_only=bool(args.get("core_only", False)),
            search=args.get("search"),
        )
        limit = int(args.get("limit") or 30)
        out: Dict[str, Any] = {
            "coverage_summary": summarize_dataset(cases),
            "filtered_total": len(filtered),
            "cases": [c.to_dict() for c in filtered[:limit]],
        }
        if args.get("export_markdown"):
            out["markdown"] = export_dataset_markdown(filtered)
        return out

    if name == "rag_eval_list_methods":
        return {"methods": [m.to_dict() for m in METHOD_SPECS.values()]}

    if name == "rag_eval_run":
        workdir = _wd(args)
        res = run_evaluation(
            workdir=workdir,
            methods=args.get("methods"),
            case_ids=args.get("case_ids"),
            categories=args.get("categories"),
            core_only=bool(args.get("core_only", False)),
            adapter_override=args.get("adapter_override"),
            workers=args.get("workers"),
            use_llm_judge=bool(args.get("use_llm_judge", False)),
            force_unconfirmed=bool(args.get("force_unconfirmed", False)),
        )
        html_path, md_path, run_id = generate_reports(workdir, run_id=res["run_id"])
        out: Dict[str, Any] = {
            "run_id": run_id,
            "execution_mode": res["execution_mode"],
            "executed_cases": res["dataset_meta"]["executed_cases"],
            "metrics": res["metrics"],
            "category_breakdown": res["category_breakdown"],
            "diagnosis": res["diagnosis"],
            "html_report": str(html_path),
            "markdown_report": str(md_path),
            "resource_stats": res["resource_stats"],
        }
        if args.get("include_cases"):
            out["case_evaluations"] = res["case_evaluations"]
        return out

    if name == "rag_eval_manual_eval":
        return evaluate_manual_input(
            query=str(args["query"]),
            answer=str(args["answer"]),
            expected_answer=str(args.get("expected_answer") or ""),
            retrieved_chunks=args.get("retrieved_chunks") or [],
            expected_doc_ids=args.get("expected_doc_ids") or [],
            must_include_facts=args.get("must_include_facts") or [],
            must_not_claim=args.get("must_not_claim") or [],
            should_abstain=bool(args.get("should_abstain", False)),
            methods=args.get("methods"),
        )

    if name == "rag_eval_generate_report":
        workdir = _wd(args)
        html_path, md_path, run_id = generate_reports(
            workdir,
            run_id=args.get("run_id"),
            update_latest=args.get("update_latest"),
        )
        return {"run_id": run_id, "html_report": str(html_path), "markdown_report": str(md_path)}

    if name == "rag_eval_status":
        workdir = _wd(args)
        profile = load_profile(workdir)
        plan = load_scope_plan(workdir)
        cases, _ = load_dataset(workdir)
        return {
            "workdir": str(workdir),
            "profile_exists": profile is not None,
            "confirmed": profile.confirmed if profile else False,
            "readiness_score": profile.readiness_score if profile else 0,
            "scope_mode": plan.profile_mode if plan else None,
            "dataset_cases": len(cases),
            "recent_runs": list_runs(workdir)[:5],
            "process_rss_mb": get_peak_rss_mb(),
        }

    raise ValueError(f"Unknown tool: {name}")


def handle_rpc_message(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Process one JSON-RPC 2.0 MCP message and return response dict (or None for notifications)."""
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "notifications/initialized" or msg_id is None:
        return None

    if method == "initialize":
        client_ver = params.get("protocolVersion", SUPPORTED_PROTOCOLS[0])
        chosen = client_ver if client_ver in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": chosen,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "rag-eval-suite", "version": __version__},
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        tname = str(params.get("name", ""))
        targs = params.get("arguments") or {}
        try:
            payload = dispatch_tool(tname, targs)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
                    "isError": False,
                },
            }
        except UnderstandingGateError as exc:
            err_payload = {
                "error": "UNDERSTANDING_GATE_BLOCKED",
                "message": str(exc),
                "blocking_unknowns": exc.blocking_unknowns,
            }
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(err_payload, ensure_ascii=False, indent=2)}],
                    "isError": True,
                },
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps({"error": str(exc), "trace": traceback.format_exc(limit=3)}, ensure_ascii=False)}],
                    "isError": True,
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def serve_stdio() -> None:
    """Run newline-delimited JSON-RPC 2.0 MCP loop on stdin/stdout."""
    stdin = sys.stdin
    stdout = sys.stdout
    for raw_line in stdin:
        line = raw_line.strip()
        if not line:
            continue
        # Tolerate optional Content-Length header lines if a legacy client sends them
        if line.lower().startswith("content-length:"):
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle_rpc_message(msg)
        if resp is not None:
            stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            stdout.flush()


if __name__ == "__main__":
    serve_stdio()
