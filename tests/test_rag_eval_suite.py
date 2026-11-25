"""Comprehensive automated tests for RAG Eval Suite."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from rag_eval.dataset_gen import (  # noqa: E402
    export_dataset_markdown,
    filter_dataset,
    generate_test_dataset,
    save_dataset,
    summarize_dataset,
)
from rag_eval.inspector import (  # noqa: E402
    UnderstandingGateError,
    confirm_profile,
    inspect_rag_system,
    save_profile,
)
from rag_eval.mcp_server import TOOLS as MCP_TOOLS, handle_rpc_message  # noqa: E402
from rag_eval.methods import (  # noqa: E402
    aggregate_metrics,
    eval_answer_accuracy_case,
    eval_hallucination_case,
    eval_retrieval_case,
    wilson_ci,
)
from rag_eval.models import ObservabilityProfile, RAGProfile, RAGResponse, RetrievedChunk, TestCase  # noqa: E402
from rag_eval.reporter import generate_reports  # noqa: E402
from rag_eval.resource_guard import get_peak_rss_mb, write_jsonl_atomic  # noqa: E402
from rag_eval.runner import evaluate_manual_input, run_evaluation  # noqa: E402
from rag_eval.scope_pruner import plan_evaluation_scope, save_scope_plan  # noqa: E402
from rag_eval.server import create_studio_server  # noqa: E402


SAMPLE_KB = PLUGIN_ROOT / "examples" / "sample_kb"


class TestUnderstandFirstGate(unittest.TestCase):
    def test_gate_blocks_unconfirmed_generation(self) -> None:
        profile, _ = inspect_rag_system(project_path=str(PLUGIN_ROOT), kb_paths=[str(SAMPLE_KB)])
        self.assertFalse(profile.confirmed)
        self.assertTrue(len(profile.blocking_unknowns()) >= 1)
        plan = plan_evaluation_scope(profile, requested_mode="smoke")

        with self.assertRaises(UnderstandingGateError):
            generate_test_dataset(profile, plan)

        # Confirming without resolving adapter & observability blocking unknowns must still fail
        with self.assertRaises(UnderstandingGateError):
            confirm_profile(profile, confirmed_by="tester")

        # Resolve unknowns cleanly -> unlocks gate
        confirm_profile(
            profile,
            confirmed_by="tester",
            adapter_config={"type": "demo", "demo_mode": "realistic_flawed"},
            observability_overrides={"retrieved_docs": True, "citations": True},
        )
        self.assertTrue(profile.confirmed)
        self.assertEqual(len(profile.blocking_unknowns()), 0)
        cases, meta = generate_test_dataset(profile, plan)
        self.assertGreaterEqual(len(cases), 8)
        self.assertEqual(meta["gate_warnings"], [w for w in meta["gate_warnings"] if "UNVERIFIED" not in w])

    def test_kb_auto_discovery_fallback_on_project_root(self) -> None:
        # Pointing --project directly at a KB folder without --kb must auto-discover docs
        profile, _ = inspect_rag_system(project_path=str(SAMPLE_KB))
        self.assertEqual(profile.corpus_stats["readable_text_files"], 4)
        self.assertNotIn("UNK-KB-01", [u.id for u in profile.unknowns])
        auto_unk = [u for u in profile.unknowns if u.id == "UNK-KB-AUTO"]
        self.assertEqual(len(auto_unk), 1)
        self.assertFalse(auto_unk[0].blocking)


class TestScopePruner(unittest.TestCase):
    def test_complex_large_rag_recommends_core_pruning(self) -> None:
        fake_inventory = [
            {
                "path": f"/kb/dir_{i % 5}/doc_{i}.md",
                "name": f"doc_{i}.md",
                "ext": ".md",
                "size_bytes": 200_000,
                "has_table": i % 4 == 0,
                "has_dates": i % 3 == 0,
                "char_count": 5000,
            }
            for i in range(260)
        ]
        profile = RAGProfile(
            system_name="MegaEnterpriseRAG",
            project_path="/fake",
            kb_paths=["/kb"],
            detected_stack={"retrievers_and_stores": ["BM25 / Lexical", "Milvus", "GraphRAG / KG"]},
            pipeline_stages={
                "retrieval": {"hybrid_suspected": True},
                "reranking": {"enabled": True},
                "query_understanding": {"query_rewrite_or_hyde": True, "multi_kb_routing": True},
            },
            observability=ObservabilityProfile(retrieved_docs=True, citations=False),
            corpus_stats={
                "readable_text_files": 260,
                "total_size_mb": 52.0,
                "binary_files_count": 20,
                "file_inventory": fake_inventory,
            },
            confirmed=True,
        )
        plan = plan_evaluation_scope(profile, requested_mode="auto")
        self.assertEqual(plan.complexity_level, "extreme")
        self.assertEqual(plan.recommended_mode, "core")
        self.assertEqual(plan.profile_mode, "core")
        self.assertLessEqual(len(plan.selected_kb_files), 20)
        self.assertGreater(plan.deferred_kb_files_count, 200)
        self.assertIn("精简核心模式", plan.disclosure_statement)
        self.assertIn("暂缓", plan.disclosure_statement)


class TestMetricFormulasAndHallucination(unittest.TestCase):
    def test_retrieval_metrics_and_rerank_cutoff(self) -> None:
        tc = TestCase(
            id="TC-R1",
            category="multi_hop_reasoning",
            difficulty="hard",
            core_priority=1,
            query="Q",
            expected_answer="A",
            expected_doc_ids=["doc_a.md", "doc_b.md"],
        )
        resp = RAGResponse(
            case_id="TC-R1",
            answer="A",
            retrieved_chunks=[
                RetrievedChunk(doc_id="noise.md", text="x", rank=1),
                RetrievedChunk(doc_id="doc_a.md", text="y", rank=2),
                RetrievedChunk(doc_id="doc_b.md", text="z", rank=3),
            ],
            reranked_chunks=[
                RetrievedChunk(doc_id="noise.md", text="x", rank=1),
                RetrievedChunk(doc_id="doc_a.md", text="y", rank=2),
            ],
        )
        res = eval_retrieval_case(tc, resp)
        self.assertTrue(res["applicable"])
        self.assertEqual(res["hit_at_1"], 0.0)
        self.assertEqual(res["hit_at_3"], 1.0)
        self.assertEqual(res["mrr"], 0.5)
        self.assertEqual(res["recall_at_k"], 0.5)
        self.assertEqual(res["raw_recall_at_k"], 1.0)
        self.assertTrue(res["rerank_cutoff_loss"])
        self.assertGreaterEqual(res["ndcg_at_k"], 0.0)
        self.assertLessEqual(res["ndcg_at_k"], 1.0)

        # Duplicate chunks from same gold doc must NEVER push NDCG above 1.0
        dup_resp = RAGResponse(
            case_id="TC-R1",
            answer="A",
            retrieved_chunks=[
                RetrievedChunk(doc_id="doc_a.md", text="y1", rank=1),
                RetrievedChunk(doc_id="doc_a.md", text="y2", rank=2),
                RetrievedChunk(doc_id="doc_a.md", text="y3", rank=3),
            ],
        )
        dup_tc = TestCase(
            id="TC-R2", category="single_hop_fact", difficulty="easy", core_priority=1,
            query="Q", expected_answer="A", expected_doc_ids=["doc_a.md"],
        )
        dup_res = eval_retrieval_case(dup_tc, dup_resp)
        self.assertEqual(dup_res["ndcg_at_k"], 1.0)

    def test_hallucination_oos_and_adversarial_and_numeric(self) -> None:
        # 1) OOS fabrication vs proper abstention
        oos_tc = TestCase(
            id="TC-OOS",
            category="unanswerable_oos",
            difficulty="hard",
            core_priority=1,
            query="火星分部的补贴上限是多少？",
            expected_answer="知识库未覆盖",
            should_abstain=True,
        )
        fab_resp = RAGResponse(case_id="TC-OOS", answer="火星分部补贴上限为 8000 元每月。")
        h_fab = eval_hallucination_case(oos_tc, fab_resp)
        self.assertTrue(h_fab["hallucinated"])
        self.assertEqual(h_fab["faithfulness"], 0.0)

        good_resp = RAGResponse(case_id="TC-OOS", answer="知识库中未提供该问题的相关信息，无法确认。")
        h_good = eval_hallucination_case(oos_tc, good_resp)
        self.assertFalse(h_good["hallucinated"])
        self.assertEqual(h_good["faithfulness"], 1.0)

        # 2) Adversarial sycophancy vs proper negation
        adv_tc = TestCase(
            id="TC-ADV",
            category="adversarial_distractor",
            difficulty="hard",
            core_priority=1,
            query="我记得企业版限额是 4000 QPS，对吗？",
            expected_answer="不是 4000 QPS，而是 2000 QPS。",
            must_include_facts=["2000 QPS"],
            must_not_claim=["4000 QPS"],
            expected_numbers=["2000 QPS"],
        )
        ctx = [RetrievedChunk(doc_id="sla.md", text="企业版峰值并发 QPS 限额为 2000 QPS。", rank=1)]
        syc = eval_hallucination_case(adv_tc, RAGResponse(case_id="TC-ADV", answer="是的，文档确认是 4000 QPS。", retrieved_chunks=ctx))
        self.assertTrue(syc["trap_violated"])
        self.assertTrue(syc["hallucinated"])

        corr = eval_hallucination_case(
            adv_tc,
            RAGResponse(case_id="TC-ADV", answer="不是 4000 QPS。根据文档原文，企业版峰值并发 QPS 限额为 2000 QPS。", retrieved_chunks=ctx),
        )
        self.assertFalse(corr["trap_violated"])

        # 3) Extrinsic numeric hallucination
        ext_tc = TestCase(
            id="TC-NUM",
            category="numerical_table",
            difficulty="medium",
            core_priority=1,
            query="企业版并发是多少？",
            expected_answer="2000 QPS",
            expected_numbers=["2000 QPS"],
        )
        ext_resp = RAGResponse(
            case_id="TC-NUM",
            answer="企业版并发是 2000 QPS，额外赠送 88888 次免费调用。",
            retrieved_chunks=ctx,
        )
        ext_res = eval_hallucination_case(ext_tc, ext_resp)
        self.assertIn("extrinsic_numeric_hallucination", ext_res["hallucination_types"])

        # 4) Answer accuracy check
        acc = eval_answer_accuracy_case(
            adv_tc,
            RAGResponse(case_id="TC-ADV", answer="企业版峰值并发 QPS 限额为 2000 QPS。", retrieved_chunks=ctx),
        )
        self.assertTrue(acc["passed"])
        self.assertEqual(acc["numeric_accuracy"], 1.0)

        # 5) Wilson CI bounds
        ci = wilson_ci(8, 10)
        self.assertIsNotNone(ci)
        assert ci is not None
        self.assertLess(ci[0], 0.8)
        self.assertGreater(ci[1], 0.8)

    def test_small_sample_status_custom_thresholds_and_info_badges(self) -> None:
        # Construct 4 synthetic cases:
        # - context_recall = 0.74 vs target 0.90 (gap 0.16 < 0.20, N=4) -> low_sample_warn
        # - numeric_accuracy = 0.25 vs target 0.85 (gap 0.60 >= 0.20, N=4) -> hard fail kept!
        # - abstention_accuracy = 0.50 vs target 0.85 (gap 0.35 >= 0.20, N=4) -> hard fail kept!
        evals = []
        for i in range(4):
            evals.append(
                {
                    "case_id": f"TC-{i+1}",
                    "category": "numerical_table",
                    "method_scores": {
                        "retrieval_hit_rate": {
                            "applicable": True,
                            "hit_at_1": 1.0,
                            "hit_at_3": 1.0,
                            "hit_at_5": 1.0,
                            "hit_at_k": 1.0,
                            "recall_at_k": 1.0,
                            "mrr": 1.0,
                            "ndcg_at_k": 0.80,
                        },
                        "context_quality": {
                            "applicable": True,
                            "context_recall": 0.74,
                            "context_precision": 0.80,
                        },
                        "answer_accuracy": {
                            "applicable": True,
                            "score": 0.40,
                            "passed": False,
                            "numeric_accuracy": 0.25,
                        },
                        "abstention_guard": {
                            "applicable": True,
                            "bucket": "should_abstain",
                            "correct_abstention": i < 2,
                        },
                        "latency_efficiency": {
                            "applicable": True,
                            "latency_ms": 50.0,
                            "has_error": False,
                        },
                    },
                }
            )
        custom_thresholds = {
            "hit_at_k_min": 0.80,
            "recall_at_k_min": 0.75,
            "mrr_min": 0.65,
            "ndcg_at_k_min": 0.85,  # custom threshold above 0.80 -> warn
            "context_recall_min": 0.90,  # gap 0.16 (< 0.20) with N=4 -> low_sample_warn
            "context_precision_min": 0.60,
            "answer_accuracy_min": 0.78,
            "numeric_accuracy_min": 0.85,  # gap 0.60 (>= 0.20) -> hard fail preserved!
            "abstention_accuracy_min": 0.85,  # gap 0.35 (>= 0.20) -> hard fail preserved!
            "latency_p95_max_ms": 5000.0,
            "error_rate_max": 0.02,
            "min_stat_sample_size": 10.0,
            "low_sample_hard_fail_margin": 0.20,
        }
        metrics = aggregate_metrics(
            evals,
            ["retrieval_hit_rate", "context_quality", "answer_accuracy", "abstention_guard", "latency_efficiency"],
            {},
            custom_thresholds,
        )
        m_map = {m.metric_id: m for m in metrics}
        # No-target reference metrics must be `info`, never `pass`
        self.assertEqual(m_map["hit_at_1"].status, "info")
        self.assertEqual(m_map["hit_at_3"].status, "info")
        self.assertEqual(m_map["latency_p50_ms"].status, "info")
        # Custom thresholds honored
        self.assertEqual(m_map["context_recall"].target, 0.90)
        self.assertEqual(m_map["ndcg_at_k"].target, 0.85)
        self.assertEqual(m_map["ndcg_at_k"].status, "warn")
        # Moderate small-sample miss (<20% gap) downgrades to low_sample_warn
        self.assertEqual(m_map["context_recall"].status, "low_sample_warn")
        # Severe small-sample breaches (>=20% gap) MUST retain hard `fail` + caveat
        self.assertEqual(m_map["numeric_accuracy"].status, "fail")
        self.assertTrue(any("N=4" in c and "fail" in c for c in m_map["numeric_accuracy"].caveats))
        self.assertEqual(m_map["abstention_accuracy"].status, "fail")
        self.assertTrue(any("N=4" in c and "fail" in c for c in m_map["abstention_accuracy"].caveats))


class TestEndToEndPipelineAndStudio(unittest.TestCase):
    def test_full_pipeline_single_batch_offline_studio_and_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp) / ".rag_eval"

            # 1. Inspect + Confirm
            profile, _ = inspect_rag_system(
                project_path=str(PLUGIN_ROOT),
                kb_paths=[str(SAMPLE_KB)],
                system_name="EnterpriseDemoRAG",
                adapter_config={"type": "demo", "demo_mode": "realistic_flawed"},
                observability_overrides={"retrieved_docs": True, "citations": True, "reranked_docs": True},
            )
            confirm_profile(profile, confirmed_by="unittest")
            save_profile(workdir, profile)

            # 2. Scope + Categorized Dataset
            plan = plan_evaluation_scope(profile, requested_mode="full", max_cases=28)
            save_scope_plan(workdir, plan)
            cases, meta = generate_test_dataset(profile, plan)
            save_dataset(workdir, cases, meta)

            cov = summarize_dataset(cases)
            self.assertGreaterEqual(cov["active_categories"], 6)
            self.assertGreater(cov["abstain_cases"], 0)

            # Standalone dataset filtering & markdown export
            oos_only = filter_dataset(cases, category="unanswerable_oos")
            self.assertEqual(len(oos_only), cov["abstain_cases"])
            ds_md = export_dataset_markdown(cases)
            self.assertIn("RAG 分类测试数据集清单", ds_md)

            # 3. Single-case + Single-method execution
            single_res = run_evaluation(
                workdir=workdir,
                methods=["hallucination_audit"],
                case_ids=[cases[0].id],
                workers=1,
            )
            self.assertEqual(single_res["execution_mode"], "single_case")
            self.assertEqual(single_res["methods_executed"], ["hallucination_audit"])

            # 4. Full batch execution + P0-P3 diagnosis + HTML/MD reports
            batch_res = run_evaluation(workdir=workdir, methods=["all"], workers=2)
            self.assertEqual(batch_res["execution_mode"], "batch")
            # Verify Demo adapter propagated resource scanning stats into run result
            self.assertGreaterEqual(batch_res["resource_stats"]["files_scanned"], 4)
            self.assertGreater(batch_res["resource_stats"]["bytes_read_kb"], 0.0)
            self.assertGreaterEqual(batch_res["diagnosis"]["total_issues"], 1)
            self.assertTrue(
                batch_res["diagnosis"]["severity_counts"]["P0"] > 0
                or batch_res["diagnosis"]["severity_counts"]["P1"] > 0
            )
            html_path, md_path, run_id = generate_reports(workdir, run_id=batch_res["run_id"])
            html_text = html_path.read_text(encoding="utf-8")
            md_text = md_path.read_text(encoding="utf-8")

            # Verify historical report regeneration does NOT clobber latest_report.*
            generate_reports(workdir, run_id=single_res["run_id"])
            latest_md_text = (workdir / "reports" / "latest_report.md").read_text(encoding="utf-8")
            self.assertIn(batch_res["run_id"], latest_md_text)
            self.assertNotIn(single_res["run_id"], latest_md_text)
            # Explicit update_latest=True overrides latest_report.* cleanly
            generate_reports(workdir, run_id=single_res["run_id"], update_latest=True)
            self.assertIn(single_res["run_id"], (workdir / "reports" / "latest_report.md").read_text(encoding="utf-8"))
            # Restore latest_report.* to batch_res for subsequent Studio check
            generate_reports(workdir, run_id=batch_res["run_id"], update_latest=True)

            # Verify zero external CDN scripts/stylesheets and built-in MD download
            self.assertNotIn("https://cdn", html_text)
            self.assertIn("下载 Markdown 报告 (.md)", html_text)
            self.assertIn("RAG 系统回测与根因分析报告", md_text)
            self.assertIn("问题原因分级评价与解决方案", md_text)

            # 5. Offline manual responses snapshot evaluation
            offline_file = Path(tmp) / "manual_responses.jsonl"
            write_jsonl_atomic(
                offline_file,
                [
                    {
                        "case_id": cases[0].id,
                        "answer": cases[0].expected_answer,
                        "retrieved_chunks": [
                            {"doc_id": cases[0].expected_doc_ids[0], "text": cases[0].expected_answer, "score": 0.98}
                        ]
                        if cases[0].expected_doc_ids
                        else [],
                        "latency_ms": 120.0,
                    }
                ],
            )
            off_res = run_evaluation(
                workdir=workdir,
                case_ids=[cases[0].id],
                adapter_override={"type": "offline", "responses_file": str(offline_file)},
            )
            self.assertEqual(off_res["adapter_type"], "offline")
            self.assertTrue(off_res["case_evaluations"][0]["passed"])

            # 6. Observability honesty test (retrieved_docs=False skips Hit@K cleanly)
            profile.observability.retrieved_docs = False
            save_profile(workdir, profile)
            unobs_res = run_evaluation(
                workdir=workdir,
                case_ids=[cases[0].id],
                methods=["retrieval_hit_rate", "answer_accuracy"],
                adapter_override={"type": "offline", "responses_file": str(offline_file)},
            )
            self.assertIn("retrieval_hit_rate", unobs_res["methods_skipped"])
            hit_metric = next(m for m in unobs_res["metrics"] if m["metric_id"] == "hit_at_k")
            self.assertEqual(hit_metric["status"], "unobservable")

            # Restore observability for studio test
            profile.observability.retrieved_docs = True
            save_profile(workdir, profile)

            # 7. Lightweight Web Studio HTTP API test
            server = create_studio_server(workdir, host="127.0.0.1", port=0, idle_timeout_sec=0)
            port = server.server_address[1]
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as r:
                    health = json.loads(r.read().decode("utf-8"))
                    self.assertTrue(health["ok"])
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/report/download.md?run_id={run_id}", timeout=5) as r:
                    md_dl = r.read().decode("utf-8")
                    self.assertIn(run_id, md_dl)
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/dataset?category=unanswerable_oos", timeout=5) as r:
                    ds_api = json.loads(r.read().decode("utf-8"))
                    self.assertEqual(ds_api["filtered_count"], len(oos_only))

                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/manual-eval",
                    data=json.dumps(
                        {
                            "query": "一线城市住宿上限是多少？",
                            "answer": "一线城市住宿上限为 650 元/晚。",
                            "expected_answer": "650 元",
                            "retrieved_chunks": [{"doc_id": "finance.md", "text": "一线城市住宿上限 650 元/晚"}],
                            "must_include_facts": ["650 元"],
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as r:
                    me = json.loads(r.read().decode("utf-8"))
                    self.assertTrue(me["case_evaluation"]["passed"])
            finally:
                server.shutdown()
                server.server_close()

            # 8. Stdio MCP JSON-RPC 2.0 protocol test
            init_resp = handle_rpc_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}})
            self.assertIsNotNone(init_resp)
            assert init_resp is not None
            self.assertEqual(init_resp["result"]["serverInfo"]["name"], "rag-eval-suite")

            tools_resp = handle_rpc_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            assert tools_resp is not None
            self.assertEqual(len(tools_resp["result"]["tools"]), 10)

            manual_rpc = handle_rpc_message(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "rag_eval_manual_eval",
                        "arguments": {
                            "query": "RTO 是多久？",
                            "answer": "企业版 RTO 为 5 分钟。",
                            "expected_answer": "5 分钟",
                            "retrieved_chunks": [{"doc_id": "sla.md", "text": "企业版故障恢复 RTO 为 5 分钟。"}],
                            "must_include_facts": ["5 分钟"],
                        },
                    },
                }
            )
            assert manual_rpc is not None
            self.assertFalse(manual_rpc["result"]["isError"])

            # 9. Verify low memory footprint (< 150 MB RSS)
            rss_mb = get_peak_rss_mb()
            self.assertLess(rss_mb, 150.0, f"Unexpectedly high RSS: {rss_mb} MB")

    def test_manual_eval_helper(self) -> None:
        res = evaluate_manual_input(
            query="错误码 ERR-AUTH-4012 是什么意思？",
            answer="ERR-AUTH-4012 表示 JWT 签名校验失败或令牌超过 30 分钟有效期。",
            expected_answer="JWT 签名校验失败或令牌超过 30 分钟有效期",
            retrieved_chunks=[{"doc_id": "sec.md", "text": "ERR-AUTH-4012：表示 JWT 签名校验失败或令牌已超过 30 分钟有效期。"}],
            must_include_facts=["ERR-AUTH-4012", "30 分钟"],
        )
        self.assertTrue(res["case_evaluation"]["passed"])


class TestMultiPlatformManifests(unittest.TestCase):
    def test_installer_verify_and_dsh_esm(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(PLUGIN_ROOT / "scripts" / "install.py"), "--verify"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

        # Validate Antigravity plugin.json only uses official top-level fields
        allowed = {"name", "displayName", "version", "description", "logo", "suggestedPrompts", "author", "homepage", "repository", "disabled"}
        pj = json.loads((PLUGIN_ROOT / "plugin.json").read_text(encoding="utf-8"))
        self.assertTrue(set(pj.keys()).issubset(allowed), f"Unexpected fields in plugin.json: {set(pj.keys()) - allowed}")

        # Validate DSH bundle manifest compliance (Loader insert dialect + exports + locale + icon)
        patch_text = (PLUGIN_ROOT / "cordis.patch.yml").read_text(encoding="utf-8")
        self.assertIn("- insert:", patch_text)
        self.assertIn("id: rag-eval-suite", patch_text)
        self.assertNotIn("target: plugin-loader", patch_text)

        pkg = json.loads((PLUGIN_ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(pkg.get("icon"), "./assets/logo.svg")
        self.assertIn(".", pkg.get("exports", {}))
        self.assertIn("./icon", pkg.get("exports", {}))
        self.assertIn("./locale/*", pkg.get("exports", {}))
        for loc in ("en.json", "zh.json"):
            loc_data = json.loads((PLUGIN_ROOT / "locale" / loc).read_text(encoding="utf-8"))
            self.assertIn("name", loc_data["meta"])
            self.assertEqual(len(loc_data["tools"]), 10)

        # Validate DSH Cordis ESM syntax, strict schema.ts contract (explicit boolean additionalProperties
        # on every object schema!), real defineTool when available, and p.case_ids string/array paths
        expected_mcp_names = sorted(t["name"] for t in MCP_TOOLS)
        node_script = f"""
import * as mod from '{PLUGIN_ROOT / 'dsh' / 'index.mjs'}';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

if (mod.name !== 'rag-eval-suite' || typeof mod.apply !== 'function') process.exit(1);
if (!Array.isArray(mod.inject) || !mod.inject.includes('tools')) process.exit(2);

const specs = mod.buildToolSpecs();
const dshNames = specs.map(s => s.name).sort();
const mcpNames = {json.dumps(expected_mcp_names)};
if (JSON.stringify(dshNames) !== JSON.stringify(mcpNames)) {{
  console.error('MISMATCH', dshNames, mcpNames);
  process.exit(3);
}}

// Strict replica of @deepseek-ai/dsh-tools schema.ts authorError checks + real defineTool when resolvable
function assertValidDshSchema(schema, label) {{
  if (!schema || typeof schema !== 'object') {{
    throw new Error(`${{label}}: schema must be an object`);
  }}
  const allowedTypes = new Set(['string', 'number', 'integer', 'boolean', 'object', 'array']);
  if (!allowedTypes.has(schema.type)) {{
    throw new Error(`${{label}}: invalid or missing schema.type=${{schema.type}}`);
  }}
  if (schema.type === 'object') {{
    if (typeof schema.additionalProperties !== 'boolean') {{
      throw new Error(`${{label}}.additionalProperties must be explicitly true or false`);
    }}
    if (schema.properties && typeof schema.properties === 'object') {{
      for (const [k, v] of Object.entries(schema.properties)) {{
        assertValidDshSchema(v, `${{label}}.properties.${{k}}`);
      }}
    }}
  }}
  if (schema.type === 'array') {{
    if (!schema.items || typeof schema.items !== 'object') {{
      throw new Error(`${{label}}.items is required for array schema`);
    }}
    assertValidDshSchema(schema.items, `${{label}}.items`);
  }}
}}

let realDefineTool = null;
const customEntry = process.env.DSH_TOOLS_ENTRY;
try {{
  const dshMod = customEntry ? await import(customEntry) : await import('@deepseek-ai/dsh-tools');
  if (typeof dshMod?.defineTool === 'function') realDefineTool = dshMod.defineTool;
}} catch {{
  // Fallback to strict schema.ts contract validator when running outside DSH runtime
}}

function assertValueMatchesSchema(val, schema, label) {{
  if (schema.type === 'object') {{
    if (val === null || typeof val !== 'object' || Array.isArray(val)) {{
      throw new Error(`tool returned invalid output at ${{label}}: "value" must be an object`);
    }}
  }} else if (schema.type === 'array') {{
    if (!Array.isArray(val)) {{
      throw new Error(`tool returned invalid output at ${{label}}: "value" must be an array`);
    }}
  }} else if (schema.type === 'string' && typeof val !== 'string') {{
    throw new Error(`tool returned invalid output at ${{label}}: "value" must be a string`);
  }}
}}

function strictDefineTool(spec) {{
  if (spec.parameters?.type === 'object' && spec.parameters?.properties) {{
    throw new Error(`${{spec.name}}: parameters must use property-map DSL, not root JSON Schema`);
  }}
  for (const [k, propSchema] of Object.entries(spec.parameters || {{}})) {{
    assertValidDshSchema(propSchema, `parameters.${{k}}`);
  }}
  if (!spec.output?.schema || typeof spec.output?.render !== 'function') {{
    throw new Error(`${{spec.name}}: missing output.schema or output.render`);
  }}
  assertValidDshSchema(spec.output.schema, 'output.schema');
  const rendered = spec.output.render({{ dummyArg: 1 }}, {{ ok: true, sentinel: 42 }});
  if (!Array.isArray(rendered) || !rendered[0].text.includes('42')) {{
    throw new Error(`${{spec.name}}: bad output.render(args, value) signature`);
  }}
  if (realDefineTool) return realDefineTool(spec);
  return {{
    ...spec,
    async execute(args, execCtx) {{
      const value = await spec.execute(args, execCtx);
      assertValueMatchesSchema(value, spec.output.schema, spec.name);
      const blocks = spec.output.render(args, value);
      if (!Array.isArray(blocks) || blocks.length === 0) {{
        throw new Error(`${{spec.name}}: output.render returned invalid content blocks`);
      }}
      return value;
    }},
  }};
}}

// Negative self-checks:
// 1) missing additionalProperties at definition time
let caughtMissingAddProps = false;
try {{
  strictDefineTool({{
    name: 'bad_tool',
    description: 'bad',
    parameters: {{ broken: {{ type: 'object', description: 'missing additionalProperties' }} }},
    output: {{ schema: {{ type: 'object', additionalProperties: true }}, render: (_a, v) => [{{ type: 'text', text: String(v) }}] }},
    execute: async () => ({{}}),
  }});
}} catch (e) {{
  if (String(e.message).includes('additionalProperties must be explicitly true or false')) {{
    caughtMissingAddProps = true;
  }}
}}
if (!caughtMissingAddProps) process.exit(17);

// 2) returning top-level array when output.schema is object at execution time!
let caughtArrayOutput = false;
try {{
  const badArrTool = strictDefineTool({{
    name: 'bad_arr_tool',
    description: 'bad array return',
    parameters: {{}},
    output: {{ schema: {{ type: 'object', additionalProperties: true }}, render: (_a, v) => [{{ type: 'text', text: JSON.stringify(v) }}] }},
    execute: async () => ([1, 2, 3]),
  }});
  await badArrTool.execute({{}});
}} catch (e) {{
  if (String(e.message).includes('"value" must be an object')) {{
    caughtArrayOutput = true;
  }}
}}
if (!caughtArrayOutput) process.exit(19);

// Compile all 10 plugin specs through strictDefineTool (and real defineTool when present) via mod.apply()
const registeredTools = [];
const mockCtx = {{
  tools: {{ register(t) {{ registeredTools.push(t); return () => {{}}; }} }},
  effect(fn) {{ fn(); }},
}};
mod.apply(mockCtx, {{ defineTool: strictDefineTool }});
if (registeredTools.length !== 10) {{
  console.error('REGISTERED_COUNT', registeredTools.length);
  process.exit(4);
}}

// Test non-silent warning when tools service is absent
const warns = [];
mod.apply({{ logger: {{ warn: (m) => warns.push(m) }} }});
if (warns.length === 0) process.exit(5);

// Execute ALL 10/10 registered (wrapped!) tools end-to-end so every runtime output is schema-checked!
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-rag-'));
const wd = path.join(tmp, '.rag_eval');
const byName = Object.fromEntries(registeredTools.map(t => [t.name, t]));

// (1) rag_eval_inspect
const insp = await byName.rag_eval_inspect.execute({{ project_path: '{SAMPLE_KB}', workdir: wd }});
if (insp.corpus_summary.readable_text_files !== 4) process.exit(6);
const blocked = await byName.rag_eval_generate_dataset.execute({{ workdir: wd }});
if (blocked.error !== 'UNDERSTANDING_GATE_BLOCKED') process.exit(7);

// (2) rag_eval_confirm
const conf = await byName.rag_eval_confirm.execute({{
  workdir: wd,
  adapter: 'demo',
  observability_overrides: {{ retrieved_docs: true, citations: true }},
}});
if (!conf.confirmed) process.exit(8);

// (3) rag_eval_scope
const sc = await byName.rag_eval_scope.execute({{ workdir: wd, profile: 'smoke', max_cases: 8 }});
if (!sc.scope_plan || sc.scope_plan.profile_mode !== 'smoke') process.exit(20);

// (4) rag_eval_generate_dataset
const gen = await byName.rag_eval_generate_dataset.execute({{ workdir: wd, scope: 'smoke', max_cases: 8 }});
if (!gen.dataset_path) process.exit(13);

// (5) rag_eval_view_dataset (including export_markdown parity)
const view = await byName.rag_eval_view_dataset.execute({{ workdir: wd, core_only: true, export_markdown: true }});
if (!Array.isArray(view.cases) || typeof view.markdown !== 'string') process.exit(21);

// (6) rag_eval_list_methods (the exact regression target!)
const methodsRes = await byName.rag_eval_list_methods.execute({{}});
if (!Array.isArray(methodsRes.methods) || methodsRes.methods.length !== 7) process.exit(22);

// (7) rag_eval_run (summary + array case_ids + string case_ids)
const runSummary = await byName.rag_eval_run.execute({{ workdir: wd, methods: ['all'] }});
if (!runSummary.diagnosis || !Array.isArray(runSummary.diagnosis.issues) || runSummary.case_evaluations) process.exit(14);
const runDetailArr = await byName.rag_eval_run.execute({{ workdir: wd, case_ids: ['TC-001'], include_cases: true }});
if (!Array.isArray(runDetailArr.case_evaluations) || runDetailArr.case_evaluations.length !== 1) process.exit(15);
const runDetailStr = await byName.rag_eval_run.execute({{ workdir: wd, case_ids: 'TC-001', include_cases: true }});
if (!Array.isArray(runDetailStr.case_evaluations) || runDetailStr.case_evaluations.length !== 1) process.exit(18);

// (8) rag_eval_generate_report
const rep = await byName.rag_eval_generate_report.execute({{ workdir: wd, run_id: runSummary.run_id }});
if (!rep.html_report || !rep.markdown_report) process.exit(23);

// (9) rag_eval_status
const st = await byName.rag_eval_status.execute({{ workdir: wd }});
if (!st.confirmed) process.exit(9);

// (10) rag_eval_manual_eval
const man = await byName.rag_eval_manual_eval.execute({{
  query: 'RTO?',
  answer: 'RTO is 5 minutes.',
  expected_answer: '5 minutes',
  retrieved_chunks: ['RTO is 5 minutes.'],
}});
if (!man.case_evaluation?.passed) process.exit(16);
fs.rmSync(tmp, {{ recursive: true, force: true }});
console.log('DSH_PARITY_OK', registeredTools.length, 'all10Executed=true', 'realDefineTool=' + Boolean(realDefineTool));
"""
        node_check = subprocess.run(
            ["node", "--input-type=module", "-e", node_script],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(node_check.returncode, 0, node_check.stdout + node_check.stderr)
        self.assertIn("DSH_PARITY_OK 10", node_check.stdout)

        # Explicitly verify CLI `status --json` & `manual-eval --json` accept --json (rc=0)
        cli_status = subprocess.run(
            [sys.executable, "-m", "rag_eval", "status", "--workdir", str(PLUGIN_ROOT / ".rag_eval_nonexistent"), "--json"],
            cwd=str(PLUGIN_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(cli_status.returncode, 0, cli_status.stderr)
        self.assertFalse(json.loads(cli_status.stdout)["profile_exists"])

        cli_manual = subprocess.run(
            [
                sys.executable, "-m", "rag_eval", "manual-eval", "--json",
                "--query", "RTO?", "--answer", "5 分钟", "--expected", "5 分钟", "--chunk", "RTO 5 分钟",
            ],
            cwd=str(PLUGIN_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(cli_manual.returncode, 0, cli_manual.stderr)
        self.assertTrue(json.loads(cli_manual.stdout)["case_evaluation"]["passed"])


if __name__ == "__main__":
    unittest.main()
