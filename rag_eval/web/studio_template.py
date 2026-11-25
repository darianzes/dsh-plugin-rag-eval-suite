"""Zero-dependency, self-contained HTML/CSS/JS Studio & Report renderer.

Works both as a static offline single-file HTML report (0 MB background RAM)
and as a live interactive Web Studio when served by `rag_eval.server`.
"""

from __future__ import annotations

import json
from typing import Any, Dict


def _safe_json_for_script(obj: Any) -> str:
    """Serialize JSON safely for inline <script> injection (prevents </script> breakouts)."""
    raw = json.dumps(obj, ensure_ascii=False)
    return raw.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def render_studio_html(payload: Dict[str, Any], live_mode: bool = False) -> str:
    """Render the complete interactive RAG Evaluation Report / Web Studio HTML page."""
    title = payload.get("title", "RAG Eval Suite · 分析报告与评测台")
    data_json = _safe_json_for_script(payload)
    live_flag = "true" if live_mode else "false"

    return f"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="light">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{title}</title>
<style>
  :root {{
    --bg: #f8fafc;
    --surface: #ffffff;
    --surface-2: #f1f5f9;
    --border: #e2e8f0;
    --text: #0f172a;
    --muted: #475569;
    --accent: #2563eb;
    --accent-soft: #dbeafe;
    --pass: #059669;
    --pass-bg: #d1fae5;
    --warn: #d97706;
    --warn-bg: #fef3c7;
    --fail: #dc2626;
    --fail-bg: #fee2e2;
    --p0: #991b1b;
    --p0-bg: #fee2e2;
    --p1: #c2410c;
    --p1-bg: #ffedd5;
    --p2: #a16207;
    --p2-bg: #fef9c3;
    --p3: #1d4ed8;
    --p3-bg: #dbeafe;
    --shadow: 0 1px 3px rgba(15,23,42,0.06), 0 1px 2px rgba(15,23,42,0.04);
    --radius: 12px;
  }}
  [data-theme="dark"] {{
    --bg: #0b1120;
    --surface: #111827;
    --surface-2: #1e293b;
    --border: #334155;
    --text: #f1f5f9;
    --muted: #94a3b8;
    --accent: #60a5fa;
    --accent-soft: #1e3a5f;
    --pass: #34d399;
    --pass-bg: #064e3b;
    --warn: #fbbf24;
    --warn-bg: #451a03;
    --fail: #f87171;
    --fail-bg: #450a0a;
    --p0: #fca5a5;
    --p0-bg: #450a0a;
    --p1: #fdba74;
    --p1-bg: #431407;
    --p2: #fde047;
    --p2-bg: #422006;
    --p3: #93c5fd;
    --p3-bg: #172554;
    --shadow: 0 2px 6px rgba(0,0,0,0.35);
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.55;
  }}
  header {{
    position: sticky;
    top: 0;
    z-index: 50;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 12px 24px;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }}
  .brand {{ display: flex; align-items: center; gap: 12px; }}
  .brand-logo {{
    width: 34px; height: 34px; border-radius: 9px;
    background: #0f172a; color: #38bdf8;
    display: flex; align-items: center; justify-content: center;
    font-weight: 800; font-size: 14px; border: 1.5px solid #38bdf8;
  }}
  .brand h1 {{ margin: 0; font-size: 17px; font-weight: 700; }}
  .brand .sub {{ font-size: 12px; color: var(--muted); }}
  .actions {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
  button, .btn {{
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--text);
    padding: 7px 13px;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.15s ease;
    display: inline-flex; align-items: center; gap: 6px;
  }}
  button:hover {{ border-color: var(--accent); color: var(--accent); }}
  button.primary {{
    background: var(--accent); color: #ffffff; border-color: var(--accent);
  }}
  button.primary:hover {{ opacity: 0.92; color: #ffffff; }}
  button:disabled {{ opacity: 0.5; cursor: not-allowed; }}

  .tabs-bar {{
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 0 24px;
    display: flex;
    gap: 6px;
    overflow-x: auto;
  }}
  .tab-btn {{
    border: none;
    border-bottom: 2.5px solid transparent;
    border-radius: 0;
    padding: 12px 14px;
    background: transparent;
    color: var(--muted);
    font-size: 14px;
    font-weight: 600;
    white-space: nowrap;
  }}
  .tab-btn.active {{
    color: var(--accent);
    border-bottom-color: var(--accent);
  }}
  main {{ max-width: 1280px; margin: 0 auto; padding: 22px 24px 64px; }}
  .tab-panel {{ display: none; }}
  .tab-panel.active {{ display: block; }}

  .banner {{
    padding: 14px 18px;
    border-radius: var(--radius);
    margin-bottom: 18px;
    border: 1px solid var(--border);
    background: var(--surface);
    box-shadow: var(--shadow);
  }}
  .banner.warn {{ background: var(--warn-bg); border-color: var(--warn); }}
  .banner.fail {{ background: var(--fail-bg); border-color: var(--fail); }}
  .banner.info {{ background: var(--accent-soft); border-color: var(--accent); }}

  .grid {{ display: grid; gap: 14px; }}
  .grid-4 {{ grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }}
  .grid-3 {{ grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }}
  .grid-2 {{ grid-template-columns: repeat(auto-fit, minmax(380px, 1fr)); }}

  .card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 16px 18px;
    box-shadow: var(--shadow);
  }}
  .card h2, .card h3 {{ margin: 0 0 10px; font-size: 15px; }}
  .kpi-val {{ font-size: 28px; font-weight: 800; letter-spacing: -0.02em; margin: 4px 0; }}
  .kpi-meta {{ font-size: 12px; color: var(--muted); }}

  .badge {{
    display: inline-flex; align-items: center; padding: 2px 9px;
    border-radius: 999px; font-size: 11px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.02em;
  }}
  .badge.pass {{ background: var(--pass-bg); color: var(--pass); }}
  .badge.warn, .badge.low_sample_warn {{ background: var(--warn-bg); color: var(--warn); }}
  .badge.fail {{ background: var(--fail-bg); color: var(--fail); }}
  .badge.info {{ background: var(--accent-soft); color: var(--accent); }}
  .badge.unobservable, .badge.insufficient_data, .badge.neutral {{ background: var(--surface-2); color: var(--muted); }}
  .badge.P0 {{ background: var(--p0-bg); color: var(--p0); }}
  .badge.P1 {{ background: var(--p1-bg); color: var(--p1); }}
  .badge.P2 {{ background: var(--p2-bg); color: var(--p2); }}
  .badge.P3 {{ background: var(--p3-bg); color: var(--p3); }}

  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ padding: 10px 12px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }}
  th {{ font-weight: 700; color: var(--muted); background: var(--surface-2); font-size: 12px; }}
  tr:hover td {{ background: var(--surface-2); }}

  .bar-track {{ height: 8px; background: var(--surface-2); border-radius: 999px; overflow: hidden; margin-top: 6px; }}
  .bar-fill {{ height: 100%; border-radius: 999px; transition: width 0.3s ease; }}

  .filters {{ display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 14px; align-items: center; }}
  input[type="text"], select, textarea {{
    background: var(--surface);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 7px 10px;
    font-size: 13px;
    font-family: inherit;
  }}
  textarea {{ width: 100%; min-height: 76px; resize: vertical; }}
  .pill {{
    padding: 5px 11px; border-radius: 999px; font-size: 12px; font-weight: 600;
    border: 1px solid var(--border); background: var(--surface); cursor: pointer;
  }}
  .pill.active {{ background: var(--accent); color: #fff; border-color: var(--accent); }}

  .issue-card {{
    border-left: 4px solid var(--accent);
    margin-bottom: 14px;
  }}
  .issue-card.sev-P0 {{ border-left-color: var(--fail); }}
  .issue-card.sev-P1 {{ border-left-color: var(--p1); }}
  .issue-card.sev-P2 {{ border-left-color: var(--warn); }}
  .issue-card.sev-P3 {{ border-left-color: var(--accent); }}

  .sol-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; margin-top: 12px; }}
  .sol-box {{ background: var(--surface-2); border-radius: 8px; padding: 11px 13px; font-size: 13px; }}
  .sol-box h4 {{ margin: 0 0 6px; font-size: 12px; text-transform: uppercase; color: var(--muted); }}
  .sol-box ul {{ margin: 0; padding-left: 18px; }}

  .claim-item {{ padding: 6px 10px; border-radius: 6px; margin: 4px 0; font-size: 12.5px; }}
  .claim-supported {{ background: var(--pass-bg); }}
  .claim-unsupported {{ background: var(--fail-bg); }}
  .claim-other {{ background: var(--surface-2); }}

  .case-box {{
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 13px 15px;
    margin-bottom: 10px;
    background: var(--surface);
  }}
  .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 12px; }}
  .toast {{
    position: fixed; right: 20px; bottom: 20px; z-index: 200;
    background: #0f172a; color: #fff; padding: 10px 16px; border-radius: 9px;
    font-size: 13px; box-shadow: 0 10px 25px rgba(0,0,0,0.25); display: none;
  }}
  details summary {{ cursor: pointer; font-weight: 600; color: var(--accent); }}
  .muted {{ color: var(--muted); }}
  .mt-12 {{ margin-top: 12px; }}
  .mt-18 {{ margin-top: 18px; }}
  .flex-between {{ display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; }}
</style>
</head>
<body>
<header>
  <div class="brand">
    <div class="brand-logo">RAG</div>
    <div>
      <h1 id="hdr-title">RAG Eval Suite · 分析报告与评测台</h1>
      <div class="sub" id="hdr-sub">Understand-First · 命中率 / 准确率 / 幻觉率回测与 P0-P3 根因诊断</div>
    </div>
  </div>
  <div class="actions">
    <span class="badge neutral" id="mode-badge"></span>
    <button class="primary" onclick="downloadMarkdown()">⬇️ 下载 Markdown 报告 (.md)</button>
    <button onclick="copyMarkdown()">📋 复制 MD</button>
    <button onclick="downloadDatasetMd()">📥 导出测试集 (.md)</button>
    <button onclick="toggleTheme()">🌓 主题</button>
  </div>
</header>

<nav class="tabs-bar">
  <button class="tab-btn active" data-tab="overview" onclick="switchTab('overview')">📊 指标总览</button>
  <button class="tab-btn" data-tab="diagnosis" onclick="switchTab('diagnosis')">🚨 原因分级与方案</button>
  <button class="tab-btn" data-tab="dataset" onclick="switchTab('dataset')">🗂️ 测试数据分类浏览</button>
  <button class="tab-btn" data-tab="methods" onclick="switchTab('methods')">⚙️ 测试方法与手动执行</button>
  <button class="tab-btn" data-tab="drilldown" onclick="switchTab('drilldown')">🔍 用例逐条复盘</button>
</nav>

<main>
  <section id="tab-overview" class="tab-panel active"></section>
  <section id="tab-diagnosis" class="tab-panel"></section>
  <section id="tab-dataset" class="tab-panel"></section>
  <section id="tab-methods" class="tab-panel"></section>
  <section id="tab-drilldown" class="tab-panel"></section>
</main>

<div id="toast" class="toast"></div>

<script>
let STATE = {data_json};
const LIVE_MODE = {live_flag};

let dsFilter = {{ category: "ALL", difficulty: "ALL", coreOnly: false, search: "" }};
let diagFilter = "ALL";
let drillFilter = {{ status: "ALL", category: "ALL", search: "" }};

function esc(s) {{
  if (s === null || s === undefined) return "";
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}}

function fmtPct(v) {{
  if (v === null || v === undefined) return "N/A";
  return (Number(v) * 100).toFixed(1) + "%";
}}

function fmtMetric(m) {{
  if (m.value === null || m.value === undefined) return m.status === "unobservable" ? "不可观测" : "N/A";
  if (m.unit === "ms") return Number(m.value).toFixed(1) + " ms";
  if (m.unit === "score") return Number(m.value).toFixed(3);
  return fmtPct(m.value);
}}

function toast(msg) {{
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.style.display = "block";
  clearTimeout(window.__toastTimer);
  window.__toastTimer = setTimeout(() => {{ el.style.display = "none"; }}, 3200);
}}

function toggleTheme() {{
  const html = document.documentElement;
  html.setAttribute("data-theme", html.getAttribute("data-theme") === "dark" ? "light" : "dark");
}}

function switchTab(tabId) {{
  document.querySelectorAll(".tab-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === tabId));
  document.querySelectorAll(".tab-panel").forEach(p => p.classList.toggle("active", p.id === "tab-" + tabId));
}}

function jumpToCase(caseId) {{
  drillFilter = {{ status: "ALL", category: "ALL", search: caseId }};
  renderDrilldown();
  switchTab("drilldown");
}}

function downloadBlob(content, filename, mime) {{
  const blob = new Blob([content], {{ type: mime || "text/markdown;charset=utf-8" }});
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}}

function downloadMarkdown() {{
  if (LIVE_MODE && STATE.run && STATE.run.run_id) {{
    window.location.href = "/api/report/download.md?run_id=" + encodeURIComponent(STATE.run.run_id);
    toast("正在下载 Markdown 分析报告...");
    return;
  }}
  const md = STATE.markdown_report || "# 暂无分析报告";
  const fname = (STATE.run && STATE.run.run_id ? STATE.run.run_id : "rag_eval") + "_report.md";
  downloadBlob(md, fname, "text/markdown;charset=utf-8");
  toast("已下载 Markdown 报告: " + fname);
}}

function downloadDatasetMd() {{
  if (LIVE_MODE) {{
    window.location.href = "/api/dataset/download.md";
    toast("正在导出分类测试数据集 (.md)...");
    return;
  }}
  const md = STATE.dataset_markdown || "# 暂无测试数据集";
  downloadBlob(md, "rag_test_dataset.md", "text/markdown;charset=utf-8");
  toast("已导出测试数据集 Markdown");
}}

function copyMarkdown() {{
  const md = STATE.markdown_report || "";
  if (navigator.clipboard && md) {{
    navigator.clipboard.writeText(md).then(() => toast("已复制完整 Markdown 报告到剪贴板"));
  }} else {{
    toast("当前浏览器限制剪贴板访问，请使用下载按钮");
  }}
}}

function barColor(status) {{
  if (status === "pass") return "var(--pass)";
  if (status === "warn" || status === "low_sample_warn") return "var(--warn)";
  if (status === "fail") return "var(--fail)";
  if (status === "info") return "var(--accent)";
  return "var(--muted)";
}}

function renderHeader() {{
  const prof = STATE.profile || (STATE.run && STATE.run.profile_snapshot) || {{}};
  const run = STATE.run;
  document.getElementById("hdr-title").textContent = (prof.system_name || "RAG Eval Suite") + " · 评测分析台";
  document.getElementById("hdr-sub").textContent = run
    ? `Run: ${{run.run_id}} · 模式: ${{run.execution_mode}} · 评判: ${{run.judge_mode}} · 生成于 ${{run.created_at}}`
    : "尚未执行回测，可在「测试方法与手动执行」页签触发评测";
  const badge = document.getElementById("mode-badge");
  badge.textContent = LIVE_MODE ? "Live Web Studio · 轻量常驻" : "独立可浏览报告 · 0MB 后台占用";
}}

function renderOverview() {{
  const el = document.getElementById("tab-overview");
  const run = STATE.run;
  if (!run) {{
    el.innerHTML = `<div class="banner info"><b>尚未生成回测结果。</b> 请先查看「测试数据分类浏览」或前往「测试方法与手动执行」运行单条/批量回测。</div>`;
    return;
  }}

  const diag = run.diagnosis || {{ severity_counts: {{}}, overall_grade: "N/A" }};
  const sev = diag.severity_counts || {{}};
  const scope = run.scope_plan;
  const res = run.resource_stats || {{}};
  const prof = run.profile_snapshot || {{}};

  let html = "";
  if (run.unverified_warning) {{
    html += `<div class="banner fail"><b>⚠️ 严谨性警示（UNVERIFIED UNDERSTANDING）：</b>本次运行强制跳过了理解门禁，结论可能包含未验证假设，请先核对 RAG 档案后重跑。</div>`;
  }}

  // Grade + Scope Banner
  const gradeCls = sev.P0 > 0 ? "fail" : (sev.P1 > 0 ? "warn" : "info");
  html += `
    <div class="banner ${{gradeCls}}">
      <div class="flex-between">
        <div>
          <div style="font-size:12px;font-weight:700;opacity:0.8">综合健康评级 · Overall RAG Health Grade</div>
          <div style="font-size:19px;font-weight:800;margin-top:2px">${{esc(diag.overall_grade)}}</div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <span class="badge P0">P0 阻断: ${{sev.P0 || 0}}</span>
          <span class="badge P1">P1 严重: ${{sev.P1 || 0}}</span>
          <span class="badge P2">P2 中等: ${{sev.P2 || 0}}</span>
          <span class="badge P3">P3 优化: ${{sev.P3 || 0}}</span>
        </div>
      </div>
      ${{scope && scope.disclosure_statement ? `<div class="mt-12" style="font-size:13px"><b>📌 范围与精简核心披露：</b>${{esc(scope.disclosure_statement)}}</div>` : ""}}
    </div>
  `;

  // KPI Cards
  const metrics = run.metrics || [];
  html += `<div class="grid grid-4">`;
  for (const m of metrics) {{
    const pctWidth = m.value !== null && m.value !== undefined && m.unit !== "ms"
      ? Math.max(2, Math.min(100, Number(m.value) * 100))
      : 50;
    const ciTxt = m.ci_95 ? `95% CI [${{fmtPct(m.ci_95[0])}}, ${{fmtPct(m.ci_95[1])}}]` : "";
    const targetTxt = m.target !== null && m.target !== undefined
      ? `目标 ${{m.direction === "lower_better" ? "≤" : "≥"}} ${{m.unit === "ms" ? m.target + " ms" : (m.unit === "score" ? m.target : fmtPct(m.target))}}`
      : "参考指标";
    html += `
      <div class="card">
        <div class="flex-between">
          <span style="font-size:13px;font-weight:700">${{esc(m.zh_name)}}</span>
          <span class="badge ${{esc(m.status)}}">${{esc(m.status)}}</span>
        </div>
        <div class="kpi-val">${{fmtMetric(m)}}</div>
        <div class="kpi-meta">${{targetTxt}} · N=${{m.sample_size}} ${{ciTxt ? "· " + ciTxt : ""}}</div>
        ${{m.value !== null && m.unit !== "ms" ? `<div class="bar-track"><div class="bar-fill" style="width:${{pctWidth}}%;background:${{barColor(m.status)}}"></div></div>` : ""}}
        ${{m.caveats && m.caveats.length ? `<div class="kpi-meta mt-12" style="color:var(--warn)">⚠️ ${{esc(m.caveats[0])}}</div>` : ""}}
      </div>
    `;
  }}
  html += `</div>`;

  // Category Breakdown Table + System/Resource Card
  const cats = run.category_breakdown || [];
  html += `
    <div class="grid grid-2 mt-18">
      <div class="card">
        <h2>📈 分类指标明细 (Category Breakdown)</h2>
        <div style="overflow-x:auto">
          <table>
            <thead>
              <tr>
                <th>测试分类</th><th>用例数</th><th>通过率</th><th>Hit@K</th><th>准确率</th><th>幻觉率</th>
              </tr>
            </thead>
            <tbody>
              ${{cats.map(c => `
                <tr>
                  <td><b>${{esc(c.zh_name)}}</b><div class="mono muted">${{esc(c.category)}}</div></td>
                  <td>${{c.total}}</td>
                  <td><span class="badge ${{c.pass_rate >= 0.8 ? 'pass' : (c.pass_rate >= 0.6 ? 'warn' : 'fail')}}">${{fmtPct(c.pass_rate)}}</span></td>
                  <td>${{fmtPct(c.hit_at_k)}}</td>
                  <td>${{fmtPct(c.answer_accuracy)}}</td>
                  <td>${{c.hallucination_rate !== null && c.hallucination_rate !== undefined ? `<span class="badge ${{c.hallucination_rate <= 0.1 ? 'pass' : 'fail'}}">${{fmtPct(c.hallucination_rate)}}</span>` : "N/A"}}</td>
                </tr>
              `).join("")}}
            </tbody>
          </table>
        </div>
      </div>

      <div class="card">
        <h2>🧠 系统理解档案 & 低资源运行监控</h2>
        <p style="font-size:13px;margin:0 0 10px">${{esc(prof.architecture_summary || "无架构摘要")}}</p>
        <table>
          <tbody>
            <tr><th>理解就绪度</th><td><b>${{prof.readiness_score ?? "N/A"}}/100</b> · 已确认: ${{prof.confirmed ? "✅ 是" : "⚠️ 否"}}</td></tr>
            <tr><th>执行方法</th><td class="mono">${{esc((run.methods_executed || []).join(", "))}}</td></tr>
            <tr><th>因不可观测跳过</th><td class="mono">${{Object.keys(run.methods_skipped || {{}}).length ? esc(JSON.stringify(run.methods_skipped)) : "无"}}</td></tr>
            <tr><th>峰值内存 (Peak RSS)</th><td><b>${{res.peak_rss_mb ?? "N/A"}} MB</b> / 预算 ${{res.memory_budget_mb ?? 256}} MB (${{res.within_memory_budget === false ? "⚠️ 触发限流" : "✅ 轻量化运行"}})</td></tr>
            <tr><th>并发度 & 耗时</th><td>Workers = <b>${{res.workers_used ?? 1}}</b> · 总耗时 <b>${{res.elapsed_sec ?? 0}} s</b></td></tr>
          </tbody>
        </table>
      </div>
    </div>
  `;

  el.innerHTML = html;
}}

function renderDiagnosis() {{
  const el = document.getElementById("tab-diagnosis");
  const run = STATE.run;
  const diag = run && run.diagnosis;
  if (!diag) {{
    el.innerHTML = `<div class="banner info">暂无诊断结果，请先运行回测。</div>`;
    return;
  }}

  const sevCounts = diag.severity_counts || {{}};
  const allIssues = diag.issues || [];
  const shown = diagFilter === "ALL" ? allIssues : allIssues.filter(i => i.severity === diagFilter);

  let html = `
    <div class="filters">
      <span class="pill ${{diagFilter === 'ALL' ? 'active' : ''}}" onclick="diagFilter='ALL';renderDiagnosis()">全部问题 (${{allIssues.length}})</span>
      <span class="pill ${{diagFilter === 'P0' ? 'active' : ''}}" onclick="diagFilter='P0';renderDiagnosis()">P0 阻断 (${{sevCounts.P0 || 0}})</span>
      <span class="pill ${{diagFilter === 'P1' ? 'active' : ''}}" onclick="diagFilter='P1';renderDiagnosis()">P1 严重 (${{sevCounts.P1 || 0}})</span>
      <span class="pill ${{diagFilter === 'P2' ? 'active' : ''}}" onclick="diagFilter='P2';renderDiagnosis()">P2 中等 (${{sevCounts.P2 || 0}})</span>
      <span class="pill ${{diagFilter === 'P3' ? 'active' : ''}}" onclick="diagFilter='P3';renderDiagnosis()">P3 优化 (${{sevCounts.P3 || 0}})</span>
    </div>
  `;

  if (!shown.length) {{
    html += `<div class="card"><p class="muted">当前筛选级别下无已发现问题，系统表现健康。</p></div>`;
    el.innerHTML = html;
    return;
  }}

  for (const iss of shown) {{
    const caseLinks = (iss.affected_case_ids || []).slice(0, 12).map(id =>
      `<button style="padding:2px 8px;font-size:11.5px" onclick="jumpToCase('${{esc(id)}}')">${{esc(id)}}</button>`
    ).join(" ");

    html += `
      <div class="card issue-card sev-${{esc(iss.severity)}}">
        <div class="flex-between">
          <div>
            <span class="badge ${{esc(iss.severity)}}">${{esc(iss.severity)}}</span>
            <span class="mono muted" style="margin-left:6px">${{esc(iss.code)}}</span>
            <span style="font-size:15px;font-weight:700;margin-left:8px">${{esc(iss.title)}}</span>
          </div>
          <div class="kpi-meta">归因层级: <b>${{esc(iss.pipeline_stage)}}</b> · 置信度: <b>${{esc(iss.confidence)}}</b> · 影响: <b>${{iss.affected_count}} 条 (${{fmtPct(iss.impact_ratio)}})</b></div>
        </div>
        <p style="margin:10px 0 8px;font-size:13.5px">${{esc(iss.summary)}}</p>
        ${{caseLinks ? `<div style="margin-bottom:10px;font-size:12px">涉及用例：${{caseLinks}}</div>` : ""}}

        <div class="sol-grid">
          <div class="sol-box">
            <h4>⚡ 短期止血方案 (Quick Wins)</h4>
            <ul>${{(iss.quick_wins || []).map(x => `<li>${{esc(x)}}</li>`).join("")}}</ul>
          </div>
          <div class="sol-box">
            <h4>🏗️ 中长期结构修复 (Structural Fixes)</h4>
            <ul>${{(iss.structural_fixes || []).map(x => `<li>${{esc(x)}}</li>`).join("")}}</ul>
          </div>
          <div class="sol-box">
            <h4>✅ 回归验证方案 (Verification)</h4>
            <p style="margin:0">${{esc(iss.verification_plan)}}</p>
          </div>
        </div>

        ${{(iss.sample_examples || []).length ? `
          <details class="mt-12">
            <summary>查看代表性失败样例 (${{iss.sample_examples.length}})</summary>
            <div class="mt-12">
              ${{iss.sample_examples.map(ex => `
                <div class="case-box" style="font-size:12.5px">
                  <div><b>${{esc(ex.case_id)}}</b> <span class="badge neutral">${{esc(ex.category)}}</span></div>
                  <div><b>Query:</b> ${{esc(ex.query)}}</div>
                  <div><b>Expected:</b> ${{esc(ex.expected)}}</div>
                  <div><b>Actual:</b> ${{esc(ex.actual)}}</div>
                </div>
              `).join("")}}
            </div>
          </details>
        ` : ""}}
      </div>
    `;
  }}

  el.innerHTML = html;
}}

function renderDataset() {{
  const el = document.getElementById("tab-dataset");
  const ds = STATE.dataset || [];
  const cov = STATE.dataset_coverage || {{ categories: [], balance_warnings: [] }};

  let html = "";
  if ((cov.balance_warnings || []).length) {{
    html += `<div class="banner warn"><b>⚖️ 覆盖度平衡提醒：</b><ul>${{cov.balance_warnings.map(w => `<li>${{esc(w)}}</li>`).join("")}}</ul></div>`;
  }}

  // Category Cards
  html += `<div class="filters">`;
  html += `<span class="pill ${{dsFilter.category === 'ALL' ? 'active' : ''}}" onclick="dsFilter.category='ALL';renderDataset()">全部分类 (${{ds.length}})</span>`;
  for (const c of (cov.categories || [])) {{
    if (c.count > 0) {{
      html += `<span class="pill ${{dsFilter.category === c.category ? 'active' : ''}}" onclick="dsFilter.category='${{esc(c.category)}}';renderDataset()">${{esc(c.zh_name)}} (${{c.count}})</span>`;
    }}
  }}
  html += `</div>`;

  // Sub-filters
  html += `
    <div class="filters">
      <input type="text" placeholder="搜索用例 ID / 提问 / 证据 / 文档名..." value="${{esc(dsFilter.search)}}" oninput="dsFilter.search=this.value;renderDataset()" style="min-width:260px" />
      <select onchange="dsFilter.difficulty=this.value;renderDataset()">
        <option value="ALL" ${{dsFilter.difficulty==='ALL'?'selected':''}}>全部难度</option>
        <option value="easy" ${{dsFilter.difficulty==='easy'?'selected':''}}>Easy</option>
        <option value="medium" ${{dsFilter.difficulty==='medium'?'selected':''}}>Medium</option>
        <option value="hard" ${{dsFilter.difficulty==='hard'?'selected':''}}>Hard</option>
      </select>
      <label style="font-size:13px;display:flex;align-items:center;gap:5px;cursor:pointer">
        <input type="checkbox" ${{dsFilter.coreOnly ? 'checked' : ''}} onchange="dsFilter.coreOnly=this.checked;renderDataset()" />
        仅看精简核心集 (Core Priority=1)
      </label>
    </div>
  `;

  const filtered = ds.filter(c => {{
    if (dsFilter.category !== "ALL" && c.category !== dsFilter.category) return false;
    if (dsFilter.difficulty !== "ALL" && c.difficulty !== dsFilter.difficulty) return false;
    if (dsFilter.coreOnly && c.core_priority !== 1) return false;
    if (dsFilter.search) {{
      const hay = `${{c.id}} ${{c.query}} ${{c.expected_answer}} ${{(c.expected_doc_ids||[]).join(' ')}}`.toLowerCase();
      if (!hay.includes(dsFilter.search.toLowerCase())) return false;
    }}
    return true;
  }});

  html += `<div class="muted" style="font-size:12.5px;margin-bottom:10px">当前展示 <b>${{filtered.length}}</b> / ${{ds.length}} 条测试数据</div>`;

  for (const c of filtered) {{
    const catSpec = (cov.categories || []).find(x => x.category === c.category);
    const catZh = catSpec ? catSpec.zh_name : c.category;
    html += `
      <div class="case-box">
        <div class="flex-between">
          <div>
            <b>${{esc(c.id)}}</b>
            <span class="badge neutral" style="margin-left:6px">${{esc(catZh)}}</span>
            <span class="badge ${{c.difficulty==='hard'?'warn':'pass'}}" style="margin-left:4px">${{esc(c.difficulty)}}</span>
            ${{c.core_priority === 1 ? `<span class="badge P3" style="margin-left:4px">CORE</span>` : ""}}
            ${{c.should_abstain ? `<span class="badge P0" style="margin-left:4px">必须拒答</span>` : ""}}
          </div>
          ${{LIVE_MODE ? `<button style="padding:4px 10px;font-size:12px" onclick="runLiveSingleCase('${{esc(c.id)}}')">▶ 单独执行此用例</button>` : `<span class="mono muted">rag-eval run --case-id ${{esc(c.id)}}</span>`}}
        </div>
        <div class="mt-12"><b>提问 (Query)：</b>${{esc(c.query)}}</div>
        <div style="margin-top:6px"><b>参考答案：</b>${{esc(c.expected_answer)}}</div>
        <div class="kpi-meta mt-12">
          ${{(c.expected_doc_ids||[]).length ? `<span>📄 Gold Docs: <code class="mono">${{esc(c.expected_doc_ids.join(", "))}}</code></span> · ` : ""}}
          ${{(c.must_include_facts||[]).length ? `<span>✅ 必含要点: ${{esc(c.must_include_facts.join(" | "))}}</span> · ` : ""}}
          ${{(c.must_not_claim||[]).length ? `<span style="color:var(--fail)">🚫 幻觉禁区: ${{esc(c.must_not_claim.join(" | "))}}</span>` : ""}}
        </div>
        ${{c.rationale ? `<div class="kpi-meta" style="margin-top:4px">💡 设计意图：${{esc(c.rationale)}}</div>` : ""}}
      </div>
    `;
  }}

  el.innerHTML = html;
}}

function renderMethods() {{
  const el = document.getElementById("tab-methods");
  const methods = STATE.methods_catalog || [];

  let html = `
    <div class="grid grid-2">
      <div class="card">
        <h2>⚙️ 模块化测试方法（支持单独 + 批量执行）</h2>
        <p class="kpi-meta" style="margin-bottom:12px">勾选所需方法可一键批量运行，或点击右侧按钮单独执行某一项测试方法。</p>
        <table>
          <thead>
            <tr><th>选</th><th>方法名称</th><th>阶段</th><th>输出指标</th><th>操作</th></tr>
          </thead>
          <tbody>
            ${{methods.map(m => `
              <tr>
                <td><input type="checkbox" class="method-chk" value="${{esc(m.id)}}" checked /></td>
                <td><b>${{esc(m.zh_name)}}</b><div class="mono muted">${{esc(m.id)}}</div><div class="kpi-meta">${{esc(m.description)}}</div></td>
                <td><span class="badge neutral">${{esc(m.stage)}}</span></td>
                <td class="mono">${{esc((m.metrics||[]).join(", "))}}</td>
                <td>
                  ${{LIVE_MODE
                    ? `<button style="padding:4px 9px;font-size:12px" onclick="runLiveMethods(['${{esc(m.id)}}'])">▶ 单独运行</button>`
                    : `<code class="mono">--methods ${{esc(m.id)}}</code>`}}
                </td>
              </tr>
            `).join("")}}
          </tbody>
        </table>
        <div class="filters mt-12">
          ${{LIVE_MODE ? `
            <button class="primary" onclick="runLiveSelectedBatch(false)">🚀 批量运行所选方法（全量）</button>
            <button onclick="runLiveSelectedBatch(true)">⚡ 仅运行精简核心集 (Core Only)</button>
            <span id="live-run-status" class="kpi-meta"></span>
          ` : `
            <div class="kpi-meta">静态报告模式 · 可在本地终端执行：<code class="mono">python3 -m rag_eval serve</code> 开启网页一键执行，或手动执行 <code class="mono">python3 -m rag_eval run --methods retrieval_hit_rate</code></div>
          `}}
        </div>
      </div>

      <div class="card">
        <h2>🧪 手动单条评测演练台 (Manual Scratchpad)</h2>
        <p class="kpi-meta" style="margin-bottom:10px">无需连接线上接口，直接粘贴一次 RAG 的提问、召回片段与生成回答，立即校验准确率与幻觉声明。</p>
        <div style="display:flex;flex-direction:column;gap:8px">
          <input type="text" id="m-query" placeholder="输入测试问题 (Query)..." />
          <textarea id="m-chunks" placeholder="粘贴召回上下文片段（每行一个片段，可选）..."></textarea>
          <textarea id="m-answer" placeholder="粘贴目标 RAG 生成的实际回答 (Actual Answer)..."></textarea>
          <input type="text" id="m-expected" placeholder="参考标准答案 (Expected Answer，可选)..." />
          <div class="filters" style="margin:0">
            <input type="text" id="m-facts" placeholder="必含要点（逗号分隔）" style="flex:1" />
            <label style="font-size:12.5px"><input type="checkbox" id="m-abstain" /> 知识盲区（应拒答）</label>
          </div>
          <button class="primary" onclick="runManualScratchpad()">▶ 立即执行手动评测</button>
          <div id="manual-result"></div>
        </div>
      </div>
    </div>
  `;
  el.innerHTML = html;
}}

function renderDrilldown() {{
  const el = document.getElementById("tab-drilldown");
  const run = STATE.run;
  const evals = (run && run.case_evaluations) || [];
  if (!evals.length) {{
    el.innerHTML = `<div class="banner info">暂无用例执行明细。</div>`;
    return;
  }}

  const cats = [...new Set(evals.map(e => e.category))];
  const filtered = evals.filter(e => {{
    if (drillFilter.status === "FAILED" && e.passed) return false;
    if (drillFilter.status === "PASSED" && !e.passed) return false;
    if (drillFilter.status === "HALLUCINATED" && !(e.failure_tags || []).includes("hallucination")) return false;
    if (drillFilter.status === "MISS" && !(e.failure_tags || []).includes("retrieval_miss")) return false;
    if (drillFilter.category !== "ALL" && e.category !== drillFilter.category) return false;
    if (drillFilter.search) {{
      const hay = `${{e.case_id}} ${{e.query}} ${{e.expected_answer}} ${{(e.response||{{}}).answer||''}}`.toLowerCase();
      if (!hay.includes(drillFilter.search.toLowerCase())) return false;
    }}
    return true;
  }});

  let html = `
    <div class="filters">
      <span class="pill ${{drillFilter.status==='ALL'?'active':''}}" onclick="drillFilter.status='ALL';renderDrilldown()">全部 (${{evals.length}})</span>
      <span class="pill ${{drillFilter.status==='FAILED'?'active':''}}" onclick="drillFilter.status='FAILED';renderDrilldown()">仅失败 (${{evals.filter(x=>!x.passed).length}})</span>
      <span class="pill ${{drillFilter.status==='HALLUCINATED'?'active':''}}" onclick="drillFilter.status='HALLUCINATED';renderDrilldown()">触发幻觉 (${{evals.filter(x=>(x.failure_tags||[]).includes('hallucination')).length}})</span>
      <span class="pill ${{drillFilter.status==='MISS'?'active':''}}" onclick="drillFilter.status='MISS';renderDrilldown()">检索漏命中 (${{evals.filter(x=>(x.failure_tags||[]).includes('retrieval_miss')).length}})</span>
      <select onchange="drillFilter.category=this.value;renderDrilldown()">
        <option value="ALL">全部分类</option>
        ${{cats.map(c => `<option value="${{esc(c)}}" ${{drillFilter.category===c?'selected':''}}>${{esc(c)}}</option>`).join("")}}
      </select>
      <input type="text" placeholder="按 Case ID 或关键词过滤..." value="${{esc(drillFilter.search)}}" oninput="drillFilter.search=this.value;renderDrilldown()" />
    </div>
    <div class="muted" style="font-size:12.5px;margin-bottom:10px">展示 <b>${{filtered.length}}</b> / ${{evals.length}} 条复盘记录</div>
  `;

  for (const ce of filtered) {{
    const ret = ce.method_scores.retrieval_hit_rate || {{}};
    const acc = ce.method_scores.answer_accuracy || {{}};
    const hal = ce.method_scores.hallucination_audit || {{}};
    const chunks = (ce.response.reranked_chunks && ce.response.reranked_chunks.length)
      ? ce.response.reranked_chunks : (ce.response.retrieved_chunks || []);

    html += `
      <div class="case-box">
        <div class="flex-between">
          <div>
            <b>${{esc(ce.case_id)}}</b>
            <span class="badge ${{ce.passed ? 'pass' : 'fail'}}" style="margin-left:6px">${{ce.passed ? 'PASS' : 'FAIL'}}</span>
            <span class="badge neutral" style="margin-left:4px">${{esc(ce.category)}}</span>
            ${{(ce.failure_tags || []).map(t => `<span class="badge P1" style="margin-left:4px">${{esc(t)}}</span>`).join("")}}
          </div>
          <div class="kpi-meta">
            ${{ret.applicable ? `Hit@K: <b>${{fmtPct(ret.hit_at_k)}}</b> · MRR: <b>${{ret.mrr}}</b> · ` : ""}}
            ${{acc.applicable ? `Acc: <b>${{fmtPct(acc.score)}}</b> · ` : ""}}
            ${{hal.applicable ? `Faithfulness: <b>${{fmtPct(hal.faithfulness)}}</b> · ` : ""}}
            Latency: <b>${{ce.response.latency_ms}} ms</b>
          </div>
        </div>
        <div class="mt-12"><b>Query：</b>${{esc(ce.query)}}</div>
        <div class="grid grid-2 mt-12">
          <div style="background:var(--surface-2);padding:10px;border-radius:8px;font-size:13px">
            <div class="kpi-meta"><b>参考答案 (Expected)</b></div>
            <div>${{esc(ce.expected_answer)}}</div>
          </div>
          <div style="background:var(--surface-2);padding:10px;border-radius:8px;font-size:13px">
            <div class="kpi-meta"><b>实际回答 (Actual RAG Output)</b></div>
            <div>${{esc(ce.response.answer || ce.response.error || "(空)")}}</div>
          </div>
        </div>
        ${{(hal.claims_audit || []).length ? `
          <details class="mt-12" ${{hal.hallucinated ? 'open' : ''}}>
            <summary>声明级幻觉审计明细 (${{hal.claims_audit.length}} 条声明)</summary>
            <div class="mt-12">
              ${{hal.claims_audit.map(cl => `
                <div class="claim-item ${{cl.status==='unsupported'?'claim-unsupported':(cl.status==='supported'||cl.status==='supported_abstention'?'claim-supported':'claim-other')}}">
                  <b>[${{esc(cl.status)}}]</b> ${{esc(cl.claim)}}
                  ${{cl.reason ? `<div class="kpi-meta">${{esc(cl.reason)}}</div>` : ""}}
                </div>
              `).join("")}}
            </div>
          </details>
        ` : ""}}
        ${{chunks.length ? `
          <details class="mt-12">
            <summary>召回上下文片段 (${{chunks.length}} 条 · Gold: ${{esc((ce.expected_doc_ids||[]).join(', ') || '无')}})</summary>
            <div class="mt-12">
              ${{chunks.map((ch, idx) => `
                <div style="border-left:3px solid var(--border);padding:4px 10px;margin:5px 0;font-size:12px">
                  <b>#${{idx+1}} ${{esc(ch.doc_id)}}</b> ${{ch.score !== null && ch.score !== undefined ? `(score=${{ch.score}})` : ""}}
                  <div class="muted">${{esc((ch.text || "").slice(0, 260))}}</div>
                </div>
              `).join("")}}
            </div>
          </details>
        ` : ""}}
      </div>
    `;
  }}

  el.innerHTML = html;
}}

// --- Live Studio API Helpers ---
async function runLiveMethods(methods, opts = {{}}) {{
  if (!LIVE_MODE) return;
  const statusEl = document.getElementById("live-run-status");
  if (statusEl) statusEl.textContent = "⏳ 正在执行评测（受控低并发）...";
  toast("开始执行评测...");
  try {{
    const resp = await fetch("/api/run", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{ methods, ...opts }}),
    }});
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "执行失败");
    STATE = data.state;
    renderAll();
    switchTab("overview");
    toast("评测完成！Run ID: " + STATE.run.run_id);
  }} catch (e) {{
    toast("执行失败: " + e.message);
    if (statusEl) statusEl.textContent = "❌ " + e.message;
  }}
}}

function runLiveSelectedBatch(coreOnly) {{
  const checked = [...document.querySelectorAll(".method-chk:checked")].map(x => x.value);
  if (!checked.length) {{ toast("请至少勾选 1 项测试方法"); return; }}
  runLiveMethods(checked, {{ core_only: coreOnly }});
}}

function runLiveSingleCase(caseId) {{
  runLiveMethods(["all"], {{ case_ids: [caseId] }});
}}

async function runManualScratchpad() {{
  const query = document.getElementById("m-query").value.trim();
  const answer = document.getElementById("m-answer").value.trim();
  const expected = document.getElementById("m-expected").value.trim();
  const chunksRaw = document.getElementById("m-chunks").value.trim();
  const factsRaw = document.getElementById("m-facts").value.trim();
  const shouldAbstain = document.getElementById("m-abstain").checked;
  const outEl = document.getElementById("manual-result");

  if (!query || !answer) {{
    toast("请至少填写测试问题与实际回答");
    return;
  }}

  const chunks = chunksRaw ? chunksRaw.split(/\\n+/).filter(Boolean).map((t, i) => ({{ doc_id: "manual_chunk_" + (i+1), text: t }})) : [];
  const facts = factsRaw ? factsRaw.split(/[,，]/).map(s => s.trim()).filter(Boolean) : [];

  if (LIVE_MODE) {{
    try {{
      const resp = await fetch("/api/manual-eval", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ query, answer, expected_answer: expected, retrieved_chunks: chunks, must_include_facts: facts, should_abstain: shouldAbstain }}),
      }});
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || "手动评测失败");
      const ce = data.case_evaluation;
      const hal = ce.method_scores.hallucination_audit || {{}};
      const acc = ce.method_scores.answer_accuracy || {{}};
      outEl.innerHTML = `
        <div class="case-box mt-12">
          <div><b>评测结果：</b><span class="badge ${{ce.passed?'pass':'fail'}}">${{ce.passed?'PASS':'FAIL'}}</span>
            ${{acc.applicable ? ` · 准确率: <b>${{fmtPct(acc.score)}}</b>` : ""}}
            ${{hal.applicable ? ` · 忠实度: <b>${{fmtPct(hal.faithfulness)}}</b> · 幻觉判定: <b>${{hal.hallucinated ? '是' : '否'}}</b>` : ""}}
          </div>
          ${{(hal.claims_audit || []).map(cl => `<div class="claim-item ${{cl.status==='unsupported'?'claim-unsupported':'claim-supported'}}"><b>[${{esc(cl.status)}}]</b> ${{esc(cl.claim)}} ${{cl.reason ? '— ' + esc(cl.reason) : ''}}</div>`).join("")}}
        </div>
      `;
    }} catch (e) {{
      outEl.innerHTML = `<div class="banner fail">${{esc(e.message)}}</div>`;
    }}
  }} else {{
    // Client-side lightweight heuristic preview when viewing static report offline
    const abstained = /未提供|未找到|未包含|无法确认|not mentioned|cannot answer|insufficient/i.test(answer);
    const ctxAll = chunks.map(c => c.text).join(" ").toLowerCase();
    const matchedFacts = facts.filter(f => answer.toLowerCase().includes(f.toLowerCase()));
    const factPct = facts.length ? Math.round((matchedFacts.length / facts.length) * 100) + "%" : "N/A";
    const statusOk = shouldAbstain ? abstained : (facts.length ? matchedFacts.length >= Math.ceil(facts.length * 0.6) : true);
    outEl.innerHTML = `
      <div class="case-box mt-12">
        <div><b>离线快速预检结果：</b><span class="badge ${{statusOk?'pass':'fail'}}">${{statusOk?'PASS':'CHECK'}}</span></div>
        <div class="kpi-meta mt-12">拒答检测: ${{abstained ? '已拒答' : '未拒答'}} · 必含要点覆盖: ${{factPct}} · 上下文片段数: ${{chunks.length}}</div>
        <div class="kpi-meta">提示：运行 <code class="mono">python3 -m rag_eval serve</code> 可启用完整后端声明级幻觉审计。</div>
      </div>
    `;
  }}
}}

function renderAll() {{
  renderHeader();
  renderOverview();
  renderDiagnosis();
  renderDataset();
  renderMethods();
  renderDrilldown();
}}

renderAll();
</script>
</body>
</html>
"""
