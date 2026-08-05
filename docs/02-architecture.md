# 技术架构文档 V1.0

## 1. 总体架构

```
AI录音机
  │
  ├── BLE:设备发现、状态、控制、文件索引、OTA控制
  │
  └── Wi-Fi:音频文件高速传输
          │
          ▼
iOS / Android App (Flutter + 原生模块)
  │
  ├── 本地数据库 (SQLite)
  ├── 文件下载与断点续传
  ├── 音频播放
  ├── 上传队列
  ├── 文档编辑
  └── 支付与权限
          │
          ▼
API网关与用户系统 (FastAPI)
          │
          ├── 对象存储 (S3 兼容)
          ├── 任务队列 (Redis Queue,首版)
          ├── 音频预处理 (FFmpeg)
          ├── ASR转写 (多供应商适配层)
          ├── 说话人分离
          ├── 摘要与待办 (LLM)
          ├── 搜索与问答 (PG 全文检索 + pgvector)
          ├── 会员权益 (usage_ledger)
          └── 管理后台 (React 骨架)
```

## 2. 技术选型与理由

### 2.1 移动端:Flutter 统一业务 + Swift/Kotlin 原生硬件模块

- Flutter 负责约 75%—85% 的页面与业务代码(最新稳定版,Riverpod 状态管理,Clean Architecture 分层,SQLite,Repository 统一数据源,Feature Flag 远程开关)。
- BLE、Wi-Fi 文件传输、后台任务、音频、OTA、双平台支付通过 Platform Channel 调用 Swift/Kotlin 实现。
- **不依赖通用 Flutter BLE 插件**:BLE 长连接、Wi-Fi 切换、大文件断点续传、固件升级、后台状态恢复、多型号多固件兼容必须由团队掌握原生代码。

iOS 原生层(Swift):CoreBluetooth(含 State Restoration)、NEHotspotConfiguration、本地网络访问授权、AVAudioSession 播放/录音、BGTaskScheduler、StoreKit 2、Keychain、APNs、OTA 数据传输。

Android 原生层(Kotlin):Bluetooth LE、Companion Device、Wi-Fi Direct/局部热点(NEARBY_WIFI_DEVICES,Android 13+)、Foreground Service(dataSync/connectedDevice 类型)、WorkManager、MediaRecorder/AudioTrack、Play Billing、Keystore、FCM、OTA。

Platform Channel 契约见 `mobile/lib/platform/device_channel.dart`(唯一事实来源,Swift/Kotlin 按此实现)。

### 2.2 服务端

首版技术栈(刻意保守,不引入 Kafka/复杂微服务/大规模 K8s):

| 组件 | 选型 |
|---|---|
| API | FastAPI (Python 3.11) |
| AI Worker | Python |
| 数据库 | PostgreSQL 15+ |
| 缓存 | Redis |
| 消息队列 | Redis Queue(RabbitMQ 可替换,接口抽象在 `app/services/queue.py`) |
| 文件 | S3 兼容对象存储 |
| 搜索 | PostgreSQL 全文搜索 + pgvector |
| 音频 | FFmpeg |
| 管理后台 | React(骨架;首版由 `/admin` API 驱动) |
| 容器 | Docker;部署用托管容器服务,K8s 延后 |

## 3. 设备通信设计

- **BLE 负责**:发现、绑定、状态、文件索引、发起 Wi-Fi 传输、控制录音、OTA 控制。
- **Wi-Fi 负责**:音频大文件传输、固件包传输、批量同步。
- **禁止**通过 BLE 传完整录音文件。

同步流程与异常清单见 PRD §6.2;协议契约在真机协议到位前以 `mock_device/protocol.md` 为准,App 侧通过 `DeviceTransport` 抽象接入,替换真机协议时只改映射层。

### 检查点与校验
- 每 4—8MB 保存下载检查点(HTTP Range 续传)。
- 文件完成后计算 SHA-256 与设备端索引比对;失败自动重试,重试超限进入错误码 `DEV_1204`。
- 本地库以 `(device_sn, file_id, sha256)` 判重,重复文件率目标 0。

## 4. AI 处理管线(十步)

```
上传接收 → 标准化(FFmpeg 单声道/固定采样率/归一化) → VAD → 分段语言识别(5—20s)
→ ASR(文本+时间戳+置信度+标点) → 说话人分离(Speaker N + 起止) → 文本后处理
→ 摘要(8 内置模板,结论强制时间戳引用) → 翻译层(独立,不改原文) → 问答(P1)
```

- 上传即计算 SHA-256 去重:**同一用户的同一文件不重复上传、不重复收费**。
  去重范围限定单用户(`(user_id, sha256)`):跨用户 Hash 命中会泄露"某音频已存在"
  并共享资产 ID,涉及删除生命周期、引用计数、数据区域与加密域问题;
  数据治理方案(asset_references + reference_count)落地前**不做全局去重**。
- ASR 适配层支持多供应商路由(成本与语种路由),短/长音频分级。
- 任务状态机:`Waiting → Uploading → Preprocessing → Transcribing → Diarizing → Summarizing → Completed / Failed / Retrying`,实现与合法迁移表在 `server/app/services/job_state_machine.py`,**禁止绕过状态机直接改状态**。

## 5. 服务划分(单体内模块化,物理仍是一个 FastAPI 应用)

- 用户服务:注册、登录、Token、三方账号、注销、数据导出、地区、语言、同意记录。
- 设备服务:型号、SN、绑定、固件、激活、保修、黑名单、设备密钥、最后在线。
- 录音服务:文件元数据、时长、模式、本地/云端状态、来源、Hash、保存期限。
- AI 任务服务:状态机 + 队列 + 重试 + 成本记录。
- 权益服务:免费/赠送/会员/购买分钟、过期规则、扣费顺序、退款回收、苹果/Google 订单、企业额度。
- 通知服务:转写完成/失败、额度不足、固件升级、会员到期、安全提醒。

## 6. 数据模型

24 张核心表,DDL 见 `server/migrations/schema.sql`,SQLAlchemy 模型见 `server/app/models/`:

users, user_identities, devices, device_bindings, firmware_versions, recordings, media_assets, upload_parts, transcription_jobs, transcript_segments, speakers, summaries, summary_templates, translations, folders, tags, subscriptions, entitlements, usage_ledger, orders, consent_logs, audit_logs, deletion_requests, notification_jobs

## 7. 权益与计费(usage_ledger 铁律)

**所有 AI 分钟扣减必须通过 usage_ledger 流水表完成,不允许只在用户表改一个"剩余分钟"数字。**

- 权益桶(entitlements):`free_monthly / gift / member / purchased / enterprise`,各带过期时间。
- 扣费顺序:先到期先扣(free_monthly → gift → member → purchased),实现在 `entitlement_service.py`,含单元测试。
- 每笔扣减/充值/退款/过期回收 = 一条 ledger 流水(`+/-` 分钟,关联 job/order),余额 = 流水聚合,支持对账。
- 退款:按原扣减流水冲正;重复扣费目标 0(幂等键 = `job_id` + `reason`)。
- 人工重试计费:终态失败已冲正后,重试开启新 generation(`charge_ref = {job_id}#g{n}`)
  **重新扣费**,余额不足拒绝;未冲正的失败任务重试免费。杜绝"扣费→退款→免费重试"。
- **原子性(Outbox)**:扣分钟 + 建任务 + 写 `outbox_events` 必须在同一 PostgreSQL
  事务(锁录音→查活跃任务→锁权益桶→写 ledger→建 job→写 outbox→提交);
  Publisher 轮询未投递行送队列(至少一次投递,消费侧按 job 状态幂等)。
- 订单:StoreKit 2 App Store Server API / Google Play Developer API 服务端验证,框架在 `order_verification.py`(供应商 SDK 接入点留接口)。

## 8. 安全与合规架构

- 传输:全链路 TLS;设备 Wi-Fi 面在局域网内,文件级 SHA-256 校验。
- 存储:音频对象存储服务端加密;设备密钥入库加密;用户区域字段决定存储区域。
- 审计:audit_logs 记录敏感操作(导出、删除、后台访问);consent_logs 记录合规同意。
- 删除:deletion_requests 驱动云端 + 缓存 + 搜索索引三处同步删除,App 内注销 + 网页删除入口。
- OTA:固件签名验证、型号限制、电量阈值、双分区/失败恢复、禁降级、App 最低版本。

## 9. 可观测性

- 移动端:Crash 收集(Crashlytics/Sentry)、埋点(见 `08-analytics-plan.md`)、日志分级上报。
- 服务端:结构化日志、任务级 AI 成本记录(usage_ledger + orders 汇总)、错误码维度告警。
- 指标看板:验收指标(`05-acceptance-criteria.md`)全部可从埋点与服务端指标直接计算。

## 10. 环境与发布

- 环境:dev / staging / prod;Feature Flag 远程开关控制灰度。
- 发布:商店分阶段放量 5% → 20% → 50% → 100%,每阶段观察 24—48 小时。
- CI:见 `.github/workflows/ci.yml`(服务端测试、mock 设备测试、Flutter analyze)。
