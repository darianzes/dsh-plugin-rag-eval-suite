"""Optional ultra-lightweight HTTP-only LLM-as-a-Judge (DeepSeek / OpenAI-compatible).

Strictly opt-in, zero external dependencies, temperature=0, structured JSON rubric,
and explicit provenance tracking so deterministic vs LLM-assisted scores are never blurred.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Dict, List, Optional


class LLMJudge:
    """Minimal OpenAI-compatible / DeepSeek HTTP judge client."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout_sec: float = 25.0,
    ):
        self.api_key = (
            api_key
            or os.environ.get("RAG_EVAL_JUDGE_API_KEY")
            or os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        )
        default_url = (
            "https://api.deepseek.com/v1/chat/completions"
            if os.environ.get("DEEPSEEK_API_KEY") and not os.environ.get("RAG_EVAL_JUDGE_BASE_URL")
            else "https://api.openai.com/v1/chat/completions"
        )
        self.base_url = base_url or os.environ.get("RAG_EVAL_JUDGE_BASE_URL") or default_url
        default_model = "deepseek-chat" if "deepseek" in self.base_url else "gpt-4o-mini"
        self.model = model or os.environ.get("RAG_EVAL_JUDGE_MODEL") or default_model
        self.timeout_sec = timeout_sec

    @property
    def is_available(self) -> bool:
        return bool(self.api_key.strip())

    def evaluate_case(
        self,
        query: str,
        expected_answer: str,
        actual_answer: str,
        context_snippets: List[str],
        should_abstain: bool,
    ) -> Optional[Dict[str, Any]]:
        if not self.is_available:
            return None

        ctx_block = "\n\n".join(f"[Chunk {i+1}] {c[:500]}" for i, c in enumerate(context_snippets[:5]))
        if not ctx_block:
            ctx_block = "(No retrieved context provided)"

        prompt = (
            "You are a strict RAG evaluation auditor. Output ONLY valid JSON with keys:\n"
            '- "accuracy_score": float in [0.0, 1.0]\n'
            '- "faithfulness_score": float in [0.0, 1.0] (1.0 = every claim supported by context or proper abstention)\n'
            '- "hallucinated": boolean\n'
            '- "unsupported_claims": array of short strings quoted from actual_answer\n'
            '- "reasoning": concise 1-2 sentence explanation in Chinese\n\n'
            f"Query: {query}\n"
            f"Should Abstain (Out-of-Scope): {should_abstain}\n"
            f"Expected Reference Answer: {expected_answer}\n"
            f"Retrieved Context:\n{ctx_block}\n\n"
            f"Actual RAG Answer:\n{actual_answer}\n"
        )

        payload = {
            "model": self.model,
            "temperature": 0.0,
            "messages": [
                {"role": "system", "content": "Return strictly valid JSON only, no markdown fences."},
                {"role": "user", "content": prompt},
            ],
        }
        req = urllib.request.Request(
            self.base_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                raw = json.loads(resp.read().decode("utf-8", errors="replace"))
            content = raw["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.strip("`").lstrip("json").strip()
            parsed = json.loads(content)
            return {
                "accuracy_score": max(0.0, min(1.0, float(parsed.get("accuracy_score", 0.0)))),
                "faithfulness_score": max(0.0, min(1.0, float(parsed.get("faithfulness_score", 0.0)))),
                "hallucinated": bool(parsed.get("hallucinated", False)),
                "unsupported_claims": [str(x) for x in (parsed.get("unsupported_claims") or [])],
                "reasoning": str(parsed.get("reasoning", "")),
                "judge_model": self.model,
            }
        except Exception:
            return None
