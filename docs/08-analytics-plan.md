# 埋点方案 V1.0

## 1. 原则

- 每个验收指标(`05-acceptance-criteria.md`)必须能由埋点或服务端指标直接计算,禁止"上线后再补"。
- 事件命名:`snake_case`,`<域>_<动作>[_result]`;公共属性自动携带,不重复定义。
- 隐私:埋点不含录音内容、转写文本、邮箱明文;user_id 使用服务端 UUID;遵守 App 隐私标签与 Data Safety 申报;用户可在设置中关闭产品分析类埋点(崩溃与错误类除外)。
- 双端一致:Flutter 层统一封装 `AnalyticsService`,原生模块事件经 Platform Channel 汇入同一管道。

## 2. 公共属性

`app_version, platform, os_version, device_model(手机), language, region, network_type, session_id, user_id?, hardware_sn_hash?(SHA-256 前 8 位), firmware_version?, feature_flags_hash`

## 3. 核心事件表

### 3.1 生命周期与账户

| 事件 | 属性 | 用途 |
|---|---|---|
| app_launch | cold_start, duration_ms | 冷启动 P95 ≤3s |
| login_result | method(email/apple/google), success, error_code | 登录漏斗 |
| signup_complete | method | 新增用户 |
| account_delete_requested / _cancelled | — | 合规监控 |
| consent_granted | consent_type | 合规审计对齐 consent_logs |

### 3.2 设备连接(验收 §1)

| 事件 | 属性 | 用途 |
|---|---|---|
| device_scan_started / _found | duration_ms | 发现耗时 |
| device_bind_result | success, error_code, duration_ms | 绑定成功率 |
| device_connect_result | success, error_code, duration_ms, is_first, is_auto_reconnect | 首连 ≥98%、重连 ≥95%、P95 ≤8s |
| device_state_read_result | success, error_code | 状态读取 ≥99% |
| device_unbind | reason | — |

### 3.3 文件同步(验收 §2)

| 事件 | 属性 | 用途 |
|---|---|---|
| sync_started | file_count, total_bytes | — |
| wifi_join_result | success, error_code, duration_ms | 加网成功率 |
| file_download_result | success, error_code, size_bytes, duration_ms, resumed(bool), retry_count | 1h 音频 P95 ≤3min、断点续传 ≥99% |
| file_hash_check | match(bool) | Hash 一致率 100% |
| sync_recovered_from_background | queue_len | 队列恢复 |
| sync_duplicate_skipped | — | 重复率 0 监控 |

### 3.4 上传与 AI 任务(验收 §3/§4)

| 事件 | 属性 | 用途 |
|---|---|---|
| upload_result | success, error_code, size_bytes, duration_ms, parts, resumed | 上传成功率 ≥99.5% |
| job_created | recording_duration_ms, language_hint, deduplicated | 任务量、去重率 |
| job_status_changed | job_id, from, to | 状态机漏斗 |
| job_completed | processing_ms, audio_ms | 60min P95 ≤8min |
| job_failed | error_code, stage, retry_count | 成功率 ≥99% |
| transcript_edited | segment_count | 转写编辑率(质量代理指标) |
| speaker_renamed | — | 功能使用 |
| summary_viewed / todo_checked | template_id | 摘要价值 |
| export_result | format, success | 导出 |
| share_link_created | expire_hours | 分享 |

### 3.5 商业化

| 事件 | 属性 | 用途 |
|---|---|---|
| paywall_viewed | source(额度不足/会员页/...) | 转化漏斗入口 |
| purchase_started / _result | product_id, success, error_code | 付费转化 ≥5% |
| restore_result | success | 恢复购买 |
| redeem_result | success, error_code | 兑换码 |
| quota_exhausted_shown | remaining_minutes | 额度不足触达 |

### 3.6 OTA 与错误

| 事件 | 属性 | 用途 |
|---|---|---|
| ota_check / ota_started / ota_result | from, to, success, error_code, duration_ms | OTA 变砖 0 |
| error_occurred | error_code, context | 全量错误码监控(`/admin/errors` 对齐) |

## 4. 服务端指标(非埋点,Prometheus/日志聚合)

- API 成功率 ≥99.9%(按路由聚合,排除 4xx 用户错误的口径单独出)。
- 任务队列深度、各状态任务数、重试率、供应商错误率。
- AI 成本:usage_ledger + provider 计费记录按天汇总 → `/admin/costs`。
- 数据隔离审计:抽样断言响应 user_id == 请求者。

## 5. 看板与告警

- 发布放量看板:Crash-free、API 成功率、job_failed 率、purchase_result 失败率 — 放量阶段(5/20/50/100%)每档观察 24—48h。
- 告警阈值:Crash-free <99.5% 、任务失败率 >2%、ORD_5004/JOB_4001 出现即告警、OTA 失败率 >1%。
- 周报:验收指标全表自动出数,对照 `05-acceptance-criteria.md` 红黄绿。

## 6. 实施

- SDK:首版 Firebase Analytics + Crashlytics(或 Sentry),埋点封装隔离供应商;事件表以本文档为唯一事实来源,新增事件走文档 PR。
- QA:每个事件在测试环境有自动化校验(事件名、必填属性、类型);发版前跑埋点回归清单。
