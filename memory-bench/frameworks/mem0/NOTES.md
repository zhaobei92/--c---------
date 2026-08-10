# Mem0

**调研基于**：`mem0ai/mem0` @ `4debc58a`（2026-08-07）
**License**：Apache-2.0

## 状态
- 阶段2 静态调研：✅ 完成
- 环境安装：⬜ 未执行
- 适配器：✅ 已写，**未验证**
- 实测：⬜ 未执行

## 优势
本地优先的支持面是五个里最广的，直接对应宪法硬性约束 1：
LLM 侧有 `ollama` / `vllm` / `lmstudio`，embedding 侧有 `ollama` / `fastembed` /
`huggingface`，向量库 26 种（含 faiss / chroma / qdrant 本地模式）。
活跃度也高（近 90 天 395 提交 / 98 位作者）。

`history(memory_id)` 保留每条记忆的变更流水，可解释性比预想的好。

## 已确认的坑：开源版没有事件时间
两个时间参数在 OSS 都是**硬报错**，不是静默忽略：

| 位置 | 行为 |
|---|---|
| `mem0/memory/main.py:812` | `add(timestamp=...)` → `raise ValueError` |
| `mem0/memory/main.py:1427` | `search(reference_date=...)` → `raise ValueError` |

文档字符串写的是 `Platform-only temporal parameter. Not supported in OSS.`

默认路径下 `created_at` 取 `datetime.now(timezone.utc)`（`:1028`），
灌入 30 天语料后时间结构全丢。

**绕行**：`:1024` 是 `mem_metadata = deepcopy(metadata)`，`:1028` 是
`if "created_at" not in mem_metadata:`——用户 metadata 显式给 `created_at` 不会被覆盖。
适配器走这条路。三点局限（必须写进报告）：

1. 非官方用法，未来版本可能失效；
2. 只是把时间存下来，检索排序不感知时间，as_of 只能靠元数据 `lte` 事后过滤；
3. **最根本**：UPDATE/DELETE 是原地覆盖，旧事实不在库里，再怎么过滤也回溯不出来。

> **待验证假设**：3 道回溯题预期接近全错，且这是架构决定的，不是调参能救的。
> 若实测推翻此假设，要认真复查是不是判分或适配器出了问题。

## 指标适用性
mem0 存的是 LLM 重写过的事实，不是原始对话，其 memory id 无法与 `gold_evidence`
的 utt_id 对齐，因此 **`gold_evidence_recall` 对 mem0 不适用**，报告里不要拿这个
数字跟 Graphiti 比。

## 首次运行时要验证的点
1. `Memory.from_config` 里 ollama provider 的键名（`ollama_base_url`）是否与当前版本一致。
2. Qdrant 本地文件模式下元数据比较运算（`{"created_at": {"lte": ...}}`）是否真的生效——
   适配器有退化分支，但退化就意味着 as_of 完全失效，必须在结果里看清楚是否触发。
3. `BENCH_EMBED_DIMS` 必须与所选 embedding 模型的维度一致（bge-m3 是 1024）。
