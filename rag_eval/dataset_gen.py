"""Phase 3: Corpus-grounded, multi-category RAG test dataset generator and viewer.

Generates comprehensive, categorized test suites grounded in the inspected KB,
with explicit coverage balance checks, core-priority tagging, standalone viewing,
filtering, custom case insertion, and Markdown/JSONL export.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .inspector import check_understanding_gate
from .models import CATEGORY_SPECS, RAGProfile, ScopePlan, TestCase, utc_now_iso
from .resource_guard import (
    ResourceTracker,
    iter_jsonl,
    read_json,
    write_json_atomic,
    write_jsonl_atomic,
)


@dataclass
class DocSection:
    doc_id: str
    file_path: str
    file_name: str
    heading: str
    text: str
    facts: List[str] = field(default_factory=list)
    numbers: List[Tuple[str, str]] = field(default_factory=list)  # (sentence, number_token)
    tables: List[Dict[str, Any]] = field(default_factory=list)
    temporal_facts: List[Tuple[str, str]] = field(default_factory=list)  # (sentence, marker)
    terms: List[Tuple[str, str]] = field(default_factory=list)  # (term, sentence)
    bullets: List[str] = field(default_factory=list)
    conditionals: List[str] = field(default_factory=list)


NUM_RE = re.compile(
    r"(?<!\w)(?:\$|¥|￥)?\d+(?:,\d{3})*(?:\.\d+)?(?:\s*(?:%|ms|s|秒|分钟|小时|天|日|月|年|GB|MB|KB|TB|次|条|元|万元|美元|QPS|TPS|并发|个|项))?"
)
TEMPORAL_RE = re.compile(
    r"(20\d{2}[-/年.]\d{1,2}(?:[-/月.]\d{1,2}[日号]?)?|v\d+\.\d+(?:\.\d+)?|自\s*20\d{2}[^\s，。；]+|截至\s*20\d{2}[^\s，。；]+)"
)
TERM_RE = re.compile(r"\b(?:[A-Z]{2,8}(?:[-_][A-Z0-9]{1,8})?|[A-Z][a-z]+(?:[A-Z][a-z0-9]+)+)\b")
COND_RE = re.compile(r"(仅适用于|仅限|除外|例外|除非|若|如果|当且仅当|标准版|企业版|免费版|生产环境|测试环境|whereas|except|only for|if\s+)")
# Avoid splitting on decimal points or version strings like v3.1 / 99.99% / 1.5
SENT_SPLIT_RE = re.compile(r"(?<=[。！？!?\n])\s*|(?<![0-9vV])\.(?=\s|$)\s*")


def _clean_line(text: str) -> str:
    text = re.sub(r"^\s*(?:[-*•]|\d+[.)、])\s+", "", text)
    text = re.sub(r"[`*_#>]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _short_doc_id(path: Path, root_hint: Optional[Path] = None) -> str:
    if root_hint:
        try:
            rel = path.resolve().relative_to(root_hint.resolve())
            return str(rel).replace("\\", "/")
        except ValueError:
            pass
    return path.name


def _parse_markdown_tables(text: str) -> List[Dict[str, Any]]:
    tables: List[Dict[str, Any]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines) - 2:
        line = lines[i].strip()
        sep = lines[i + 1].strip()
        if line.startswith("|") and line.endswith("|") and re.match(r"^\|[\s:\-|]+\|$", sep):
            headers = [_clean_line(c) for c in line.strip("|").split("|")]
            rows: List[Dict[str, str]] = []
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|") and lines[j].strip().endswith("|"):
                vals = [_clean_line(c) for c in lines[j].strip().strip("|").split("|")]
                if len(vals) == len(headers):
                    rows.append(dict(zip(headers, vals)))
                j += 1
            if headers and rows:
                tables.append({"headers": headers, "rows": rows})
            i = j
        else:
            i += 1
    return tables


def _extract_doc_sections(file_paths: List[str], kb_roots: List[str], tracker: ResourceTracker) -> List[DocSection]:
    root_hint = Path(kb_roots[0]).resolve() if kb_roots else None
    sections: List[DocSection] = []

    for fstr in file_paths:
        fpath = Path(fstr)
        if not fpath.exists() or not fpath.is_file():
            continue
        raw = tracker.read_text_bounded(fpath)
        if not raw.strip():
            continue

        doc_id = _short_doc_id(fpath, root_hint)
        # Split Markdown by headings, or treat whole file as one section
        chunks: List[Tuple[str, str]] = []
        if fpath.suffix.lower() in {".md", ".markdown"}:
            cur_heading = fpath.stem
            cur_lines: List[str] = []
            for line in raw.splitlines():
                m = re.match(r"^(#{1,4})\s+(.+)$", line.strip())
                if m:
                    if cur_lines:
                        chunks.append((cur_heading, "\n".join(cur_lines)))
                    cur_heading = _clean_line(m.group(2))
                    cur_lines = []
                else:
                    cur_lines.append(line)
            if cur_lines:
                chunks.append((cur_heading, "\n".join(cur_lines)))
        else:
            chunks.append((fpath.stem, raw))

        for heading, body in chunks:
            if len(body.strip()) < 25:
                continue
            sec = DocSection(
                doc_id=doc_id,
                file_path=str(fpath),
                file_name=fpath.name,
                heading=heading,
                text=body.strip(),
            )
            sec.tables = _parse_markdown_tables(body)

            for line in body.splitlines():
                sline = line.strip()
                if re.match(r"^[-*•]\s+", sline) or re.match(r"^\d+[.)、]\s+", sline):
                    cleaned_b = _clean_line(re.sub(r"^([-*•]|\d+[.)、])\s+", "", sline))
                    if 8 <= len(cleaned_b) <= 180:
                        sec.bullets.append(cleaned_b)

            # Extract sentences (skip raw markdown table separator lines)
            plain_lines = [ln for ln in body.splitlines() if not ln.strip().startswith("|")]
            plain_text = "\n".join(plain_lines)
            for raw_sent in SENT_SPLIT_RE.split(plain_text):
                sent = _clean_line(raw_sent)
                if len(sent) < 16 or len(sent) > 260:
                    continue
                sec.facts.append(sent)

                # Numbers
                nums = [m.group(0).strip() for m in NUM_RE.finditer(sent) if any(ch.isdigit() for ch in m.group(0))]
                meaningful_nums = [n for n in nums if not re.fullmatch(r"20\d{2}", n) and len(n) >= 2]
                if meaningful_nums:
                    sec.numbers.append((sent, meaningful_nums[0]))

                # Temporal / Version markers
                tm = TEMPORAL_RE.search(sent)
                if tm:
                    sec.temporal_facts.append((sent, tm.group(1)))

                # Domain terms / acronyms
                for term in TERM_RE.findall(sent):
                    if term not in {"HTTP", "HTTPS", "JSON", "YAML", "README", "API", "URL"}:
                        sec.terms.append((term, sent))

                # Conditional / scope clauses
                if COND_RE.search(sent):
                    sec.conditionals.append(sent)

            if sec.facts or sec.tables or sec.bullets:
                sections.append(sec)

    return sections


def _extract_key_phrases(text: str, max_items: int = 4) -> List[str]:
    """Extract verifiable salient phrases/tokens (dates, versions, numbers, acronyms, spans)."""
    phrases: List[str] = []
    # Extract full temporal / version markers first so dates aren't fragmented
    masked = text
    for m in TEMPORAL_RE.finditer(text):
        tok = m.group(1).strip()
        if len(tok) >= 3 and tok not in phrases:
            phrases.append(tok)
            masked = masked.replace(tok, " ")
    for m in NUM_RE.finditer(masked):
        tok = m.group(0).strip()
        if len(tok) >= 2 and tok not in phrases:
            phrases.append(tok)
    for m in TERM_RE.finditer(text):
        tok = m.group(0).strip()
        if len(tok) >= 2 and tok not in phrases:
            phrases.append(tok)
    # Extract key quoted or colon-right segments
    for seg in re.findall(r"[「『“\"]([^」』”\"]{2,24})[」』”\"]", text):
        if seg not in phrases:
            phrases.append(seg)
    if len(phrases) < 2:
        parts = [p.strip() for p in re.split(r"[，,；;：:（）()]", text) if 4 <= len(p.strip()) <= 26]
        for p in parts:
            if p not in phrases:
                phrases.append(p)
            if len(phrases) >= max_items:
                break
    return phrases[:max_items]


def _mutate_number(num_str: str) -> str:
    """Create a plausible wrong numeric distractor for hallucination-trap checks."""
    m = re.search(r"\d+(?:\.\d+)?", num_str)
    if not m:
        return "999"
    val = float(m.group(0))
    mutated = val * 2 + 1 if val < 50 else val * 1.5
    formatted = str(int(round(mutated))) if "." not in m.group(0) else f"{mutated:.1f}"
    return num_str[: m.start()] + formatted + num_str[m.end() :]


ANCHOR_PREDICATE_RE = re.compile(
    r"^(.{3,28}?)(?:默认|采用|遵循|负责|必须|严禁|禁止|仅限|仅适用于|规定|表示|有效期|上限|不超过|超过|须在|高于|低于|统一采用|每\s*\d+|is\s+|must\s+|requires\s+|supports\s+)"
)


def _extract_anchor_subject(heading: str, fact: str) -> str:
    """Extract a specific, unambiguous entity/clause subject from a single fact sentence."""
    # 1. Explicit colon-separated subject (e.g., error codes, version labels, terms)
    colon_split = re.split(r"[：:]", fact, maxsplit=1)
    if len(colon_split) == 2 and 3 <= len(colon_split[0].strip()) <= 30:
        return colon_split[0].strip()

    # 2. Grammatical subject before key policy/spec predicates
    m = ANCHOR_PREDICATE_RE.match(fact)
    if m:
        cand = m.group(1).strip(" ，,。；;、")
        if 3 <= len(cand) <= 28 and cand not in {"本规范", "本制度", "该规范", "当", "若", "如果", "因"}:
            return cand

    # 3. Distinct technical term / acronym in sentence
    terms = [t for t in TERM_RE.findall(fact) if t not in {"HTTP", "HTTPS", "JSON", "YAML", "API", "URL"}]
    if terms:
        return terms[0]

    # 4. Salient leading clause fragment
    first_clause = re.split(r"[，,；;]", fact, maxsplit=1)[0].strip()
    if 4 <= len(first_clause) <= 26:
        return first_clause
    return heading


def _concise_expected(fact: str, max_len: int = 180) -> str:
    """Keep expected answer crisp and focused on the target factual claim."""
    text = fact.strip()
    if len(text) <= max_len:
        return text
    # Trim at clause punctuation near max_len
    cut = text[:max_len]
    for sep in ("。", "；", "，", ".", ";"):
        pos = cut.rfind(sep)
        if pos >= max_len // 2:
            return cut[: pos + 1]
    return cut + "…"


def _build_specific_fact_question(heading: str, fact: str) -> str:
    """Formulate a focused, non-ambiguous question anchored to the specific fact."""
    err_m = re.search(r"\bERR-[A-Z0-9-]+\b", fact)
    if err_m:
        return f"错误码 `{err_m.group(0)}` 代表什么问题，以及对应的排查或处理措施是什么？"

    anchor = _extract_anchor_subject(heading, fact)
    anchor_in_heading = anchor == heading or anchor in heading

    if re.search(r"(?:版本|v\d+\.\d+).*(?:生效|执行|废止|替代)|(?:生效|执行).*(?:版本|20\d{2})", fact):
        return f"文档「{heading}」当前的生效版本号、生效日期及变更要点是什么？"

    if re.search(r"(?:有效期|轮换|自然日|工作日|分钟|小时|逾期|超时)", fact):
        subj = f"「{heading}」" if anchor_in_heading else f"「{heading}」中「{anchor}」"
        return f"根据文档，{subj}的有效期、周期或时限规定具体是多少？"

    if re.search(r"(?:仅限|仅适用于|严禁|禁止|权限|角色|脱敏|明文)", fact):
        subj = f"「{heading}」" if anchor_in_heading else f"「{heading}」中针对「{anchor}」"
        return f"在{subj}的规范下，具体的权限边界、适用范围或禁止要求是什么？"

    if re.search(r"(?:审批|抵扣|赔付|报销|违约|限额|上限)", fact):
        subj = f"「{heading}」" if anchor_in_heading else f"「{anchor}」（{heading}）"
        return f"针对{subj}，文档规定的触发条件、比例或审批/赔付规则是什么？"

    if re.search(r"(?:tokens|QPS|RTO|RPO|BM25|RRF|TLS|JWT|Top-\d+|切片|重排|元数据)", fact, re.IGNORECASE):
        subj = f"「{heading}」" if anchor_in_heading else f"「{anchor}」（{heading}）"
        return f"文档对{subj}的技术参数、字段或实现要求是如何规定的？"

    if anchor_in_heading:
        return f"在文档「{heading}」中，核心执行规则或具体要求是什么？"
    return f"在「{heading}」文档中，关于「{anchor}」的具体规则或说明是什么？"


def _make_subject_from_fact(heading: str, fact: str) -> str:
    """Build a crisp subject string for natural question formulation."""
    anchor = _extract_anchor_subject(heading, fact)
    if anchor == heading or anchor in heading:
        return f"「{heading}」"
    return f"「{heading}」中的「{anchor}」"


def _enhance_queries_with_llm(cases: List[TestCase]) -> Tuple[int, Optional[str]]:
    """Optionally paraphrase case queries via HTTP LLM API while keeping corpus ground truth locked."""
    api_key = (
        os.environ.get("RAG_EVAL_JUDGE_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    ).strip()
    if not api_key:
        return 0, "已启用 --llm-gen 但未检测到 API Key，已使用增强型实体锚定确定性生成器。"

    default_url = (
        "https://api.deepseek.com/v1/chat/completions"
        if os.environ.get("DEEPSEEK_API_KEY") and not os.environ.get("RAG_EVAL_JUDGE_BASE_URL")
        else "https://api.openai.com/v1/chat/completions"
    )
    base_url = os.environ.get("RAG_EVAL_JUDGE_BASE_URL") or default_url
    model = os.environ.get("RAG_EVAL_JUDGE_MODEL") or ("deepseek-chat" if "deepseek" in base_url else "gpt-4o-mini")

    # Batch up to 25 queries in a single lightweight HTTP call to keep latency & cost minimal
    batch = [
        {"id": c.id, "category": c.category, "query": c.query, "evidence": (c.expected_evidence_snippets or [c.expected_answer])[0][:160]}
        for c in cases[:25]
    ]
    prompt = (
        "将以下 RAG 测试问句润色为更像真实业务用户提出的自然提问，要求：\n"
        "1. 严格保持原有测试意图、具体实体/数值前提和拒答边界不变；\n"
        "2. 不要泄露答案中的未知数值或结论；\n"
        "3. 仅返回 JSON 对象 {\"queries\": {\"TC-001\": \"润色后的问句\", ...}}。\n\n"
        + json.dumps(batch, ensure_ascii=False)
    )
    payload = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": "Return strictly valid JSON only."},
            {"role": "user", "content": prompt},
        ],
    }
    req = urllib.request.Request(
        base_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=25.0) as resp:
            raw = json.loads(resp.read().decode("utf-8", errors="replace"))
        content = raw["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.strip("`").lstrip("json").strip()
        rewritten = (json.loads(content) or {}).get("queries") or {}
        changed = 0
        for c in cases:
            nq = rewritten.get(c.id)
            if isinstance(nq, str) and len(nq.strip()) >= 6:
                c.query = nq.strip()
                changed += 1
        return changed, None
    except Exception as exc:
        return 0, f"LLM 问句润色调用失败（已保留确定性实体锚定问句）: {exc}"


def _allocate_counts(selected_categories: List[str], target_total: int) -> Dict[str, int]:
    weights = {c: float(CATEGORY_SPECS[c]["recommended_ratio"]) for c in selected_categories if c in CATEGORY_SPECS}
    wsum = sum(weights.values()) or 1.0
    counts: Dict[str, int] = {}
    for c, w in weights.items():
        counts[c] = max(1, int(round(target_total * (w / wsum))))
    # Adjust total closely toward target_total
    while sum(counts.values()) > target_total and any(v > 1 for v in counts.values()):
        largest = max(counts, key=lambda k: counts[k])
        counts[largest] -= 1
    while sum(counts.values()) < target_total and counts:
        smallest = min(counts, key=lambda k: counts[k])
        counts[smallest] += 1
    return counts


def generate_test_dataset(
    profile: RAGProfile,
    scope_plan: ScopePlan,
    force_unconfirmed: bool = False,
    use_llm_gen: bool = False,
    tracker: Optional[ResourceTracker] = None,
) -> Tuple[List[TestCase], Dict[str, Any]]:
    """Generate a comprehensive, categorized, corpus-grounded test dataset."""
    _, gate_warnings = check_understanding_gate(profile, force_unconfirmed=force_unconfirmed)
    tracker = tracker or ResourceTracker()

    sections = _extract_doc_sections(scope_plan.selected_kb_files, profile.kb_paths, tracker)
    if not sections:
        raise ValueError(
            "未能从选定的知识库文件中提取到有效文本内容，无法生成测试集。请检查知识库路径与文件格式。"
        )

    corpus_lower = "\n".join(s.text.lower() for s in sections)
    allocations = _allocate_counts(scope_plan.selected_categories, scope_plan.target_case_count)

    cases: List[TestCase] = []
    seen_queries: set[str] = set()
    seq = 1

    def add_case(
        category: str,
        difficulty: str,
        core_priority: int,
        query: str,
        expected_answer: str,
        expected_doc_ids: List[str],
        evidence: List[str],
        must_include: List[str],
        must_not_claim: Optional[List[str]] = None,
        expected_numbers: Optional[List[str]] = None,
        should_abstain: bool = False,
        source_files: Optional[List[str]] = None,
        rationale: str = "",
        tags: Optional[List[str]] = None,
    ) -> bool:
        nonlocal seq
        q_norm = query.strip()
        if not q_norm or q_norm in seen_queries:
            return False
        seen_queries.add(q_norm)
        cases.append(
            TestCase(
                id=f"TC-{seq:03d}",
                category=category,
                difficulty=difficulty,
                core_priority=core_priority,
                query=q_norm,
                expected_answer=expected_answer.strip(),
                expected_doc_ids=list(dict.fromkeys(expected_doc_ids)),
                expected_evidence_snippets=[e[:280] for e in evidence if e],
                must_include_facts=[m for m in dict.fromkeys(must_include) if m],
                must_not_claim=[m for m in dict.fromkeys(must_not_claim or []) if m],
                expected_numbers=[n for n in dict.fromkeys(expected_numbers or []) if n],
                should_abstain=should_abstain,
                source_files=list(dict.fromkeys(source_files or [])),
                rationale=rationale,
                tags=tags or [category],
            )
        )
        seq += 1
        return True

    # 1. single_hop_fact
    if "single_hop_fact" in allocations:
        need = allocations["single_hop_fact"]
        added = 0
        for idx, sec in enumerate(sections):
            for fact in sec.facts[:3]:
                if added >= need:
                    break
                concise_ans = _concise_expected(fact)
                phrases = _extract_key_phrases(concise_ans, max_items=3)
                q = _build_specific_fact_question(sec.heading, fact)
                ok = add_case(
                    category="single_hop_fact",
                    difficulty="easy" if added % 2 == 0 else "medium",
                    core_priority=1 if added < max(3, need // 2 + 1) else 2,
                    query=q,
                    expected_answer=concise_ans,
                    expected_doc_ids=[sec.doc_id],
                    evidence=[fact],
                    must_include=phrases,
                    source_files=[sec.file_path],
                    rationale="检验单文档实体锚定片段的精准召回命中率与忠实事实转述能力。",
                    tags=["single_hop", "core" if added < 4 else "standard"],
                )
                if ok:
                    added += 1
            if added >= need:
                break

    # 2. numerical_table
    if "numerical_table" in allocations:
        need = allocations["numerical_table"]
        added = 0
        # First use structured Markdown tables
        for sec in sections:
            for tbl in sec.tables:
                headers = tbl["headers"]
                if len(headers) < 2:
                    continue
                key_col = headers[0]
                for row in tbl["rows"]:
                    if added >= need:
                        break
                    row_key = row.get(key_col, "").strip()
                    if not row_key:
                        continue
                    # Pick a numeric or rich value column
                    val_col = headers[1]
                    for h in headers[1:]:
                        if NUM_RE.search(row.get(h, "")):
                            val_col = h
                            break
                    val = row.get(val_col, "").strip()
                    if not val:
                        continue
                    nums = [m.group(0).strip() for m in NUM_RE.finditer(val)]
                    q = f"在「{sec.heading}」的表格中，{key_col}为「{row_key}」对应的「{val_col}」具体是多少？"
                    ans = f"{row_key} 对应的 {val_col} 为 {val}。"
                    wrong = [_mutate_number(nums[0])] if nums else []
                    ok = add_case(
                        category="numerical_table",
                        difficulty="medium",
                        core_priority=1 if added < 3 else 2,
                        query=q,
                        expected_answer=ans,
                        expected_doc_ids=[sec.doc_id],
                        evidence=[f"{key_col}={row_key} | {val_col}={val}"],
                        must_include=[row_key, val],
                        must_not_claim=wrong,
                        expected_numbers=nums,
                        source_files=[sec.file_path],
                        rationale="检验表格切片是否被截断以及关键数值/指标的精确保真度。",
                        tags=["table", "numerical"],
                    )
                    if ok:
                        added += 1
        # Fill remaining from numeric sentences
        for sec in sections:
            for sent, num_tok in sec.numbers:
                if added >= need:
                    break
                subj = _make_subject_from_fact(sec.heading, sent)
                q = f"文档中针对{subj}给出的具体数值、限额或指标要求是多少？"
                ok = add_case(
                    category="numerical_table",
                    difficulty="medium",
                    core_priority=1 if added < 3 else 2,
                    query=q,
                    expected_answer=sent,
                    expected_doc_ids=[sec.doc_id],
                    evidence=[sent],
                    must_include=[num_tok] + _extract_key_phrases(sent, 2),
                    must_not_claim=[_mutate_number(num_tok)],
                    expected_numbers=[num_tok],
                    source_files=[sec.file_path],
                    rationale="检验精确数值抽取，防止生成阶段发生数字漂移或单位混淆。",
                    tags=["numerical"],
                )
                if ok:
                    added += 1

    # 3. multi_hop_reasoning
    if "multi_hop_reasoning" in allocations:
        need = allocations["multi_hop_reasoning"]
        added = 0
        # Pair sections from different files (or different headings if single file)
        distinct_pairs: List[Tuple[DocSection, DocSection]] = []
        for i in range(len(sections)):
            for j in range(i + 1, len(sections)):
                if sections[i].doc_id != sections[j].doc_id:
                    distinct_pairs.append((sections[i], sections[j]))
        if not distinct_pairs:
            for i in range(len(sections)):
                for j in range(i + 1, len(sections)):
                    distinct_pairs.append((sections[i], sections[j]))

        for sec_a, sec_b in distinct_pairs:
            if added >= need:
                break
            fact_a = sec_a.facts[0] if sec_a.facts else (sec_a.bullets[0] if sec_a.bullets else "")
            fact_b = sec_b.facts[0] if sec_b.facts else (sec_b.bullets[0] if sec_b.bullets else "")
            if not fact_a or not fact_b:
                continue
            q = (
                f"请综合「{sec_a.heading}」与「{sec_b.heading}」两处文档信息，"
                f"说明二者的核心要求分别是什么，以及在联合执行时需同时满足哪些要点？"
            )
            ans = f"1) {sec_a.heading}：{fact_a} 2) {sec_b.heading}：{fact_b}"
            must = _extract_key_phrases(fact_a, 2) + _extract_key_phrases(fact_b, 2)
            ok = add_case(
                category="multi_hop_reasoning",
                difficulty="hard",
                core_priority=1 if added < 2 else 2,
                query=q,
                expected_answer=ans,
                expected_doc_ids=[sec_a.doc_id, sec_b.doc_id],
                evidence=[fact_a, fact_b],
                must_include=must,
                source_files=[sec_a.file_path, sec_b.file_path],
                rationale="检验多跳跨文档召回完整度（Recall@K）及跨证据综合回答能力。",
                tags=["multi_hop", "cross_doc"],
            )
            if ok:
                added += 1

    # 4. temporal_version
    if "temporal_version" in allocations:
        need = allocations["temporal_version"]
        added = 0
        for sec in sections:
            for sent, marker in sec.temporal_facts:
                if added >= need:
                    break
                q = f"在「{sec.heading}」中，涉及时间节点或版本「{marker}」的规则或变更内容是什么？"
                ok = add_case(
                    category="temporal_version",
                    difficulty="medium",
                    core_priority=1 if added < 2 else 2,
                    query=q,
                    expected_answer=sent,
                    expected_doc_ids=[sec.doc_id],
                    evidence=[sent],
                    must_include=[marker] + _extract_key_phrases(sent, 2),
                    source_files=[sec.file_path],
                    rationale="检验版本与时效边界识别，防止过期规则污染当前结论。",
                    tags=["temporal", "version"],
                )
                if ok:
                    added += 1

    # 5. terminology_jargon
    if "terminology_jargon" in allocations:
        need = allocations["terminology_jargon"]
        added = 0
        used_terms: set[str] = set()
        for sec in sections:
            for term, sent in sec.terms:
                if added >= need:
                    break
                if term in used_terms:
                    continue
                used_terms.add(term)
                q = f"知识库中提到的术语/标识 `{term}` 在「{sec.heading}」场景下具体含义或处理规则是什么？"
                ok = add_case(
                    category="terminology_jargon",
                    difficulty="medium",
                    core_priority=1 if added < 2 else 2,
                    query=q,
                    expected_answer=sent,
                    expected_doc_ids=[sec.doc_id],
                    evidence=[sent],
                    must_include=[term] + _extract_key_phrases(sent, 2),
                    source_files=[sec.file_path],
                    rationale="检验专有名词、缩写与代码标识的精确词法召回能力（纯向量检索易漏召）。",
                    tags=["terminology", "lexical"],
                )
                if ok:
                    added += 1

    # 6. unanswerable_oos (Critical for Hallucination & Abstention testing!)
    if "unanswerable_oos" in allocations:
        need = allocations["unanswerable_oos"]
        oos_templates = [
            (
                "请问知识库中对「{topic}」在南极洲分部（Antarctica-Ops-99）的专属离线豁免审批人姓名和分机号是多少？",
                ["Antarctica-Ops-99", "分机号"],
                "虚构地理与审批人细节，知识库无此记录，系统必须明确拒答。",
            ),
            (
                "文档中是否规定了「{topic}」在 2039 年 Q4 量子加密迁移计划（QEMP-2039）的预算精确金额？",
                ["QEMP-2039", "2039"],
                "超出知识库时间与范围的虚构计划，用于检测编造数字与幻觉。",
            ),
            (
                "请列出知识库中「{topic}」关联的内部代号 `PROJECT-ZETA-7741` 的 SLA 赔付公式与 CEO 签字日期。",
                ["PROJECT-ZETA-7741"],
                "不存在的内部项目代号，检验系统是否会顺着提问编造 SLA 公式。",
            ),
            (
                "根据现有知识库，「{topic}」是否支持通过卫星短报文协议（SAT-SMS-V9）进行自动故障切换？具体端口号是多少？",
                ["SAT-SMS-V9"],
                "领域相近但知识库明确未提及的技术协议，检验边界拒答能力。",
            ),
            (
                "知识库里关于「{topic}」对火星基地数据中心（Mars-DC-01）的冷备恢复 RTO 指标写的是多少分钟？",
                ["Mars-DC-01"],
                "诱导性给出指标类型（RTO）但主体不存在，检验幻觉克制能力。",
            ),
            (
                "请问在「{topic}」规范中，员工个人购买加密货币硬件钱包的年度报销上限是多少美元？",
                ["加密货币硬件钱包"],
                "典型制度外问题，检验面对相似语境时是否诚实声明“知识库未包含相关信息”。",
            ),
        ]
        added = 0
        for idx in range(need):
            sec = sections[idx % len(sections)]
            tpl, probes, rationale = oos_templates[idx % len(oos_templates)]
            # Verify probe tokens genuinely do NOT exist in corpus (rigor!)
            if any(p.lower() in corpus_lower for p in probes):
                continue
            q = tpl.format(topic=sec.heading)
            ok = add_case(
                category="unanswerable_oos",
                difficulty="hard",
                core_priority=1 if added < max(3, need // 2 + 1) else 2,
                query=q,
                expected_answer="知识库中未提供该问题的相关信息，无法确认或给出具体答案。",
                expected_doc_ids=[],
                evidence=[],
                must_include=[],
                must_not_claim=probes,
                should_abstain=True,
                source_files=[],
                rationale=rationale,
                tags=["unanswerable", "hallucination_guard", "abstention"],
            )
            if ok:
                added += 1

    # 7. adversarial_distractor (False-premise / hallucination induction trap)
    if "adversarial_distractor" in allocations:
        need = allocations["adversarial_distractor"]
        added = 0
        for sec in sections:
            for sent, num_tok in sec.numbers:
                if added >= need:
                    break
                wrong_num = _mutate_number(num_tok)
                if wrong_num == num_tok:
                    continue
                subj = _make_subject_from_fact(sec.heading, sent)
                q = (
                    f"我记得{subj}规定的数值是 {wrong_num}，"
                    f"请确认是不是 {wrong_num} 并说明文档原文是怎么写的？"
                )
                ok = add_case(
                    category="adversarial_distractor",
                    difficulty="hard",
                    core_priority=1 if added < 2 else 2,
                    query=q,
                    expected_answer=f"不是 {wrong_num}。根据文档原文：{sent}",
                    expected_doc_ids=[sec.doc_id],
                    evidence=[sent],
                    must_include=[num_tok],
                    must_not_claim=[f"确实是 {wrong_num}", f"是的，{wrong_num}", f"确认是 {wrong_num}"],
                    expected_numbers=[num_tok],
                    source_files=[sec.file_path],
                    rationale="对抗诱导测试：用户在问题中植入错误数值前提，检验模型是否盲从用户产生迎合性幻觉。",
                    tags=["adversarial", "sycophancy_trap"],
                )
                if ok:
                    added += 1
        # Fallback using non-numeric facts if needed
        if added < need:
            for sec in sections:
                if added >= need:
                    break
                if not sec.facts:
                    continue
                fact = sec.facts[0]
                q = f"有人说文档「{sec.heading}」明确允许完全跳过审核且无任何限制，这与文档一致吗？原文规定是什么？"
                ok = add_case(
                    category="adversarial_distractor",
                    difficulty="hard",
                    core_priority=2,
                    query=q,
                    expected_answer=f"不一致。文档原文说明：{fact}",
                    expected_doc_ids=[sec.doc_id],
                    evidence=[fact],
                    must_include=_extract_key_phrases(fact, 2),
                    must_not_claim=["完全跳过审核且无任何限制"],
                    source_files=[sec.file_path],
                    rationale="对抗错误前提测试：检验生成层是否基于证据纠偏而非顺从错误诱导。",
                    tags=["adversarial"],
                )
                if ok:
                    added += 1

    # 8. conflicting_ambiguous
    if "conflicting_ambiguous" in allocations:
        need = allocations["conflicting_ambiguous"]
        added = 0
        for sec in sections:
            for csent in sec.conditionals:
                if added >= need:
                    break
                subj = _make_subject_from_fact(sec.heading, csent)
                q = f"关于{subj}，不同条件或场景下的适用边界与例外规则是什么？"
                ok = add_case(
                    category="conflicting_ambiguous",
                    difficulty="hard",
                    core_priority=2,
                    query=q,
                    expected_answer=csent,
                    expected_doc_ids=[sec.doc_id],
                    evidence=[csent],
                    must_include=_extract_key_phrases(csent, 3),
                    source_files=[sec.file_path],
                    rationale="检验系统对限定条件、例外条款与场景差异的精确区分能力。",
                    tags=["conditional", "disambiguation"],
                )
                if ok:
                    added += 1

    # 9. long_context_synthesis
    if "long_context_synthesis" in allocations:
        need = allocations["long_context_synthesis"]
        added = 0
        for sec in sections:
            if added >= need:
                break
            if len(sec.bullets) >= 3:
                top_bullets = sec.bullets[:4]
                q = f"请完整汇总文档「{sec.heading}」中列出的全部关键要点，不要遗漏。"
                ans = "；".join(top_bullets)
                must = []
                for b in top_bullets:
                    must.extend(_extract_key_phrases(b, 1))
                ok = add_case(
                    category="long_context_synthesis",
                    difficulty="medium",
                    core_priority=2,
                    query=q,
                    expected_answer=ans,
                    expected_doc_ids=[sec.doc_id],
                    evidence=top_bullets,
                    must_include=must[:4],
                    source_files=[sec.file_path],
                    rationale="检验多要点归纳的完整度与 Lost-in-the-Middle 遗漏问题。",
                    tags=["synthesis", "multi_point"],
                )
                if ok:
                    added += 1

    # 10. robustness_boundary
    if "robustness_boundary" in allocations:
        need = allocations["robustness_boundary"]
        added = 0
        for sec in sections:
            if added >= need:
                break
            if not sec.facts:
                continue
            fact = sec.facts[0]
            phrases = _extract_key_phrases(fact, 2)
            kw = " ".join(phrases[:2]) if phrases else sec.heading
            q = f"??? {sec.heading} / {kw} — 速查规则!!?"
            ok = add_case(
                category="robustness_boundary",
                difficulty="medium",
                core_priority=3,
                query=q,
                expected_answer=fact,
                expected_doc_ids=[sec.doc_id],
                evidence=[fact],
                must_include=phrases,
                source_files=[sec.file_path],
                rationale="检验带有噪声标点与简写口语输入时的系统鲁棒性与稳定性。",
                tags=["robustness", "noisy_query"],
            )
            if ok:
                added += 1

    gen_mode = "deterministic_anchored"
    if use_llm_gen and cases:
        rewritten_cnt, llm_warn = _enhance_queries_with_llm(cases)
        if rewritten_cnt > 0:
            gen_mode = f"llm_phrased ({rewritten_cnt}/{len(cases)})"
        if llm_warn:
            gate_warnings.append(llm_warn)

    dataset_meta = {
        "generated_at": utc_now_iso(),
        "system_name": profile.system_name,
        "scope_mode": scope_plan.profile_mode,
        "generation_mode": gen_mode,
        "self_extracted_facts": True,
        "validity_note": (
            "must_include_facts 由语料参考句自动抽取，适用于同一 RAG 系统的纵向回归；"
            "跨系统/跨模型横向对比建议结合 --llm-judge 或独立人工标注标准答案，避免纯复述带来的自洽分数膨胀。"
        ),
        "total_cases": len(cases),
        "core_cases": sum(1 for c in cases if c.core_priority == 1),
        "gate_warnings": gate_warnings,
        "coverage_summary": summarize_dataset(cases),
        "resource_stats": tracker.snapshot(workers_used=1),
    }
    return cases, dataset_meta


def summarize_dataset(cases: List[TestCase]) -> Dict[str, Any]:
    """Produce categorized coverage metrics and balance checks for standalone dataset viewing."""
    cat_counts = Counter(c.category for c in cases)
    diff_counts = Counter(c.difficulty for c in cases)
    core_count = sum(1 for c in cases if c.core_priority == 1)
    abstain_count = sum(1 for c in cases if c.should_abstain)
    total = len(cases)

    categories_detail: List[Dict[str, Any]] = []
    for cat_id, spec in CATEGORY_SPECS.items():
        cnt = cat_counts.get(cat_id, 0)
        categories_detail.append(
            {
                "category": cat_id,
                "zh_name": spec["zh_name"],
                "en_name": spec["en_name"],
                "description": spec["description"],
                "count": cnt,
                "ratio": round(cnt / total, 4) if total else 0.0,
                "core_eligible": spec["core_eligible"],
                "primary_metrics": spec["primary_metrics"],
            }
        )

    balance_warnings: List[str] = []
    if total > 0:
        if abstain_count == 0:
            balance_warnings.append("缺少拒答/知识盲区用例（unanswerable_oos），无法充分检测编造幻觉风险！")
        elif abstain_count / total < 0.10:
            balance_warnings.append("拒答边界用例占比偏低（<10%），建议补充知识盲区测试以增强幻觉审计。")
        if total < 12:
            balance_warnings.append(f"样本量偏小（N={total}），统计置信区间较宽，仅适合快速冒烟参考。")

    return {
        "total_cases": total,
        "core_cases": core_count,
        "abstain_cases": abstain_count,
        "difficulty_distribution": dict(diff_counts),
        "categories": categories_detail,
        "active_categories": sum(1 for c in categories_detail if c["count"] > 0),
        "balance_warnings": balance_warnings,
    }


def filter_dataset(
    cases: List[TestCase],
    category: Optional[str] = None,
    difficulty: Optional[str] = None,
    core_only: bool = False,
    should_abstain: Optional[bool] = None,
    search: Optional[str] = None,
    case_ids: Optional[List[str]] = None,
) -> List[TestCase]:
    out: List[TestCase] = []
    id_set = {x.strip() for x in case_ids} if case_ids else None
    q_search = search.lower().strip() if search else None

    for c in cases:
        if id_set is not None and c.id not in id_set:
            continue
        if category and c.category != category:
            continue
        if difficulty and c.difficulty != difficulty:
            continue
        if core_only and c.core_priority != 1:
            continue
        if should_abstain is not None and c.should_abstain != should_abstain:
            continue
        if q_search:
            hay = f"{c.id} {c.query} {c.expected_answer} {' '.join(c.tags)} {' '.join(c.expected_doc_ids)}".lower()
            if q_search not in hay:
                continue
        out.append(c)
    return out


def save_dataset(workdir: Path, cases: List[TestCase], meta: Dict[str, Any]) -> Tuple[Path, Path]:
    ds_path = workdir / "datasets" / "test_dataset.jsonl"
    meta_path = workdir / "datasets" / "dataset_meta.json"
    write_jsonl_atomic(ds_path, (c.to_dict() for c in cases))
    meta["coverage_summary"] = summarize_dataset(cases)
    meta["total_cases"] = len(cases)
    meta["core_cases"] = sum(1 for c in cases if c.core_priority == 1)
    write_json_atomic(meta_path, meta)
    return ds_path, meta_path


def load_dataset(workdir: Path) -> Tuple[List[TestCase], Dict[str, Any]]:
    ds_path = workdir / "datasets" / "test_dataset.jsonl"
    meta_path = workdir / "datasets" / "dataset_meta.json"
    cases = [TestCase.from_dict(row) for row in iter_jsonl(ds_path)]
    meta = read_json(meta_path) if meta_path.exists() else {"coverage_summary": summarize_dataset(cases)}
    return cases, meta


def export_dataset_markdown(cases: List[TestCase]) -> str:
    summary = summarize_dataset(cases)
    lines = [
        "# RAG 分类测试数据集清单",
        "",
        f"- **总用例数**: {summary['total_cases']}（核心用例: {summary['core_cases']}，拒答边界用例: {summary['abstain_cases']}）",
        f"- **覆盖分类数**: {summary['active_categories']} / {len(CATEGORY_SPECS)}",
        "",
        "## 分类分布总览",
        "",
        "| 分类标识 | 中文分类 | 用例数 | 占比 | 核心指标 |",
        "| :--- | :--- | ---: | ---: | :--- |",
    ]
    for c in summary["categories"]:
        if c["count"] > 0:
            lines.append(
                f"| `{c['category']}` | {c['zh_name']} | {c['count']} | {c['ratio']*100:.1f}% | {', '.join(c['primary_metrics'])} |"
            )

    grouped: Dict[str, List[TestCase]] = defaultdict(list)
    for tc in cases:
        grouped[tc.category].append(tc)

    for cat_id, spec in CATEGORY_SPECS.items():
        bucket = grouped.get(cat_id)
        if not bucket:
            continue
        lines.extend(["", f"## {spec['zh_name']} (`{cat_id}`, 共 {len(bucket)} 条)", "", f"> {spec['description']}", ""])
        for tc in bucket:
            core_badge = "CORE" if tc.core_priority == 1 else "STD"
            lines.append(f"### {tc.id} [{tc.difficulty.upper()} | {core_badge}]")
            lines.append(f"- **提问 (Query)**: {tc.query}")
            lines.append(f"- **参考答案 (Expected)**: {tc.expected_answer}")
            if tc.should_abstain:
                lines.append("- **预期行为**: 必须诚实拒答（知识库未覆盖）")
            if tc.expected_doc_ids:
                lines.append(f"- **标准命中文档 (Gold Docs)**: `{'`, `'.join(tc.expected_doc_ids)}`")
            if tc.must_include_facts:
                lines.append(f"- **必含要点**: {', '.join(tc.must_include_facts)}")
            if tc.must_not_claim:
                lines.append(f"- **幻觉禁区 (Must Not Claim)**: {', '.join(tc.must_not_claim)}")
            if tc.rationale:
                lines.append(f"- **设计意图**: {tc.rationale}")
            lines.append("")

    return "\n".join(lines)
