"""Low-footprint resource guard, streaming JSONL utilities, and safe concurrency caps."""

from __future__ import annotations

import json
import os
import resource
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional


DEFAULT_IGNORE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "dist",
    "build",
    "target",
    ".next",
    ".nuxt",
    ".cache",
    ".rag_eval",
    ".idea",
    ".vscode",
    "coverage",
    "htmlcov",
}


def get_peak_rss_mb() -> float:
    """Return process peak resident set size in MB (handles macOS bytes vs Linux KiB)."""
    usage = resource.getrusage(resource.RUSAGE_SELF)
    if sys.platform == "darwin":
        return round(usage.ru_maxrss / (1024.0 * 1024.0), 2)
    return round(usage.ru_maxrss / 1024.0, 2)


def safe_worker_count(requested: Optional[int] = None, hard_cap: int = 4) -> int:
    """Keep concurrency conservative to avoid CPU/RAM spikes on user machines."""
    env_cap = os.environ.get("RAG_EVAL_MAX_WORKERS")
    if env_cap and env_cap.isdigit():
        hard_cap = max(1, min(hard_cap, int(env_cap)))
    cpu_cnt = os.cpu_count() or 2
    auto_cap = max(1, min(hard_cap, max(1, cpu_cnt // 2)))
    if requested is None:
        return min(2, auto_cap)
    return max(1, min(int(requested), hard_cap))


@dataclass
class ResourceBudget:
    max_workers: int = 2
    max_scan_files: int = 600
    max_file_bytes: int = 262_144  # 256 KB read window per document
    max_memory_mb: float = 256.0
    request_timeout_sec: float = 30.0

    @classmethod
    def from_env(cls) -> "ResourceBudget":
        mem_env = os.environ.get("RAG_EVAL_MEM_LIMIT_MB")
        mem_limit = float(mem_env) if mem_env else 256.0
        return cls(
            max_workers=safe_worker_count(None),
            max_memory_mb=max(64.0, mem_limit),
        )


@dataclass
class ResourceTracker:
    budget: ResourceBudget = field(default_factory=ResourceBudget.from_env)
    started_at: float = field(default_factory=time.perf_counter)
    files_scanned: int = 0
    files_skipped_cap: int = 0
    bytes_read: int = 0
    throttle_events: int = 0
    warnings: List[str] = field(default_factory=list)

    def check_memory(self) -> float:
        rss_mb = get_peak_rss_mb()
        if rss_mb > self.budget.max_memory_mb:
            self.throttle_events += 1
            msg = (
                f"Process peak RSS ({rss_mb:.1f} MB) exceeded soft budget "
                f"({self.budget.max_memory_mb:.1f} MB); switching to sequential/streaming mode."
            )
            if msg not in self.warnings:
                self.warnings.append(msg)
        return rss_mb

    def read_text_bounded(self, path: Path) -> str:
        """Read at most `max_file_bytes` from a text file to prevent memory spikes."""
        try:
            with path.open("rb") as fh:
                raw = fh.read(self.budget.max_file_bytes)
            self.bytes_read += len(raw)
            return raw.decode("utf-8", errors="replace")
        except OSError:
            return ""

    def iter_files_bounded(
        self,
        root: Path,
        allowed_exts: Optional[set[str]] = None,
        max_depth: int = 8,
    ) -> Iterator[Path]:
        """Breadth-bounded, ignore-aware directory walk with strict file count caps."""
        if not root.exists():
            return
        if root.is_file():
            if allowed_exts is None or root.suffix.lower() in allowed_exts:
                self.files_scanned += 1
                yield root
            return

        root_resolved = root.resolve()
        root_parts = len(root_resolved.parts)
        for dirpath, dirnames, filenames in os.walk(root_resolved, followlinks=False):
            current = Path(dirpath)
            depth = len(current.parts) - root_parts
            # Prune ignored directories in-place
            dirnames[:] = sorted(
                d for d in dirnames
                if d not in DEFAULT_IGNORE_DIRS and not d.startswith(".")
            )
            if depth >= max_depth:
                dirnames[:] = []

            for fname in sorted(filenames):
                if fname.startswith("."):
                    continue
                fpath = current / fname
                if allowed_exts is not None and fpath.suffix.lower() not in allowed_exts:
                    continue
                if self.files_scanned >= self.budget.max_scan_files:
                    self.files_skipped_cap += 1
                    continue
                self.files_scanned += 1
                yield fpath

        if self.files_skipped_cap > 0:
            self.warnings.append(
                f"Reached scan cap ({self.budget.max_scan_files} files); "
                f"{self.files_skipped_cap} additional files were deferred to protect CPU/RAM. "
                "Use Core Scope Pruning or narrow --kb paths."
            )

    def snapshot(self, workers_used: int = 1) -> Dict[str, Any]:
        elapsed = round(time.perf_counter() - self.started_at, 3)
        rss_mb = get_peak_rss_mb()
        return {
            "peak_rss_mb": rss_mb,
            "memory_budget_mb": self.budget.max_memory_mb,
            "within_memory_budget": rss_mb <= self.budget.max_memory_mb,
            "elapsed_sec": elapsed,
            "workers_used": workers_used,
            "files_scanned": self.files_scanned,
            "files_skipped_cap": self.files_skipped_cap,
            "bytes_read_kb": round(self.bytes_read / 1024.0, 2),
            "throttle_events": self.throttle_events,
            "warnings": list(self.warnings),
        }


# --- Streaming JSON / JSONL I/O ---

def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def iter_jsonl(path: Path) -> Iterator[Dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            yield json.loads(stripped)


def append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_jsonl_atomic(path: Path, records: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp_", suffix=".jsonl", dir=str(path.parent))
    count = 0
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                count += 1
        os.replace(tmp_name, path)
        return count
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
