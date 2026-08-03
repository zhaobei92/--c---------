# 定了 · AI 决策闭环系统

帮助用户识别纠结原因、澄清真实偏好、完成结构化决策、接受选择代价，
并在没有新事实时停止反复推翻决定。

## Monorepo 结构

```
apps/
  api/        FastAPI 后端（状态机、决策 API、Alembic 迁移）
  web/        Next.js + TypeScript + Tailwind 前端（PWA 方向）
packages/
  decision-engine/   确定性决策算法包（禁止依赖 LLM，可离线单测）
  model-gateway/     模型网关（模型名全部走环境变量，禁止硬编码）
  shared-schemas/    共享 Pydantic Schema（API 与模型输出契约）
  evals/             离线评测集（阶段8）
prompts/     按任务拆分的提示词（版本化管理）
infra/       Dockerfile 与迁移相关
tests/       跨模块测试（unit / integration / algorithm / model-evals / e2e）
```

## 本地开发

```bash
# 1. 后端
python3 -m venv .venv && source .venv/bin/activate
pip install -e packages/shared-schemas -e packages/model-gateway \
    -e packages/decision-engine -e "apps/api[dev]"

# 2. 数据库（PostgreSQL + Redis）
docker compose up -d postgres redis
cp .env.example apps/api/.env
cd apps/api && alembic upgrade head

# 3. 启动 API
uvicorn app.main:app --reload --port 8000

# 4. 前端
cd apps/web && npm install && npm run dev
```

或者一键全部启动：

```bash
docker compose up --build
```

## 测试

```bash
python -m pytest apps/api/tests packages/model-gateway/tests -q
```

测试使用内存 SQLite，不需要外部服务；生产与开发环境使用 PostgreSQL。

## 当前进度

- [x] 阶段0：Monorepo、Docker Compose、CI、迁移系统、健康检查
- [x] 阶段1：决策录入与结构化
  - 新建决策 / 详情 / 列表 / 对话消息 API 与页面
  - Intake Parser：LLM 结构化提取（Structured Outputs + Pydantic 验证），
    未配置或调用失败时自动降级为确定性启发式解析，流程不中断
  - SSE 流式输出（`/decisions/{id}/stream`），前端自动连接
  - 确定性风险分级（RESTRICTED/HIGH → GUIDED_ONLY，不直接拍板）
  - 选项与约束的增删改（用户可纠正 AI 错误），全部写入审计日志
  - audit_events / model_invocations 落库
- [x] 阶段2：纠结诊断与追问
  - 纠结类型分类（LLM + 关键词启发式降级），概率分布输出
  - 信息完整度 / 决策准备度永远由确定性代码计算（模型自报数值丢弃）
  - 追问价值 = 影响×不确定性−成本；每轮一问、不重复、最多5轮、低价值即停
  - Orchestrator `/advance` 单步推进，全部状态转换经校验并审计
- [x] 阶段3：偏好学习
  - 标准生成（LLM 禁止携带权重，降级为领域默认标准）
  - 成对比较 API + Bradley–Terry MLE，每次回答即时重拟合
  - 冲突回答检测：一致性下降、不确定度上升，绝不输出伪高置信权重
- [x] 阶段4：决策计算引擎（`packages/decision-engine`，零 LLM 依赖）
  - 硬约束/veto 淘汰（零违反）、六种效用曲线、MAUT
  - 1000 次蒙特卡洛：胜率、稳定性、期望效用差、关键变量
  - Minimax Regret、单变量敏感性翻转条件；固定 seed 完全可复现
  - 每次计算版本化保存 decision_runs（输入/输出快照）
- [x] 阶段5：Challenger 与结果页
  - 推荐解释先由确定性代码构建（收益与代价必须同时存在）
  - LLM 只润色措辞；若篡改 recommended_option_id 则整体作废并审计
  - Challenger 仅在稳定性低/差距小时调用，只提问题，永不覆盖推荐
  - 结果页：推荐卡、稳定性条、理由、代价、未知项、重开/非重开条件
- [x] 阶段6：决策锁与重开
  - 决策契约（接受代价必选、允许不采纳推荐并如实记录）
  - Reopen Gate：0.20N+0.20C+0.25R+0.25F+0.10V，阈值 0.70/0.45
  - 无新事实拒绝重开（规定话术）；反刍检测触发关闭干预；全部留痕
- [x] 阶段7：回访与长期偏好
  - h24/d7/d30/d90 回访；只有已执行的决定才更新偏好后验
  - 后验伪先验锚定、std 有下限；证据≥2次才影响后续同类决策
  - 用户可查看并删除偏好画像
- [x] 阶段8：评测与加固
  - 离线评测集（8类种子案例，runner 断言风险门控/提取/禁语）
  - 算法黄金回归（改算法必须显式更新黄金值并 bump 版本）
  - 故障注入（全挂/超时/间歇）；Prompt 版本回归；管理统计 API
- [x] 审查修正：Lexicographic 规则、Beta/Triangular/Categorical 分布采样、
  模型调用重试、LLM 风险精细化（只升不降）、快速模式（直接给结论）、
  决策记录删除

## 已知偏差（相对原始方案，有意为之）

- evidence_items 表未建：V1 事实以 JSON 存于 decision_cases，证据分级
  服务（来源A-E级）留待联网证据搜索阶段一并实现
- 语义新颖度用字符 n-gram 近似（阈值0.5），接入 embedding 后换回 0.15
- 追问措辞用确定性模板（选择逻辑符合方案6.8近似算法），LLM 润色待加
- Redis+ARQ 后台任务未启用：回访到期目前是拉取式（/followups/due）
- 认证为 dev 用户占位；/login、/settings 页面待接入真实 Auth
- criterion_weights/simulation_results/challenge_results/intervention_events
  分别并入 decision_criteria/decision_runs/recommendations/audit_events
- 熔断、单次成本上限、用户日额度、trace_id 透传：上线加固项待做
- 评测集为种子规模，扩充到方案要求的200例是内容标注工作
