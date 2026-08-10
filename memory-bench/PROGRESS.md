# 进度

> 会话中断后从这里恢复上下文。已完成的部分不要重做。

**最后更新**：2026-08-10
**当前阶段**：阶段1 ✅ 完成并冻结；阶段2 静态部分 ✅ 完成；阶段2 实测 ⛔ 待在 GPU 机器执行

---

## 阶段状态

| 阶段 | 状态 | 说明 |
|---|---|---|
| 阶段1 测试方案 + question_set.json | ✅ 完成 | 已批准，问题集冻结为 v1.0 |
| 阶段2-A 静态调研（源码/依赖/端侧） | ✅ 完成 | 见 `docs/frameworks-survey.md` |
| 阶段2-B 逐框架实测 | ⛔ 阻断 | 本机无 GPU/模型/网络，需在自有 GPU 机器执行 |
| 阶段3 汇总写报告 | ⬜ 未开始 | 依赖 2-B |

**外部 API 成本：截至目前 0**。全部工作为本地纯 Python + git clone 读源码，
未调用任何付费 API。

---

## 环境阻断（阶段2-B 无法在当前机器执行）

对当前远程容器的完整勘察结果，如实记录（宪法第 1、5 条）：

| 项目 | 实测结果 | 影响 |
|---|---|---|
| GPU | 无（`nvidia-smi` 不存在） | 本地跑开源 LLM/embedding 的前提不成立 |
| CPU / 内存 / 磁盘 | 4 核 / 15 GB / 30 GB 可用 | 跑不动 7B 以上 LLM |
| Docker daemon | 未运行 | 影响需容器后端的框架 |
| huggingface.co | 403 被网络策略拒绝 | 下载不了模型权重 |
| hf-mirror / modelscope | 不通 | 无镜像可绕 |
| api.openai.com | 403 被网络策略拒绝 | 外部 API 路线也被挡 |
| **GitHub REST API** | **403（会话仓库范围限制）** | **issue 响应时间取不到，是本轮缺口** |
| pypi.org / github.com(git) | 通 | 装包、clone 源码可行 |

**已确认方案**（2026-08-10 决定）：本会话只做静态部分，实测脚本写成开箱即跑，
由你在自有 GPU 机器上执行，把 `results/` 回传后再写报告。

---

## 已完成

### 阶段1（已批准冻结）

- `CONSTITUTION.md` / `TASK.md` / `docs/plan.md`
- `scripts/gen_data.py`：种子 `20260310`，2026-03-02（周一）~ 03-31 共 30 天，
  3 场景 **430 条对话 / 39 条事实**，其中 **9 条状态事实中途被新版本取代**。
  `facts_timeline.json` 是答案唯一来源，自带版本链与证据支撑自检。
  已验证**重跑产出逐字节一致**。
- `question_set.json`：**v1.0，`frozen: true`**，60 题（5 类 × 12 题，3 场景各 20 题），
  含 3 道 `requires_as_of` 历史回溯题。生成器逐题对着语料校验，首版即抓出 10 处
  证据 id 错误。**冻结后不得增删改题；确需修改应新开 v1.1 并把所有框架重测。**
- `harness/`：只依赖标准库，可在任意框架 venv 下直接运行。
- `harness/adapters/baseline_bm25.py`：零依赖 BM25 基线，已跑通全链路。

### 基线实测（BM25，非框架，仅作下限参照）

4 核 CPU、无 GPU、无网络，60 题：

| 指标 | 数值 |
|---|---|
| 自动判定准确率 | 29/60 = **48.3%** |
| ├ fact_recall | 10/12 = 83% |
| ├ temporal_locate | 8/12 = 67% |
| ├ cross_session | 7/12 = 58% |
| ├ fact_change | 4/12 = 33% |
| └ refusal | **0/12 = 0%** |
| 检索延迟 P50 / P95 | 0.74 ms / 1.52 ms |
| 灌入 token（3 场景合计） | 10,790 |
| 单次查询平均 token in/out | 16.8 / 223.9 |
| 内存峰值 | 16 MB |

已抽查确认非判分器 bug：拒答 0/12 是纯检索无弃权能力的先天缺陷；
fact_change 失败样式是把含旧值的原文原样吐出。弃权阈值 `SCORE_FLOOR=1.0`
为先验设定，**未针对问题集调过**（调它属于宪法第 5 条禁止的凑数据）。

### 阶段2-A 静态调研（完整结论见 `docs/frameworks-survey.md`）

活跃度（本地 git 历史统计，截止 2026-08-10，近90天=自 2026-05-12）：

| 框架 | 仓库 @ commit | 近90天提交/作者 | License | 结论 |
|---|---|---|---|---|
| Hindsight | `vectorize-io/hindsight` @ `00b520e5` | **1087 / 122** | **MIT** | 建议升为正式待测 |
| Mem0 | `mem0ai/mem0` @ `4debc58a` | 395 / 98 | Apache-2.0 | 正式待测 |
| Graphiti | `getzep/graphiti` @ `f17526d` | 110 / 24 | Apache-2.0 | 正式待测，**重点** |
| Memvid | `memvid/memvid` @ `e6bd9f7` | 4 / 2 | Apache-2.0 | 降级为可行性验证 |
| Letta（旧） | `letta-ai/letta` @ `ff19ffeaf` | 6 / 2 | Apache-2.0 | **建议跳过**（官方弃用） |
| Letta（新） | `letta-ai/letta-code` @ `9d8fb8d6` | 1019 / 33 | Apache-2.0 | TS CLI，形态错位 |

**四条影响选型的关键发现：**

1. **Letta 主仓已被官方明确弃用**（`AGENTS.md` 白纸黑字 "This repository is
   deprecated"），2026-04 起提交断崖归零。开发迁到 TypeScript 的 `letta-code`，
   与"Python 记忆核心"目标错位。
2. **Mem0 开源版没有事件时间**。`add(timestamp=)` 与 `search(reference_date=)`
   在 OSS 都是 `raise ValueError` 硬报错。已找到 metadata 绕行路径并写进适配器，
   但根本问题是它 UPDATE/DELETE **原地覆盖**，旧事实不在库里，回溯题无解。
3. **Graphiti 是唯一原生双时间轴**：`valid_at`/`invalid_at` + `created_at`/
   `expired_at`，事实变更打标不覆盖，`SearchFilters` 支持按时间点过滤。
   `entity_types` 收 Pydantic 模型，与"通用核心+专用 schema"吻合度最高。
4. **Hindsight 的时间能力被低估了**：`retain(timestamp=)` 与
   `recall(query_timestamp=)` 都是开源客户端一等参数，与 Graphiti 同级。
   加上 MIT License 和最高活跃度，建议从加分项升为正式待测项。

**端侧（Jetson ARM64）结论**：记忆框架本身不是瓶颈。`mem0ai`、`graphiti-core`、
`qdrant-client`、`neo4j` 是纯 Python 轮子；`falkordblite`、`kuzu`、`onnxruntime`、
`tantivy`、`numpy` 都有 ARM64 预编译轮子。真正吃资源的是 LLM/embedding 推理。
**注意坑**：Graphiti 的嵌入式后端**不要选 Kuzu**，其 `pyproject.toml` 注明上游
Kuzu 已停维护、该 extra 将被移除；应选 `falkordblite`（需 Python ≥ 3.12）。

### 已写好但**未验证**的实测资产

以下适配器与安装脚本照着各仓库源码写成，**本机无法运行验证**（无 GPU/模型/网络）。
首次在 GPU 机器上跑可能需要小幅调整，改动请记进对应 NOTES.md。

| 框架 | setup.sh | adapter.py | NOTES.md |
|---|---|---|---|
| graphiti | ✅ | ✅ 未验证 | ✅ |
| mem0 | ✅ | ✅ 未验证 | ✅ |
| hindsight | ✅ | ✅ 未验证 | ✅ |
| letta | ❌ 建议跳过 | ❌ | ✅ 含跳过理由 |
| memvid | ❌ 无 Python 绑定 | ❌ | ✅ 含桥接方案 |

统一模型配置（公平性约束，三个适配器共用）：
`BENCH_LLM_MODEL=qwen2.5:14b-instruct`、`BENCH_EMBED_MODEL=bge-m3`（1024 维），
均走本机 Ollama。要换模型请三个一起换，否则跨框架数字不可比。

---

## 待办

### 需要决策

1. **确认框架清单调整**（详见 `docs/frameworks-survey.md` 第七节）：
   Letta 跳过实测、Memvid 降为可行性验证、Hindsight 升为正式待测。
   建议优先级：**Graphiti → Mem0 → Hindsight → Memvid（可行性）→ Letta（仅记录）**。

### 在 GPU 机器上执行（阶段2-B）

```bash
ollama pull qwen2.5:14b-instruct && ollama pull bge-m3
bash frameworks/graphiti/setup.sh
source frameworks/graphiti/.venv/bin/activate
python -m harness.runner --framework graphiti --limit 3   # 先冒烟
python -m harness.runner --framework graphiti             # 跑满
```

每个框架跑完：人工复核 `results/<框架>/review.csv` 填 `human_verdict`，
更新对应 `NOTES.md`（安装耗时、依赖体积、踩坑），更新本文件。

| 框架 | 安装 | 最小示例 | 适配器验证 | 实测 | 人工复核 |
|---|---|---|---|---|---|
| Graphiti | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| Mem0 | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| Hindsight | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| Memvid | ⬜ | ⬜ | — | ⬜ 可行性 | ⬜ |
| Letta | — | — | — | 跳过 | — |

### 本轮遗留缺口（阶段3 前要补）

1. **issue 响应时间未取得**（GitHub API 403）。需在能访问 API 的机器上补，
   或人工抽样 20 个近期 issue 估算。**补上之前不许写"社区响应及时/迟缓"。**
2. Hindsight 的 `query_timestamp` 是硬过滤还是仅作 LLM 提示，需单独设计最小验证。
3. Memvid 的 timeline 是否支持事实级失效，读 spec 无法确定，需实测。
4. 安装耗时与依赖体积要真正 `pip install` 才有数。
5. 各框架宣称的 benchmark 分数一律未核实，本项目不采信厂商自述。

### 阶段3

- `results/` 汇总对比表
- `report.md`：结论先行，推荐谁做通用核心、谁适合端侧、各自风险与规避
