"""Phase 4: Modular RAG evaluation methods (individually or batch executable).

Provides transparent, deterministic mathematical scoring for retrieval, context quality,
answer accuracy, claim-level hallucination & faithfulness auditing, abstention guard,
citation verification, and latency/efficiency profiling — plus Wilson 95% confidence intervals.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from .models import MetricSummary, RAGResponse, TestCase


# --- Text & Token Utilities (pure stdlib, CJK + English aware) ---

ABSTENTION_PATTERNS = [
    r"(?:知识库|文档|上下文|资料|检索结果).*?(?:未提供|未找到|未包含|未提及|未覆盖|没有找到|没有包含|没有提及|无相关|不存在)",
    r"未提供(?:该|此|相关)?(?:问题|信息|依据|内容)",
    r"未找到(?:与该问题|直接相关|足够|相关)(?:的)?(?:依据|信息|文档|记录|内容)",
    r"无法(?:确认|回答|确定|给出|提供)(?:具体|可靠|准确)?(?:答案|回答|结论|信息)?",
    r"缺乏(?:足够)?(?:依据|信息|证据)",
    r"超出.*?(?:范围|知识库)",
    r"\b(?:cannot answer|not mentioned|no information|insufficient context|out of scope|not covered|cannot verify|unknown from the provided)\b",
]
ABSTENTION_RE = re.compile("|".join(ABSTENTION_PATTERNS), re.IGNORECASE)

NEGATION_WORDS = ["不是", "并非", "不一致", "错误", "不实", "没有", "并未", "不能", "禁止", "不得", "incorrect", "not ", "no,", "false"]
NUM_TOKEN_RE = re.compile(r"(?<!\w)(?:\$|¥|￥)?\d+(?:,\d{3})*(?:\.\d+)?%?")
INLINE_CITATION_RE = re.compile(r"\[([^\[\]]{1,60})\]")
CLAIM_SPLIT_RE = re.compile(r"(?<=[。！？!?\n;；])\s*|(?<![0-9vV])\.(?=\s|$)\s*")


def normalize_doc_id(doc_id: str) -> str:
    return doc_id.strip().replace("\\", "/").lower()


def doc_matches_gold(retrieved_doc_id: str, retrieved_text: str, gold_doc_ids: List[str], gold_snippets: List[str]) -> bool:
    norm_r = normalize_doc_id(retrieved_doc_id)
    r_base = norm_r.split("/")[-1] if norm_r else ""
    for g in gold_doc_ids:
        norm_g = normalize_doc_id(g)
        g_base = norm_g.split("/")[-1] if norm_g else ""
        if not norm_g:
            continue
        if norm_r == norm_g or norm_r.endswith("/" + norm_g) or norm_g.endswith("/" + norm_r):
            return True
        if r_base and g_base and r_base == g_base:
            return True
    if retrieved_text and gold_snippets:
        r_low = retrieved_text.lower()
        for snip in gold_snippets:
            probe = snip.strip().lower()[:60]
            if len(probe) >= 16 and probe in r_low:
                return True
    return False


def tokenize_eval(text: str) -> List[str]:
    tokens: List[str] = []
    for tok in re.findall(r"[a-zA-Z0-9][a-zA-Z0-9_\-.%]*", text.lower()):
        cleaned = tok.rstrip("._-")
        if cleaned:
            tokens.append(cleaned)
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(run) == 1:
            tokens.append(run)
        else:
            for i in range(len(run) - 1):
                tokens.append(run[i : i + 2])
    return tokens


def token_f1(pred: str, ref: str) -> float:
    p_toks = tokenize_eval(pred)
    r_toks = tokenize_eval(ref)
    if not p_toks or not r_toks:
        return 0.0
    p_counts: Dict[str, int] = {}
    for t in p_toks:
        p_counts[t] = p_counts.get(t, 0) + 1
    r_counts: Dict[str, int] = {}
    for t in r_toks:
        r_counts[t] = r_counts.get(t, 0) + 1
    overlap = sum(min(p_counts[t], r_counts.get(t, 0)) for t in p_counts)
    if overlap == 0:
        return 0.0
    prec = overlap / len(p_toks)
    rec = overlap / len(r_toks)
    return round(2 * prec * rec / (prec + rec), 4)


def is_abstention(answer: str) -> bool:
    if not answer or not answer.strip():
        return True
    return bool(ABSTENTION_RE.search(answer))


def extract_numbers_normalized(text: str) -> List[str]:
    out: List[str] = []
    for m in NUM_TOKEN_RE.finditer(text):
        raw = m.group(0).replace(",", "").strip()
        if raw and any(ch.isdigit() for ch in raw):
            out.append(raw)
    return out


def wilson_ci(successes: float, n: int, z: float = 1.96) -> Optional[List[float]]:
    """Wilson score 95% confidence interval for a binomial proportion."""
    if n <= 0:
        return None
    p = max(0.0, min(1.0, successes / n))
    denom = 1.0 + (z * z) / n
    center = (p + (z * z) / (2.0 * n)) / denom
    half = (z * math.sqrt((p * (1.0 - p) + (z * z) / (4.0 * n)) / n)) / denom
    return [round(max(0.0, center - half), 4), round(min(1.0, center + half), 4)]


def percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    idx = (pct / 100.0) * (len(ordered) - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return round(ordered[lo], 2)
    frac = idx - lo
    return round(ordered[lo] * (1 - frac) + ordered[hi] * frac, 2)


# --- Method Catalog Metadata ---

@dataclass
class MethodSpec:
    id: str
    zh_name: str
    en_name: str
    stage: str
    description: str
    metrics: List[str]
    requires_retrieved_docs: bool = False
    requires_citations: bool = False
    supports_single_case: bool = True
    cost_tier: str = "low"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "zh_name": self.zh_name,
            "en_name": self.en_name,
            "stage": self.stage,
            "description": self.description,
            "metrics": self.metrics,
            "requires_retrieved_docs": self.requires_retrieved_docs,
            "requires_citations": self.requires_citations,
            "supports_single_case": self.supports_single_case,
            "cost_tier": self.cost_tier,
        }


METHOD_SPECS: Dict[str, MethodSpec] = {
    "retrieval_hit_rate": MethodSpec(
        id="retrieval_hit_rate",
        zh_name="检索命中与排序评测",
        en_name="Retrieval Hit Rate, Recall & Ranking",
        stage="retrieval",
        description="计算 Hit@1/Hit@3/Hit@5、Recall@K、Precision@K、MRR、NDCG@K，并检测重排截断损失（Rerank Cutoff Loss）。",
        metrics=["hit_at_1", "hit_at_3", "hit_at_5", "recall_at_k", "mrr", "ndcg_at_k"],
        requires_retrieved_docs=True,
    ),
    "context_quality": MethodSpec(
        id="context_quality",
        zh_name="上下文证据质量评测",
        en_name="Context Recall & Precision",
        stage="reranking",
        description="检验送入生成层的上下文片段是否覆盖核心证据（Context Recall）及信噪比（Context Precision / Noise Ratio）。",
        metrics=["context_recall", "context_precision"],
        requires_retrieved_docs=True,
    ),
    "answer_accuracy": MethodSpec(
        id="answer_accuracy",
        zh_name="答案准确率与数值保真评测",
        en_name="Answer Accuracy & Numeric Fidelity",
        stage="generation",
        description="综合关键事实覆盖率（Key-Fact Coverage）、词元 F1 与精确数值匹配率，评估回答准确性。",
        metrics=["answer_accuracy", "answer_pass_rate", "numeric_accuracy"],
        requires_retrieved_docs=False,
    ),
    "hallucination_audit": MethodSpec(
        id="hallucination_audit",
        zh_name="声明级幻觉与忠实度审计",
        en_name="Claim-Level Hallucination & Faithfulness Audit",
        stage="generation",
        description="拆分生成答案的原子声明，核验数字出处、上下文支撑度、对抗诱导陷阱及盲区编造，计算幻觉率与忠实度（支持可选 LLM-as-a-Judge 融合）。",
        metrics=["hallucination_rate", "faithfulness"],
        requires_retrieved_docs=False,
    ),
    "abstention_guard": MethodSpec(
        id="abstention_guard",
        zh_name="知识盲区拒答守卫评测",
        en_name="Abstention & Refusal Guard",
        stage="generation",
        description="专门评测知识库未覆盖问题（OOS）的正确拒答率，以及可回答问题的误拒率（False Refusal Rate）。",
        metrics=["abstention_accuracy", "false_refusal_rate"],
        requires_retrieved_docs=False,
    ),
    "citation_check": MethodSpec(
        id="citation_check",
        zh_name="溯源引用准确度评测",
        en_name="Citation Grounding Verification",
        stage="postprocessing",
        description="验证答案是否附带来源引用，以及所引文档是否真实存在于召回片段和标准证据中。",
        metrics=["citation_presence_rate", "citation_accuracy"],
        requires_retrieved_docs=False,
        requires_citations=True,
    ),
    "latency_efficiency": MethodSpec(
        id="latency_efficiency",
        zh_name="延迟与稳定性画像",
        en_name="Latency, Error Rate & Context Budget",
        stage="system",
        description="统计 P50/P95 延迟、请求错误率及上下文字符体积，识别尾延迟与上下文膨胀瓶颈。",
        metrics=["latency_p50_ms", "latency_p95_ms", "error_rate"],
        requires_retrieved_docs=False,
    ),
}


# --- Per-Case Evaluators ---

def eval_retrieval_case(case: TestCase, resp: RAGResponse) -> Dict[str, Any]:
    if case.should_abstain or not case.expected_doc_ids:
        return {"applicable": False, "reason": "unanswerable_or_no_gold_docs"}

    active_chunks = resp.reranked_chunks if resp.reranked_chunks else resp.retrieved_chunks
    raw_chunks = resp.retrieved_chunks if resp.retrieved_chunks else active_chunks

    gold_set = [normalize_doc_id(g) for g in case.expected_doc_ids]
    hit_ranks: List[int] = []
    first_gold_ranks: List[int] = []
    matched_golds: set[str] = set()

    for idx, ch in enumerate(active_chunks, start=1):
        matched_any = False
        for g in case.expected_doc_ids:
            if doc_matches_gold(ch.doc_id, ch.text, [g], case.expected_evidence_snippets):
                ng = normalize_doc_id(g)
                if ng not in matched_golds:
                    matched_golds.add(ng)
                    first_gold_ranks.append(idx)
                matched_any = True
        if matched_any:
            hit_ranks.append(idx)

    raw_matched: set[str] = set()
    for ch in raw_chunks:
        for g in case.expected_doc_ids:
            if doc_matches_gold(ch.doc_id, ch.text, [g], case.expected_evidence_snippets):
                raw_matched.add(normalize_doc_id(g))

    rerank_dropped = bool(resp.reranked_chunks and len(raw_matched) > len(matched_golds))

    first_rank = hit_ranks[0] if hit_ranks else None
    hit_at_1 = 1.0 if (first_rank is not None and first_rank <= 1) else 0.0
    hit_at_3 = 1.0 if (first_rank is not None and first_rank <= 3) else 0.0
    hit_at_5 = 1.0 if (first_rank is not None and first_rank <= 5) else 0.0
    hit_at_k = 1.0 if first_rank is not None else 0.0
    recall_at_k = round(len(matched_golds) / max(1, len(set(gold_set))), 4)
    precision_at_k = round(len(hit_ranks) / max(1, len(active_chunks)), 4) if active_chunks else 0.0
    mrr = round(1.0 / first_rank, 4) if first_rank else 0.0

    # Document-level binary relevance NDCG@K (strictly bounded in [0.0, 1.0])
    dcg = sum(1.0 / math.log2(r + 1) for r in first_gold_ranks)
    ideal_hits = min(len(set(gold_set)), max(1, len(active_chunks)))
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    ndcg = round(min(1.0, dcg / idcg), 4) if idcg > 0 else 0.0

    return {
        "applicable": True,
        "hit_at_1": hit_at_1,
        "hit_at_3": hit_at_3,
        "hit_at_5": hit_at_5,
        "hit_at_k": hit_at_k,
        "recall_at_k": recall_at_k,
        "precision_at_k": precision_at_k,
        "mrr": mrr,
        "ndcg_at_k": ndcg,
        "first_hit_rank": first_rank,
        "matched_gold_docs": sorted(matched_golds),
        "missing_gold_docs": sorted(set(gold_set) - matched_golds),
        "raw_recall_at_k": round(len(raw_matched) / max(1, len(set(gold_set))), 4),
        "rerank_cutoff_loss": rerank_dropped,
        "k_evaluated": len(active_chunks),
    }


def eval_context_quality_case(case: TestCase, resp: RAGResponse) -> Dict[str, Any]:
    if case.should_abstain or not (case.must_include_facts or case.expected_evidence_snippets):
        return {"applicable": False, "reason": "no_expected_evidence"}

    active_chunks = resp.reranked_chunks if resp.reranked_chunks else resp.retrieved_chunks
    if not active_chunks:
        return {"applicable": True, "context_recall": 0.0, "context_precision": 0.0, "noise_ratio": 1.0}

    joined_ctx = "\n".join(c.text for c in active_chunks).lower()
    probes = case.must_include_facts or case.expected_evidence_snippets
    hits = 0
    for p in probes:
        p_low = p.strip().lower()
        if not p_low:
            continue
        if p_low in joined_ctx or token_f1(joined_ctx, p_low) >= 0.25:
            hits += 1
    ctx_recall = round(hits / max(1, len(probes)), 4)

    relevant_chunks = 0
    for ch in active_chunks:
        if doc_matches_gold(ch.doc_id, ch.text, case.expected_doc_ids, case.expected_evidence_snippets):
            relevant_chunks += 1
        elif any(p.lower() in ch.text.lower() for p in probes if len(p.strip()) >= 3):
            relevant_chunks += 1

    ctx_precision = round(relevant_chunks / len(active_chunks), 4)
    return {
        "applicable": True,
        "context_recall": ctx_recall,
        "context_precision": ctx_precision,
        "noise_ratio": round(1.0 - ctx_precision, 4),
    }


def eval_answer_accuracy_case(case: TestCase, resp: RAGResponse, judge_res: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if resp.error:
        return {"applicable": True, "score": 0.0, "passed": False, "reason": "adapter_error"}

    ans = resp.answer or ""
    if case.should_abstain:
        return {
            "applicable": False,
            "delegated_to": "abstention_guard",
            "abstained": is_abstention(ans),
        }

    ans_low = ans.lower()
    facts = case.must_include_facts
    matched_facts: List[str] = []
    missing_facts: List[str] = []
    for f in facts:
        fl = f.strip().lower()
        if not fl:
            continue
        if fl in ans_low or token_f1(ans, f) >= 0.45:
            matched_facts.append(f)
        else:
            missing_facts.append(f)
    fact_cov = round(len(matched_facts) / len(facts), 4) if facts else token_f1(ans, case.expected_answer)
    f1 = token_f1(ans, case.expected_answer)

    num_acc: Optional[float] = None
    missing_nums: List[str] = []
    if case.expected_numbers:
        ans_clean = ans.replace(",", "").lower()
        matched_n = 0
        for n in case.expected_numbers:
            n_clean = n.replace(",", "").strip().lower()
            digits_only = re.search(r"\d+(?:\.\d+)?", n_clean)
            if n_clean in ans_clean or (digits_only and digits_only.group(0) in ans_clean):
                matched_n += 1
            else:
                missing_nums.append(n)
        num_acc = round(matched_n / len(case.expected_numbers), 4)

    false_refusal = is_abstention(ans) and len(matched_facts) == 0

    if false_refusal:
        det_score = 0.0
    elif num_acc is not None:
        det_score = round(0.45 * fact_cov + 0.35 * num_acc + 0.20 * f1, 4)
    else:
        det_score = round(0.65 * fact_cov + 0.35 * f1, 4)

    # Boost cleanly only when key facts are 100% covered AND answer isn't an overly noisy dump
    if not false_refusal and fact_cov >= 0.99 and (num_acc is None or num_acc >= 0.99) and f1 >= 0.25:
        det_score = max(det_score, 0.90)

    final_score = det_score
    mode = "deterministic"
    if judge_res is not None:
        final_score = round(0.5 * det_score + 0.5 * judge_res["accuracy_score"], 4)
        mode = "hybrid"

    return {
        "applicable": True,
        "score": final_score,
        "deterministic_score": det_score,
        "key_fact_coverage": fact_cov,
        "token_f1": f1,
        "numeric_accuracy": num_acc,
        "matched_facts": matched_facts,
        "missing_facts": missing_facts,
        "missing_numbers": missing_nums,
        "false_refusal": false_refusal,
        "passed": final_score >= 0.65 and (num_acc is None or num_acc >= 0.8),
        "mode": mode,
    }


def eval_hallucination_case(case: TestCase, resp: RAGResponse, judge_res: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if resp.error:
        return {"applicable": False, "reason": "adapter_error"}

    ans = (resp.answer or "").strip()
    active_chunks = resp.reranked_chunks if resp.reranked_chunks else resp.retrieved_chunks
    ctx_text = "\n".join(c.text for c in active_chunks)
    ans_low = ans.lower()

    claims_audit: List[Dict[str, Any]] = []
    trap_violations: List[str] = []
    hallucination_types: List[str] = []

    if case.should_abstain:
        abstained = is_abstention(ans)
        if abstained:
            return {
                "applicable": True,
                "faithfulness": 1.0,
                "hallucination_rate": 0.0,
                "hallucinated": False,
                "trap_violated": False,
                "hallucination_types": [],
                "claims_audit": [{"claim": ans[:140], "status": "supported_abstention", "reason": "正确声明知识库未覆盖"}],
                "context_observable": bool(ctx_text.strip()),
                "mode": "deterministic" if judge_res is None else "hybrid",
            }
        hallucination_types.append("oos_fabrication")
        claims_audit.append(
            {
                "claim": ans[:180],
                "status": "unsupported",
                "type": "oos_fabrication",
                "reason": "知识库未包含该问题依据，但模型未拒答而直接编造了回答。",
            }
        )
        return {
            "applicable": True,
            "faithfulness": 0.0,
            "hallucination_rate": 1.0,
            "hallucinated": True,
            "trap_violated": True,
            "hallucination_types": hallucination_types,
            "claims_audit": claims_audit,
            "context_observable": bool(ctx_text.strip()),
            "mode": "deterministic" if judge_res is None else "hybrid",
        }

    for forbidden in case.must_not_claim:
        f_low = forbidden.strip().lower()
        if not f_low or f_low not in ans_low:
            continue
        pos = ans_low.find(f_low)
        window = ans_low[max(0, pos - 24) : pos + len(f_low) + 12]
        negated = any(neg in window for neg in NEGATION_WORDS)
        if not negated:
            trap_violations.append(forbidden)
            hallucination_types.append("adversarial_trap_or_wrong_value")
            claims_audit.append(
                {
                    "claim": ans[max(0, pos - 20) : pos + len(f_low) + 40],
                    "status": "unsupported",
                    "type": "trap_violation",
                    "reason": f"答案包含未否定的错误/禁区陈述 `{forbidden}`。",
                }
            )

    has_context = bool(ctx_text.strip())
    ctx_nums = set(extract_numbers_normalized(ctx_text))
    expected_nums_norm = set(extract_numbers_normalized(" ".join(case.expected_numbers + [case.expected_answer])))
    allowed_nums = ctx_nums | expected_nums_norm

    raw_claims = [c.strip() for c in CLAIM_SPLIT_RE.split(ans) if len(c.strip()) >= 8]
    supported_count = 0
    total_claims = 0

    for cl in raw_claims[:12]:
        if is_abstention(cl):
            continue
        cl_toks_list = tokenize_eval(cl)
        # Skip pure formatting / Markdown table divider lines without substantive tokens
        if len(cl_toks_list) < 2:
            continue
        total_claims += 1
        cl_nums = extract_numbers_normalized(cl)
        ungrounded_nums = [
            n for n in cl_nums
            if n not in allowed_nums and not re.fullmatch(r"[1-9]", n)
        ]
        if ungrounded_nums and has_context:
            hallucination_types.append("extrinsic_numeric_hallucination")
            claims_audit.append(
                {
                    "claim": cl[:180],
                    "status": "unsupported",
                    "type": "extrinsic_numeric",
                    "reason": f"包含上下文与标准证据中均未出现的数值: {', '.join(ungrounded_nums)}",
                }
            )
            continue

        if has_context:
            cl_toks = set(cl_toks_list)
            ctx_toks = set(tokenize_eval(ctx_text))
            overlap_ratio = len(cl_toks & ctx_toks) / max(1, len(cl_toks))
            if overlap_ratio >= 0.35:
                supported_count += 1
                claims_audit.append({"claim": cl[:180], "status": "supported", "overlap": round(overlap_ratio, 3)})
            else:
                hallucination_types.append("extrinsic_unsupported_claim")
                claims_audit.append(
                    {
                        "claim": cl[:180],
                        "status": "unsupported",
                        "type": "extrinsic_claim",
                        "overlap": round(overlap_ratio, 3),
                        "reason": f"声明与召回上下文词元支撑度仅 {overlap_ratio*100:.1f}%（低于 35% 阈值），疑似外部幻觉。",
                    }
                )
        else:
            ov = token_f1(cl, case.expected_answer)
            if ov >= 0.25 and not trap_violations:
                supported_count += 1
                claims_audit.append({"claim": cl[:180], "status": "ref_consistent", "overlap": ov})
            else:
                claims_audit.append(
                    {
                        "claim": cl[:180],
                        "status": "unverified_no_context",
                        "overlap": ov,
                        "reason": "未观测到召回上下文，且与参考答案偏离较大。",
                    }
                )

    if total_claims == 0:
        det_faith = 0.0 if trap_violations else 1.0
    else:
        det_faith = round(supported_count / total_claims, 4)
        if trap_violations:
            det_faith = min(det_faith, 0.35)

    final_faith = det_faith
    mode = "deterministic"
    if judge_res is not None:
        final_faith = round(0.5 * det_faith + 0.5 * judge_res["faithfulness_score"], 4)
        mode = "hybrid"
        for uc in judge_res.get("unsupported_claims", []):
            claims_audit.append({"claim": uc, "status": "unsupported", "type": "llm_judge_flag", "reason": judge_res.get("reasoning", "")})

    hall_rate = round(max(0.0, min(1.0, 1.0 - final_faith)), 4)
    hallucinated = bool(trap_violations) or hall_rate >= 0.25 or bool(judge_res and judge_res.get("hallucinated"))

    return {
        "applicable": True,
        "faithfulness": final_faith,
        "hallucination_rate": hall_rate,
        "hallucinated": hallucinated,
        "trap_violated": bool(trap_violations),
        "trap_violations": trap_violations,
        "hallucination_types": sorted(set(hallucination_types)),
        "claims_audit": claims_audit,
        "context_observable": has_context,
        "mode": mode,
    }


def eval_abstention_case(case: TestCase, resp: RAGResponse) -> Dict[str, Any]:
    if resp.error:
        return {"applicable": False, "reason": "adapter_error"}
    abstained = is_abstention(resp.answer)
    if case.should_abstain:
        return {
            "applicable": True,
            "bucket": "should_abstain",
            "abstained": abstained,
            "correct_abstention": abstained,
            "oos_fabrication": not abstained,
        }
    return {
        "applicable": True,
        "bucket": "answerable",
        "abstained": abstained,
        "false_refusal": abstained,
    }


def eval_citation_case(case: TestCase, resp: RAGResponse) -> Dict[str, Any]:
    if resp.error or case.should_abstain:
        return {"applicable": False, "reason": "abstain_or_error"}

    cited = list(resp.citations)
    if not cited:
        for m in INLINE_CITATION_RE.findall(resp.answer or ""):
            if "." in m or "/" in m or m.isdigit():
                cited.append(m)

    if not cited:
        return {"applicable": True, "has_citation": False, "citation_accuracy": 0.0, "cited": []}

    active_chunks = resp.reranked_chunks if resp.reranked_chunks else resp.retrieved_chunks
    retrieved_ids = [normalize_doc_id(c.doc_id) for c in active_chunks]
    gold_ids = [normalize_doc_id(g) for g in case.expected_doc_ids]

    valid = 0
    for c in cited:
        cn = normalize_doc_id(c)
        c_base = cn.split("/")[-1]
        in_gold = any(cn == g or g.endswith("/" + cn) or c_base == g.split("/")[-1] for g in gold_ids)
        in_ret = any(cn == r or r.endswith("/" + cn) or c_base == r.split("/")[-1] for r in retrieved_ids)
        if in_gold or (not gold_ids and in_ret) or (c.isdigit() and 1 <= int(c) <= len(active_chunks)):
            valid += 1

    return {
        "applicable": True,
        "has_citation": True,
        "citation_accuracy": round(valid / len(cited), 4),
        "cited": cited,
    }


def eval_latency_case(case: TestCase, resp: RAGResponse) -> Dict[str, Any]:
    active_chunks = resp.reranked_chunks if resp.reranked_chunks else resp.retrieved_chunks
    ctx_chars = sum(len(c.text) for c in active_chunks)
    return {
        "applicable": True,
        "latency_ms": round(resp.latency_ms, 2),
        "has_error": bool(resp.error),
        "context_chars": ctx_chars,
        "chunk_count": len(active_chunks),
    }


CASE_EVALUATORS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "retrieval_hit_rate": lambda c, r, j=None: eval_retrieval_case(c, r),
    "context_quality": lambda c, r, j=None: eval_context_quality_case(c, r),
    "answer_accuracy": lambda c, r, j=None: eval_answer_accuracy_case(c, r, j),
    "hallucination_audit": lambda c, r, j=None: eval_hallucination_case(c, r, j),
    "abstention_guard": lambda c, r, j=None: eval_abstention_case(c, r),
    "citation_check": lambda c, r, j=None: eval_citation_case(c, r),
    "latency_efficiency": lambda c, r, j=None: eval_latency_case(c, r),
}


# --- Metric Aggregation with Statistical Rigor (Wilson 95% CI & Caveats) ---

def _status_for_metric(
    val: float,
    target: Optional[float],
    direction: str,
    sample_size: int = 999,
    ci_95: Optional[List[float]] = None,
    min_stat_n: int = 10,
    hard_fail_margin: float = 0.20,
    unit: str = "ratio",
) -> Tuple[str, bool]:
    """Return (status, severe_breach_kept_fail).

    Small-sample rigor: downgrades borderline `fail` -> `low_sample_warn` when sample
    size is small or Wilson 95% CI spans target, UNLESS the point estimate breaches
    the target by >= `hard_fail_margin` (default 20% absolute, or >= 2x for ms latency),
    preventing severe defects like 50% abstention failure from being masked.
    """
    if target is None:
        return "info", False

    if direction == "higher_better":
        if val >= target:
            raw = "pass"
        elif val >= target - 0.12:
            raw = "warn"
        else:
            raw = "fail"
        ci_covers_target = bool(ci_95 and ci_95[1] >= target)
        severe_breach = (target - val) >= hard_fail_margin
    else:
        if val <= target:
            raw = "pass"
        elif val <= target * 1.5 + 0.02:
            raw = "warn"
        else:
            raw = "fail"
        ci_covers_target = bool(ci_95 and ci_95[0] <= target)
        severe_breach = (val >= target * 2.0) if unit == "ms" else ((val - target) >= hard_fail_margin)

    is_small_or_uncertain = sample_size < min_stat_n or (sample_size < 15 and ci_covers_target)
    if raw == "fail" and is_small_or_uncertain:
        if severe_breach:
            return "fail", True
        return "low_sample_warn", False
    return raw, False


def aggregate_metrics(
    case_evals: List[Dict[str, Any]],
    methods_executed: List[str],
    methods_skipped: Dict[str, str],
    thresholds: Dict[str, float],
    judge_used: bool = False,
) -> List[MetricSummary]:
    metrics: List[MetricSummary] = []
    mode_tag = "hybrid" if judge_used else "deterministic"
    min_stat_n = max(1, int(thresholds.get("min_stat_sample_size", 10)))
    hard_fail_margin = float(thresholds.get("low_sample_hard_fail_margin", 0.20))

    def mean_of(rows: List[Dict[str, Any]], key: str) -> Tuple[Optional[float], int]:
        vals = [float(r[key]) for r in rows if r.get(key) is not None]
        if not vals:
            return None, 0
        return round(sum(vals) / len(vals), 4), len(vals)

    def _sample_caveats(n: int, status: str, severe_kept_fail: bool, base_caveats: List[str]) -> List[str]:
        caveats = list(base_caveats)
        if 0 < n < 15:
            if severe_kept_fail:
                caveats.append(
                    f"严重偏离目标阈值（差距 ≥ {hard_fail_margin*100:.0f}%），虽样本量偏小 (N={n}) 仍保留 fail 硬阻断警示，同时建议扩充样本复验。"
                )
            elif status == "low_sample_warn":
                caveats.append(
                    f"样本量偏小 (N={n} < {min_stat_n} 或 95% CI 仍覆盖目标线)，已降级为低样本预警（low_sample_warn）而非统计确凿失败，建议扩充样本复验。"
                )
            else:
                caveats.append(f"样本量偏小 (N={n})，95% 置信区间较宽，建议扩大样本后再做重大架构决策。")
        return caveats

    def add_prop_metric(
        mid: str,
        name: str,
        zh_name: str,
        method_id: str,
        successes: float,
        n: int,
        target: Optional[float],
        direction: str = "higher_better",
        mode: str = "deterministic",
        extra_caveats: Optional[List[str]] = None,
    ) -> None:
        base_cav = list(extra_caveats or [])
        if n == 0:
            metrics.append(
                MetricSummary(
                    metric_id=mid,
                    name=name,
                    zh_name=zh_name,
                    method_id=method_id,
                    value=None,
                    sample_size=0,
                    target=target,
                    status="insufficient_data",
                    direction=direction,
                    unit="ratio",
                    mode=mode,
                    caveats=base_cav + ["无适用样本"],
                )
            )
            return
        val = round(successes / n, 4)
        ci = wilson_ci(successes, n)
        status, severe_kept = _status_for_metric(
            val, target, direction, sample_size=n, ci_95=ci, min_stat_n=min_stat_n, hard_fail_margin=hard_fail_margin, unit="ratio"
        )
        metrics.append(
            MetricSummary(
                metric_id=mid,
                name=name,
                zh_name=zh_name,
                method_id=method_id,
                value=val,
                ci_95=ci,
                sample_size=n,
                target=target,
                status=status,
                direction=direction,
                unit="ratio",
                mode=mode,
                caveats=_sample_caveats(n, status, severe_kept, base_cav),
            )
        )

    def add_mean_metric(
        mid: str,
        name: str,
        zh_name: str,
        method_id: str,
        val: Optional[float],
        n: int,
        target: Optional[float],
        direction: str = "higher_better",
        unit: str = "ratio",
        mode: str = "deterministic",
        extra_caveats: Optional[List[str]] = None,
    ) -> None:
        base_cav = list(extra_caveats or [])
        if val is None or n == 0:
            metrics.append(
                MetricSummary(
                    metric_id=mid,
                    name=name,
                    zh_name=zh_name,
                    method_id=method_id,
                    value=None,
                    sample_size=0,
                    target=target,
                    status="insufficient_data",
                    direction=direction,
                    unit=unit,
                    mode=mode,
                    caveats=base_cav + ["无适用样本"],
                )
            )
            return
        status, severe_kept = _status_for_metric(
            val, target, direction, sample_size=n, min_stat_n=min_stat_n, hard_fail_margin=hard_fail_margin, unit=unit
        )
        metrics.append(
            MetricSummary(
                metric_id=mid,
                name=name,
                zh_name=zh_name,
                method_id=method_id,
                value=val,
                sample_size=n,
                target=target,
                status=status,
                direction=direction,
                unit=unit,
                mode=mode,
                caveats=_sample_caveats(n, status, severe_kept, base_cav),
            )
        )

    if "retrieval_hit_rate" in methods_executed:
        rows = [
            ce["method_scores"]["retrieval_hit_rate"]
            for ce in case_evals
            if ce["method_scores"].get("retrieval_hit_rate", {}).get("applicable")
        ]
        n = len(rows)
        add_prop_metric(
            "hit_at_k", "Hit@K Rate", "检索命中率 (Hit@K)", "retrieval_hit_rate",
            sum(r["hit_at_k"] for r in rows), n, thresholds.get("hit_at_k_min", 0.80)
        )
        add_prop_metric(
            "hit_at_1", "Hit@1 Rate", "首位命中率 (Hit@1)", "retrieval_hit_rate",
            sum(r["hit_at_1"] for r in rows), n, None
        )
        add_prop_metric(
            "hit_at_3", "Hit@3 Rate", "前三命中率 (Hit@3)", "retrieval_hit_rate",
            sum(r["hit_at_3"] for r in rows), n, None
        )
        rec_val, rec_n = mean_of(rows, "recall_at_k")
        add_mean_metric(
            "recall_at_k", "Recall@K", "证据召回率 (Recall@K)", "retrieval_hit_rate",
            rec_val, rec_n, thresholds.get("recall_at_k_min", 0.75)
        )
        mrr_val, mrr_n = mean_of(rows, "mrr")
        add_mean_metric(
            "mrr", "MRR", "平均倒数排名 (MRR)", "retrieval_hit_rate",
            mrr_val, mrr_n, thresholds.get("mrr_min", 0.65), unit="score"
        )
        ndcg_val, ndcg_n = mean_of(rows, "ndcg_at_k")
        add_mean_metric(
            "ndcg_at_k", "NDCG@K", "排序增益 (NDCG@K)", "retrieval_hit_rate",
            ndcg_val, ndcg_n, thresholds.get("ndcg_at_k_min", 0.70), unit="score"
        )
    elif "retrieval_hit_rate" in methods_skipped:
        metrics.append(
            MetricSummary(
                metric_id="hit_at_k",
                name="Hit@K Rate",
                zh_name="检索命中率 (Hit@K)",
                method_id="retrieval_hit_rate",
                value=None,
                sample_size=0,
                target=thresholds.get("hit_at_k_min", 0.80),
                status="unobservable",
                caveats=[f"严谨性保护：{methods_skipped['retrieval_hit_rate']}"],
            )
        )

    if "context_quality" in methods_executed:
        rows = [
            ce["method_scores"]["context_quality"]
            for ce in case_evals
            if ce["method_scores"].get("context_quality", {}).get("applicable")
        ]
        cr, cr_n = mean_of(rows, "context_recall")
        cp, cp_n = mean_of(rows, "context_precision")
        add_mean_metric(
            "context_recall", "Context Recall", "上下文证据召回率", "context_quality",
            cr, cr_n, thresholds.get("context_recall_min", 0.75)
        )
        add_mean_metric(
            "context_precision", "Context Precision", "上下文精确率 (抗噪度)", "context_quality",
            cp, cp_n, thresholds.get("context_precision_min", 0.60)
        )

    if "answer_accuracy" in methods_executed:
        rows = [
            ce["method_scores"]["answer_accuracy"]
            for ce in case_evals
            if ce["method_scores"].get("answer_accuracy", {}).get("applicable")
        ]
        acc_val, acc_n = mean_of(rows, "score")
        add_prop_metric(
            "answer_pass_rate", "Answer Pass Rate", "答案达标率", "answer_accuracy",
            sum(1.0 for r in rows if r.get("passed")), acc_n, thresholds.get("answer_accuracy_min", 0.78), mode=mode_tag
        )
        add_mean_metric(
            "answer_accuracy", "Answer Accuracy Score", "综合答案准确率", "answer_accuracy",
            acc_val, acc_n, thresholds.get("answer_accuracy_min", 0.78), mode=mode_tag
        )
        num_rows = [r for r in rows if r.get("numeric_accuracy") is not None]
        if num_rows:
            na_val, na_n = mean_of(num_rows, "numeric_accuracy")
            add_mean_metric(
                "numeric_accuracy", "Numeric Accuracy", "关键数值准确率", "answer_accuracy",
                na_val, na_n, thresholds.get("numeric_accuracy_min", 0.85)
            )

    if "hallucination_audit" in methods_executed:
        rows = [
            ce["method_scores"]["hallucination_audit"]
            for ce in case_evals
            if ce["method_scores"].get("hallucination_audit", {}).get("applicable")
        ]
        n = len(rows)
        no_ctx_cnt = sum(1 for r in rows if r.get("context_observable") is False)
        extra_cav = ["部分用例缺少召回上下文，幻觉审计已退化为基于参考答案的一致性检查。"] if no_ctx_cnt > 0 else []
        add_prop_metric(
            "hallucination_rate", "Hallucination Rate", "幻觉触发率 (Case)", "hallucination_audit",
            sum(1.0 for r in rows if r.get("hallucinated")), n,
            thresholds.get("hallucination_rate_max", 0.10), direction="lower_better", mode=mode_tag, extra_caveats=extra_cav
        )
        faith_val, faith_n = mean_of(rows, "faithfulness")
        add_mean_metric(
            "faithfulness", "Claim Faithfulness", "声明忠实度 (Faithfulness)", "hallucination_audit",
            faith_val, faith_n, thresholds.get("faithfulness_min", 0.88), mode=mode_tag, extra_caveats=extra_cav
        )

    if "abstention_guard" in methods_executed:
        rows = [
            ce["method_scores"]["abstention_guard"]
            for ce in case_evals
            if ce["method_scores"].get("abstention_guard", {}).get("applicable")
        ]
        oos_rows = [r for r in rows if r.get("bucket") == "should_abstain"]
        ans_rows = [r for r in rows if r.get("bucket") == "answerable"]
        if oos_rows:
            add_prop_metric(
                "abstention_accuracy", "Abstention Accuracy", "知识盲区正确拒答率", "abstention_guard",
                sum(1.0 for r in oos_rows if r.get("correct_abstention")), len(oos_rows),
                thresholds.get("abstention_accuracy_min", 0.85), direction="higher_better"
            )
        if ans_rows:
            add_prop_metric(
                "false_refusal_rate", "False Refusal Rate", "可答问题误拒率", "abstention_guard",
                sum(1.0 for r in ans_rows if r.get("false_refusal")), len(ans_rows),
                thresholds.get("false_refusal_rate_max", 0.08), direction="lower_better"
            )

    if "citation_check" in methods_executed:
        rows = [
            ce["method_scores"]["citation_check"]
            for ce in case_evals
            if ce["method_scores"].get("citation_check", {}).get("applicable")
        ]
        if rows:
            add_prop_metric(
                "citation_presence_rate", "Citation Presence", "引用覆盖率", "citation_check",
                sum(1.0 for r in rows if r.get("has_citation")), len(rows),
                thresholds.get("citation_presence_min", 0.80)
            )
            ca_val, ca_n = mean_of(rows, "citation_accuracy")
            add_mean_metric(
                "citation_accuracy", "Citation Accuracy", "引用溯源准确率", "citation_check",
                ca_val, ca_n, thresholds.get("citation_accuracy_min", 0.75)
            )

    if "latency_efficiency" in methods_executed:
        rows = [
            ce["method_scores"]["latency_efficiency"]
            for ce in case_evals
            if ce["method_scores"].get("latency_efficiency", {}).get("applicable")
        ]
        latencies = [float(r["latency_ms"]) for r in rows]
        p50 = percentile(latencies, 50)
        p95 = percentile(latencies, 95)
        err_cnt = sum(1 for r in rows if r.get("has_error"))
        add_mean_metric(
            "latency_p50_ms", "Latency P50", "响应延迟 P50", "latency_efficiency",
            p50, len(latencies), None, direction="lower_better", unit="ms"
        )
        add_mean_metric(
            "latency_p95_ms", "Latency P95", "尾延迟 P95", "latency_efficiency",
            p95, len(latencies), thresholds.get("latency_p95_max_ms", 5000.0), direction="lower_better", unit="ms"
        )
        add_prop_metric(
            "error_rate", "Adapter Error Rate", "接口异常错误率", "latency_efficiency",
            float(err_cnt), len(rows), thresholds.get("error_rate_max", 0.02), direction="lower_better"
        )

    return metrics
