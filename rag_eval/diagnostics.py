"""Phase 5: Root-Cause Attribution & P0-P3 Severity Grading Engine.

Separates failures cleanly across the 6 RAG pipeline layers (Data/Chunking,
Query Understanding, Retrieval, Reranking/Context, Generation/Grounding, and
System/Harness), grades severity rigorously (P0-P3), quantifies impact & confidence,
and produces tiered remediation plans (Quick Win, Structural Fix, A/B Verification).
"""

from __future__ import annotations

from typing import Any, Dict, List

from .models import DiagnosisIssue, MetricSummary, RAGProfile


SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def _confidence_from_n(n: int, has_retrieval_obs: bool = True) -> str:
    if n >= 5 and has_retrieval_obs:
        return "High"
    if n >= 2:
        return "Medium"
    return "Low"


def _sample_excerpt(ce: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "case_id": ce["case_id"],
        "category": ce["category"],
        "query": ce["query"][:120],
        "expected": ce["expected_answer"][:140],
        "actual": (ce["response"].get("answer") or "")[:180],
        "failure_tags": ce.get("failure_tags", []),
    }


def diagnose_run(
    case_evals: List[Dict[str, Any]],
    metrics: List[MetricSummary],
    profile: RAGProfile,
) -> Dict[str, Any]:
    """Analyze case-level failures and aggregate metrics into graded P0-P3 DiagnosisIssues."""
    total = max(1, len(case_evals))
    metric_map = {m.metric_id: m for m in metrics}
    has_ret_obs = profile.observability.retrieved_docs
    issues: List[DiagnosisIssue] = []
    seq = 1

    def add_issue(
        code: str,
        severity: str,
        stage: str,
        title: str,
        summary: str,
        affected: List[Dict[str, Any]],
        metric_evidence: Dict[str, Any],
        quick_wins: List[str],
        structural_fixes: List[str],
        verification_plan: str,
        confidence_override: str = "",
    ) -> None:
        nonlocal seq
        ids = [c["case_id"] for c in affected]
        issues.append(
            DiagnosisIssue(
                issue_id=f"DIAG-{seq:02d}",
                code=code,
                severity=severity,
                pipeline_stage=stage,
                title=title,
                summary=summary,
                affected_case_ids=ids,
                affected_count=len(ids),
                impact_ratio=round(len(ids) / total, 4),
                metric_evidence=metric_evidence,
                confidence=confidence_override or _confidence_from_n(len(ids), has_ret_obs),
                quick_wins=quick_wins,
                structural_fixes=structural_fixes,
                verification_plan=verification_plan,
                sample_examples=[_sample_excerpt(c) for c in affected[:3]],
            )
        )
        seq += 1

    # --- 1. Unanswerable / OOS Fabrication (Safety-critical Hallucination) ---
    oos_fab = [
        ce for ce in case_evals
        if ce["method_scores"].get("abstention_guard", {}).get("oos_fabrication")
        or "oos_fabrication" in ce["method_scores"].get("hallucination_audit", {}).get("hallucination_types", [])
    ]
    if oos_fab:
        oos_total = sum(1 for ce in case_evals if ce.get("should_abstain"))
        fab_rate = len(oos_fab) / max(1, oos_total)
        sev = "P0" if (fab_rate >= 0.20 or len(oos_fab) >= 2) else "P1"
        add_issue(
            code="RC-GEN-02",
            severity=sev,
            stage="生成与约束层 (Generation & Grounding)",
            title="知识盲区未拒答导致凭空编造幻觉 (OOS Fabrication)",
            summary=(
                f"在 {oos_total} 条知识库明确未覆盖的拒答边界用例中，有 {len(oos_fab)} 条（{fab_rate*100:.1f}%）"
                f"未能诚实拒答，反而顺着提问编造了具体数字、规则或流程，属于高危可信度缺陷。"
            ),
            affected=oos_fab,
            metric_evidence={
                "abstention_accuracy": metric_map["abstention_accuracy"].value if "abstention_accuracy" in metric_map else None,
                "oos_fabrication_rate": round(fab_rate, 4),
            },
            quick_wins=[
                "在 System Prompt 顶部强制加入“严格证据闭环约束”：仅允许基于 <context> 作答；若召回片段未直接回答问题，必须输出标准拒答模板，严禁补全或推测。",
                "在检索/重排层增加最低相似度阈值门控（Score Threshold Gate）：当 Top-1 归一化重排分低于阈值（如 0.35）时直接短路返回拒答，跳过 LLM 自由生成。",
            ],
            structural_fixes=[
                "引入独立的前置相关性判别器（Relevance / Answerability Classifier）或轻量 NLI 校验层，在输出前拦截无支撑回答。",
                "将本次失败的 OOS 样本沉淀为 CI 回归红线集，要求拒答准确率 >= 90% 才允许发布。",
            ],
            verification_plan="执行 `rag-eval run --methods abstention_guard,hallucination_audit --categories unanswerable_oos`，验证盲区正确拒答率提升至 >= 90% 且可答问题误拒率 <= 5%。",
        )

    # --- 2. Adversarial Sycophancy / Trap Violation ---
    trap_cases = [
        ce for ce in case_evals
        if ce["method_scores"].get("hallucination_audit", {}).get("trap_violated")
        and not ce.get("should_abstain")
    ]
    if trap_cases:
        sev = "P0" if len(trap_cases) >= 2 else "P1"
        add_issue(
            code="RC-GEN-04",
            severity=sev,
            stage="生成与约束层 (Generation & Grounding)",
            title="对抗诱导与错误前提迎合性幻觉 (Sycophancy / Trap Violation)",
            summary=(
                f"共 {len(trap_cases)} 条用例中，面对包含错误数值或错误前提的提问，生成层未优先采信知识库原文进行纠偏，"
                f"而是顺从用户错误前提输出了禁区陈述。"
            ),
            affected=trap_cases,
            metric_evidence={
                "hallucination_rate": metric_map["hallucination_rate"].value if "hallucination_rate" in metric_map else None,
                "trap_violation_cases": len(trap_cases),
            },
            quick_wins=[
                "在 Prompt 中显式加入“纠偏优先指令”：当用户提问中的前提或数字与检索证据冲突时，必须先明确指出冲突，再逐字引用证据原文。",
                "将生成温度（temperature）降至 0.0~0.1，降低模型附和对话语境的概率。",
            ],
            structural_fixes=[
                "采用“先抽取证据摘要，再对比用户前提，最后生成答案”的两步式（Extract-then-Verify）生成模板。",
                "对数值/条款类问答增加正则或结构化槽位比对后校验。",
            ],
            verification_plan="单独复测对抗分类：`rag-eval run --categories adversarial_distractor --methods hallucination_audit,answer_accuracy`，目标禁区违规率降至 0%。",
        )

    # --- 3. Extrinsic / Unsupported Claims Hallucination (despite context) ---
    extrinsic_cases = [
        ce for ce in case_evals
        if ce["method_scores"].get("hallucination_audit", {}).get("hallucinated")
        and not ce.get("should_abstain")
        and ce not in trap_cases
    ]
    if extrinsic_cases:
        hall_m = metric_map.get("hallucination_rate")
        hval = hall_m.value if (hall_m and hall_m.value is not None) else (len(extrinsic_cases) / total)
        sev = "P0" if hval > 0.20 else ("P1" if hval > 0.10 else "P2")
        add_issue(
            code="RC-GEN-01",
            severity=sev,
            stage="生成与约束层 (Generation & Grounding)",
            title="生成内容超出上下文证据支撑（外部/数值幻觉）",
            summary=(
                f"检测到 {len(extrinsic_cases)} 条可答用例包含上下文未支撑的声明或未出现过的外部数值，"
                f"说明模型在生成阶段掺杂了预训练先验知识或进行了未经证实的外推。"
            ),
            affected=extrinsic_cases,
            metric_evidence={
                "hallucination_rate": hval,
                "faithfulness": metric_map["faithfulness"].value if "faithfulness" in metric_map else None,
            },
            quick_wins=[
                "强制要求“逐句附带片段编号引用 [Chunk i]”，无对应片段编号的陈述禁止输出。",
                "精简送入 Prompt 的冗余低分片段（减少噪音诱导模型发散）。",
            ],
            structural_fixes=[
                "对高风险业务路径增加 Post-Generation Faithfulness Guard（声明级数字与实体回查拦截器）。",
                "对复杂问答开启结构化输出（JSON: {evidence_quotes, answer, missing_info}），先摘录再组织回答。",
            ],
            verification_plan="运行 `rag-eval run --methods hallucination_audit`（可选开启 `--llm-judge` 交叉复核），验证声明忠实度 >= 90% 且幻觉触发率 <= 10%。",
        )

    # --- 4. Rerank Cutoff Loss (Retrieved in raw, dropped before generation!) ---
    rerank_dropped = [
        ce for ce in case_evals
        if ce["method_scores"].get("retrieval_hit_rate", {}).get("rerank_cutoff_loss")
    ]
    if rerank_dropped:
        sev = "P1" if len(rerank_dropped) >= 2 else "P2"
        add_issue(
            code="RC-RANK-02",
            severity=sev,
            stage="重排与上下文拼接层 (Reranking & Context Assembly)",
            title="初排已命中但重排/截断阶段丢失标准证据 (Rerank Cutoff Loss)",
            summary=(
                f"共 {len(rerank_dropped)} 条用例的黄金文档已在初排候选池中成功召回，但在 Rerank 或 Top-N 截断后被丢弃，"
                f"直接导致生成层看不见关键证据（尤其多跳跨文档场景影响显著）。"
            ),
            affected=rerank_dropped,
            metric_evidence={
                "rerank_cutoff_loss_cases": len(rerank_dropped),
                "recall_at_k": metric_map["recall_at_k"].value if "recall_at_k" in metric_map else None,
            },
            quick_wins=[
                "适当放宽重排后送入 LLM 的 Top-N 截断值（例如从 Top-1/Top-2 提升至 Top-4~Top-6），并按文档多样性去重（MMR）防止单一文档霸占全部窗口。",
                "检查重排模型输入长度限制（如 512 token 截断），避免长切片后半段信息在重排打分时被截断。",
            ],
            structural_fixes=[
                "针对多跳问题实现“子查询独立召回 + RRF 融合 + 文档级保底配额”，确保每个子问题至少保留 1-2 个高分文档进入生成上下文。",
                "使用领域难负样本（Hard Negatives）微调 Cross-Encoder 重排器。",
            ],
            verification_plan="复测 `rag-eval run --methods retrieval_hit_rate,context_quality --categories multi_hop_reasoning`，验证 Recall@K 提升且 rerank_cutoff_loss 归零。",
        )

    # --- 5. Primary Retrieval Miss (Hit@K miss) ---
    ret_misses = [
        ce for ce in case_evals
        if ce["method_scores"].get("retrieval_hit_rate", {}).get("applicable")
        and ce["method_scores"]["retrieval_hit_rate"].get("hit_at_k", 1.0) < 1.0
        and ce not in rerank_dropped
    ]
    if ret_misses:
        hit_m = metric_map.get("hit_at_k")
        hit_val = hit_m.value if (hit_m and hit_m.value is not None) else 0.0
        sev = "P0" if hit_val < 0.55 else ("P1" if hit_val < profile.thresholds.get("hit_at_k_min", 0.80) else "P2")
        jargon_misses = [c for c in ret_misses if c["category"] in {"terminology_jargon", "numerical_table", "temporal_version"}]
        extra_hint = (
            f"其中 {len(jargon_misses)} 条集中在专有名词/表格数值/版本时效类查询，强烈指向纯语义向量检索对精确词法与元数据不敏感。"
            if jargon_misses else ""
        )
        add_issue(
            code="RC-RET-01",
            severity=sev,
            stage="检索与索引层 (Retrieval & Indexing)",
            title="检索召回漏命中导致上游无米之炊 (Retrieval Hit Miss)",
            summary=(
                f"共 {len(ret_misses)} 条可答用例在最终召回列表中未命中标准证据文档（当前 Hit@K = {hit_val*100:.1f}%）。"
                + extra_hint
            ),
            affected=ret_misses,
            metric_evidence={
                "hit_at_k": hit_val,
                "mrr": metric_map["mrr"].value if "mrr" in metric_map else None,
            },
            quick_wins=[
                "启用混合检索（Dense Embedding + BM25 稀疏检索 + RRF 倒数排名融合），优先补齐专有名词、错误码、版本号和精确数字召回。",
                "将文档标题、所属章节路径（Breadcrumb Header）与版本/日期元数据前置拼接到每个切片文本中再建索引。",
            ],
            structural_fixes=[
                "审查切片策略：采用语义/标题边界切片（Markdown Header / 表格整体不拆分），引入父子文档索引（Small-to-Big Retrieval）。",
                "针对同义词与口语改写，接入轻量查询扩展/同义词典或领域微调 Embedding 模型。",
            ],
            verification_plan="执行 `rag-eval run --methods retrieval_hit_rate` 对比优化前后 Hit@1/Hit@3/Hit@K 与 MRR 曲线。",
        )

    # --- 6. Multi-Hop Incomplete Recall ---
    multihop_partial = [
        ce for ce in case_evals
        if ce["category"] == "multi_hop_reasoning"
        and ce["method_scores"].get("retrieval_hit_rate", {}).get("applicable")
        and 0.0 < ce["method_scores"]["retrieval_hit_rate"].get("recall_at_k", 1.0) < 1.0
    ]
    if multihop_partial:
        add_issue(
            code="RC-QRY-01",
            severity="P1" if len(multihop_partial) >= 2 else "P2",
            stage="查询理解与拆解层 (Query Understanding & Multi-Hop)",
            title="多跳跨文档证据召回不完整（仅命中单侧文档）",
            summary=(
                f"有 {len(multihop_partial)} 条多跳推理用例仅召回了部分关联文档（0 < Recall@K < 1），"
                f"导致最终答案只能覆盖单个知识点而遗漏跨文档综合结论。"
            ),
            affected=multihop_partial,
            metric_evidence={"affected_multihop_cases": len(multihop_partial)},
            quick_wins=[
                "对含多个实体或并列条件的提问，先执行轻量子问题拆解（Query Decomposition），分别召回 Top-3 后按文档去重合并。",
            ],
            structural_fixes=[
                "构建两轮迭代检索（Iterative / Hop-2 Retrieval）或实体关联索引，依据首轮证据缺口触发二次补充召回。",
            ],
            verification_plan="复测 `rag-eval run --categories multi_hop_reasoning --methods retrieval_hit_rate,answer_accuracy`，目标多跳 Recall@K >= 0.85。",
        )

    # --- 7. Context Hit OK, Yet Answer Accuracy Failed (Generation Extraction / Numerical Drift) ---
    gen_extract_fail = [
        ce for ce in case_evals
        if ce["method_scores"].get("answer_accuracy", {}).get("applicable")
        and not ce["method_scores"]["answer_accuracy"].get("passed")
        and (
            not has_ret_obs
            or ce["method_scores"].get("retrieval_hit_rate", {}).get("hit_at_k", 1.0) >= 1.0
        )
        and ce not in oos_fab
        and ce not in trap_cases
    ]
    if gen_extract_fail:
        num_fail = [
            c for c in gen_extract_fail
            if (c["method_scores"]["answer_accuracy"].get("numeric_accuracy") or 1.0) < 0.8
        ]
        acc_m = metric_map.get("answer_accuracy")
        acc_val = acc_m.value if (acc_m and acc_m.value is not None) else 0.0
        sev = "P1" if acc_val < profile.thresholds.get("answer_accuracy_min", 0.78) else "P2"
        add_issue(
            code="RC-GEN-03",
            severity=sev,
            stage="生成与信息抽取层 (Generation & Context Utilization)",
            title="证据已召回但答案要点遗漏或数值抽取失真",
            summary=(
                f"共 {len(gen_extract_fail)} 条用例在证据文档已命中的情况下答案仍未达标"
                + (f"（含 {len(num_fail)} 条关键数值/表格字段抽取偏差）" if num_fail else "")
                + "，指向上下文噪音干扰（Lost-in-the-Middle）、切片截断或生成提示词未约束要点完整性。"
            ),
            affected=gen_extract_fail,
            metric_evidence={
                "answer_accuracy": acc_val,
                "numeric_accuracy": metric_map["numeric_accuracy"].value if "numeric_accuracy" in metric_map else None,
            },
            quick_wins=[
                "优化上下文排序（将最高分核心片段置于 Prompt 最前与最后，规避 Lost-in-the-Middle），并清理无关低分噪音块。",
                "针对表格/数值类问题，保留 Markdown/HTML 完整表格结构并在 Prompt 中要求逐项核对数字与单位。",
            ],
            structural_fixes=[
                "对含表格/规章文档使用结构化解析器（保留表头上下文注入每一行切片），避免表格跨页/跨块断裂。",
                "将生成模型升级为长上下文推理更稳健的模型版本，或对多要点汇总问题使用分点 Checklist 模板。",
            ],
            verification_plan="执行 `rag-eval run --methods answer_accuracy,context_quality --categories numerical_table,single_hop_fact`，验证数值准确率 >= 90%。",
        )

    # --- 8. False Refusal on Answerable Queries ---
    false_refusals = [
        ce for ce in case_evals
        if ce["method_scores"].get("abstention_guard", {}).get("false_refusal")
    ]
    if false_refusals:
        fr_m = metric_map.get("false_refusal_rate")
        fr_val = fr_m.value if (fr_m and fr_m.value is not None) else (len(false_refusals) / total)
        if fr_val > 0.08:
            add_issue(
                code="RC-GEN-05",
                severity="P2",
                stage="生成与约束层 (Generation & Grounding)",
                title="可回答问题过度拒答（误拒率偏高）",
                summary=f"有 {len(false_refusals)} 条知识库内可回答问题被模型错误拒答（误拒率 {fr_val*100:.1f}%），可能由拒答阈值过严或上游召回不足引发。",
                affected=false_refusals,
                metric_evidence={"false_refusal_rate": fr_val},
                quick_wins=["区分“完全无证据（拒答）”与“部分证据（基于已有证据谨慎作答并注明范围）”，避免一刀切拒答。"],
                structural_fixes=["联合校准检索相似度阈值与重排分数分布，绘制 Precision-Recall 曲线选取最优拒答门限。"],
                verification_plan="复测 `rag-eval run --methods abstention_guard,answer_accuracy`，确保误拒率 <= 5% 同时盲区拒答率 >= 85%。",
            )

    # --- 9. Citation Missing or Ungrounded ---
    if "citation_accuracy" in metric_map and metric_map["citation_accuracy"].value is not None:
        ca = metric_map["citation_accuracy"].value
        if ca < profile.thresholds.get("citation_accuracy_min", 0.75):
            bad_cite = [
                ce for ce in case_evals
                if ce["method_scores"].get("citation_check", {}).get("applicable")
                and ce["method_scores"]["citation_check"].get("citation_accuracy", 1.0) < 0.75
            ]
            if bad_cite:
                add_issue(
                    code="RC-CIT-01",
                    severity="P2" if ca < 0.5 else "P3",
                    stage="后处理与溯源层 (Citation & Post-Processing)",
                    title="溯源引用缺失或引用文档 ID 不匹配",
                    summary=f"引用溯源准确率为 {ca*100:.1f}%，共 {len(bad_cite)} 条可答用例缺少有效出处或引用了非目标文档。",
                    affected=bad_cite,
                    metric_evidence={"citation_accuracy": ca},
                    quick_wins=["在上下文片段头部注入统一可解析标识 `[DOC:<doc_id>]`，并在输出层校验返回 citations 与实际入参片段列表一致。"],
                    structural_fixes=["改用结构化引用绑定（Sentence-to-Chunk Alignment），避免由大模型自由手写文件名产生拼写偏差。"],
                    verification_plan="执行 `rag-eval run --methods citation_check`，验证引用覆盖率与准确率 >= 85%。",
                )

    # --- 10. Latency / Error Rate / Context Bloat ---
    err_cases = [ce for ce in case_evals if ce["response"].get("error")]
    if err_cases:
        err_rate = len(err_cases) / total
        add_issue(
            code="RC-SYS-01",
            severity="P0" if err_rate > 0.10 else "P1",
            stage="系统稳定性与接口层 (System & Adapter)",
            title="RAG 接口调用异常或超时失败",
            summary=f"共有 {len(err_cases)} 条用例出现请求异常或超时（错误率 {err_rate*100:.1f}%），直接影响线上可用性与指标完整性。",
            affected=err_cases,
            metric_evidence={"error_rate": round(err_rate, 4)},
            quick_wins=["检查超时阈值、限流配置及目标接口响应 JSON 结构是否与适配器字段映射一致。"],
            structural_fixes=["增加指数退避重试、熔断降级与请求级链路追踪日志。"],
            verification_plan="修复接口稳定性后复测 `rag-eval run --methods latency_efficiency`，确保错误率 < 1%。",
        )

    p95_m = metric_map.get("latency_p95_ms")
    if p95_m and p95_m.value is not None and p95_m.value > profile.thresholds.get("latency_p95_max_ms", 5000.0):
        slow_cases = [
            ce for ce in case_evals
            if ce["method_scores"].get("latency_efficiency", {}).get("latency_ms", 0.0)
            > profile.thresholds.get("latency_p95_max_ms", 5000.0)
        ]
        add_issue(
            code="RC-PERF-01",
            severity="P2",
            stage="系统性能与资源层 (Latency & Context Budget)",
            title="尾延迟 P95 超过性能目标阈值",
            summary=f"当前响应延迟 P95 为 {p95_m.value:.0f} ms（目标 <= {profile.thresholds.get('latency_p95_max_ms', 5000.0):.0f} ms），影响交互体验。",
            affected=slow_cases,
            metric_evidence={"latency_p95_ms": p95_m.value, "latency_p50_ms": metric_map["latency_p50_ms"].value if "latency_p50_ms" in metric_map else None},
            quick_wins=["压缩送入 LLM 的上下文 Token 预算（如限制 Top-4 片段且单块 <= 500 字），开启首字流式返回（Streaming）。"],
            structural_fixes=["增加热点 Query 语义缓存、异步并发执行向量+BM25 检索，并用轻量化重排模型降低排队延迟。"],
            verification_plan="执行 `rag-eval run --methods latency_efficiency`，验证 P95 延迟降至目标阈值以内。",
        )

    # --- 11. Harness / Observability Rigor Warning ---
    if not has_ret_obs:
        add_issue(
            code="RC-OBS-01",
            severity="P3",
            stage="评测可观测性层 (Evaluation Observability)",
            title="检索中间结果不可观测（召回层与生成层无法彻底解耦）",
            summary="当前 RAG 接口未暴露 retrieved_chunks 中间召回结果，因此系统严格将 Hit@K/Recall@K 标记为不可观测，暂无法精确区分漏召回与生成错误。",
            affected=[],
            metric_evidence={"retrieved_docs_observable": False},
            quick_wins=["在测试环境为 RAG 接口增加可选参数 `debug=true` / `return_sources=true`，返回召回文档 ID、原文片段与打分。"],
            structural_fixes=["在检索与重排节点落盘结构化 Trace 日志，接入离线回测流水线实现全链路逐层归因。"],
            verification_plan="暴露召回字段后更新理解档案 `rag-eval confirm --obs-retrieved true`，再运行全套检索+生成解耦回测。",
            confidence_override="High",
        )

    issues.sort(key=lambda x: (SEVERITY_ORDER.get(x.severity, 9), -x.affected_count))

    counts = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    for iss in issues:
        if iss.severity in counts:
            counts[iss.severity] += 1

    if counts["P0"] > 0:
        overall_grade = "D (存在阻断级 P0 风险，禁止直接全量上线)"
    elif counts["P1"] > 0:
        overall_grade = "C (存在严重级 P1 短板，需优先修复核心链路)"
    elif counts["P2"] > 0:
        overall_grade = "B (核心指标基本达标，局部存在中等级 P2 优化项)"
    else:
        overall_grade = "A (关键指标健康，无显著阻断或严重问题)"

    return {
        "overall_grade": overall_grade,
        "severity_counts": counts,
        "total_issues": len(issues),
        "issues": [i.to_dict() for i in issues],
    }
