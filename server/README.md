# server/ — FastAPI 服务端

## 已落地

- **24 张核心表**:`migrations/schema.sql`(PostgreSQL 权威 DDL)+ `app/models/tables.py`
  (SQLAlchemy,可跑 SQLite 单测);表清单与计划一致,由 `tests/test_models.py` 守护。
- **AI 任务状态机**:`app/services/job_state_machine.py` — 合法迁移表、重试计数、终态锁定;
  `waiting→uploading→preprocessing→transcribing→(diarizing)→summarizing→completed`,
  失败走 `retrying`(≤3 次)→ `failed` + 分钟冲正。
- **权益服务(usage_ledger 铁律)**:`app/services/entitlement_service.py` — 所有分钟增减
  一律流水;扣费顺序 free_monthly→gift→member→purchased(同桶先到期先扣);
  幂等(同 job 不重复扣)、退款逐桶冲正、过期冲销。
- **分片上传**:`app/services/upload_service.py` — init 按 sha256 去重(同文件不重复收费)、
  分片登记幂等、缺片拒绝合并、整体 Hash 校验失败保留分片重传。
- **订单验证框架**:`app/services/order_verification.py` — PaymentProvider 抽象,
  transaction_id 幂等发放,退款回收;Apple/Google 真实 SDK 在阶段5 接入(TODO 注释处)。
- **任务队列抽象**:`app/services/queue.py`(InMemory / Redis)。
- **管线 Worker**:`app/workers/pipeline.py` — 十步管线骨架,阶段以可替换 Stage 注入,
  失败自动重试/冲正/通知。
- **API 骨架**:`app/api/`(auth、devices、recordings、uploads、jobs、billing、
  notifications、admin),错误码统一 `app/core/errors.py`(与 docs/07 同步)。

## 骨架说明

阶段1 内存仓储(`app/api/deps.py`)承载路由,DB 会话接入在阶段3 替换,服务层接口不变。
`schemas/` 校验用 pydantic 模型内联在各路由文件。

## 运行

```bash
pip install -e ".[dev]"
pytest                    # 76 个测试:状态机/权益/上传/订单/模型/契约/越权/计费/验证码/端到端
uvicorn app.main:app --reload   # /docs 查看 OpenAPI
```
