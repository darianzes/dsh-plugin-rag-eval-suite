"""Phase 6: Dual Report Generator (Interactive Browsable HTML + Downloadable Markdown)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .dataset_gen import export_dataset_markdown, load_dataset, summarize_dataset
from .inspector import load_profile
from .methods import METHOD_SPECS
from .resource_guard import read_json
from .runner import load_run_result
from .web.studio_template import render_studio_html


def _fmt_metric_md(m: Dict[str, Any]) -> str:
    val = m.get("value")
    if val is None:
        return "不可观测 (Unobservable)" if m.get("status") == "unobservable" else "N/A"
    unit = m.get("unit", "ratio")
    if unit == "ms":
        return f"{float(val):.1f} ms"
    if unit == "score":
        return f"{float(val):.3f}"
    return f"{float(val) * 100:.1f}%"


def _fmt_target_md(m: Dict[str, Any]) -> str:
    t = m.get("target")
    if t is None:
        return "-"
    op = "≤" if m.get("direction") == "lower_better" else "≥"
    unit = m.get("unit", "ratio")
    if unit == "ms":
        return f"{op} {float(t):.0f} ms"
    if unit == "score":
        return f"{op} {float(t):.2f}"
    return f"{op} {float(t) * 100:.1f}%"


def render_markdown_report(run_result: Dict[str, Any]) -> str:
    """Render a rigorous, executive-ready Markdown (.md) evaluation & root-cause report."""
    prof = run_result.get("profile_snapshot") or {}
    scope = run_result.get("scope_plan") or {}
    diag = run_result.get("diagnosis") or {}
    res = run_result.get("resource_stats") or {}
    sev = diag.get("severity_counts") or {}

    lines: List[str] = [
        f"# RAG 系统回测与根因分析报告 — {prof.get('system_name', 'Target RAG')}",
        "",
        f"- **Run ID**: `{run_result.get('run_id', '')}`",
        f"- **生成时间 (UTC)**: {run_result.get('created_at', '')}",
        f"- **执行模式**: `{run_result.get('execution_mode', 'batch')}` | **适配器**: `{run_result.get('adapter_type', '')}` | **评判模式**: `{run_result.get('judge_mode', 'deterministic')}`",
        f"- **综合评级**: **{diag.get('overall_grade', 'N/A')}**（P0: {sev.get('P0', 0)} / P1: {sev.get('P1', 0)} / P2: {sev.get('P2', 0)} / P3: {sev.get('P3', 0)}）",
        f"- **系统理解就绪度**: {prof.get('readiness_score', 'N/A')}/100（已确认: {'是' if prof.get('confirmed') else '否'}）",
        f"- **资源占用**: 峰值内存 `{res.get('peak_rss_mb', 'N/A')} MB` / 预算 `{res.get('memory_budget_mb', 256)} MB` · 并发 `{res.get('workers_used', 1)}` · 耗时 `{res.get('elapsed_sec', 0)} s`",
        "",
    ]

    if run_result.get("unverified_warning"):
        lines.extend(
            [
                "> [!CAUTION]",
                "> 本次运行强制跳过了 Understand-First 理解门禁，部分系统假设未经人工确认，结论需复核后再用于线上决策。",
                "",
            ]
        )

    lines.extend(["## 1. 系统理解与测试范围披露", ""])
    if prof.get("architecture_summary"):
        lines.append(f"- **架构摘要**: {prof['architecture_summary']}")
    if scope.get("disclosure_statement"):
        lines.append(f"- **范围与精简核心说明**: {scope['disclosure_statement']}")
    lines.append(
        f"- **执行用例数**: {run_result.get('dataset_meta', {}).get('executed_cases', 0)} / "
        f"总数据集 {run_result.get('dataset_meta', {}).get('total_dataset_cases', 0)} 条"
    )
    lines.append(f"- **已执行测试方法**: `{'`, `'.join(run_result.get('methods_executed', []))}`")
    if run_result.get("methods_skipped"):
        lines.append(f"- **因不可观测/范围精简跳过的方法**: `{run_result['methods_skipped']}`")

    lines.extend(["", "## 2. 核心指标总览 (Key Metrics)", ""])
    lines.extend(
        [
            "| 指标 | 当前值 | 95% 置信区间 | 目标阈值 | 样本量 (N) | 状态 | 评判模式 |",
            "| :--- | ---: | :---: | :---: | ---: | :---: | :---: |",
        ]
    )
    for m in run_result.get("metrics", []):
        ci = m.get("ci_95")
        ci_str = f"[{ci[0]*100:.1f}%, {ci[1]*100:.1f}%]" if ci else "-"
        lines.append(
            f"| {m['zh_name']} (`{m['metric_id']}`) | **{_fmt_metric_md(m)}** | {ci_str} | "
            f"{_fmt_target_md(m)} | {m.get('sample_size', 0)} | `{m.get('status', '')}` | `{m.get('mode', 'deterministic')}` |"
        )

    caveat_notes = [
        f"- `{m['metric_id']}`: {c}"
        for m in run_result.get("metrics", [])
        for c in (m.get("caveats") or [])
    ]
    if caveat_notes:
        lines.extend(["", "### 统计严谨性与边界备注", *caveat_notes])

    lines.extend(["", "## 3. 分类维度指标拆解 (Category Breakdown)", ""])
    lines.extend(
        [
            "| 分类 | 用例数 | 通过率 | Hit@K | Recall@K | 综合准确率 | 幻觉率 | 声明忠实度 |",
            "| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )

    def _p(v: Any) -> str:
        return f"{float(v)*100:.1f}%" if v is not None else "-"

    for row in run_result.get("category_breakdown", []):
        lines.append(
            f"| {row['zh_name']} (`{row['category']}`) | {row['total']} | {_p(row['pass_rate'])} | "
            f"{_p(row['hit_at_k'])} | {_p(row['recall_at_k'])} | {_p(row['answer_accuracy'])} | "
            f"{_p(row['hallucination_rate'])} | {_p(row['faithfulness'])} |"
        )

    lines.extend(["", "## 4. 问题原因分级评价与解决方案 (P0-P3 Root-Cause Matrix)", ""])
    issues = diag.get("issues", [])
    if not issues:
        lines.append("当前回测未发现显著的 P0-P3 缺陷，核心指标表现健康。")
    else:
        for iss in issues:
            lines.extend(
                [
                    f"### [{iss['severity']}] {iss['title']} (`{iss['code']}`)",
                    "",
                    f"- **归因链路层**: {iss['pipeline_stage']}",
                    f"- **影响范围**: {iss['affected_count']} 条用例（占比 {iss['impact_ratio']*100:.1f}%）| **诊断置信度**: `{iss['confidence']}`",
                    f"- **受影响用例 ID**: `{', '.join(iss.get('affected_case_ids', [])[:15]) or '系统级'}`",
                    f"- **问题剖析**: {iss['summary']}",
                    "",
                    "**短期止血方案 (Quick Wins)**",
                ]
            )
            for qw in iss.get("quick_wins", []):
                lines.append(f"- {qw}")
            lines.extend(["", "**中长期结构优化 (Structural Fixes)**"])
            for sf in iss.get("structural_fixes", []):
                lines.append(f"- {sf}")
            lines.extend(
                [
                    "",
                    f"**回归验证方法**: {iss.get('verification_plan', '')}",
                    "",
                ]
            )

    # Failure Case Drilldown Appendix
    failed = [ce for ce in run_result.get("case_evaluations", []) if not ce.get("passed")]
    lines.extend(["## 5. 失败与异常用例复盘明细", ""])
    if not failed:
        lines.append("所有已执行用例均已通过，无失败样例。")
    else:
        lines.append(f"共记录 **{len(failed)}** 条未通过用例（以下展示前 {min(25, len(failed))} 条）：")
        lines.append("")
        for ce in failed[:25]:
            tags = ", ".join(ce.get("failure_tags") or [])
            ans_txt = (ce.get("response", {}).get("answer") or ce.get("response", {}).get("error") or "").replace("\n", " ")
            exp_txt = (ce.get("expected_answer") or "").replace("\n", " ")
            lines.extend(
                [
                    f"### {ce['case_id']} (`{ce['category']}` · 标签: `{tags}`)",
                    f"- **提问**: {ce['query']}",
                    f"- **期望答案**: {exp_txt[:260]}",
                    f"- **实际回答**: {ans_txt[:300]}",
                ]
            )
            hal = ce.get("method_scores", {}).get("hallucination_audit", {})
            unsup = [c for c in (hal.get("claims_audit") or []) if c.get("status") == "unsupported"]
            if unsup:
                lines.append(f"- **未支撑/幻觉声明**: {unsup[0].get('claim', '')[:160]}（原因：{unsup[0].get('reason', '')}）")
            lines.append("")

    return "\n".join(lines)


def build_studio_payload(workdir: Path, run_id: Optional[str] = None) -> Dict[str, Any]:
    """Assemble full JSON payload for both Static HTML Report and Live Web Studio."""
    profile = load_profile(workdir)
    cases, ds_meta = load_dataset(workdir)
    try:
        run_res = load_run_result(workdir, run_id=run_id)
    except FileNotFoundError:
        run_res = None

    md_report = render_markdown_report(run_res) if run_res else "# 尚未生成回测报告"
    ds_md = export_dataset_markdown(cases) if cases else "# 尚未生成测试数据集"

    return {
        "title": f"{profile.system_name if profile else 'RAG Eval Suite'} · 评测分析报告",
        "profile": profile.to_dict() if profile else None,
        "run": run_res,
        "dataset": [c.to_dict() for c in cases],
        "dataset_coverage": summarize_dataset(cases),
        "methods_catalog": [m.to_dict() for m in METHOD_SPECS.values()],
        "markdown_report": md_report,
        "dataset_markdown": ds_md,
    }


def generate_reports(
    workdir: Path,
    run_id: Optional[str] = None,
    update_latest: Optional[bool] = None,
) -> Tuple[Path, Path, str]:
    """Write standalone browsable HTML (`report.html`) and downloadable Markdown (`report.md`).

    Protects `latest_report.*` from being silently overwritten when re-rendering a historical run_id
    unless `update_latest=True` is explicitly passed.
    """
    payload = build_studio_payload(workdir, run_id=run_id)
    if not payload.get("run"):
        raise FileNotFoundError("未找到可用的回测结果，请先运行 `rag-eval run`。")

    actual_run_id = payload["run"]["run_id"]
    out_dir = workdir / "reports" / actual_run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    md_path = out_dir / "report.md"
    html_path = out_dir / "report.html"

    md_content = payload["markdown_report"]
    html_content = render_studio_html(payload, live_mode=False)

    md_path.write_text(md_content, encoding="utf-8")
    html_path.write_text(html_content, encoding="utf-8")

    latest_run_file = workdir / "latest_run.json"
    latest_run_id = read_json(latest_run_file).get("run_id") if latest_run_file.exists() else None
    should_update_latest = (
        True if update_latest is True
        else False if update_latest is False
        else (run_id is None or latest_run_id is None or actual_run_id == latest_run_id)
    )
    if should_update_latest:
        (workdir / "reports" / "latest_report.md").write_text(md_content, encoding="utf-8")
        (workdir / "reports" / "latest_report.html").write_text(html_content, encoding="utf-8")

    return html_path, md_path, actual_run_id
