# 指标数学定义、状态判定与测量效度规范

本插件所有确定性指标均采用公开、可审计公式计算，避免黑盒拍脑袋打分。

## 1. 检索层指标（仅在 `observability.retrieved_docs = true` 时计算）

设标准文档集合为 $G$，模型实际进入生成层的有序召回片段序列为 $R = (r_1, r_2, \dots, r_K)$：

- **Hit@k** ($k \in \{1, 3, 5, K\}$):
  $$\text{Hit@}k = \mathbb{I}\left(\exists\, i \le k,\; r_i \in G\right)$$
- **Recall@K**:
  $$\text{Recall@}K = \frac{|\{g \in G : g \text{ matched in } R\}|}{|G|}$$
- **MRR (Mean Reciprocal Rank)**:
  $$\text{MRR} = \frac{1}{N}\sum_{q=1}^{N}\frac{1}{\text{rank}_q}$$
  其中 $\text{rank}_q$ 为首个命中黄金文档的位置，未命中则贡献 $0$。
- **NDCG@K**（文档级唯一首次命中二值相关性，严格 $\in [0, 1]$）:
  $$\text{DCG@}K = \sum_{g \in G \text{ matched}}\frac{1}{\log_2(\text{first\_rank}(g)+1)}, \quad \text{NDCG@}K = \min\left(1,\;\frac{\text{DCG@}K}{\text{IDCG@}K}\right)$$
- **Rerank Cutoff Loss（重排截断损失）**:
  当粗排列表 $R_{\text{raw}}$ 召回的黄金文档数大于重排截断后列表 $R_{\text{rerank}}$ 的命中数时触发，精准定位重排/Top-N 截断问题。

## 2. 生成准确率与幻觉审计指标

- **综合答案准确率 (`answer_accuracy`)**:
  - 含数值用例：$0.45 \times \text{KeyFactCoverage} + 0.35 \times \text{NumericAccuracy} + 0.20 \times \text{TokenF1}$
  - 非数值用例：$0.65 \times \text{KeyFactCoverage} + 0.35 \times \text{TokenF1}$
  - 仅在要点全覆盖、数值全对且 `TokenF1 >= 0.25` 时才允许触发精炼忠实保底加权，防止冗长堆词获得虚高分。
  - 若开启 `--llm-judge`，以 $0.5 \times \text{Deterministic} + 0.5 \times \text{LLMJudge}$ 融合，并显式标注 `mode = hybrid`。
- **声明忠实度 (`faithfulness`) 与 幻觉触发率 (`hallucination_rate`)**:
  - 逐句拆分原子声明（自动跳过纯 Markdown 表格分隔符等无实质词元行），分别核查：
    1. 知识盲区（`should_abstain=true`）是否编造具体结论；
    2. 对抗诱导禁区（`must_not_claim`）是否未经否定即被复述/附和；
    3. 数值声明是否在召回上下文或标准证据中存在出处（防止外部数字幻觉）；
    4. 声明词元在召回上下文中的支撑覆盖度（默认 $\ge 35\%$）。

## 3. 小样本置信区间（Wilson 95% CI）与 7 态状态判定

对所有比例类指标，使用 Wilson Score 区间 ($z = 1.96$)：

$$\frac{\hat{p} + \frac{z^2}{2n} \pm z\sqrt{\frac{\hat{p}(1-\hat{p})}{n} + \frac{z^2}{4n^2}}}{1 + \frac{z^2}{n}}$$

### 状态语义（7 种）
- `pass`：达到可配置目标阈值（均可在 `rag_profile.json -> thresholds` 自定义，无写死魔法数）。
- `warn`：小幅低于目标阈值（处于预警缓冲带）。
- `low_sample_warn`：点估计低于目标，但样本量 $N < \text{min\_stat\_sample\_size}$（默认 10）或 95% CI 仍覆盖目标线，且偏离未达严重红线——避免拿极少样本下统计确凿失败结论。
- `fail`：样本充足且明确不达标，**或者虽为小样本但严重偏离目标阈值 $\ge 20\%$**（`low_sample_hard_fail_margin`，如拒答率 50% vs 目标 85%，防止高危硬伤被小样本降级掩盖）。
- `info`：无硬阈值的参考观测指标（如 `hit_at_1`、`hit_at_3`、`latency_p50_ms`），绝不误标为绿色 `pass`。
- `unobservable`：目标 RAG 未暴露所需中间观测数据（如 `retrieved_docs=false`）。
- `insufficient_data`：当前测试切片无适用样本（$N=0$）。

## 4. 测量效度与隐私边界说明
- **自抽取要点效度边界**：确定性生成器抽取的 `must_include_facts` 源自 `expected_answer`，非常适合**同一 RAG 系统的版本回归**；跨系统横向评测建议启用 `--llm-judge` 或人工独立编写参考答案，避免纯原文复述带来的自洽分数膨胀。
- **可选 LLM 外传边界**：默认 100% 离线确定性执行。仅当显式开启 `--llm-gen`（发送问句 + 160 字证据片段）或 `--llm-judge`（发送问答 + 召回片段）时才会请求外部 API；涉密/内网语料应保持关闭或指向内网自托管端点。
