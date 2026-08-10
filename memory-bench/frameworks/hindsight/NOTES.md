# Hindsight

**调研基于**：`vectorize-io/hindsight` @ `00b520e5`（2026-08-10）
**License**：**MIT**（五个里唯一非 Apache-2.0，也是最宽松的）

## 状态
- 阶段2 静态调研：✅ 完成
- 环境安装：⬜ 未执行
- 适配器：✅ 已写，**未验证**
- 实测：⬜ 未执行

## 建议从"加分项"提升为正式待测项
理由：
1. **最活跃**：近 90 天 1087 提交 / 122 位作者（五个里第一）。
2. **License 最宽松**：MIT。
3. **时间能力是一等公民**，与 Graphiti 同级——这点是读源码才发现的，
   比任务书里"加分项"的定位高不少：
   - `retain(bank_id, content, timestamp: datetime | None, ...)`
   - `recall(bank_id, query, query_timestamp: str | None, ...)`
4. **有无 Docker 的本地路径**：`pip install hindsight-embed`，本地守护进程 +
   内嵌 PostgreSQL(pg0)。

## 需要警惕的点
- **形态偏平台而非库**：主仓有 helm / monitoring / control-plane / 多语言客户端
  （py 1389 个文件、ts 416、go 202）。端侧要评估最小可用子集有多小。
- **首次启动 1-3 分钟**（官方 README 自述，加载 ML 模型）。跑测试前必须预热，
  否则第一题的延迟会包含模型加载时间，污染 P50/P95。
- **recall 不是 top-k 接口**，而是按 token 预算组装上下文
  （`max_tokens` / `budget`）。统一 top-k=8 的公平性约束对它无法完全适用，
  适配器用 `max_tokens=1024` 近似，报告里要如实说明这一处不可消除的差异。
- **embedding 由服务端自带**，未与其它框架统一。这会削弱横向可比性，
  要在报告里标注；若能配置成统一模型，优先配置。
- 官方宣称 LongMemEval 91.4% / LoCoMo 89.61%。**厂商自述，本项目不采信**，
  以我们自己的实测为准。

## 首次运行时要验证的点
1. `HindsightClient` 是否有清空 bank 的方法（适配器里是探测式调用
   `delete_bank` / `clear_bank`）。若都没有，改成每次用带时间戳后缀的新 bank_id，
   否则场景之间会串味。
2. `query_timestamp` 到底是硬过滤还是仅作为提示传给 LLM——这决定它在 3 道回溯题上
   的真实水平，务必单独设计一个最小验证。
3. `recall` 返回对象的字段名（适配器里是探测式提取 `context`/`memories`/`content`）。
