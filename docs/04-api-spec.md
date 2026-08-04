# API 接口规范 V1.0

> **实现状态标注**:本规范是目标契约,不代表全部已实现。当前状态:
> - **Implemented+Tested**:users/me、consents、devices(bind/list/heartbeat/unbind)、recordings CRUD、uploads(init/part/progress/complete,含越权校验与断点续传契约)、jobs(create/get/retry,含重试计费规则)、entitlements/usage、redeem、admin 骨架、notifications 骨架
> - **开发环境已实现,生产链路 Skeleton**:auth 邮箱验证码(Hash 存储/有效期/频控/尝试上限已实现并测试;dev 用 Console Provider,prod 走 SMTP Provider 但未对真实邮件服务联调;多实例共享验证码状态需 Redis)
> - **Skeleton**:orders/{platform}/verify(Provider 未接真实平台 SDK)、firmware/check
> - **Planned**:Apple/Google 登录、账户删除执行、数据导出、转写编辑、speakers、summary、translations、export、share、search、orders/restore、webhooks、WebSocket、`Idempotency-Key` 通用支持
>
> 新增实现必须同步更新本标注;严禁把 Planned 当作已完成汇报。

- Base URL:`https://api.example.com`(dev/staging/prod 各自域名)
- 版本前缀:`/v1`;管理后台前缀:`/admin`(独立鉴权)
- 认证:`Authorization: Bearer <access_token>`(JWT,短期)+ refresh token 轮换
- 实现骨架:`server/app/api/`,与本规范一一对应;OpenAPI 由 FastAPI 自动生成(`/docs`)

## 0. 通用约定

### 响应包裹
成功直接返回资源 JSON;错误统一:

```json
{ "error": { "code": "ENT_3001", "message": "insufficient minutes", "detail": {"required": 60, "available": 12} } }
```

`code` 取值见 `07-error-codes.md`。HTTP 状态码:400 参数错误 / 401 未认证 / 403 无权限 / 404 不存在 / 409 冲突(幂等键重复、状态机非法迁移)/ 422 校验失败 / 429 限流 / 5xx 服务端。

### 幂等
所有创建型接口支持 `Idempotency-Key` 请求头;上传分片与扣费天然幂等(见各节)。

### 分页
`?page=1&page_size=20`,响应含 `total`, `page`, `page_size`, `items`。

## 1. 认证与用户

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /v1/auth/email/code | 发送邮箱验证码 `{email}` |
| POST | /v1/auth/email/verify | 验证码登录/注册 `{email, code}` → `{access_token, refresh_token, is_new_user}` |
| POST | /v1/auth/apple | Apple 登录 `{identity_token}` |
| POST | /v1/auth/google | Google 登录 `{id_token}` |
| POST | /v1/auth/refresh | 刷新 `{refresh_token}` |
| GET | /v1/users/me | 当前用户(含 region、language、consent 状态) |
| PATCH | /v1/users/me | 修改昵称、语言、地区、音频保留策略 `audio_retention_days` |
| POST | /v1/users/me/export | 发起个人数据导出(异步,完成后通知) |
| POST | /v1/users/me/delete | 发起注销(写 deletion_requests,冷静期后执行) |
| POST | /v1/consents | 记录合规同意 `{consent_type, granted}`(录音合规说明确认等) |

## 2. 设备

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /v1/devices/bind | 绑定 `{sn, model, firmware_version, ble_mac?}`;SN 已被他人绑定 → `DEV_1101` |
| GET | /v1/devices | 我的设备列表(电量/容量为 App 上报的最近值) |
| GET | /v1/devices/{sn} | 设备详情 |
| POST | /v1/devices/{sn}/heartbeat | 上报状态 `{battery, storage_free, firmware_version, recording_state}` |
| DELETE | /v1/devices/{sn}/binding | 解绑 |
| GET | /v1/firmware/check?model=&current= | OTA 检测 → `{latest, min_battery, force, url, sha256, signature, min_app_version}` |
| POST | /v1/devices/{sn}/ota/report | OTA 结果上报 `{from, to, status, error_code?}` |

## 3. 录音与文件

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /v1/recordings | 登记录音元数据 `{title, source(device/phone), device_sn?, device_file_id?, duration_ms, sha256, size_bytes, recorded_at, mode}`;同 `(user, sha256)` 已存在 → 200 返回已有记录,`deduplicated: true` |
| GET | /v1/recordings | 列表(过滤:folder、tag、状态、q 全文) |
| GET | /v1/recordings/{id} | 详情(含云端/转写状态) |
| PATCH | /v1/recordings/{id} | 重命名、移动文件夹、标签、保留期限 |
| DELETE | /v1/recordings/{id} | 删除(级联音频对象、转写、摘要、索引;写 audit_logs) |

## 4. 分片上传

流程:`init → 逐分片 PUT(预签名 URL)→ 逐分片 complete 登记 → complete 合并校验`。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /v1/uploads/init | `{recording_id, size_bytes, sha256, part_size?}` → `{upload_id, part_size, parts:[{part_no, put_url}]}`;同 sha256 已有资产 → `{deduplicated: true, media_asset_id}`(不再收费) |
| POST | /v1/uploads/{upload_id}/parts/{part_no}/complete | 登记分片 `{etag, size_bytes, sha256?}`;重复登记幂等返回 200 |
| GET | /v1/uploads/{upload_id} | 查询进度(断点续传:App 重启后拉取未完成分片列表) |
| POST | /v1/uploads/{upload_id}/complete | 合并 + 整体 SHA-256 校验;不一致 → `UPL_2103` 且分片保留可重传 |
| DELETE | /v1/uploads/{upload_id} | 放弃上传 |

## 5. AI 任务

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /v1/jobs | 创建转写任务 `{recording_id, language_hint?, diarize: true, summary_template_id?}`;预检权益,不足 → `ENT_3001`;同 recording 已有成功任务 → 复用结果,`deduplicated: true`,**不重复扣费** |
| GET | /v1/jobs/{id} | 状态:`waiting/uploading/preprocessing/transcribing/diarizing/summarizing/completed/failed/retrying` + 进度 + 错误码 |
| POST | /v1/jobs/{id}/retry | 失败重试(不重复扣费,幂等键 job_id) |
| GET | /v1/jobs/{id}/events | 轮询增量事件;WebSocket `/v1/ws/jobs` 可选 |

## 6. 转写结果与文档

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /v1/recordings/{id}/transcript | 分段列表:`{segment_id, start_ms, end_ms, speaker_id, text, language, confidence}` |
| PATCH | /v1/transcript/segments/{segment_id} | 编辑文本(保留原文与编辑痕迹) |
| GET/PATCH | /v1/recordings/{id}/speakers | Speaker 列表 / 改名(`{speaker_id, display_name}` 全篇统一生效) |
| GET | /v1/recordings/{id}/summary | 摘要 + 待办;每条含 `{text, evidence:[{segment_id, start_ms, end_ms}], confidence}` |
| POST | /v1/recordings/{id}/summary/regenerate | 按模板重新生成(计入模板次数限制) |
| GET | /v1/recordings/{id}/translations?lang=zh\|en\|ar | 翻译层(独立,不改原文) |
| POST | /v1/recordings/{id}/export | `{format: txt|docx|pdf|srt|audio}` → 异步生成下载链接 |
| POST | /v1/recordings/{id}/share | 生成分享链接 `{expire_hours}`;DELETE 撤销 |
| GET | /v1/search?q= | 跨文档全文搜索(P0:标题+正文;P1:语义) |

## 7. 权益与订单

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /v1/entitlements/me | 余额分桶:`{free_monthly, gift, member, purchased, total_minutes, expiring:[...]}` |
| GET | /v1/usage | usage_ledger 流水(分页) |
| POST | /v1/orders/apple/verify | `{transaction_id 或 signed_transaction}` 服务端验证 → 发放权益(幂等:同 transaction 只发一次) |
| POST | /v1/orders/google/verify | `{purchase_token, product_id}` 同上 |
| POST | /v1/orders/restore | 恢复购买(拉取平台订单重放发放) |
| POST | /v1/redeem | 兑换码 `{code}` |
| GET | /v1/orders | 我的订单 |

服务端另接收 App Store Server Notifications V2 与 Google RTDN 回调:`/v1/webhooks/apple`、`/v1/webhooks/google`(退款→ledger 冲正,续费→顺延,过期→降级)。

## 8. 通知

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /v1/push/tokens | 注册推送 token `{platform, token}` |
| GET | /v1/notifications | 站内通知列表;PATCH 已读 |
| GET/PATCH | /v1/notification-settings | 通知开关 |

## 9. 管理后台(/admin,RBAC,全部写 audit_logs)

| 路径 | 说明 |
|---|---|
| GET /admin/users, /admin/users/{id} | 用户查询、封禁、权益调整(走 ledger adjust 流水) |
| GET /admin/devices | 设备与绑定、黑名单 |
| GET /admin/orders | 订单与退款状态 |
| GET /admin/jobs | 任务队列监控、手动重试 |
| GET /admin/costs | AI 成本汇总(按天/供应商/任务类型) |
| GET /admin/errors | 错误码维度统计 |
| POST /admin/firmware | 固件版本发布/黑名单/强制升级 |
| POST /admin/redeem-batches | 兑换码批次生成 |

## 10. 限流与配额(默认值,Feature Flag 可调)

- 验证码:同邮箱 1 次/分钟,10 次/天;登录尝试 5 次/10 分钟。
- 上传:单文件 ≤ 2GB;并发上传任务 ≤ 3。
- AI 任务:并发 ≤ 2(免费)/ 5(会员);摘要重新生成 ≤ 5 次/文档/天。
- 所有套餐均有分钟数、并发、存储与问答次数上限(无限转写不在产品承诺内)。
