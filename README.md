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
- [ ] 阶段2：纠结诊断与追问（标签、信息完整度、候选问题、问题价值排序）
- [ ] 阶段3+：偏好学习、决策计算引擎、决策锁与重开（见实施计划）
