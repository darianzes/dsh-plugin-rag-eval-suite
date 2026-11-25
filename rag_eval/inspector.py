"""Phase 1: RAG System Inspector and Understand-First Gate.

Strictly prevents blind test generation or backtesting before the target RAG
architecture, corpus properties, adapter schema, and observability boundaries
are inspected and confirmed.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    DEFAULT_THRESHOLDS,
    ObservabilityProfile,
    RAGProfile,
    UnknownItem,
    utc_now_iso,
)
from .resource_guard import ResourceTracker, read_json, write_json_atomic

CODE_EXTS = {".py", ".ts", ".tsx", ".js", ".mjs", ".go", ".java", ".rs", ".yaml", ".yml", ".toml", ".json"}
TEXT_KB_EXTS = {".md", ".markdown", ".txt", ".rst", ".csv", ".tsv", ".json", ".jsonl", ".html", ".htm", ".yaml", ".yml"}
BINARY_KB_EXTS = {".pdf", ".docx", ".doc", ".pptx", ".xlsx", ".xls"}
REPO_META_FILENAMES = {
    "readme.md",
    "changelog.md",
    "license",
    "license.md",
    "license.txt",
    "agents.md",
    "gemini.md",
    "claude.md",
    "skill.md",
    "package.json",
    "package-lock.json",
    "tsconfig.json",
    "plugin.json",
    "mcp.json",
    "mcp_config.json",
    "hooks.json",
    "pyproject.toml",
    "cordis.patch.yml",
    "requirements.txt",
}

STACK_SIGNATURES: Dict[str, Dict[str, List[str]]] = {
    "frameworks": {
        "LangChain": [r"langchain", r"RetrievalQA", r"RecursiveCharacterTextSplitter"],
        "LlamaIndex": [r"llama_index", r"VectorStoreIndex", r"SentenceSplitter"],
        "Haystack": [r"haystack", r"DocumentStore"],
        "Dify": [r"dify", r"knowledge_retrieval"],
        "RAGFlow": [r"ragflow", r"deepdoc"],
        "FastAPI": [r"from fastapi", r"FastAPI\("],
        "Flask": [r"from flask", r"Flask\("],
        "Express": [r"express\(\)", r"from ['\"]express['\"]"],
    },
    "retrievers_and_stores": {
        "BM25 / Lexical": [r"BM25", r"rank_bm25", r"elasticsearch", r"opensearch", r"tantivy"],
        "FAISS": [r"\bfaiss\b"],
        "Milvus": [r"pymilvus", r"milvus"],
        "Qdrant": [r"qdrant"],
        "Chroma": [r"chromadb", r"\bChroma\b"],
        "pgvector": [r"pgvector", r"vector\("],
        "Weaviate": [r"weaviate"],
        "Pinecone": [r"pinecone"],
        "GraphRAG / KG": [r"neo4j", r"networkx", r"graphrag", r"knowledge_graph"],
    },
    "rerankers": {
        "BGE-Reranker": [r"bge-reranker", r"FlagReranker"],
        "Cohere Rerank": [r"cohere.*rerank", r"Rerank"],
        "CrossEncoder": [r"CrossEncoder", r"cross-encoder"],
        "Jina Reranker": [r"jina-reranker"],
        "RRF Fusion": [r"reciprocal_rank", r"\brrf\b"],
    },
    "models": {
        "DeepSeek": [r"deepseek", r"api\.deepseek\.com"],
        "OpenAI": [r"openai", r"gpt-4", r"text-embedding-"],
        "Anthropic": [r"anthropic", r"claude-"],
        "Gemini": [r"google\.genai", r"gemini-"],
        "Ollama / Local": [r"ollama", r"vllm", r"sglang"],
        "BGE / M3E Embedding": [r"bge-", r"m3e-", r"bce-embedding"],
    },
}

PARAM_PATTERNS: Dict[str, List[str]] = {
    "chunk_size": [r"chunk_size\s*[:=]\s*(\d+)", r"CHUNK_SIZE\s*[:=]\s*(\d+)"],
    "chunk_overlap": [r"chunk_overlap\s*[:=]\s*(\d+)", r"CHUNK_OVERLAP\s*[:=]\s*(\d+)"],
    "top_k": [r"top_k\s*[:=]\s*(\d+)", r"similarity_top_k\s*[:=]\s*(\d+)", r"TOP_K\s*[:=]\s*(\d+)"],
    "rerank_top_n": [r"rerank_top_[kn]\s*[:=]\s*(\d+)", r"top_n\s*[:=]\s*(\d+)"],
}

ENDPOINT_PATTERN = re.compile(
    r"""(?:@app|@router|app|router)\.(?:post|get)\(\s*['"](/[^'"]*(?:rag|query|search|chat|ask|retrieve|qa)[^'"]*)['"]""",
    re.IGNORECASE,
)

DATE_PATTERN = re.compile(r"\b(?:20\d{2}[-/年.](?:0?[1-9]|1[0-2])(?:[-/月.](?:0?[1-9]|[12]\d|3[01]))?|v\d+\.\d+(?:\.\d+)?)\b")
TABLE_PATTERN = re.compile(r"^\|.+\|\s*$", re.MULTILINE)
ACRONYM_PATTERN = re.compile(r"\b[A-Z]{2,6}(?:-[A-Z0-9]{1,5})?\b")


class UnderstandingGateError(RuntimeError):
    """Raised when attempting generation or backtesting without confirmed RAG understanding."""

    def __init__(self, message: str, blocking_unknowns: Optional[List[Dict[str, Any]]] = None):
        super().__init__(message)
        self.blocking_unknowns = blocking_unknowns or []


def _detect_stack_and_params(
    project_root: Path,
    tracker: ResourceTracker,
) -> Tuple[Dict[str, List[str]], Dict[str, Any], List[str], Dict[str, bool]]:
    detected: Dict[str, set[str]] = {cat: set() for cat in STACK_SIGNATURES}
    params: Dict[str, Any] = {}
    endpoints: List[str] = []
    obs_hints = {
        "retrieved_docs": False,
        "retrieval_scores": False,
        "reranked_docs": False,
        "citations": False,
        "query_rewrite": False,
        "multi_kb": False,
    }

    for fpath in tracker.iter_files_bounded(project_root, allowed_exts=CODE_EXTS, max_depth=6):
        # Skip our own plugin internals/tests if scanning inside the plugin repo itself
        if (project_root / "plugin.json").exists() and ({"rag_eval", "tests", "dsh", "scripts"} & set(fpath.parts)):
            continue
        text = tracker.read_text_bounded(fpath)
        if not text:
            continue

        for category, tech_map in STACK_SIGNATURES.items():
            for tech_name, patterns in tech_map.items():
                if tech_name in detected[category]:
                    continue
                if any(re.search(pat, text, re.IGNORECASE) for pat in patterns):
                    detected[category].add(tech_name)

        for pname, patterns in PARAM_PATTERNS.items():
            if pname not in params:
                for pat in patterns:
                    m = re.search(pat, text)
                    if m:
                        params[pname] = int(m.group(1))
                        break

        for m in ENDPOINT_PATTERN.finditer(text):
            ep = m.group(1)
            if ep not in endpoints:
                endpoints.append(ep)

        lower = text.lower()
        if any(k in lower for k in ("retrieved_docs", "retrieved_chunks", "source_documents", "contexts", "chunks")):
            obs_hints["retrieved_docs"] = True
        if any(k in lower for k in ("similarity_score", "rerank_score", "doc_score", '"score"')):
            obs_hints["retrieval_scores"] = True
        if any(k in lower for k in ("reranked", "rerank_docs", "reranker")):
            obs_hints["reranked_docs"] = True
        if any(k in lower for k in ("citations", "references", "source_id", "[1]")):
            obs_hints["citations"] = True
        if any(k in lower for k in ("query_rewrite", "hyde", "multi_query", "sub_question", "decompose")):
            obs_hints["query_rewrite"] = True
        if any(k in lower for k in ("collection_name", "knowledge_bases", "kb_router", "multi_kb")):
            obs_hints["multi_kb"] = True

    return (
        {k: sorted(v) for k, v in detected.items() if v},
        params,
        endpoints[:12],
        obs_hints,
    )


def _scan_knowledge_base(
    kb_paths: List[Path],
    tracker: ResourceTracker,
    exclude_repo_meta: bool = False,
) -> Dict[str, Any]:
    ext_counts: Counter[str] = Counter()
    text_files: List[Dict[str, Any]] = []
    binary_files: List[str] = []
    total_bytes = 0
    docs_with_tables = 0
    docs_with_dates = 0
    acronym_counter: Counter[str] = Counter()

    for kb_root in kb_paths:
        if not kb_root.exists():
            continue
        for fpath in tracker.iter_files_bounded(kb_root, allowed_exts=TEXT_KB_EXTS | BINARY_KB_EXTS, max_depth=8):
            if exclude_repo_meta and fpath.name.lower() in REPO_META_FILENAMES:
                continue
            ext = fpath.suffix.lower()
            ext_counts[ext] += 1
            try:
                size = fpath.stat().st_size
            except OSError:
                size = 0
            total_bytes += size

            if ext in BINARY_KB_EXTS:
                binary_files.append(str(fpath))
                continue

            content = tracker.read_text_bounded(fpath)
            has_table = bool(TABLE_PATTERN.search(content)) or ext in {".csv", ".tsv"}
            has_dates = bool(DATE_PATTERN.search(content))
            if has_table:
                docs_with_tables += 1
            if has_dates:
                docs_with_dates += 1
            for acr in ACRONYM_PATTERN.findall(content[:8000]):
                if acr not in {"HTTP", "HTTPS", "JSON", "YAML", "HTML", "UTF", "README", "TODO", "NOTE"}:
                    acronym_counter[acr] += 1

            text_files.append(
                {
                    "path": str(fpath),
                    "name": fpath.name,
                    "ext": ext,
                    "size_bytes": size,
                    "has_table": has_table,
                    "has_dates": has_dates,
                    "char_count": len(content),
                }
            )

    return {
        "total_files": sum(ext_counts.values()),
        "readable_text_files": len(text_files),
        "binary_files_count": len(binary_files),
        "binary_files_sample": binary_files[:10],
        "total_size_mb": round(total_bytes / (1024.0 * 1024.0), 3),
        "extensions": dict(ext_counts),
        "docs_with_tables": docs_with_tables,
        "docs_with_temporal_markers": docs_with_dates,
        "top_acronyms": [k for k, _ in acronym_counter.most_common(15)],
        "file_inventory": text_files,
    }


def inspect_rag_system(
    project_path: str,
    kb_paths: Optional[List[str]] = None,
    system_name: Optional[str] = None,
    adapter_config: Optional[Dict[str, Any]] = None,
    observability_overrides: Optional[Dict[str, bool]] = None,
    tracker: Optional[ResourceTracker] = None,
) -> Tuple[RAGProfile, Dict[str, Any]]:
    """Inspect a RAG codebase and knowledge base to build an auditable RAGProfile."""
    tracker = tracker or ResourceTracker()
    project_root = Path(project_path).expanduser().resolve()

    resolved_kb: List[Path] = []
    kb_auto_from_root = False
    if kb_paths:
        for p in kb_paths:
            resolved_kb.append(Path(p).expanduser().resolve())
    else:
        # Auto-discover common KB directory names inside project
        for candidate in ("kb", "knowledge_base", "docs", "corpus", "data", "documents", "examples/sample_kb"):
            cpath = project_root / candidate
            if cpath.exists() and cpath.is_dir():
                resolved_kb.append(cpath)
                break
        # Fallback: inspect project_root itself when user points --project directly at a KB folder
        if not resolved_kb and project_root.exists():
            has_direct_kb = any(
                child.is_file()
                and child.suffix.lower() in (TEXT_KB_EXTS | BINARY_KB_EXTS)
                and child.name.lower() not in REPO_META_FILENAMES
                for child in (project_root.iterdir() if project_root.is_dir() else [project_root])
            )
            if has_direct_kb:
                resolved_kb.append(project_root)
                kb_auto_from_root = True

    detected_stack, params, endpoints, obs_hints = _detect_stack_and_params(project_root, tracker)
    corpus_stats = _scan_knowledge_base(resolved_kb, tracker, exclude_repo_meta=kb_auto_from_root)

    obs = ObservabilityProfile(
        retrieved_docs=obs_hints["retrieved_docs"],
        retrieval_scores=obs_hints["retrieval_scores"],
        reranked_docs=obs_hints["reranked_docs"],
        citations=obs_hints["citations"],
        latency_ms=True,
        prompt_context=False,
    )
    if observability_overrides:
        for k, v in observability_overrides.items():
            if hasattr(obs, k):
                setattr(obs, k, bool(v))

    adapter = dict(adapter_config or {})
    if not adapter and endpoints:
        adapter = {"type": "http", "detected_endpoints": endpoints, "configured": False}
    elif adapter and "configured" not in adapter:
        adapter["configured"] = bool(
            adapter.get("type") in {"demo", "offline"}
            or adapter.get("endpoint")
            or adapter.get("callable")
            or adapter.get("command")
        )

    # Assemble pipeline stages summary
    retrievers = detected_stack.get("retrievers_and_stores", [])
    rerankers = detected_stack.get("rerankers", [])
    pipeline_stages = {
        "chunking": {
            "chunk_size": params.get("chunk_size", "unknown"),
            "chunk_overlap": params.get("chunk_overlap", "unknown"),
        },
        "query_understanding": {
            "query_rewrite_or_hyde": obs_hints["query_rewrite"],
            "multi_kb_routing": obs_hints["multi_kb"],
        },
        "retrieval": {
            "backends": retrievers or ["unknown"],
            "hybrid_suspected": any("BM25" in r for r in retrievers) and len(retrievers) >= 2,
            "top_k": params.get("top_k", 5),
        },
        "reranking": {
            "enabled": bool(rerankers or obs_hints["reranked_docs"]),
            "models": rerankers,
            "top_n": params.get("rerank_top_n", "unknown"),
        },
        "generation": {
            "models": detected_stack.get("models", []) or ["unknown"],
        },
        "endpoints": endpoints,
    }

    # Build strict list of unknowns (Understand-First Gate!)
    unknowns: List[UnknownItem] = []

    if corpus_stats["readable_text_files"] == 0:
        unknowns.append(
            UnknownItem(
                id="UNK-KB-01",
                area="knowledge_base",
                question="尚未发现可读文本知识库文件（如 .md/.txt/.csv/.json）。请指定 --kb 路径或先导出 PDF/DOCX 文本。",
                impact="缺乏真实知识库语料将导致无法生成 grounded 测试集与标准证据锚点。",
                blocking=True,
            )
        )
    else:
        if kb_auto_from_root:
            unknowns.append(
                UnknownItem(
                    id="UNK-KB-AUTO",
                    area="knowledge_base",
                    question=(
                        f"未显式传入 --kb，已自动回退识别项目根目录 `{project_root}` 下的 "
                        f"{corpus_stats['readable_text_files']} 份文档作为知识库（已排除常见仓库说明与配置文件）。请确认该目录是否为目标知识库。"
                    ),
                    impact="若目录混入非知识库文档可能影响生成用例的业务纯度，建议确认或显式指定 --kb。",
                    blocking=False,
                )
            )
        if corpus_stats["binary_files_count"] > 0:
            unknowns.append(
                UnknownItem(
                    id="UNK-KB-02",
                    area="knowledge_base",
                    question=f"知识库包含 {corpus_stats['binary_files_count']} 个二进制文档（PDF/DOCX/XLSX 等），轻量扫描器未解析其正文。是否仅评测已提取文本，或需先转换？",
                    impact="未解析文档不会进入自动生成的测试集中，报告覆盖度需明确说明。",
                    blocking=False,
                )
            )

    if not adapter.get("configured"):
        unknowns.append(
            UnknownItem(
                id="UNK-ADAPTER-01",
                area="interface",
                question="尚未确认目标 RAG 的调用接口（HTTP endpoint / Python 函数 / CLI 命令 / 离线手动结果文件）及请求响应字段映射。",
                impact="未明确输入输出契约会导致评测请求失败或字段误读，严禁盲测。",
                blocking=True,
            )
        )

    if not obs.retrieved_docs and not (observability_overrides and "retrieved_docs" in observability_overrides):
        unknowns.append(
            UnknownItem(
                id="UNK-OBS-01",
                area="observability",
                question="未自动检测到中间检索片段（retrieved_chunks/source_documents）输出字段。目标 RAG 是否可返回召回列表？",
                impact="若无法观测召回列表，则命中率（Hit@K/Recall@K/MRR）必须标记为不可观测（Unobservable），不能臆测或混入生成准确率。",
                blocking=True,
            )
        )

    if params.get("chunk_size") is None or not retrievers:
        unknowns.append(
            UnknownItem(
                id="UNK-ARCH-01",
                area="architecture",
                question="未能静态识别全部切片参数（chunk_size/overlap）或检索器类型（向量/BM25/混合），建议确认实际线上配置以便更精准归因。",
                impact="不影响客观指标计算，但会影响根因分析中对切片层与检索层的具体参数建议精度。",
                blocking=False,
            )
        )

    # Calculate Understanding Readiness Score (0-100)
    score = 100
    for u in unknowns:
        score -= 25 if u.blocking else 8
    readiness_score = max(0, min(100, score))

    stack_flat = [item for vals in detected_stack.values() for item in vals]
    arch_summary = (
        f"系统 `{system_name or project_root.name}`："
        f"识别技术栈 {', '.join(stack_flat) if stack_flat else '未静态检出（待人工补充）'}；"
        f"知识库可读文档 {corpus_stats['readable_text_files']} 份"
        f"（共 {corpus_stats['total_size_mb']} MB，含表格文档 {corpus_stats['docs_with_tables']} 份，"
        f"含版本/时间标识文档 {corpus_stats['docs_with_temporal_markers']} 份）；"
        f"召回层可观测={'是' if obs.retrieved_docs else '待确认/否'}。"
    )

    profile = RAGProfile(
        system_name=system_name or project_root.name,
        project_path=str(project_root),
        kb_paths=[str(p) for p in resolved_kb],
        architecture_summary=arch_summary,
        detected_stack=detected_stack,
        pipeline_stages=pipeline_stages,
        observability=obs,
        corpus_stats=corpus_stats,
        adapter_config=adapter,
        thresholds=dict(DEFAULT_THRESHOLDS),
        unknowns=unknowns,
        readiness_score=readiness_score,
        confirmed=False,
    )
    return profile, tracker.snapshot(workers_used=1)


def confirm_profile(
    profile: RAGProfile,
    confirmed_by: str = "user",
    resolutions: Optional[Dict[str, str]] = None,
    adapter_config: Optional[Dict[str, Any]] = None,
    observability_overrides: Optional[Dict[str, bool]] = None,
    threshold_overrides: Optional[Dict[str, float]] = None,
    architecture_note: Optional[str] = None,
) -> RAGProfile:
    """Resolve unknowns and explicitly confirm the RAG understanding profile."""
    if adapter_config:
        profile.adapter_config.update(adapter_config)
        profile.adapter_config["configured"] = True
        for u in profile.unknowns:
            if u.id == "UNK-ADAPTER-01":
                u.resolved = True
                u.resolution = f"Configured adapter: {profile.adapter_config.get('type', 'custom')}"

    if observability_overrides:
        for k, v in observability_overrides.items():
            if hasattr(profile.observability, k):
                setattr(profile.observability, k, bool(v))
        if "retrieved_docs" in observability_overrides:
            for u in profile.unknowns:
                if u.id == "UNK-OBS-01":
                    u.resolved = True
                    u.resolution = f"retrieved_docs observable = {profile.observability.retrieved_docs}"

    if threshold_overrides:
        for k, v in threshold_overrides.items():
            profile.thresholds[k] = float(v)

    if resolutions:
        for uid, text in resolutions.items():
            for u in profile.unknowns:
                if u.id == uid:
                    u.resolved = True
                    u.resolution = text

    if architecture_note:
        profile.notes = (profile.notes + "\n" + architecture_note).strip() if profile.notes else architecture_note

    # Recompute readiness score
    score = 100
    for u in profile.unknowns:
        if not u.resolved:
            score -= 25 if u.blocking else 8
    profile.readiness_score = max(0, min(100, score))

    remaining_blocking = profile.blocking_unknowns()
    if remaining_blocking:
        profile.confirmed = False
        raise UnderstandingGateError(
            "仍存在未解决的阻断性未知项（Blocking Unknowns），无法确认并进入测试阶段。请先补充缺失信息。",
            blocking_unknowns=[u.to_dict() for u in remaining_blocking],
        )

    profile.confirmed = True
    profile.confirmed_by = confirmed_by
    profile.confirmed_at = utc_now_iso()
    return profile


def check_understanding_gate(profile: Optional[RAGProfile], force_unconfirmed: bool = False) -> Tuple[bool, List[str]]:
    """Enforce Understand-First discipline before test data generation or backtesting."""
    if profile is None:
        raise UnderstandingGateError(
            "未找到系统理解档案（rag_profile.json）。严禁在不理解 RAG 系统的情况下直接测试！请先运行 `rag-eval inspect` 并确认理解档案。"
        )

    warnings: List[str] = []
    blocking = profile.blocking_unknowns()
    if blocking and not force_unconfirmed:
        summary_lines = [f"- [{u.id}] ({u.area}): {u.question}" for u in blocking]
        raise UnderstandingGateError(
            "【理解门禁拦截】检测到以下阻断性未知项尚未确认，已阻止盲目测试以保障严谨性：\n"
            + "\n".join(summary_lines)
            + "\n请先通过 `rag-eval confirm` 或补充配置解决上述问题。",
            blocking_unknowns=[u.to_dict() for u in blocking],
        )

    if not profile.confirmed and not force_unconfirmed:
        raise UnderstandingGateError(
            "【理解门禁拦截】系统理解档案尚未经过显式确认（confirmed=false）。"
            "请核对架构、知识库范围与可观测性后运行 `rag-eval confirm` 再执行测试。"
        )

    if force_unconfirmed and (not profile.confirmed or blocking):
        warnings.append(
            "UNVERIFIED_UNDERSTANDING: 本次执行使用了强制跳过理解门禁参数，结果可能存在假设偏差，报告已标注警示。"
        )

    for u in profile.unknowns:
        if not u.resolved and not u.blocking:
            warnings.append(f"非阻断待确认项 [{u.id}]: {u.question}")

    return True, warnings


def save_profile(workdir: Path, profile: RAGProfile) -> Path:
    path = workdir / "rag_profile.json"
    write_json_atomic(path, profile.to_dict())
    return path


def load_profile(workdir: Path) -> Optional[RAGProfile]:
    path = workdir / "rag_profile.json"
    if not path.exists():
        return None
    return RAGProfile.from_dict(read_json(path))
