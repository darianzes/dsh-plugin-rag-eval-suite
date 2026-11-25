"""Full-featured manual & scriptable CLI for RAG Eval Suite."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

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
from .models import CATEGORY_SPECS, TestCase
from .reporter import generate_reports
from .resource_guard import get_peak_rss_mb
from .runner import evaluate_manual_input, list_runs, load_run_result, run_evaluation
from .scope_pruner import load_scope_plan, plan_evaluation_scope, save_scope_plan
from .server import serve_studio


def _print_out(data: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif isinstance(data, str):
        print(data)
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def _parse_bool(val: Optional[str]) -> Optional[bool]:
    if val is None:
        return None
    low = val.strip().lower()
    if low in {"1", "true", "yes", "y", "on"}:
        return True
    if low in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"无法解析布尔值: {val}")


def cmd_inspect(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).expanduser().resolve()
    kb_list = [p.strip() for p in args.kb.split(",")] if args.kb else None
    obs_overrides: Dict[str, bool] = {}
    if args.obs_retrieved is not None:
        obs_overrides["retrieved_docs"] = bool(_parse_bool(args.obs_retrieved))
    if args.obs_citations is not None:
        obs_overrides["citations"] = bool(_parse_bool(args.obs_citations))

    ad_cfg: Dict[str, Any] = {}
    if args.adapter:
        ad_cfg["type"] = args.adapter
        if args.endpoint:
            ad_cfg["endpoint"] = args.endpoint
        if args.callable:
            ad_cfg["callable"] = args.callable
        if args.responses:
            ad_cfg["responses_file"] = args.responses

    profile, res_stats = inspect_rag_system(
        project_path=args.project,
        kb_paths=kb_list,
        system_name=args.name,
        adapter_config=ad_cfg or None,
        observability_overrides=obs_overrides or None,
    )
    out_path = save_profile(workdir, profile)
    payload = {
        "profile_path": str(out_path),
        "readiness_score": profile.readiness_score,
        "confirmed": profile.confirmed,
        "architecture_summary": profile.architecture_summary,
        "blocking_unknowns": [u.to_dict() for u in profile.blocking_unknowns()],
        "all_unknowns": [u.to_dict() for u in profile.unknowns],
        "corpus_summary": {
            k: v for k, v in profile.corpus_stats.items() if k != "file_inventory"
        },
        "resource_stats": res_stats,
    }
    if args.json:
        _print_out(payload, True)
    else:
        print(f"✅ 已完成 RAG 系统理解扫描 -> {out_path}")
        print(f"   系统摘要: {profile.architecture_summary}")
        print(f"   理解就绪度: {profile.readiness_score}/100 | 已确认: {profile.confirmed}")
        blocking = profile.blocking_unknowns()
        if blocking:
            print("   ⚠️ 存在阻断性待确认项（解决后方可通过理解门禁）：")
            for u in blocking:
                print(f"      - [{u.id}] ({u.area}) {u.question}")
        else:
            print("   👍 无阻断性未知项，请核对档案后运行 `rag-eval confirm` 解锁测试生成与回测。")
    return 0


def cmd_confirm(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).expanduser().resolve()
    profile = load_profile(workdir)
    if not profile:
        raise FileNotFoundError(f"未找到理解档案: {workdir / 'rag_profile.json'}，请先执行 `inspect`。")

    ad_cfg: Dict[str, Any] = {}
    if args.adapter:
        ad_cfg["type"] = args.adapter
    if args.endpoint:
        ad_cfg["endpoint"] = args.endpoint
    if args.callable:
        ad_cfg["callable"] = args.callable
    if args.command:
        ad_cfg["command"] = args.command
    if args.responses:
        ad_cfg["responses_file"] = args.responses
    if args.demo_mode:
        ad_cfg["demo_mode"] = args.demo_mode

    obs: Dict[str, bool] = {}
    if args.obs_retrieved is not None:
        obs["retrieved_docs"] = bool(_parse_bool(args.obs_retrieved))
    if args.obs_citations is not None:
        obs["citations"] = bool(_parse_bool(args.obs_citations))

    resolutions: Dict[str, str] = {}
    for item in args.resolve or []:
        if "=" in item:
            k, v = item.split("=", 1)
            resolutions[k.strip()] = v.strip()

    updated = confirm_profile(
        profile,
        confirmed_by=args.by or "user",
        resolutions=resolutions or None,
        adapter_config=ad_cfg or None,
        observability_overrides=obs or None,
        architecture_note=args.note,
    )
    path = save_profile(workdir, updated)
    if args.json:
        _print_out({"confirmed": updated.confirmed, "readiness_score": updated.readiness_score, "profile_path": str(path)}, True)
    else:
        print(f"✅ 系统理解档案已确认！Readiness: {updated.readiness_score}/100 -> {path}")
    return 0


def cmd_scope(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).expanduser().resolve()
    profile = load_profile(workdir)
    if not profile:
        raise FileNotFoundError("未找到理解档案，请先执行 `inspect`。")
    core_paths = [p.strip() for p in args.core_paths.split(",")] if args.core_paths else None
    plan = plan_evaluation_scope(
        profile,
        requested_mode=args.profile,
        max_cases=args.max_cases,
        core_paths=core_paths,
    )
    out_path = save_scope_plan(workdir, plan)
    if args.json:
        _print_out({"scope_path": str(out_path), "scope_plan": plan.to_dict()}, True)
    else:
        print(f"✅ 测试范围与核心精简方案已生成 -> {out_path}")
        print(f"   复杂度: {plan.complexity_level} ({plan.complexity_score}/100) | 推荐模式: {plan.recommended_mode} | 采用模式: {plan.profile_mode}")
        print(f"   披露声明: {plan.disclosure_statement}")
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).expanduser().resolve()
    profile = load_profile(workdir)
    if not profile:
        raise UnderstandingGateError("未找到理解档案，请先执行 `inspect` 并 `confirm`。")
    plan = load_scope_plan(workdir)
    if plan is None or args.scope or args.max_cases:
        plan = plan_evaluation_scope(
            profile,
            requested_mode=args.scope or (plan.profile_mode if plan else "auto"),
            max_cases=args.max_cases,
        )
        save_scope_plan(workdir, plan)

    cases, meta = generate_test_dataset(
        profile,
        plan,
        force_unconfirmed=bool(args.force_unconfirmed),
        use_llm_gen=bool(args.llm_gen),
    )
    ds_path, meta_path = save_dataset(workdir, cases, meta)
    if args.json:
        _print_out({"dataset_path": str(ds_path), "meta_path": str(meta_path), "meta": meta}, True)
    else:
        cov = meta["coverage_summary"]
        print(f"✅ 已生成分类测试数据集: {len(cases)} 条用例（核心用例 {cov['core_cases']} 条，拒答边界 {cov['abstain_cases']} 条）-> {ds_path}")
        for c in cov["categories"]:
            if c["count"] > 0:
                print(f"   - {c['zh_name']} ({c['category']}): {c['count']} 条 ({c['ratio']*100:.1f}%)")
    return 0


def cmd_dataset(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).expanduser().resolve()
    cases, _ = load_dataset(workdir)
    action = args.ds_action

    if action == "summary":
        summary = summarize_dataset(cases)
        if args.json:
            _print_out(summary, True)
        else:
            print(f"📊 测试数据集总览: 共 {summary['total_cases']} 条（核心 {summary['core_cases']} / 拒答 {summary['abstain_cases']}）")
            for c in summary["categories"]:
                if c["count"] > 0:
                    print(f"   [{c['category']:22s}] {c['zh_name']:12s}: {c['count']:3d} 条 ({c['ratio']*100:4.1f}%)")
            for w in summary["balance_warnings"]:
                print(f"   ⚠️ {w}")
        return 0

    if action == "list":
        filtered = filter_dataset(
            cases,
            category=args.category,
            difficulty=args.difficulty,
            core_only=bool(args.core_only),
            search=args.search,
        )
        limited = filtered[: args.limit] if args.limit else filtered
        if args.json:
            _print_out({"total": len(filtered), "shown": len(limited), "cases": [c.to_dict() for c in limited]}, True)
        else:
            print(f"🗂️ 展示 {len(limited)} / {len(filtered)} 条测试数据：")
            for c in limited:
                cat_zh = CATEGORY_SPECS.get(c.category, {}).get("zh_name", c.category)
                badge = "CORE" if c.core_priority == 1 else "STD"
                print(f"  {c.id} [{cat_zh} | {c.difficulty} | {badge}] {c.query}")
        return 0

    if action == "show":
        matches = [c for c in cases if c.id == args.case_id]
        if not matches:
            raise ValueError(f"未找到用例 ID: {args.case_id}")
        _print_out(matches[0].to_dict(), True)
        return 0

    if action == "export":
        md = export_dataset_markdown(cases)
        if args.output:
            outp = Path(args.output).expanduser().resolve()
            outp.parent.mkdir(parents=True, exist_ok=True)
            outp.write_text(md, encoding="utf-8")
            print(f"✅ 已导出分类测试集 Markdown -> {outp}")
        else:
            print(md)
        return 0

    if action == "add":
        new_id = args.id or f"TC-CUSTOM-{len(cases)+1:03d}"
        tc = TestCase(
            id=new_id,
            category=args.category or "single_hop_fact",
            difficulty=args.difficulty or "medium",
            core_priority=1 if args.core else 2,
            query=args.query,
            expected_answer=args.expected,
            expected_doc_ids=[x.strip() for x in (args.docs or "").split(",") if x.strip()],
            must_include_facts=[x.strip() for x in (args.facts or "").split(",") if x.strip()],
            must_not_claim=[x.strip() for x in (args.forbid or "").split(",") if x.strip()],
            should_abstain=bool(args.abstain),
            rationale="用户手动添加用例",
        )
        cases.append(tc)
        save_dataset(workdir, cases, {"updated_manually": True})
        _print_out({"added": tc.to_dict(), "total_cases": len(cases)}, args.json)
        return 0

    return 1


def cmd_methods(args: argparse.Namespace) -> int:
    items = [m.to_dict() for m in METHOD_SPECS.values()]
    if args.json:
        _print_out(items, True)
    else:
        print("⚙️ 可用模块化测试方法（支持单独或批量执行）：")
        for m in items:
            obs = "需召回片段" if m["requires_retrieved_docs"] else "仅需答案/元信息"
            print(f"  - {m['id']:20s} | {m['zh_name']} ({obs})")
            print(f"    指标: {', '.join(m['metrics'])}")
            print(f"    说明: {m['description']}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).expanduser().resolve()
    methods = [m.strip() for m in args.methods.split(",") if m.strip()] if args.methods else None
    case_ids = [c.strip() for c in args.case_id.split(",") if c.strip()] if args.case_id else None
    categories = [c.strip() for c in args.categories.split(",") if c.strip()] if args.categories else None

    ad_override: Dict[str, Any] = {}
    if args.adapter:
        ad_override["type"] = args.adapter
    if args.endpoint:
        ad_override["endpoint"] = args.endpoint
    if args.callable:
        ad_override["callable"] = args.callable
    if args.responses:
        ad_override["responses_file"] = args.responses
    if args.demo_mode:
        ad_override["demo_mode"] = args.demo_mode

    res = run_evaluation(
        workdir=workdir,
        methods=methods,
        case_ids=case_ids,
        categories=categories,
        core_only=bool(args.core_only),
        adapter_override=ad_override or None,
        workers=args.workers,
        use_llm_judge=bool(args.llm_judge),
        force_unconfirmed=bool(args.force_unconfirmed),
        resume_run_id=args.resume,
    )
    html_path, md_path, run_id = generate_reports(workdir, run_id=res["run_id"])

    summary = {
        "run_id": run_id,
        "execution_mode": res["execution_mode"],
        "executed_cases": res["dataset_meta"]["executed_cases"],
        "overall_grade": res["diagnosis"]["overall_grade"],
        "severity_counts": res["diagnosis"]["severity_counts"],
        "metrics": res["metrics"],
        "category_breakdown": res["category_breakdown"],
        "diagnosis": res["diagnosis"],
        "html_report": str(html_path),
        "markdown_report": str(md_path),
        "resource_stats": res["resource_stats"],
    }
    if getattr(args, "detail", False):
        summary["case_evaluations"] = res["case_evaluations"]
    if args.json:
        _print_out(summary, True)
    else:
        print(f"✅ 回测完成！Run ID: {run_id} ({res['execution_mode']}, {res['dataset_meta']['executed_cases']} 条用例)")
        print(f"   综合评级: {res['diagnosis']['overall_grade']}")
        for m in res["metrics"]:
            val_str = f"{m['value']*100:.1f}%" if (m["value"] is not None and m["unit"] == "ratio") else str(m["value"])
            print(f"   - {m['zh_name']}: {val_str} [{m['status']}] (N={m['sample_size']})")
        print(f"   📊 浏览页面: {html_path}")
        print(f"   📄 Markdown: {md_path}")
        print(f"   🪶 资源占用: Peak RSS {res['resource_stats']['peak_rss_mb']} MB | 耗时 {res['resource_stats']['elapsed_sec']}s")
    return 0


def cmd_manual_eval(args: argparse.Namespace) -> int:
    chunks = [{"doc_id": f"chunk_{i+1}", "text": c} for i, c in enumerate(args.chunk or [])]
    facts = [x.strip() for x in (args.facts or "").split(",") if x.strip()]
    forbid = [x.strip() for x in (args.forbid or "").split(",") if x.strip()]
    methods = [m.strip() for m in args.methods.split(",") if m.strip()] if args.methods else None
    res = evaluate_manual_input(
        query=args.query,
        answer=args.answer,
        expected_answer=args.expected or "",
        retrieved_chunks=chunks,
        must_include_facts=facts,
        must_not_claim=forbid,
        should_abstain=bool(args.abstain),
        methods=methods,
    )
    _print_out(res, True)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).expanduser().resolve()
    update_latest = True if args.update_latest else (False if args.no_update_latest else None)
    html_path, md_path, run_id = generate_reports(workdir, run_id=args.run_id, update_latest=update_latest)
    out = {"run_id": run_id, "html_report": str(html_path), "markdown_report": str(md_path)}
    if args.json:
        _print_out(out, True)
    else:
        print(f"✅ 报告已生成 (Run: {run_id})")
        print(f"   直接浏览页面: {html_path}")
        print(f"   可下载 MD 文件: {md_path}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).expanduser().resolve()
    serve_studio(workdir=workdir, host=args.host, port=args.port, idle_timeout_sec=args.idle_timeout)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).expanduser().resolve()
    profile = load_profile(workdir)
    plan = load_scope_plan(workdir)
    cases, _ = load_dataset(workdir)
    runs = list_runs(workdir)
    out = {
        "workdir": str(workdir),
        "profile_exists": profile is not None,
        "confirmed": profile.confirmed if profile else False,
        "readiness_score": profile.readiness_score if profile else 0,
        "scope_mode": plan.profile_mode if plan else None,
        "dataset_cases": len(cases),
        "runs_count": len(runs),
        "recent_runs": runs[:5],
        "process_rss_mb": get_peak_rss_mb(),
    }
    _print_out(out, True)
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    plugin_root = Path(__file__).resolve().parent.parent
    sample_kb = plugin_root / "examples" / "sample_kb"
    workdir = Path(args.workdir).expanduser().resolve()

    profile, _ = inspect_rag_system(
        project_path=str(plugin_root),
        kb_paths=[str(sample_kb)],
        system_name="Enterprise RAG Demo",
        adapter_config={"type": "demo", "demo_mode": args.demo_mode},
        observability_overrides={"retrieved_docs": True, "citations": True, "reranked_docs": True},
    )
    confirm_profile(profile, confirmed_by="demo_runner")
    save_profile(workdir, profile)

    plan = plan_evaluation_scope(profile, requested_mode=args.scope, max_cases=args.max_cases)
    save_scope_plan(workdir, plan)

    cases, meta = generate_test_dataset(profile, plan)
    save_dataset(workdir, cases, meta)

    res = run_evaluation(workdir=workdir, methods=["all"], workers=2)
    html_path, md_path, run_id = generate_reports(workdir, run_id=res["run_id"])
    payload = {
        "run_id": run_id,
        "cases_generated": len(cases),
        "overall_grade": res["diagnosis"]["overall_grade"],
        "severity_counts": res["diagnosis"]["severity_counts"],
        "html_report": str(html_path),
        "markdown_report": str(md_path),
        "peak_rss_mb": res["resource_stats"]["peak_rss_mb"],
    }
    if args.json:
        _print_out(payload, True)
    else:
        print("🚀 RAG Eval Suite 端到端演示已完成！")
        print(f"   生成分类用例: {len(cases)} 条 | 综合评级: {res['diagnosis']['overall_grade']}")
        print(f"   问题分级统计: {res['diagnosis']['severity_counts']}")
        print(f"   📊 交互浏览页面: {html_path}")
        print(f"   📄 Markdown 报告: {md_path}")
        print(f"   🪶 峰值内存占用: {res['resource_stats']['peak_rss_mb']} MB")
        print(f"   💡 启动实时网页控制台: python3 -m rag_eval serve --workdir {workdir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rag-eval",
        description="RAG Eval Suite — Understand-First RAG 测试数据生成、回测、P0-P3 根因诊断与可视化报告 CLI",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # inspect
    s = sub.add_parser("inspect", help="Phase 1: 扫描并理解目标 RAG 系统与知识库（理解先行）")
    s.add_argument("--project", default=".", help="目标 RAG 项目路径")
    s.add_argument("--kb", help="知识库路径（逗号分隔）")
    s.add_argument("--name", help="系统名称")
    s.add_argument("--adapter", help="适配器类型: http|python|cli|offline|demo")
    s.add_argument("--endpoint", help="HTTP 接口地址")
    s.add_argument("--callable", help="Python 函数路径 module:func")
    s.add_argument("--responses", help="离线手动响应 JSONL 文件路径")
    s.add_argument("--obs-retrieved", help="是否可观测召回片段: true|false")
    s.add_argument("--obs-citations", help="是否输出引用: true|false")
    s.add_argument("--workdir", default=".rag_eval")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_inspect)

    # confirm
    s = sub.add_parser("confirm", help="解决阻断未知项并显式确认理解档案（解锁测试）")
    s.add_argument("--workdir", default=".rag_eval")
    s.add_argument("--adapter", help="适配器类型: http|python|cli|offline|demo")
    s.add_argument("--endpoint", help="HTTP 接口地址")
    s.add_argument("--callable", help="Python 函数路径 module:func")
    s.add_argument("--command", help="CLI 调用命令")
    s.add_argument("--responses", help="离线手动响应 JSONL 文件路径")
    s.add_argument("--demo-mode", help="Demo 模式: realistic_flawed|strict_good")
    s.add_argument("--obs-retrieved", help="是否可观测召回片段: true|false")
    s.add_argument("--obs-citations", help="是否输出引用: true|false")
    s.add_argument("--resolve", action="append", help="解决未知项，如 UNK-ARCH-01=chunk_size=512")
    s.add_argument("--note", help="补充架构说明")
    s.add_argument("--by", default="user")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_confirm)

    # scope
    s = sub.add_parser("scope", help="Phase 2: 评估 RAG 复杂度并规划核心精简范围 (smoke/core/full)")
    s.add_argument("--workdir", default=".rag_eval")
    s.add_argument("--profile", default="auto", choices=["auto", "smoke", "core", "full"])
    s.add_argument("--max-cases", type=int)
    s.add_argument("--core-paths", help="优先保留的核心知识库子路径（逗号分隔）")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_scope)

    # generate
    s = sub.add_parser("generate", help="Phase 3: 基于真实语料生成多分类测试数据集")
    s.add_argument("--workdir", default=".rag_eval")
    s.add_argument("--scope", choices=["smoke", "core", "full"])
    s.add_argument("--max-cases", type=int)
    s.add_argument(
        "--llm-gen",
        action="store_true",
        help="可选：使用 LLM 润色问句（会向配置的外部 LLM API 发送问句与前 160 字语料片段；内网/敏感语料请勿开启）",
    )
    s.add_argument("--force-unconfirmed", action="store_true", help="强制跳过理解门禁（将在报告显著标注未验证警告）")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_generate)

    # dataset
    s = sub.add_parser("dataset", help="单独查看、筛选、导出或手动添加分类测试数据")
    dsub = s.add_subparsers(dest="ds_action", required=True)
    for act in ("summary", "list", "show", "export", "add"):
        ds = dsub.add_parser(act)
        ds.add_argument("--workdir", default=".rag_eval")
        ds.add_argument("--json", action="store_true")
        if act == "list":
            ds.add_argument("--category")
            ds.add_argument("--difficulty")
            ds.add_argument("--core-only", action="store_true")
            ds.add_argument("--search")
            ds.add_argument("--limit", type=int, default=50)
        elif act == "show":
            ds.add_argument("case_id")
        elif act == "export":
            ds.add_argument("-o", "--output")
        elif act == "add":
            ds.add_argument("--id")
            ds.add_argument("--category", default="single_hop_fact")
            ds.add_argument("--difficulty", default="medium")
            ds.add_argument("--core", action="store_true")
            ds.add_argument("--query", required=True)
            ds.add_argument("--expected", required=True)
            ds.add_argument("--docs", help="标准命中文档 ID（逗号分隔）")
            ds.add_argument("--facts", help="必含要点（逗号分隔）")
            ds.add_argument("--forbid", help="幻觉禁区词（逗号分隔）")
            ds.add_argument("--abstain", action="store_true")
        ds.set_defaults(func=cmd_dataset)

    # methods
    s = sub.add_parser("methods", help="查看所有可单独/批量执行的模块化测试方法")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_methods)

    # run
    s = sub.add_parser("run", help="Phase 4-6: 单独/批量/离线手动执行回测并生成诊断报告")
    s.add_argument("--workdir", default=".rag_eval")
    s.add_argument("--methods", default="all", help="逗号分隔的方法 ID，如 retrieval_hit_rate,hallucination_audit 或 all")
    s.add_argument("--case-id", help="仅执行单条或指定用例 ID（逗号分隔）")
    s.add_argument("--categories", help="仅执行指定分类（逗号分隔）")
    s.add_argument("--core-only", action="store_true", help="仅执行精简核心用例")
    s.add_argument("--adapter", help="覆盖适配器类型: http|python|cli|offline|demo")
    s.add_argument("--endpoint")
    s.add_argument("--callable")
    s.add_argument("--responses", help="离线手动采集结果 JSONL/JSON 文件")
    s.add_argument("--demo-mode")
    s.add_argument("--workers", type=int, help="并发线程数（默认<=2，保护 CPU/内存）")
    s.add_argument(
        "--llm-judge",
        action="store_true",
        help="可选启用 LLM-as-a-Judge 混合复核（会向外部 LLM API 发送问答与召回片段；内网/涉密场景请勿开启）",
    )
    s.add_argument("--resume", help="断点续跑指定 run_id")
    s.add_argument("--detail", "--include-cases", dest="detail", action="store_true", help="在 --json 输出中附加完整逐条 case_evaluations 明细")
    s.add_argument("--force-unconfirmed", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_run)

    # manual-eval
    s = sub.add_parser("manual-eval", help="免配置手动评测单条 RAG 问答（立即校验准确率与幻觉）")
    s.add_argument("--query", required=True)
    s.add_argument("--answer", required=True)
    s.add_argument("--expected", default="")
    s.add_argument("--chunk", action="append", help="召回上下文片段（可多次传入）")
    s.add_argument("--facts", help="必含要点（逗号分隔）")
    s.add_argument("--forbid", help="禁区/错误前提（逗号分隔）")
    s.add_argument("--abstain", action="store_true")
    s.add_argument("--methods", default="all")
    s.add_argument("--json", action="store_true", help="以 JSON 格式输出（manual-eval 默认即 JSON）")
    s.set_defaults(func=cmd_manual_eval)

    # report
    s = sub.add_parser("report", help="重新生成可浏览 HTML 与可下载 Markdown 分析报告")
    s.add_argument("--workdir", default=".rag_eval")
    s.add_argument("--run-id")
    s.add_argument("--update-latest", action="store_true", help="显式将本次导出的历史报告覆盖为 latest_report.*")
    s.add_argument("--no-update-latest", action="store_true", help="仅导出到 runs 对应报告目录，不更新 latest_report.*")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_report)

    # serve
    s = sub.add_parser("serve", help="启动轻量级本地 Web Studio（浏览测试集、手动执行测试、下载 MD 报告）")
    s.add_argument("--workdir", default=".rag_eval")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--idle-timeout", type=int, default=1800, help="空闲自动退出秒数（0 表示不自动退出）")
    s.set_defaults(func=cmd_serve)

    # status
    s = sub.add_parser("status", help="查看当前评测工作区状态与内存占用")
    s.add_argument("--workdir", default=".rag_eval")
    s.add_argument("--json", action="store_true", help="以 JSON 格式输出（status 默认即 JSON）")
    s.set_defaults(func=cmd_status)

    # demo
    s = sub.add_parser("demo", help="在内置示例知识库上一键跑通完整 Understand-First -> 回测 -> 报告闭环")
    s.add_argument("--workdir", default=".rag_eval_demo")
    s.add_argument("--scope", default="full", choices=["smoke", "core", "full"])
    s.add_argument("--max-cases", type=int, default=28)
    s.add_argument("--demo-mode", default="realistic_flawed", choices=["realistic_flawed", "strict_good"])
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_demo)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except UnderstandingGateError as exc:
        if getattr(args, "json", False):
            print(json.dumps({"error": "UNDERSTANDING_GATE_BLOCKED", "message": str(exc), "blocking_unknowns": exc.blocking_unknowns}, ensure_ascii=False, indent=2))
        else:
            print(f"\n🛑 {exc}\n", file=sys.stderr)
        return 2
    except Exception as exc:
        if getattr(args, "json", False):
            print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"❌ 错误: {exc}", file=sys.stderr)
        return 1
