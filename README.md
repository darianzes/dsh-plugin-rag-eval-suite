# RAG Eval Suite (`rag-eval-suite`)

[![CI](https://github.com/darianzes/dsh-plugin-rag-eval-suite/actions/workflows/ci.yml/badge.svg)](https://github.com/darianzes/dsh-plugin-rag-eval-suite/actions/workflows/ci.yml)
[![npm](https://img.shields.io/npm/v/dsh-plugin-rag-eval-suite.svg)](https://www.npmjs.com/package/dsh-plugin-rag-eval-suite)
[![license: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

> **理解先行 · 指标严谨 · 低资源占用 · 跨平台适配**  
> 专为 RAG 系统打造的测试数据生成、模块化测试方法执行（单独/批量/手动）、核心指标回测（命中率、准确率、幻觉率、拒答率、延迟等）及 P0-P3 根因分级诊断插件。

---

## 核心特性

1. **强制「理解先行」门禁（Understand-First Gate）**
   - 严禁在不理解目标 RAG 系统的情况下盲目生成数据或执行测试。
   - 第一阶段自动扫描目标代码库与知识库语料（不传 `--kb` 时按常见子目录自动探测，未命中则安全回退检测项目根目录并提示确认），生成 `rag_profile.json` 并列出阻断性未知项（Blocking Unknowns）；显式 `confirm` 后才解锁测试。
   - **可观测诚实**：若目标 RAG 不返回召回片段（`retrieved_docs=false`），严格将 `Hit@K / Recall@K / MRR` 标记为 `unobservable`，绝不编造分数。
2. **大范围/复杂 RAG 自动精简核心模式（Core Scope Pruning）**
   - 当语料文档过多或 RAG 架构复杂（混合检索、重排、多知识库路由、GraphRAG 等）时，自动评估复杂度并提供 `smoke` / `core` / `full` 三档范围。
   - 通过分层抽样精选高信息密度核心文档与核心分类，并在报告中**明确披露「已测核心范围 vs 暂缓范围」**。
3. **测试数据全面覆盖、独立分类查看（实体锚定 + 可选 LLM 润色）**
   - 覆盖 10 大维度：`single_hop_fact`、`multi_hop_reasoning`、`numerical_table`、`temporal_version`、`unanswerable_oos`、`terminology_jargon`、`adversarial_distractor`、`conflicting_ambiguous`、`long_context_synthesis`、`robustness_boundary`。
   - 默认采用**纯确定性实体锚定生成器**（零外传、零费用），也支持可选 `--llm-gen`（`use_llm_gen=true`）将问句润色为更自然的业务提问，同时锁定语料真值。
   - 支持 CLI、MCP、DSH 及网页端独立按分类/难度/核心标签筛选查看、手动新增及导出 Markdown 清单。
4. **测试方法单独 + 批量 + 手动执行**
   - 7 大解耦测试方法：`retrieval_hit_rate`、`context_quality`、`answer_accuracy`、`hallucination_audit`、`abstention_guard`、`citation_check`、`latency_efficiency`。
   - 支持单方法、单条用例、指定分类、核心集、全量批量、离线手动采集快照（`--adapter offline`）、免配置「手动单条评测演练台（`manual-eval`）」及可选 `--llm-judge` 混合复核。
5. **严谨的指标状态机与 P0-P3 原因分级方案**
   - 7 种清晰状态：`pass` / `warn` / `low_sample_warn` / `fail` / `info`（无阈值参考指标如 `Hit@1`、`Hit@3`、`P50`）/ `unobservable` / `insufficient_data`。
   - **小样本防误判与硬伤底线双重保护**：样本量低于 `min_stat_sample_size`（默认 10）或 Wilson 95% CI 覆盖目标线时，一般边界偏差降级为 `low_sample_warn`；但若**严重偏离目标阈值 ≥ 20%**（可配置 `low_sample_hard_fail_margin`，如 50% 盲区未拒答 vs 85% 目标），仍保留 `fail` 硬阻断并附加小样本备注，防止高危缺陷被掩盖。
   - 按 6 层 RAG 链路解耦归因并输出 **P0-P3 原因分级 + 短期止血（Quick Wins）+ 架构修复（Structural Fixes）+ 回归验证命令**。
6. **直接可浏览交互报告页 + 一键下载 Markdown (`.md`)**
   - 自动生成零外链 CDN 依赖的单文件 `report.html`（内置浅/深色切换、分类测试集浏览器、P0-P3 筛选、声明级幻觉高亮与**一键下载 `.md` 按钮**）及 `report.md`。
   - 重导出历史 `run_id` 时默认**不覆写** `latest_report.*`（如需覆写可显式传 `--update-latest`）。
7. **极低 CPU 与内存占用**
   - 核心引擎、Web Studio 与 Stdio MCP Server **100% 基于 Python 3 标准库实现**，峰值内存通常 **~30–40 MB RSS**，默认并发 `workers <= 2`，Web Studio 支持 `--idle-timeout` 自动退出，静态 HTML 后台占用 **0 MB**。

---

## 隐私合规与测量效度说明

> [!IMPORTANT]
> - **外部 API 数据出境提示（默认关闭）**：默认运行模式为 100% 本地离线确定性执行，不会向任何外部服务发送数据。**仅当用户显式开启 `--llm-gen` 或 `--llm-judge` 时**，`--llm-gen` 会将待润色问句与对应语料证据的前 160 字发送至配置的 LLM API（DeepSeek / OpenAI 兼容接口）；`--llm-judge` 会将问答与召回片段发送至裁判模型。**内网/涉密/合规语料请勿开启这两个开关**，或改用本地内网兼容端点（`RAG_EVAL_JUDGE_BASE_URL`）。
> - **跨系统对比的测量效度提醒**：自动生成的 `must_include_facts` 抽取自语料参考句，非常适合**同一 RAG 系统的纵向版本回归**；但在做**跨系统/跨模型横向评测**时，纯抽取式复述可能产生自洽分数膨胀，建议叠加 `--llm-judge` 或使用人工独立编写的标准参考答案。

---

## 跨平台适配矩阵（10 个工具 1:1 统一命名）

| 平台 | 适配文件 | 安装 / 启用方式 |
| :--- | :--- | :--- |
| **Antigravity** | `plugin.json`, `mcp_config.json`, `rules/AGENTS.md`, `skills/rag-eval-suite/SKILL.md` | `python3 scripts/install.py --target antigravity` |
| **Claude Code** | `.claude-plugin/plugin.json`, `.mcp.json`, `skills/`, `commands/*.md` | `claude --plugin-dir /path/to/DeepseekRagTestPlugin` 或 `python3 scripts/install.py --target claude` |
| **OpenAI Codex** | `.codex-plugin/plugin.json`, `mcp.json`, `.agents/skills/`, `AGENTS.md` | `python3 scripts/install.py --target codex` |
| **DeepSeek Harness (`dsh`)** | `package.json`（含 `exports`/`icon`/`locale/*`）, `cordis.patch.yml`（Loader `insert:` 方言）, `dsh/index.mjs`（`@deepseek-ai/dsh-tools` `defineTool` + 属性映射 DSL + 显式 `additionalProperties` + 10 个同名工具）, `.agents/skills/` | DSH `plugin_manager` (`install_bundle`) 安装本目录（推荐），或开发调试 `npx @deepseek-ai/dsh --patch ./cordis.patch.yml`，或 `python3 scripts/install.py --target dsh` |

统一工具清单（MCP 与 DSH Cordis 1:1 同名）：
`rag_eval_inspect` · `rag_eval_confirm` · `rag_eval_scope` · `rag_eval_generate_dataset` · `rag_eval_view_dataset` · `rag_eval_list_methods` · `rag_eval_run` · `rag_eval_manual_eval` · `rag_eval_generate_report` · `rag_eval_status`

### 发布/安装（npm tarball）

包名 `dsh-plugin-rag-eval-suite`（版本见 `package.json`），`npm pack` 产物自带全部跨平台清单、`examples/sample_kb`、`mcp.json` / `.mcp.json` / `mcp_config.json`、`LICENSE` 与 `CHANGELOG.md`：

```bash
npm pack                                  # 产出 dsh-plugin-rag-eval-suite-<version>.tgz
# DSH：走 plugin_manager，实体安装（非 symlink）
#   install_bundle: file:/abs/path/dsh-plugin-rag-eval-suite-<version>.tgz
python3 scripts/install.py --verify       # 校验 16 项跨平台清单
python3 -m rag_eval demo                  # 包内示例知识库一键演示
```

`mcp_config.json` 在包内是**可搬迁**的相对路径版本；`scripts/install.py --target antigravity` 会在复制安装时把它本地化为绝对路径（symlink 安装不改动源目录）。

---

## 快速上手（CLI 手动执行）

### 1. 一键端到端演示（使用内置企业示例知识库）
```bash
python3 -m rag_eval demo --workdir .rag_eval_demo
```

### 2. 对真实 RAG 项目执行标准 6 步流程
```bash
# Step 1: 理解先行 — 扫描目标 RAG 与知识库
python3 -m rag_eval inspect --project /path/to/your-rag --kb /path/to/your-kb

# Step 2: 确认架构、接口适配器与可观测性（解除理解门禁）
python3 -m rag_eval confirm \
  --adapter http \
  --endpoint http://127.0.0.1:8000/api/rag/query \
  --obs-retrieved true \
  --obs-citations true

# Step 3: 复杂度评估与核心范围精简（大系统自动推荐 core）
python3 -m rag_eval scope --profile auto

# Step 4: 生成分类测试数据集并单独查看（可选 --llm-gen 润色问句，注意外传提示）
python3 -m rag_eval generate
python3 -m rag_eval dataset summary
python3 -m rag_eval dataset list --category unanswerable_oos
python3 -m rag_eval dataset export -o rag_test_dataset.md

# Step 5: 单独 / 批量 / 离线手动执行测试方法（--json 默认含完整诊断，加 --detail 含逐条用例明细）
python3 -m rag_eval methods
python3 -m rag_eval run --methods retrieval_hit_rate --json
python3 -m rag_eval run --case-id TC-001 --json --detail
python3 -m rag_eval run --core-only --methods all --workers 2
python3 -m rag_eval run --adapter offline --responses outputs.jsonl
python3 -m rag_eval status --json

# Step 6: 导出报告或启动轻量 Web Studio
python3 -m rag_eval report --run-id <historical_run_id>           # 默认不覆写 latest_report.*
python3 -m rag_eval report --run-id <run_id> --update-latest      # 显式更新 latest_report.*
python3 -m rag_eval serve --workdir .rag_eval --port 8765 --idle-timeout 1800
```

### 3. 免配置手动单条问答评测
```bash
python3 -m rag_eval manual-eval --json \
  --query "企业版 SLA-GOLD 的峰值并发 QPS 限额是多少？" \
  --chunk "企业版 (SLA-GOLD) 月度可用性 99.99%，峰值并发 QPS 限额 2000 QPS，RTO 5 分钟。" \
  --answer "根据文档，企业版 SLA-GOLD 的峰值并发限额是 2000 QPS。" \
  --expected "2000 QPS" \
  --facts "2000 QPS"
```

---

## 目录结构

```text
DeepseekRagTestPlugin/
├── plugin.json                 # Antigravity / Open Agent Plugins 清单
├── mcp_config.json             # Antigravity MCP 配置
├── .claude-plugin/plugin.json  # Claude Code 插件清单
├── .mcp.json                   # Claude Code MCP 配置
├── .codex-plugin/plugin.json   # Codex 兼容清单
├── mcp.json                    # 通用 MCP 配置
├── package.json                # DSH 插件清单（含 exports / icon / locale/*）
├── cordis.patch.yml            # DSH Cordis Loader insert 补丁
├── locale/{en,zh}.json         # DSH 插件管理面板中英文本地化清单
├── dsh/index.mjs               # DSH Cordis 适配器（defineTool + 属性映射 DSL + 10 个同名工具）
├── assets/logo.svg             # 插件矢量图标
├── rules/AGENTS.md             # 强制理解门禁与低资源常驻规则
├── commands/                   # Claude Code 斜杠命令
├── skills/rag-eval-suite/      # 通用 SKILL.md 与数学公式/分类/根因参考手册
├── skills/dsh-plugin-release/  # 发版方法论 Skill：三道闸门 / 检查清单 / 20 条踩坑 / 契约探测器
├── rag_eval/                   # 纯 Python 标准库核心引擎 + Web Studio + Stdio MCP Server
├── scripts/                    # 跨平台安装器、MCP 启动包装脚本、release.sh 一键发版
├── examples/sample_kb/         # 内置企业知识库示例（表格/版本/错误码/制度）
└── tests/                      # 完整自动化单元测试与集成测试
```

---

## 发版（一键发布）

发版只需一条命令，脚本会把「逻辑正确 → 契约可装载 → 产物可安装」三道闸门全部跑完再动版本号：

```bash
./scripts/release.sh 0.3.1                 # 全流程：闸门 → 版本 → 打包 → 发布 → tag → GitHub Release
./scripts/release.sh patch                 # 语义化自增（patch | minor | major）
./scripts/release.sh 0.3.1 --dry-run       # 演练：跑闸门并打印计划，不改任何文件
./scripts/release.sh 0.3.1 --skip-publish  # 只准备产物与提交，稍后手动 npm publish
./scripts/release.sh 0.3.1 --otp 123456    # 直接带 6 位动态码发布
```

| 步骤 | 动作 | 拦截的问题 |
|---|---|---|
| 0/7 | 分支、干净工作区、npm 登录态、tag 占用、CHANGELOG 段落 | 发错分支 / 漏写变更记录 / 重复 tag |
| 1/7 | **G1** `unittest discover` | 逻辑回归 |
| 2/7 | **G2** `node --check` + 契约探测器 + 真实 `defineTool` 编译 | 装上了但工具注册不起来 |
| 3/7 | 六处清单版本同步（package/plugin ×3/`__init__.py`/`mcp_config.json`） | 版本互相打架 |
| 4/7 | **G3** `npm pack` → 解包 → `install.py --verify` → demo 冒烟，产物存入 `dist/` | 包能装但跑不起来 |
| 5/7 | `npm publish` + 轮询 registry 生效（最长 180s） | 误判失败重复发布 |
| 6/7 | commit `chore(release): x.y.z` + tag + push | 代码与 tag 脱节 |
| 7/7 | `gh release create`（notes 自动取自 CHANGELOG） | Release 与代码不一致 |

前置条件：工作区干净（dry-run 豁免）、`npm whoami` 有效（发布需 2FA，可传 `--otp`）、`gh` 已登录。
产物落在 `dist/dsh-plugin-rag-eval-suite-<version>.tgz`，`dist/` 已在 `.gitignore` 中。

发版方法论（三道闸门、bundle 契约、20 条真实踩坑、清单勾选）已沉淀为 `skills/dsh-plugin-release/`，
新插件可直接复用：

```bash
cp -r scripts/release.sh skills/dsh-plugin-release /path/to/new-plugin/
node skills/dsh-plugin-release/scripts/probe-define-tools.mjs ./dsh/index.mjs   # 契约自检
```
