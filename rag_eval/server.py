"""Ultra-lightweight local Web Studio server (pure Python stdlib, <30 MB RAM).

Provides live interactive browsing of categorized test datasets, single/batch/manual
test execution, real-time report viewing, and one-click Markdown report downloads.
Supports automatic idle shutdown (`--idle-timeout`) so it never lingers consuming RAM.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from .dataset_gen import export_dataset_markdown, filter_dataset, load_dataset, summarize_dataset
from .reporter import build_studio_payload, generate_reports, render_markdown_report
from .resource_guard import get_peak_rss_mb
from .runner import evaluate_manual_input, list_runs, load_run_result, run_evaluation
from .web.studio_template import render_studio_html


def create_studio_server(
    workdir: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    idle_timeout_sec: int = 0,
) -> ThreadingHTTPServer:
    """Create a configured lightweight HTTP server for RAG Eval Studio."""
    last_activity = [time.monotonic()]
    run_lock = threading.Lock()

    class StudioHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            # Keep stdout/stderr quiet unless debugging
            pass

        def _touch(self) -> None:
            last_activity[0] = time.monotonic()

        def _send_bytes(self, code: int, body: bytes, content_type: str, extra_headers: Optional[Dict[str, str]] = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if extra_headers:
                for k, v in extra_headers.items():
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, code: int, obj: Any) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self._send_bytes(code, body, "application/json; charset=utf-8")

        def _read_json_body(self) -> Dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                return {}
            if length > 4 * 1024 * 1024:
                raise ValueError("Request body exceeds 4MB safety limit")
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}

        def do_GET(self) -> None:
            self._touch()
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)

            try:
                if path in {"/", "/index.html"}:
                    run_id = qs.get("run_id", [None])[0]
                    payload = build_studio_payload(workdir, run_id=run_id)
                    html = render_studio_html(payload, live_mode=True)
                    self._send_bytes(200, html.encode("utf-8"), "text/html; charset=utf-8")
                    return

                if path == "/api/health":
                    self._send_json(200, {"ok": True, "rss_mb": get_peak_rss_mb(), "workdir": str(workdir)})
                    return

                if path == "/api/state":
                    run_id = qs.get("run_id", [None])[0]
                    payload = build_studio_payload(workdir, run_id=run_id)
                    payload["runs_history"] = list_runs(workdir)
                    payload["server_rss_mb"] = get_peak_rss_mb()
                    self._send_json(200, payload)
                    return

                if path == "/api/dataset":
                    cases, _ = load_dataset(workdir)
                    cat = qs.get("category", [None])[0]
                    diff = qs.get("difficulty", [None])[0]
                    core_only = qs.get("core_only", ["false"])[0].lower() == "true"
                    search = qs.get("search", [None])[0]
                    filtered = filter_dataset(cases, category=cat, difficulty=diff, core_only=core_only, search=search)
                    self._send_json(
                        200,
                        {
                            "coverage": summarize_dataset(cases),
                            "filtered_count": len(filtered),
                            "cases": [c.to_dict() for c in filtered],
                        },
                    )
                    return

                if path == "/api/report/download.md":
                    run_id = qs.get("run_id", [None])[0]
                    run_res = load_run_result(workdir, run_id=run_id)
                    md = render_markdown_report(run_res)
                    fname = f"{run_res.get('run_id', 'rag_eval')}_report.md"
                    self._send_bytes(
                        200,
                        md.encode("utf-8"),
                        "text/markdown; charset=utf-8",
                        {"Content-Disposition": f'attachment; filename="{fname}"'},
                    )
                    return

                if path == "/api/dataset/download.md":
                    cases, _ = load_dataset(workdir)
                    md = export_dataset_markdown(cases)
                    self._send_bytes(
                        200,
                        md.encode("utf-8"),
                        "text/markdown; charset=utf-8",
                        {"Content-Disposition": 'attachment; filename="rag_test_dataset.md"'},
                    )
                    return

                self._send_json(404, {"error": f"Not found: {path}"})
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})

        def do_POST(self) -> None:
            self._touch()
            parsed = urlparse(self.path)
            path = parsed.path

            try:
                body = self._read_json_body()

                if path == "/api/run":
                    if not run_lock.acquire(blocking=False):
                        self._send_json(409, {"error": "已有评测任务正在运行，请等待当前任务完成以避免高 CPU/内存竞争。"})
                        return
                    try:
                        res = run_evaluation(
                            workdir=workdir,
                            methods=body.get("methods"),
                            case_ids=body.get("case_ids"),
                            categories=body.get("categories"),
                            core_only=bool(body.get("core_only", False)),
                            workers=body.get("workers"),
                            use_llm_judge=bool(body.get("use_llm_judge", False)),
                        )
                        generate_reports(workdir, run_id=res["run_id"])
                        state = build_studio_payload(workdir, run_id=res["run_id"])
                        self._send_json(200, {"run_id": res["run_id"], "state": state})
                    finally:
                        run_lock.release()
                    return

                if path == "/api/manual-eval":
                    res = evaluate_manual_input(
                        query=str(body.get("query", "")),
                        answer=str(body.get("answer", "")),
                        expected_answer=str(body.get("expected_answer", "")),
                        retrieved_chunks=body.get("retrieved_chunks") or [],
                        expected_doc_ids=body.get("expected_doc_ids") or [],
                        must_include_facts=body.get("must_include_facts") or [],
                        must_not_claim=body.get("must_not_claim") or [],
                        expected_numbers=body.get("expected_numbers") or [],
                        should_abstain=bool(body.get("should_abstain", False)),
                        citations=body.get("citations") or [],
                        methods=body.get("methods"),
                    )
                    self._send_json(200, res)
                    return

                self._send_json(404, {"error": f"Not found: {path}"})
            except Exception as exc:
                self._send_json(400, {"error": str(exc)})

    server = ThreadingHTTPServer((host, port), StudioHandler)
    server.daemon_threads = True

    if idle_timeout_sec > 0:
        def _watchdog() -> None:
            while True:
                time.sleep(min(5, max(1, idle_timeout_sec // 4)))
                if time.monotonic() - last_activity[0] >= idle_timeout_sec:
                    server.shutdown()
                    break

        t = threading.Thread(target=_watchdog, daemon=True)
        t.start()

    return server


def serve_studio(
    workdir: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    idle_timeout_sec: int = 1800,
) -> None:
    server = create_studio_server(workdir, host=host, port=port, idle_timeout_sec=idle_timeout_sec)
    actual_port = server.server_address[1]
    print(f"RAG Eval Studio 已启动: http://{host}:{actual_port}/")
    print(f"当前工作目录: {workdir.resolve()} | 初始峰值内存: {get_peak_rss_mb():.1f} MB | 空闲超时: {idle_timeout_sec}s")
    print("按 Ctrl+C 停止服务。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
