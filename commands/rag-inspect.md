---
description: Phase 1 (Understand-First) — 扫描并理解目标 RAG 系统架构、知识库与可观测性，识别阻断性未知项。
---
请使用 `rag-eval-suite` Skill（及 `rag_eval_inspect` / `python3 -m rag_eval inspect`）执行阶段 1：
1. 扫描目标 RAG 项目路径与知识库目录；
2. 总结已识别的技术栈、切片/检索/重排/生成链路与可观测性；
3. 明确列出所有阻断性未知项（Blocking Unknowns），在未获得用户确认前**严禁**跳过门禁直接测试；
4. 若系统或语料规模庞大，同步调用 `rag_eval_scope` 给出 `core` 精简核心方案建议。
