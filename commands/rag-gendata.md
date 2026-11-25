---
description: Phase 3 — 基于已确认的 RAG 档案生成 10 分类测试数据集，并按分类独立展示测试数据与覆盖度检查。
---
请确认理解档案已通过门禁后，调用 `rag_eval_generate_dataset` 与 `rag_eval_view_dataset`（或 `python3 -m rag_eval generate` + `dataset summary/list`）：
1. 生成覆盖全面、贴合真实知识库的分类测试集（含单跳事实、多跳推理、数值表格、版本时效、知识盲区拒答、专有名词、对抗诱导等）；
2. 独立按分类展示测试数据分布、代表性用例和覆盖度平衡警告；
3. 告知用户如何单独筛选查看、手工补充或导出 Markdown 测试集清单。
