# 本仓库包含两个独立项目

| 项目 | 目录 | 状态 |
|---|---|---|
| [定了 · AI 决策闭环系统](#定了--ai-决策闭环系统) | `apps/` `packages/` `prompts/` `infra/` | 阶段0-8完成，148测试全绿 |
| [YS Note / 宇深AI记忆助手](#ys-note--宇深ai记忆助手-项目代号) | `server/` `mobile/` `mock_device/` `docs/` | V0.1 架构与原型基线 |

两个项目互不依赖。定了使用 `docker-compose.dingle.yml` 与
`.github/workflows/dingle-ci.yml`；YS Note 使用根 `docker-compose.yml`、
`.github/workflows/ci.yml` 与 `Makefile`。

---

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
docker compose -f docker-compose.dingle.yml up -d postgres redis
cp .env.example apps/api/.env
cd apps/api && alembic upgrade head

# 3. 启动 API
uvicorn app.main:app --reload --port 8000

# 4. 前端
cd apps/web && npm install && npm run dev
```

或者一键全部启动：

```bash
docker compose -f docker-compose.dingle.yml up --build
```

## 测试

```bash
python -m pytest apps/api/tests packages/model-gateway/tests \
    packages/decision-engine/tests -q
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

---

# YS Note / 宇深AI记忆助手 (项目代号)

类 DOWAY 的 AI 录音硬件配套 App:双平台(iOS + Android)设备管理、音频同步、AI 转写、摘要、编辑、导出与会员付费。

**当前定位:`V0.1 Architecture & Prototype Baseline`** — 架构与原型基线,
**不是**可运行 MVP,更不是可交付双平台 App。已实现/未实现边界见
`docs/04-api-spec.md` 顶部的实现状态标注;真机通信、支付平台对接、数据库持久化
接入均未完成。硬件协议验证(阶段0)完成前,所有设备相关代码针对 `mock_device/`
模拟协议开发。

统一验证入口:`make verify`(服务端测试 + 模拟设备测试 + flutter analyze/test,
任何失败即终止,CI 同口径)。

## 仓库结构

| 目录 | 内容 |
|---|---|
| `docs/` | PRD、技术架构、硬件厂协议资料清单、API 规范、验收标准、测试计划、错误码体系、埋点方案、26周交付计划 |
| `server/` | FastAPI 服务端:24 张核心表 schema、分片上传、任务队列、AI 任务状态机、权益服务(usage_ledger 流水)、订单验证框架、管理后台骨架 |
| `mock_device/` | 模拟设备服务:软件模拟 BLE 状态机 + Wi-Fi 文件服务器(Range 断点续传 + SHA-256 校验),供 App/后端在无真机时开发联调 |
| `mobile/` | Flutter 工程脚手架:Riverpod + Clean Architecture、zh/en/ar 多语言 + RTL、SQLite、上传队列、Platform Channel 接口、iOS(Swift)/Android(Kotlin) 原生模块骨架 |
| `.github/workflows/` | CI:服务端与模拟设备的测试、语法检查、Flutter 静态分析(有 SDK 时) |

## 快速开始

```bash
# 服务端测试
cd server && pip install -e ".[dev]" && pytest

# 模拟设备(BLE 控制面 :9100,Wi-Fi 文件面 :9101)
python -m mock_device.server

# Flutter(需本地安装 Flutter SDK ≥ 3.22)
cd mobile && flutter pub get && flutter gen-l10n && flutter analyze
```

## 关键工程约定

- 所有 AI 分钟扣减必须通过 `usage_ledger` 流水表,禁止直接改用户余额字段(见 `docs/02-architecture.md` §7)。
- AI 任务状态迁移必须走 `server/app/services/job_state_machine.py`,禁止直接 UPDATE 状态列。
- 错误码统一见 `docs/07-error-codes.md`,客户端与服务端共用一套编号。
- 设备通信协议以 `mock_device/protocol.md` 为占位契约,待硬件厂协议(见 `docs/03-hardware-protocol-checklist.md`)到位后替换映射层,业务层接口不变。

## 立项红线(阶段0)

硬件厂未交付完整 BLE/Wi-Fi/OTA 协议、6 台以上工程样机与协议使用授权之前,**不进入阶段2(真机设备连接)开发**。详见 `docs/09-delivery-plan.md`。
