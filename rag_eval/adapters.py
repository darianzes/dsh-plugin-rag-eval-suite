"""Target RAG system adapters (HTTP/REST, Python callable, CLI, Offline Manual, Demo).

All adapters run with pure stdlib, strict timeouts, and low memory overhead.
"""

from __future__ import annotations

import importlib
import json
import math
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import RAGResponse, RetrievedChunk, TestCase
from .resource_guard import ResourceTracker, iter_jsonl


def _extract_by_path(obj: Any, dot_path: str) -> Any:
    if not dot_path:
        return None
    cur = obj
    for part in dot_path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def _normalize_chunks(raw_items: Any, stage: str = "retrieved", cfg: Optional[Dict[str, Any]] = None) -> List[RetrievedChunk]:
    if not isinstance(raw_items, list):
        return []
    cfg = cfg or {}
    id_field = cfg.get("chunk_id_field", "doc_id")
    text_field = cfg.get("chunk_text_field", "text")
    score_field = cfg.get("chunk_score_field", "score")

    chunks: List[RetrievedChunk] = []
    for idx, item in enumerate(raw_items, start=1):
        if isinstance(item, str):
            chunks.append(RetrievedChunk(doc_id=f"chunk_{idx}", text=item, rank=idx, stage=stage))
        elif isinstance(item, dict):
            doc_id = (
                item.get(id_field)
                or item.get("doc_id")
                or item.get("source")
                or item.get("file")
                or item.get("id")
                or (item.get("metadata") or {}).get("source")
                or (item.get("metadata") or {}).get("doc_id")
                or f"chunk_{idx}"
            )
            text = (
                item.get(text_field)
                or item.get("text")
                or item.get("content")
                or item.get("page_content")
                or ""
            )
            score_val = item.get(score_field, item.get("score"))
            chunks.append(
                RetrievedChunk(
                    doc_id=str(doc_id),
                    text=str(text),
                    score=float(score_val) if score_val is not None else None,
                    rank=int(item.get("rank", idx)),
                    stage=stage,
                    metadata=dict(item.get("metadata") or {}),
                )
            )
    return chunks


class BaseRAGAdapter:
    adapter_type: str = "base"

    def query(self, case: TestCase) -> RAGResponse:
        raise NotImplementedError


class HTTPAdapter(BaseRAGAdapter):
    """Connects to any HTTP/REST RAG endpoint using Python stdlib urllib."""

    adapter_type = "http"

    def __init__(self, config: Dict[str, Any]):
        self.endpoint = str(config["endpoint"])
        self.method = str(config.get("method", "POST")).upper()
        self.query_field = str(config.get("query_field", "query"))
        self.extra_payload = dict(config.get("extra_payload") or {})
        self.headers = {"Content-Type": "application/json", **dict(config.get("headers") or {})}
        api_key_env = config.get("api_key_env")
        if api_key_env and os.environ.get(api_key_env):
            self.headers.setdefault("Authorization", f"Bearer {os.environ[api_key_env]}")
        self.timeout = float(config.get("timeout_sec", 30.0))
        self.answer_path = str(config.get("answer_path", "answer"))
        self.chunks_path = str(config.get("chunks_path", "retrieved_chunks"))
        self.reranked_path = str(config.get("reranked_path", "reranked_chunks"))
        self.citations_path = str(config.get("citations_path", "citations"))
        self.cfg = config

    def query(self, case: TestCase) -> RAGResponse:
        payload = {**self.extra_payload, self.query_field: case.query}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.endpoint, data=data, headers=self.headers, method=self.method)
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw_body = resp.read().decode("utf-8", errors="replace")
            elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            parsed = json.loads(raw_body)
            answer = _extract_by_path(parsed, self.answer_path)
            if answer is None:
                answer = parsed.get("response") or parsed.get("output") or parsed.get("text") or ""
            raw_chunks = (
                _extract_by_path(parsed, self.chunks_path)
                or parsed.get("source_documents")
                or parsed.get("contexts")
                or parsed.get("documents")
                or []
            )
            raw_reranked = _extract_by_path(parsed, self.reranked_path) or []
            raw_cites = _extract_by_path(parsed, self.citations_path) or []
            return RAGResponse(
                case_id=case.id,
                answer=str(answer),
                retrieved_chunks=_normalize_chunks(raw_chunks, stage="retrieved", cfg=self.cfg),
                reranked_chunks=_normalize_chunks(raw_reranked, stage="reranked", cfg=self.cfg),
                citations=[str(x) for x in raw_cites] if isinstance(raw_cites, list) else [],
                latency_ms=elapsed_ms,
                token_usage=dict(parsed.get("token_usage") or parsed.get("usage") or {}),
            )
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            return RAGResponse(case_id=case.id, answer="", latency_ms=elapsed_ms, error=f"HTTPAdapter error: {exc}")


class PythonCallableAdapter(BaseRAGAdapter):
    """Calls a local Python function specified as `module.submod:func_name`."""

    adapter_type = "python"

    def __init__(self, config: Dict[str, Any]):
        spec = str(config["callable"])
        if ":" not in spec:
            raise ValueError("Python callable must use format 'module.path:function_name'")
        mod_name, fn_name = spec.split(":", 1)
        mod = importlib.import_module(mod_name)
        self.fn = getattr(mod, fn_name)
        self.cfg = config

    def query(self, case: TestCase) -> RAGResponse:
        t0 = time.perf_counter()
        try:
            res = self.fn(case.query)
            elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            if isinstance(res, RAGResponse):
                res.case_id = case.id
                if not res.latency_ms:
                    res.latency_ms = elapsed_ms
                return res
            if isinstance(res, dict):
                return RAGResponse(
                    case_id=case.id,
                    answer=str(res.get("answer", res.get("response", ""))),
                    retrieved_chunks=_normalize_chunks(
                        res.get("retrieved_chunks", res.get("contexts", res.get("documents", []))),
                        stage="retrieved",
                        cfg=self.cfg,
                    ),
                    reranked_chunks=_normalize_chunks(res.get("reranked_chunks", []), stage="reranked", cfg=self.cfg),
                    citations=[str(x) for x in (res.get("citations") or [])],
                    latency_ms=float(res.get("latency_ms", elapsed_ms)),
                    token_usage=dict(res.get("token_usage") or {}),
                )
            return RAGResponse(case_id=case.id, answer=str(res), latency_ms=elapsed_ms)
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            return RAGResponse(case_id=case.id, answer="", latency_ms=elapsed_ms, error=f"PythonCallable error: {exc}")


class CLICommandAdapter(BaseRAGAdapter):
    """Executes an external CLI command that receives JSON `{query}` on stdin and prints JSON on stdout."""

    adapter_type = "cli"

    def __init__(self, config: Dict[str, Any]):
        self.command = config["command"]
        self.timeout = float(config.get("timeout_sec", 30.0))
        self.cfg = config

    def query(self, case: TestCase) -> RAGResponse:
        t0 = time.perf_counter()
        cmd = self.command if isinstance(self.command, list) else str(self.command).split()
        try:
            proc = subprocess.run(
                cmd,
                input=json.dumps({"case_id": case.id, "query": case.query}, ensure_ascii=False),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
            elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            if proc.returncode != 0:
                return RAGResponse(
                    case_id=case.id,
                    answer="",
                    latency_ms=elapsed_ms,
                    error=f"CLI exited {proc.returncode}: {proc.stderr.strip()[:300]}",
                )
            parsed = json.loads(proc.stdout.strip())
            return RAGResponse(
                case_id=case.id,
                answer=str(parsed.get("answer", "")),
                retrieved_chunks=_normalize_chunks(parsed.get("retrieved_chunks", []), stage="retrieved", cfg=self.cfg),
                reranked_chunks=_normalize_chunks(parsed.get("reranked_chunks", []), stage="reranked", cfg=self.cfg),
                citations=[str(x) for x in (parsed.get("citations") or [])],
                latency_ms=float(parsed.get("latency_ms", elapsed_ms)),
            )
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            return RAGResponse(case_id=case.id, answer="", latency_ms=elapsed_ms, error=f"CLIAdapter error: {exc}")


class OfflineSnapshotAdapter(BaseRAGAdapter):
    """Loads manually collected RAG responses from a JSONL or JSON file for offline/manual auditing."""

    adapter_type = "offline"

    def __init__(self, responses_path: str):
        path = Path(responses_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"Offline responses file not found: {path}")
        self.by_id: Dict[str, RAGResponse] = {}
        self.by_query: Dict[str, RAGResponse] = {}
        if path.suffix.lower() == ".jsonl":
            rows = list(iter_jsonl(path))
        else:
            raw = json.loads(path.read_text(encoding="utf-8"))
            rows = raw if isinstance(raw, list) else raw.get("responses", [])
        for row in rows:
            resp = RAGResponse.from_dict(row)
            if resp.case_id:
                self.by_id[resp.case_id] = resp
            if row.get("query"):
                self.by_query[str(row["query"]).strip()] = resp

    def query(self, case: TestCase) -> RAGResponse:
        if case.id in self.by_id:
            return self.by_id[case.id]
        if case.query.strip() in self.by_query:
            resp = self.by_query[case.query.strip()]
            resp.case_id = case.id
            return resp
        return RAGResponse(
            case_id=case.id,
            answer="",
            error=f"Offline snapshot missing response for case_id={case.id}",
        )


def _tokenize_mixed(text: str) -> List[str]:
    """Lightweight Chinese + English tokenizer (zero dependencies)."""
    tokens: List[str] = []
    # English/numeric/code tokens
    for tok in re.findall(r"[a-zA-Z0-9][a-zA-Z0-9_\-.]*", text.lower()):
        if len(tok) >= 2:
            tokens.append(tok)
    # Chinese character bigrams + single chars for recall
    cjk_runs = re.findall(r"[\u4e00-\u9fff]+", text)
    for run in cjk_runs:
        if len(run) == 1:
            tokens.append(run)
        else:
            for i in range(len(run) - 1):
                tokens.append(run[i : i + 2])
    return tokens


class DemoReferenceRAGAdapter(BaseRAGAdapter):
    """Lightweight stdlib BM25-style local RAG simulator for instant verification & demo.

    Supports realistic behavior modes (`realistic_flawed`, `strict_good`) so users
    can verify metric accuracy and P0-P3 root-cause diagnosis out of the box without
    loading any heavy ML models.
    """

    adapter_type = "demo"

    def __init__(
        self,
        kb_files: List[str],
        kb_root: Optional[str] = None,
        mode: str = "realistic_flawed",
        top_k: int = 4,
        tracker: Optional[ResourceTracker] = None,
    ):
        self.mode = mode
        self.top_k = top_k
        self.chunks: List[Dict[str, Any]] = []
        self.df: Counter[str] = Counter()
        root_hint = Path(kb_root).resolve() if kb_root else None
        tracker = tracker or ResourceTracker()

        for fstr in kb_files:
            fpath = Path(fstr)
            if not fpath.exists() or not fpath.is_file():
                continue
            tracker.files_scanned += 1
            text = tracker.read_text_bounded(fpath)
            if root_hint:
                try:
                    doc_id = str(fpath.resolve().relative_to(root_hint)).replace("\\", "/")
                except ValueError:
                    doc_id = fpath.name
            else:
                doc_id = fpath.name

            # Chunk by paragraph windows (~400 chars)
            paras = [p.strip() for p in re.split(r"\n\s*\n", text) if len(p.strip()) > 20]
            for idx, para in enumerate(paras):
                toks = _tokenize_mixed(para)
                tf = Counter(toks)
                for t in tf:
                    self.df[t] += 1
                self.chunks.append({"doc_id": doc_id, "chunk_idx": idx, "text": para[:600], "tf": tf, "len": len(toks) or 1})

        self.total_chunks = max(1, len(self.chunks))
        self.avg_len = sum(c["len"] for c in self.chunks) / self.total_chunks

    def _retrieve(self, query: str, k: int) -> List[RetrievedChunk]:
        q_toks = _tokenize_mixed(query)
        scored: List[tuple[float, Dict[str, Any]]] = []
        for ch in self.chunks:
            score = 0.0
            for t in q_toks:
                tf = ch["tf"].get(t, 0)
                if tf == 0:
                    continue
                idf = math.log(1.0 + (self.total_chunks - self.df[t] + 0.5) / (self.df[t] + 0.5))
                denom = tf + 1.2 * (1 - 0.75 + 0.75 * (ch["len"] / self.avg_len))
                score += idf * ((tf * 2.2) / denom)
            if score > 0:
                scored.append((score, ch))
        scored.sort(key=lambda x: -x[0])
        out: List[RetrievedChunk] = []
        for rank, (sc, ch) in enumerate(scored[:k], start=1):
            out.append(RetrievedChunk(doc_id=ch["doc_id"], text=ch["text"], score=round(sc, 4), rank=rank, stage="retrieved"))
        return out

    def query(self, case: TestCase) -> RAGResponse:
        t0 = time.perf_counter()
        # Retrieve candidate pool
        raw_retrieved = self._retrieve(case.query, k=max(self.top_k + 3, 6))
        reranked = raw_retrieved[: self.top_k]

        if self.mode == "realistic_flawed":
            # Simulate common real-world RAG failure modes cleanly & deterministically:
            # 1) Fails to abstain on half of unanswerable queries (classic hallucination!)
            if case.should_abstain:
                h = sum(ord(ch) for ch in case.id) % 2
                if h == 0:
                    trap = case.must_not_claim[0] if case.must_not_claim else "相关项目"
                    ans = f"根据系统常规规范，关于 {trap} 的标准审批上限为 5000 元，需在 3 个工作日内由区域主管线上确认。"
                else:
                    ans = "根据当前已检索到的知识库文档，未找到与该问题直接相关的依据，无法确认具体答案。"
                return RAGResponse(
                    case_id=case.id,
                    answer=ans,
                    retrieved_chunks=raw_retrieved,
                    reranked_chunks=reranked,
                    citations=[reranked[0].doc_id] if (reranked and h == 0) else [],
                    latency_ms=round((time.perf_counter() - t0) * 1000.0 + 35.0, 2),
                )

            # 2) On adversarial premise trap, occasionally agrees sycophantically
            if case.category == "adversarial_distractor" and (sum(ord(ch) for ch in case.id) % 2 == 0):
                wrong = case.must_not_claim[0] if case.must_not_claim else "是的，完全一致"
                top_text = reranked[0].text[:120] if reranked else ""
                ans = f"{wrong}。参考片段说明：{top_text}"
                return RAGResponse(
                    case_id=case.id,
                    answer=ans,
                    retrieved_chunks=raw_retrieved,
                    reranked_chunks=reranked,
                    citations=[reranked[0].doc_id] if reranked else [],
                    latency_ms=round((time.perf_counter() - t0) * 1000.0 + 42.0, 2),
                )

            # 3) On multi-hop, only uses top-1 chunk (missing second doc!)
            if case.category == "multi_hop_reasoning" and reranked:
                ans = f"根据检索到的主要文档（[{reranked[0].doc_id}]）：{reranked[0].text[:220]}"
                return RAGResponse(
                    case_id=case.id,
                    answer=ans,
                    retrieved_chunks=raw_retrieved,
                    reranked_chunks=reranked[:1],
                    citations=[reranked[0].doc_id],
                    latency_ms=round((time.perf_counter() - t0) * 1000.0 + 55.0, 2),
                )

            # 4) Standard grounded extraction from top-2 reranked chunks
            if not reranked:
                ans = "知识库中未检索到足够信息，无法回答该问题。"
            else:
                snippets = [c.text[:220] for c in reranked[:2]]
                ans = f"根据知识库文档 [{reranked[0].doc_id}]：" + " ".join(snippets)
            return RAGResponse(
                case_id=case.id,
                answer=ans,
                retrieved_chunks=raw_retrieved,
                reranked_chunks=reranked,
                citations=[c.doc_id for c in reranked[:2]],
                latency_ms=round((time.perf_counter() - t0) * 1000.0 + 28.0, 2),
            )

        # strict_good mode: high grounding & abstention
        top_score = reranked[0].score if (reranked and reranked[0].score is not None) else 0.0
        if case.should_abstain or top_score < 1.2:
            ans = "知识库中未包含与该问题相关的确切依据，因此无法提供可靠回答。"
            cites: List[str] = []
        else:
            joined = " ".join(c.text[:240] for c in reranked[:3])
            ans = f"依据知识库原文（{', '.join(c.doc_id for c in reranked[:2])}）：{joined}"
            cites = [c.doc_id for c in reranked[:2]]
        return RAGResponse(
            case_id=case.id,
            answer=ans,
            retrieved_chunks=raw_retrieved,
            reranked_chunks=reranked,
            citations=cites,
            latency_ms=round((time.perf_counter() - t0) * 1000.0 + 22.0, 2),
        )


def build_adapter(
    adapter_type: str,
    config: Dict[str, Any],
    kb_files: Optional[List[str]] = None,
    kb_root: Optional[str] = None,
    tracker: Optional[ResourceTracker] = None,
) -> BaseRAGAdapter:
    atype = (adapter_type or config.get("type") or "").lower().strip()
    if atype == "http":
        return HTTPAdapter(config)
    if atype == "python":
        return PythonCallableAdapter(config)
    if atype == "cli":
        return CLICommandAdapter(config)
    if atype == "offline":
        return OfflineSnapshotAdapter(str(config["responses_file"]))
    if atype == "demo":
        return DemoReferenceRAGAdapter(
            kb_files=kb_files or [],
            kb_root=kb_root,
            mode=str(config.get("demo_mode", "realistic_flawed")),
            top_k=int(config.get("top_k", 4)),
            tracker=tracker,
        )
    raise ValueError(
        f"不支持的适配器类型 '{atype}'。支持: http, python, cli, offline, demo。"
    )
