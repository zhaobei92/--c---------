# 框架静态调研（阶段2 · 第一部分）

**调研日期**：2026-08-10
**方法**：clone 各仓库源码直接阅读 + 查 PyPI 发行元数据。GitHub REST API 在本会话
不可用（403，受会话仓库范围限制），因此 **commit 数据来自本地 git 历史（一手）**，
而 **issue 响应时间无法取得（缺口，已在下文标明）**。

> 本文件只记录"读源码和查资料能得到的结论"。所有涉及实际性能、准确率的判断都是
> **待验证假设**，必须由阶段2 的实测来证实或推翻——不得当成结论写进 `report.md`。

---

## 一、活跃度与维护状态

commit 数据由本地 `git log` 统计，截止 2026-08-10。"近90天"= 自 2026-05-12。

| 框架 | 仓库 | 最后提交 | 近90天提交 | 近90天作者 | 总提交 | 始于 | License |
|---|---|---|---|---|---|---|---|
| Mem0 | `mem0ai/mem0` | 2026-08-07 | **395** | 98 | 2571 | 2023-06 | Apache-2.0 |
| Letta（旧） | `letta-ai/letta` | 2026-08-01 | **6** | 2 | 7469 | 2023-10 | Apache-2.0 |
| Letta（新） | `letta-ai/letta-code` | 2026-08-09 | **1019** | 33 | 3074 | — | Apache-2.0 |
| Graphiti | `getzep/graphiti` | 2026-08-10 | **110** | 24 | 937 | 2024-08 | Apache-2.0 |
| Memvid | `memvid/memvid` | 2026-07-14 | **4** | 2 | 261 | 2025-05 | Apache-2.0 |
| Hindsight | `vectorize-io/hindsight` | 2026-08-10 | **1087** | 122 | 2433 | 2025-10 | **MIT** |

**缺口**：issue 平均响应时间需要 GitHub API，本会话取不到。阶段2 在能访问 API 的
机器上补测，或人工抽样 20 个近期 issue 估算。**在补上之前，报告里不许写"社区响应
及时/迟缓"这类判断。**

### 两个必须点名的状态变化

**1. Letta 主仓已明确弃用。** 仓库根目录的 `AGENTS.md` 原文：

> ## This repository is deprecated
> This repository contains the **legacy Letta server** … It is in maintenance mode
> and is no longer where active development happens.

提交曲线印证了这一点，不是文档笔误：

```
2025-06  467    2025-11  333    2026-04    3
2025-07  524    2025-12  155    2026-05    1
2025-08  548    2026-01  310    2026-06    2
2025-09  378    2026-02  201    2026-07    2
2025-10  430    2026-03  216    2026-08    1
```

2026-04 起断崖式归零。开发迁到 `letta-ai/letta-code`，**新形态是 TypeScript 的
CLI agent + TS SDK**，自托管改用「App Server」。任务书里那个"操作系统式分层记忆
的 Python 服务端"，现在是维护模式产物。

**2. Memvid 在 2026-01-05 完整重写为 Rust。** Python 实现于 2026-01-04 被整体删除
（`memvid/*.py`、`examples/*.py` 全部消失）。当前仓库是 `memvid-core` 这个 Rust
crate，158 个 `.rs`、0 个 `.py`。重写后热度迅速回落：2026-01 有 167 次提交，
此后 2、3、5、7 月分别只有 20、6、3、2 次。

---

## 二、记忆表示与可解释性

### Mem0 — 向量库 + LLM 事实抽取，**破坏性覆盖**

- **表示**：把对话交给 LLM 抽成一条条自然语言"事实"，向量化后存进向量库。
  支持 26 种向量后端（qdrant/chroma/faiss/pgvector/milvus/redis…）。
- **更新机制**：新事实进来时，LLM 在 `ADD / UPDATE / DELETE / NONE` 四个动作里
  选一个（见 `mem0/configs/prompts.py`）。这是它处理"事实变更"的方式。
- **可解释性**：有 `get(memory_id)`、`history(memory_id)`。`history` 保留每条记忆
  的变更流水，能追到某条记忆被谁改成了什么——**这一点比预想的好**。
- **关键限制**：`UPDATE`/`DELETE` 是**原地覆盖**，向量库里只留最新值。旧事实的
  文本在 history 流水里，但**不在可检索的记忆里**。

### Letta（旧）— 分层记忆块 + 归档存储

- **表示**：core memory（常驻上下文的可编辑块）+ recall/archival memory（外部存储，
  按需检索）。agent 自己调用工具改写 core memory。
- **可解释性**：记忆块是显式命名、可直接读写的文本块，人能直接看到 agent 当前
  "脑子里装的是什么"——这是它设计上最漂亮的地方。
- **状态**：见上，维护模式。

### Graphiti — **双时间轴知识图谱**，唯一不覆盖旧事实的方案

`graphiti_core/edges.py` 里每条边同时带四个时间字段：

| 字段 | 含义 |
|---|---|
| `valid_at` | 事实**在现实中**开始成立的时间 |
| `invalid_at` | 事实在现实中失效的时间 |
| `created_at` | 这条记录**被写入系统**的时间 |
| `expired_at` | 记录被系统标记作废的时间 |

事实变更不是覆盖，而是给旧边打上 `invalid_at` 再新增一条边。旧事实**仍然可检索**。

`add_episode()` 把 `reference_time: datetime` 作为**必填参数**——事件时间是一等公民，
而不是靠 metadata 硬塞。

`SearchFilters`（`graphiti_core/search/search_filters.py`）支持对
`valid_at / invalid_at / created_at / expired_at` 做带比较运算符（`<`、`<=`、`>`、
`is_null`…）的过滤，还支持 AND/OR 嵌套。也就是说"截至某历史时点，当时的事实是
什么"这类查询是**架构原生支持**的。

- **可解释性**：图里每条边可溯源到产生它的 episode（`get_nodes_and_edges_by_episode`），
  且能单独删除某个 episode 及其派生（`remove_episode`）。定位一条错误记忆的能力
  是五个里最强的。

### Memvid — 单文件 `.mv2`，无数据库

`MV2_SPEC.md` 定义的单文件布局：头部 + 内嵌 WAL + 数据段 + Tantivy 全文索引段 +
HNSW 向量索引段 + **时间索引段** + TOC 校验尾。附带 Blake3 校验、Ed25519 签名、
可选 AES-256-GCM 加密。

- **可解释性**：追加式帧结构 + WAL，理论上可回放；但**没有 Python 侧工具链**，
  排查得靠 Rust 或 CLI。
- **时间能力**：有 `temporal_track` / `temporal_enrich` feature 和 timeline 查询，
  但不是 Graphiti 那种"事实级双时间轴"，更像"按时间浏览"。

### Hindsight — 分网络的记忆 + retain/recall/reflect

- **表示**：把长期记忆拆成事实、经历、观察、观点等**认识论上不同的网络**，
  把证据和推断分开存。三个核心操作：retain / recall / reflect。
- **存储**：PostgreSQL 系（docker-compose 里给了 pg_search / pgvector 系
  `vchord` / `pgroonga` / `pg_textsearch` 多种检索方案）。
- **注意**：官方宣称在 LongMemEval 取得 91.4%、LoCoMo 89.61%。
  **这是厂商自述，不是我们的实测**；本项目的意义恰恰在于用同一套标准自己测一遍。

---

## 三、时间能力横向对比（本项目最关键的一栏）

问题集里有 3 道 `requires_as_of` 回溯题（"截至3月9日，王秀兰吃的降压药是什么"），
外加 12 道事实变更题。这一栏基本决定选型。

| 框架 | 能否给记忆指定**原始事件时间** | 能否按**历史时点**检索 | 旧事实是否可检索 |
|---|---|---|---|
| Graphiti | ✅ `reference_time` 必填参数 | ✅ `SearchFilters` 原生支持 | ✅ 打 `invalid_at`，不删 |
| Mem0 | ⚠️ 见下 | ❌ 需自行按 metadata 后过滤 | ❌ 原地覆盖 |
| Memvid | ✅ 有时间索引段 | ⚠️ timeline 查询，粒度未知 | ⚠️ 追加式，需实测 |
| Hindsight | ⚠️ 宣称 time-aware，未读到具体 API | 待测 | 待测 |
| Letta（旧） | ❌ 未见事件时间概念 | ❌ | ❌ |

### Mem0 的时间参数：一个必须写进报告的坑

`mem0/memory/main.py` 第 782 行文档字符串：

> `timestamp (Any, optional): Platform-only temporal parameter. **Not supported in OSS.**`

而且不是静默忽略，是第 812–813 行**硬报错**：

```python
if timestamp is not None:
    raise ValueError(get_temporal_feature_error_message("sync", "add", "timestamp"))
```

也就是说，开源版**不能给记忆指定原始时间**——按默认路径灌入我们的 30 天语料，
所有记忆的 `created_at` 都会变成"灌数据那一刻"（第 1028–1029 行取
`datetime.now(timezone.utc)`），时间维度整个塌掉。

**但有绕行路径。** 第 1024 行 `mem_metadata = deepcopy(metadata)`，而第 1028 行是
`if "created_at" not in mem_metadata:` ——只要在用户 metadata 里显式传
`created_at`，就不会被覆盖：

```python
m.add(messages, user_id=..., metadata={"created_at": u.ts, "utt_id": u.utt_id})
```

适配器已按这条路径实现。**但必须注意三点**，报告里要写清楚：

1. 这是绕过官方"OSS 不支持"声明的非官方用法，未来版本可能失效；
2. 它只让时间**被存下来**，检索排序依然不感知时间，`as_of` 只能靠事后过滤；
3. 更根本的是，Mem0 的 `UPDATE`/`DELETE` 是原地覆盖，**旧事实根本不在库里**，
   再怎么过滤也回溯不出来。3 道回溯题预期会失败——这是架构决定的，不是调参能救的。

> **待验证假设**：Graphiti 在 `fact_change` 与 3 道回溯题上显著领先；Mem0 在
> `fact_change`（问"现在"）上表现尚可、在回溯题上接近全错。阶段2 用实测证实或推翻。

---

## 四、多模态扩展性

| 框架 | 情况 |
|---|---|
| **Memvid** | **最强**。内置 CLIP 视觉 embedding、Whisper 音频转写（Candle 推理，带 CUDA/Metal feature），PDF/DOCX/XLSX 解析都在核心库里 |
| Mem0 | 向量后端多，图像/传感器数据需自行转成文本或自带向量塞进 metadata |
| Graphiti | episode 以文本为主，非文本模态需先转文本；但图结构天然适合挂位置、传感器等实体属性 |
| Hindsight | 未读到原生多模态支持，待查 |
| Letta（旧） | 未见原生支持 |

对"养老/宠物/机器人"这三个目标场景，传感器与位置数据是刚需。Graphiti 的图结构在
**关系建模**上有优势，Memvid 在**原始模态摄入**上有优势——两者不冲突，可能是组合关系。

## 五、与"通用核心 + 专用 schema"的适配度

| 框架 | 加自定义字段的方式 | 难度 |
|---|---|---|
| **Graphiti** | `add_episode(entity_types=..., edge_types=..., edge_type_map=...)` 直接传 **Pydantic 模型**定义实体与关系类型 | **低**，改配置即可 |
| Mem0 | metadata 任意字典 + 可改事实抽取 prompt | 中，结构化程度弱 |
| Hindsight | 记忆分网络的设计本身接近"专用 schema"，具体扩展点待查 | 待评估 |
| Memvid | Rust 侧扩展，Python 无接口 | 高 |
| Letta（旧） | 记忆块是自由文本 | 中，但无结构约束 |

Graphiti 的 `entity_types` 接受 Pydantic 模型，意味着"用药记录""宠物行为标签"这类
专用 schema 可以直接声明成类型，抽取时由 LLM 按 schema 填充。这与目标架构
（通用核心 + 养老/宠物/机器人专用适配）**吻合度最高**。

## 六、端侧可行性（Jetson，ARM64，8–16GB）

按宪法要求只做依赖层面推演，**不实机测**。以下为 PyPI 发行元数据实查结果：

| 包 | 最新版 | ARM64 可得性 |
|---|---|---|
| `mem0ai` | 2.0.17 | 纯 Python 轮子 |
| `graphiti-core` | 0.29.3 | 纯 Python 轮子 |
| `qdrant-client` | 1.19.0 | 纯 Python 轮子 |
| `neo4j`（驱动） | 6.2.0 | 纯 Python 轮子 |
| `falkordblite` | 0.10.0 | **有 ARM64 轮子（6 个）** |
| `kuzu` | 0.11.3 | 有 ARM64 轮子（15 个） |
| `onnxruntime` | 1.28.0 | 有 ARM64 轮子（14 个） |
| `tantivy` | 0.26.0 | 有 ARM64 轮子（12 个） |
| `numpy` | 2.5.2 | 有 ARM64 轮子（30 个） |

**结论：记忆框架本身不是 Jetson 上的瓶颈。** Mem0 和 Graphiti 的核心依赖全都是纯
Python 或有 aarch64 预编译轮子，`pip install` 不需要现场编译。真正吃资源的是
LLM 与 embedding 推理，那是模型侧的问题，与选型无关。

### 各框架的端侧障碍在"后端服务"，不在 Python 包

- **Graphiti**：默认 `neo4j`（JVM，Jetson 上偏重）。但有嵌入式选项——
  **注意一个坑**：`pyproject.toml` 里 Kuzu extra 带着这条注释：

  > `# Deprecated: the upstream Kuzu project is unmaintained; this extra will be removed in a future release.`

  所以嵌入式路线**不要选 Kuzu**，应选 `falkordblite`（PyPI 摘要即
  "FalkorDB embedded in a Python package"，需 Python ≥ 3.12）。这条如果搞错，
  端侧方案会建在一个即将被移除的 extra 上。

- **Mem0**：默认 qdrant 可跑本地内存/文件模式，另有 faiss、chroma 等纯本地后端。
  LLM/embedding 支持 `ollama`、`vllm`、`lmstudio`、`fastembed`、`huggingface`
  ——**本地优先的支持面是五个里最广的**，直接对应宪法硬性约束 1。

- **Memvid**：Rust 单文件、无数据库，端侧形态上**最理想**。但 `memvid-core` 是纯
  Rust 库，仓库里**没有 Python 绑定**，也没有 `[[bin]]`；CLI 是通过 npm 包
  `memvid-cli` 分发的预编译二进制（见 `docker/cli/Dockerfile`）。Python 要用，
  只能自己写 PyO3 绑定或走 CLI 子进程。

- **Hindsight**：`hindsight-embed` 提供本地守护进程 + **内嵌 PostgreSQL（pg0）**，
  `pip install hindsight-embed` 即可，无需 Docker。但主仓带 helm/monitoring/
  control-plane，整体是平台形态，端侧要评估最小可用子集有多小。首次启动要下载
  ML 模型、耗时 1–3 分钟（其 README 自述）。

- **Letta（旧）**：需要 Postgres + 服务端进程，端侧最重；且已弃用，不建议投入。

---

## 七、阶段2 实测计划的调整建议

基于以上，建议对任务书的框架清单做三处调整，**请确认后再执行**：

1. **Letta**：主仓已弃用，新形态是 TypeScript CLI，与我们"Python 记忆核心"的选型
   目标已经错位。建议**降级为"记录状态后跳过实测"**（宪法第 5 条允许），把省下的
   时间投给 Hindsight。若你仍要实测，我按旧版 Python 服务端跑，但结论只对
   legacy 版本有效。
2. **Memvid**：无 Python 绑定，接入成本从"写个适配器"变成"写 FFI 绑定或 CLI 桥接"。
   建议先只做 CLI 子进程桥接做可行性验证，不强求跑满 60 题；跑不通就如实记录。
3. **Hindsight**：活跃度和 License（MIT）都最好，且 `hindsight-embed` 提供无
   Docker 的本地路径。建议从"加分项"**提升为正式待测项**。

优先级建议改为：**Graphiti → Mem0 → Hindsight → Memvid（可行性验证）→ Letta（仅记录）**。

---

## 八、本轮调研的已知缺口

诚实列出，避免被当成完整结论：

1. **issue 响应时间未取得**（GitHub API 403）。需在能访问 API 的机器上补。
2. **Hindsight 的时间能力细节未读透**——只确认了"宣称 time-aware"，没找到对应
   API。需要在阶段2 装上后实际验证。
3. **各框架宣称的 benchmark 分数一律未核实**，本项目不采信厂商自述数字。
4. **安装耗时、依赖体积**要等真正 `pip install` 才有数，本轮无法给出。
5. Memvid 的 timeline 查询粒度、是否支持事实级失效，读 spec 未能确定，需实测。

---

**数据来源**：各仓库 git 历史（本地 clone，一手）、各仓库源码与 `pyproject.toml` /
`Cargo.toml` / `MV2_SPEC.md` / `AGENTS.md`、PyPI JSON API 发行元数据。
Hindsight 仓库地址经网络检索定位后，已 clone 核实一手数据。

Hindsight 定位过程参考：
[Vectorize 官方介绍](https://vectorize.io/blog/introducing-hindsight-agent-memory-that-works-like-human-memory) ·
[vectorize-io/hindsight](https://github.com/vectorize-io/hindsight)
