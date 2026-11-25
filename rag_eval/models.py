"""Typed data models and canonical taxonomies for RAG Eval Suite."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# Complete 10-category RAG test taxonomy for comprehensive coverage and categorized display
CATEGORY_SPECS: Dict[str, Dict[str, Any]] = {
    "single_hop_fact": {
        "zh_name": "单跳精确事实",
        "en_name": "Single-Hop Factual Lookup",
        "description": "直接命中单个文档片段中的明确定义、规则、配置或事实陈述，检验基础召回与忠实转述能力。",
        "primary_metrics": ["hit_at_k", "mrr", "answer_accuracy", "hallucination_rate"],
        "core_eligible": True,
        "recommended_ratio": 0.18,
    },
    "multi_hop_reasoning": {
        "zh_name": "多跳跨文档推理",
        "en_name": "Multi-Hop Cross-Document Reasoning",
        "description": "需要同时召回并串联 2 个及以上片段/文档的信息才能得出完整结论，检验多证据召回与综合推理能力。",
        "primary_metrics": ["recall_at_k", "context_recall", "answer_accuracy", "hallucination_rate"],
        "core_eligible": True,
        "recommended_ratio": 0.12,
    },
    "numerical_table": {
        "zh_name": "数值与表格抽取计算",
        "en_name": "Numerical & Tabular Extraction",
        "description": "针对金额、比例、阈值、SLA、配额及表格字段进行精确提取与比对，检验切片结构完整度与数值保真度。",
        "primary_metrics": ["numeric_accuracy", "hit_at_k", "hallucination_rate"],
        "core_eligible": True,
        "recommended_ratio": 0.12,
    },
    "temporal_version": {
        "zh_name": "时效与版本演进",
        "en_name": "Temporal & Version Disambiguation",
        "description": "验证系统对生效日期、版本号、历史变更及当前有效策略的区分能力，防止旧文档覆盖新规范。",
        "primary_metrics": ["hit_at_k", "answer_accuracy", "hallucination_rate"],
        "core_eligible": True,
        "recommended_ratio": 0.10,
    },
    "unanswerable_oos": {
        "zh_name": "知识盲区与拒答边界",
        "en_name": "Unanswerable / Out-of-Scope Abstention",
        "description": "提问知识库明确未覆盖或不存在的细节，检验系统是否诚实拒答，是暴露编造幻觉的关键防线。",
        "primary_metrics": ["abstention_accuracy", "hallucination_rate"],
        "core_eligible": True,
        "recommended_ratio": 0.14,
    },
    "terminology_jargon": {
        "zh_name": "专有名词与同义改写",
        "en_name": "Terminology, Acronyms & Paraphrase",
        "description": "使用缩写、错误码、内部代号或口语化同义表述提问，检验混合检索（BM25+向量）与查询理解能力。",
        "primary_metrics": ["hit_at_k", "mrr", "answer_accuracy"],
        "core_eligible": True,
        "recommended_ratio": 0.10,
    },
    "adversarial_distractor": {
        "zh_name": "对抗诱导与近义干扰",
        "en_name": "Adversarial & Distractor Trap",
        "description": "带有错误前提或高相似干扰项的诱导性提问，检验重排抗噪能力及生成层抵御诱导幻觉的能力。",
        "primary_metrics": ["hallucination_rate", "context_precision", "answer_accuracy"],
        "core_eligible": True,
        "recommended_ratio": 0.08,
    },
    "conflicting_ambiguous": {
        "zh_name": "歧义与多源冲突消歧",
        "en_name": "Ambiguous & Conflicting Sources",
        "description": "多份文档存在条件差异、适用范围不同或表述冲突时的澄清与分条件作答能力。",
        "primary_metrics": ["answer_accuracy", "hallucination_rate", "citation_accuracy"],
        "core_eligible": False,
        "recommended_ratio": 0.06,
    },
    "long_context_synthesis": {
        "zh_name": "长文归纳与多点汇总",
        "en_name": "Long-Context Synthesis & Comparison",
        "description": "要求完整列出多条规则、步骤或对比多个方案，检验上下文窗口利用与遗漏率（Lost-in-the-Middle）。",
        "primary_metrics": ["key_fact_coverage", "recall_at_k", "citation_accuracy"],
        "core_eligible": False,
        "recommended_ratio": 0.06,
    },
    "robustness_boundary": {
        "zh_name": "边界鲁棒性测试",
        "en_name": "Robustness & Edge Boundary",
        "description": "测试中英混排、标点噪声、超短关键词及格式约束下的系统稳定性与尾延迟。",
        "primary_metrics": ["error_rate", "latency_p95_ms", "hit_at_k"],
        "core_eligible": False,
        "recommended_ratio": 0.04,
    },
}

DEFAULT_THRESHOLDS: Dict[str, float] = {
    "hit_at_k_min": 0.80,
    "recall_at_k_min": 0.75,
    "mrr_min": 0.65,
    "ndcg_at_k_min": 0.70,
    "context_recall_min": 0.75,
    "context_precision_min": 0.60,
    "answer_accuracy_min": 0.78,
    "numeric_accuracy_min": 0.85,
    "hallucination_rate_max": 0.10,
    "faithfulness_min": 0.88,
    "abstention_accuracy_min": 0.85,
    "false_refusal_rate_max": 0.08,
    "citation_presence_min": 0.80,
    "citation_accuracy_min": 0.75,
    "latency_p95_max_ms": 5000.0,
    "error_rate_max": 0.02,
    "min_stat_sample_size": 10.0,
    "low_sample_hard_fail_margin": 0.20,
}


@dataclass
class ObservabilityProfile:
    retrieved_docs: bool = True
    retrieval_scores: bool = False
    reranked_docs: bool = False
    citations: bool = False
    latency_ms: bool = True
    prompt_context: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ObservabilityProfile":
        valid = {k: bool(data.get(k, getattr(cls, k))) for k in cls.__dataclass_fields__}
        return cls(**valid)


@dataclass
class UnknownItem:
    id: str
    area: str
    question: str
    impact: str
    blocking: bool = True
    resolved: bool = False
    resolution: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UnknownItem":
        return cls(
            id=str(data.get("id", "")),
            area=str(data.get("area", "general")),
            question=str(data.get("question", "")),
            impact=str(data.get("impact", "")),
            blocking=bool(data.get("blocking", True)),
            resolved=bool(data.get("resolved", False)),
            resolution=str(data.get("resolution", "")),
        )


@dataclass
class RAGProfile:
    system_name: str
    project_path: str
    kb_paths: List[str] = field(default_factory=list)
    architecture_summary: str = ""
    detected_stack: Dict[str, List[str]] = field(default_factory=dict)
    pipeline_stages: Dict[str, Any] = field(default_factory=dict)
    observability: ObservabilityProfile = field(default_factory=ObservabilityProfile)
    corpus_stats: Dict[str, Any] = field(default_factory=dict)
    adapter_config: Dict[str, Any] = field(default_factory=dict)
    thresholds: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_THRESHOLDS))
    unknowns: List[UnknownItem] = field(default_factory=list)
    readiness_score: int = 0
    confirmed: bool = False
    confirmed_by: str = ""
    confirmed_at: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    notes: str = ""

    def blocking_unknowns(self) -> List[UnknownItem]:
        return [u for u in self.unknowns if u.blocking and not u.resolved]

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["observability"] = self.observability.to_dict()
        data["unknowns"] = [u.to_dict() for u in self.unknowns]
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RAGProfile":
        thresholds = dict(DEFAULT_THRESHOLDS)
        thresholds.update(data.get("thresholds") or {})
        return cls(
            system_name=str(data.get("system_name", "Target RAG System")),
            project_path=str(data.get("project_path", ".")),
            kb_paths=list(data.get("kb_paths") or []),
            architecture_summary=str(data.get("architecture_summary", "")),
            detected_stack=dict(data.get("detected_stack") or {}),
            pipeline_stages=dict(data.get("pipeline_stages") or {}),
            observability=ObservabilityProfile.from_dict(data.get("observability") or {}),
            corpus_stats=dict(data.get("corpus_stats") or {}),
            adapter_config=dict(data.get("adapter_config") or {}),
            thresholds=thresholds,
            unknowns=[UnknownItem.from_dict(u) for u in (data.get("unknowns") or [])],
            readiness_score=int(data.get("readiness_score", 0)),
            confirmed=bool(data.get("confirmed", False)),
            confirmed_by=str(data.get("confirmed_by", "")),
            confirmed_at=str(data.get("confirmed_at", "")),
            created_at=str(data.get("created_at", utc_now_iso())),
            notes=str(data.get("notes", "")),
        )


@dataclass
class ScopePlan:
    profile_mode: str  # smoke | core | full
    recommended_mode: str
    complexity_level: str  # low | medium | high | extreme
    complexity_score: int
    complexity_reasons: List[str] = field(default_factory=list)
    selected_categories: List[str] = field(default_factory=list)
    deferred_categories: List[str] = field(default_factory=list)
    target_case_count: int = 36
    selected_methods: List[str] = field(default_factory=list)
    deferred_methods: List[str] = field(default_factory=list)
    selected_kb_files: List[str] = field(default_factory=list)
    total_kb_files: int = 0
    deferred_kb_files_count: int = 0
    estimated_duration_sec: float = 0.0
    max_workers: int = 2
    disclosure_statement: str = ""
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScopePlan":
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})


@dataclass
class TestCase:
    id: str
    category: str
    difficulty: str  # easy | medium | hard
    core_priority: int  # 1 = core, 2 = standard, 3 = extended
    query: str
    expected_answer: str
    expected_doc_ids: List[str] = field(default_factory=list)
    expected_evidence_snippets: List[str] = field(default_factory=list)
    must_include_facts: List[str] = field(default_factory=list)
    must_not_claim: List[str] = field(default_factory=list)
    expected_numbers: List[str] = field(default_factory=list)
    should_abstain: bool = False
    source_files: List[str] = field(default_factory=list)
    rationale: str = ""
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TestCase":
        return cls(
            id=str(data["id"]),
            category=str(data.get("category", "single_hop_fact")),
            difficulty=str(data.get("difficulty", "medium")),
            core_priority=int(data.get("core_priority", 2)),
            query=str(data.get("query", "")),
            expected_answer=str(data.get("expected_answer", "")),
            expected_doc_ids=list(data.get("expected_doc_ids") or []),
            expected_evidence_snippets=list(data.get("expected_evidence_snippets") or []),
            must_include_facts=list(data.get("must_include_facts") or []),
            must_not_claim=list(data.get("must_not_claim") or []),
            expected_numbers=list(data.get("expected_numbers") or []),
            should_abstain=bool(data.get("should_abstain", False)),
            source_files=list(data.get("source_files") or []),
            rationale=str(data.get("rationale", "")),
            tags=list(data.get("tags") or []),
        )


@dataclass
class RetrievedChunk:
    doc_id: str
    text: str
    score: Optional[float] = None
    rank: int = 1
    stage: str = "retrieved"  # retrieved | reranked
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RetrievedChunk":
        return cls(
            doc_id=str(data.get("doc_id", data.get("id", data.get("source", "")))),
            text=str(data.get("text", data.get("content", data.get("page_content", "")))),
            score=float(data["score"]) if data.get("score") is not None else None,
            rank=int(data.get("rank", 1)),
            stage=str(data.get("stage", "retrieved")),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class RAGResponse:
    case_id: str
    answer: str
    retrieved_chunks: List[RetrievedChunk] = field(default_factory=list)
    reranked_chunks: List[RetrievedChunk] = field(default_factory=list)
    citations: List[str] = field(default_factory=list)
    latency_ms: float = 0.0
    token_usage: Dict[str, int] = field(default_factory=dict)
    error: Optional[str] = None
    raw_metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "answer": self.answer,
            "retrieved_chunks": [c.to_dict() for c in self.retrieved_chunks],
            "reranked_chunks": [c.to_dict() for c in self.reranked_chunks],
            "citations": list(self.citations),
            "latency_ms": self.latency_ms,
            "token_usage": dict(self.token_usage),
            "error": self.error,
            "raw_metadata": dict(self.raw_metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RAGResponse":
        return cls(
            case_id=str(data.get("case_id", "")),
            answer=str(data.get("answer", "")),
            retrieved_chunks=[RetrievedChunk.from_dict(c) for c in (data.get("retrieved_chunks") or [])],
            reranked_chunks=[RetrievedChunk.from_dict(c) for c in (data.get("reranked_chunks") or [])],
            citations=[str(x) for x in (data.get("citations") or [])],
            latency_ms=float(data.get("latency_ms", 0.0)),
            token_usage=dict(data.get("token_usage") or {}),
            error=str(data["error"]) if data.get("error") else None,
            raw_metadata=dict(data.get("raw_metadata") or {}),
        )


@dataclass
class MetricSummary:
    metric_id: str
    name: str
    zh_name: str
    method_id: str
    value: Optional[float]
    ci_95: Optional[List[float]] = None
    sample_size: int = 0
    target: Optional[float] = None
    status: str = "pass"  # pass | warn | low_sample_warn | fail | info | unobservable | insufficient_data
    direction: str = "higher_better"  # higher_better | lower_better
    unit: str = "ratio"  # ratio | ms | score | count
    mode: str = "deterministic"  # deterministic | llm_judge | hybrid
    caveats: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DiagnosisIssue:
    issue_id: str
    code: str
    severity: str  # P0 | P1 | P2 | P3
    pipeline_stage: str
    title: str
    summary: str
    affected_case_ids: List[str] = field(default_factory=list)
    affected_count: int = 0
    impact_ratio: float = 0.0
    metric_evidence: Dict[str, Any] = field(default_factory=dict)
    confidence: str = "High"  # High | Medium | Low
    quick_wins: List[str] = field(default_factory=list)
    structural_fixes: List[str] = field(default_factory=list)
    verification_plan: str = ""
    sample_examples: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
