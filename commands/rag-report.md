---
description: Phase 6 — 生成可直接浏览的交互式 HTML 分析页面与可下载的 Markdown (.md) 报告，或按需启动轻量 Web Studio。
---
请调用 `rag_eval_generate_report`（或 `python3 -m rag_eval report`）：
1. 生成独立的单文件可浏览页面 `report.html`（内置一键下载 `.md`、分类测试集浏览器、P0-P3 原因分级与用例复盘）及 `report.md`；
2. 将两份报告文件路径以可点击链接提供给用户；
3. 如用户需要在浏览器内手动触发单条/批量测试，告知其可运行 `python3 -m rag_eval serve --workdir .rag_eval --idle-timeout 1800` 启动低占用本地控制台。
