# Graphiti

**调研基于**：`getzep/graphiti` @ `f17526d`（2026-08-10）
**License**：Apache-2.0

## 状态
- 阶段2 静态调研：✅ 完成（见 `docs/frameworks-survey.md`）
- 环境安装：⬜ 未执行（本机无 GPU / 无法访问模型仓库）
- 官方最小示例：⬜ 未执行
- 适配器：✅ 已写，**未验证**
- 实测：⬜ 未执行

## 为什么它是本次调研的重点
唯一原生双时间轴：每条边带 `valid_at` / `invalid_at`（事件时间）+
`created_at` / `expired_at`（事务时间）。事实变更是给旧边打 `invalid_at` 而非覆盖，
**旧事实仍可检索**。`SearchFilters` 支持对这些字段做比较运算过滤，
所以"截至某历史时点当时的事实是什么"是架构原生能力。

`add_episode(reference_time=...)` 把事件时间设为必填参数。
`entity_types` / `edge_types` 接受 Pydantic 模型，与"通用核心 + 专用 schema"
的目标架构吻合度最高。

## 安装决策：不要用 Kuzu
`pyproject.toml` 里 kuzu extra 带着这条注释：

> `# Deprecated: the upstream Kuzu project is unmaintained; this extra will be removed in a future release.`

嵌入式路线用 `falkordblite`（需 Python >= 3.12），PyPI 摘要即
"FalkorDB embedded in a Python package"，有 ARM64 轮子，无需 Docker。
`setup.sh` 已按此实现，并在 Python 版本不足时直接报错退出。

## 首次运行时要验证的点
1. `FalkorDriver(database=...)` 在 falkordblite 下的构造参数是否与 Neo4j 驱动一致
   （适配器里是按同名参数写的，可能需要改）。
2. `SearchFilters` 的 `invalid_at` 用 OR 两组条件（is_null 或 > as_of）生成的
   Cypher 是否符合预期——这是回溯题能否答对的关键，务必单独验证。
3. episode 按天聚合（`GRAPHITI_EPISODE=day`）是否会让单次抽取的上下文过长。
   逐条模式设 `GRAPHITI_EPISODE=utterance`，但抽取调用次数会涨到 430 次/场景。
