# 10 大 RAG 测试数据分类规范

为保证测试覆盖全面且支持分类浏览，数据集严格划分为以下 10 类：

| 分类标识 | 中文名称 | 核心模式包含 | 主要检测风险 |
| :--- | :--- | :---: | :--- |
| `single_hop_fact` | 单跳精确事实 | ✅ | 基础召回遗漏、切片语义断裂 |
| `multi_hop_reasoning` | 多跳跨文档推理 | ✅ | 单跳 Top-K 不足、多文档证据遗漏 |
| `numerical_table` | 数值与表格抽取计算 | ✅ | 表格切片截断、数字/单位漂移幻觉 |
| `temporal_version` | 时效与版本演进 | ✅ | 过期旧文档覆盖最新生效版本 |
| `unanswerable_oos` | 知识盲区与拒答边界 | ✅ | 面对知识库外问题凭空编造幻觉 |
| `terminology_jargon` | 专有名词与同义改写 | ✅ | 纯向量检索漏召缩写/错误码/专词 |
| `adversarial_distractor` | 对抗诱导与近义干扰 | ✅ | 盲从用户错误前提产生迎合性幻觉 |
| `conflicting_ambiguous` | 歧义与多源冲突消歧 | 扩展 | 忽略适用条件与例外条款 |
| `long_context_synthesis` | 长文归纳与多点汇总 | 扩展 | Lost-in-the-Middle 遗漏关键要点 |
| `robustness_boundary` | 边界鲁棒性测试 | 扩展 | 噪声标点/短词/格式扰动引发异常或高延迟 |

## 覆盖平衡护栏
- `unanswerable_oos`（拒答边界）建议不低于总用例数的 **10%~15%**，否则难以暴露严重幻觉缺陷。
- 每条测试数据均带有 `core_priority`（1=核心，2=标准，3=扩展），便于大型系统快速切换到精简核心集。
