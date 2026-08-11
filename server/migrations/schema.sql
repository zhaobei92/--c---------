-- YS Note — PostgreSQL schema V1.0
-- 24 张核心表。与 docs/02-architecture.md §6 及 app/models/ 保持同步。
-- 约定:
--   * 主键统一 uuid(gen_random_uuid(),需 pgcrypto 扩展)。
--   * 所有分钟增减一律走 usage_ledger 流水;entitlements 只记"授予",余额由流水聚合。
--   * 软删除用 deleted_at;合规删除由 deletion_requests 驱动物理清除。

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;

-- ============================================================ 用户域

CREATE TABLE users (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email                citext UNIQUE,
    password_hash        text,
    nickname             text,
    region               text NOT NULL DEFAULT 'CN',          -- 决定数据存储区域
    language             text NOT NULL DEFAULT 'zh',          -- zh / en / ar
    audio_retention_days integer,                              -- NULL = 永久保留
    status               text NOT NULL DEFAULT 'active',      -- active / banned / deleting / deleted
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE user_identities (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider     text NOT NULL,                                -- email / apple / google
    provider_uid text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (provider, provider_uid)
);

CREATE TABLE consent_logs (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    consent_type text NOT NULL,        -- recording_compliance / privacy_policy / terms / analytics
    granted      boolean NOT NULL,
    app_version  text,
    ip_hash      text,                 -- 不存明文 IP
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_consent_logs_user ON consent_logs (user_id, consent_type, created_at DESC);

CREATE TABLE deletion_requests (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       uuid NOT NULL REFERENCES users(id),
    scope         text NOT NULL DEFAULT 'account',   -- account / audio / document
    target_id     uuid,                              -- scope 非 account 时指向具体资源
    status        text NOT NULL DEFAULT 'cooling',   -- cooling / executing / completed / cancelled
    requested_at  timestamptz NOT NULL DEFAULT now(),
    execute_after timestamptz NOT NULL,              -- 冷静期截止
    completed_at  timestamptz
);
CREATE INDEX idx_deletion_requests_due ON deletion_requests (status, execute_after);

-- ============================================================ 设备域

CREATE TABLE devices (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sn                 text NOT NULL UNIQUE,
    model              text NOT NULL,
    firmware_version   text,
    device_key_enc     text,                          -- 设备密钥,应用层加密存储
    first_activated_at timestamptz,
    warranty_until     date,
    blacklisted        boolean NOT NULL DEFAULT false,
    last_seen_at       timestamptz,
    battery            smallint,                      -- App 心跳上报的最近值
    storage_free_mb    integer,
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE device_bindings (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id  uuid NOT NULL REFERENCES devices(id),
    user_id    uuid NOT NULL REFERENCES users(id),
    active     boolean NOT NULL DEFAULT true,
    bound_at   timestamptz NOT NULL DEFAULT now(),
    unbound_at timestamptz
);
-- 验收红线:设备绑定串号 = 0 → 一台设备同一时刻至多一条活跃绑定
CREATE UNIQUE INDEX uq_device_bindings_active ON device_bindings (device_id) WHERE active;
CREATE INDEX idx_device_bindings_user ON device_bindings (user_id) WHERE active;

CREATE TABLE firmware_versions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    model           text NOT NULL,
    version         text NOT NULL,
    url             text NOT NULL,
    sha256          text NOT NULL,
    signature       text NOT NULL,                    -- 固件包签名,升级前验签
    min_battery     smallint NOT NULL DEFAULT 30,
    min_app_version text,
    force_update    boolean NOT NULL DEFAULT false,
    blacklisted     boolean NOT NULL DEFAULT false,   -- 事故固件拉黑
    released_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (model, version)
);

-- ============================================================ 录音与文件域

CREATE TABLE folders (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       text NOT NULL,
    parent_id  uuid REFERENCES folders(id),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE tags (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, name)
);

CREATE TABLE media_assets (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(id),
    -- 去重范围限定单用户(P0-4):跨用户去重会泄露音频存在性并共享资产 ID,
    -- 涉及删除生命周期/引用计数/数据区域/加密域;数据治理方案落地前不做全局去重。
    sha256      text NOT NULL,
    storage_key text NOT NULL,                        -- 对象存储 key
    size_bytes  bigint NOT NULL,
    mime        text,
    duration_ms integer,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, sha256)
);

CREATE TABLE recordings (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        uuid NOT NULL REFERENCES users(id),
    title          text NOT NULL,
    source         text NOT NULL DEFAULT 'device',    -- device / phone
    device_sn      text,
    device_file_id text,                              -- 设备端文件标识
    mode           text,                              -- meeting / call / memo ...
    duration_ms    integer,
    size_bytes     bigint,
    sha256         text,
    media_asset_id uuid REFERENCES media_assets(id),
    local_status   text NOT NULL DEFAULT 'none',      -- none / syncing / synced
    cloud_status   text NOT NULL DEFAULT 'none',      -- none / uploading / uploaded / processed
    folder_id      uuid REFERENCES folders(id),
    tag_ids        uuid[] NOT NULL DEFAULT '{}',      -- 关联 tags.id(首版不建 join 表)
    recorded_at    timestamptz,
    retention_days integer,                           -- 覆盖用户默认保留策略
    deleted_at     timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);
-- 验收红线:重复文件率 = 0
CREATE UNIQUE INDEX uq_recordings_user_sha ON recordings (user_id, sha256) WHERE sha256 IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX idx_recordings_user ON recordings (user_id, created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX idx_recordings_fts ON recordings USING gin (to_tsvector('simple', title));

-- 上传会话状态存 Redis(app/services/upload_service.py),分片持久化记录在此,App 被杀后据此续传。
CREATE TABLE upload_parts (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    upload_id    uuid NOT NULL,
    recording_id uuid NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
    part_no      integer NOT NULL,
    size_bytes   bigint,
    etag         text,
    sha256       text,
    status       text NOT NULL DEFAULT 'pending',     -- pending / uploaded
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (upload_id, part_no)
);
CREATE INDEX idx_upload_parts_upload ON upload_parts (upload_id, status);

-- ============================================================ AI 域

CREATE TABLE transcription_jobs (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    recording_id    uuid NOT NULL REFERENCES recordings(id),
    user_id         uuid NOT NULL REFERENCES users(id),
    -- 状态机:waiting/uploading/preprocessing/transcribing/diarizing/summarizing/completed/failed/retrying
    -- 迁移必须走 app/services/job_state_machine.py,禁止直接 UPDATE。
    status          text NOT NULL DEFAULT 'waiting',
    language_hint   text,                             -- 可空:走分段语言识别
    diarize         boolean NOT NULL DEFAULT true,
    template_id     uuid,
    error_code      text,
    retry_count     integer NOT NULL DEFAULT 0,
    minutes_charged integer NOT NULL DEFAULT 0,       -- 已扣分钟(冲正走 ledger)
    asr_provider    text,
    -- AI 成本账本(分):单次任务成本 = asr + diarization + llm + storage + egress
    cost_asr_cents      integer NOT NULL DEFAULT 0,
    cost_diar_cents     integer NOT NULL DEFAULT 0,
    cost_llm_cents      integer NOT NULL DEFAULT 0,
    cost_storage_cents  integer NOT NULL DEFAULT 0,
    cost_egress_cents   integer NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    completed_at    timestamptz
);
CREATE INDEX idx_jobs_status ON transcription_jobs (status, created_at);
CREATE INDEX idx_jobs_user ON transcription_jobs (user_id, created_at DESC);
-- 同一录音同一时刻至多一个未终态任务(防重复提交重复扣费)
CREATE UNIQUE INDEX uq_jobs_active ON transcription_jobs (recording_id)
    WHERE status NOT IN ('completed', 'failed');

CREATE TABLE speakers (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    recording_id uuid NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
    label        text NOT NULL,                       -- Speaker 1 / Speaker 2 ...
    display_name text,                                -- 用户改名,全篇经此表统一生效
    UNIQUE (recording_id, label)
);

CREATE TABLE transcript_segments (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id       uuid NOT NULL REFERENCES transcription_jobs(id) ON DELETE CASCADE,
    recording_id uuid NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
    seq          integer NOT NULL,
    start_ms     integer NOT NULL,
    end_ms       integer NOT NULL,
    speaker_id   uuid REFERENCES speakers(id),
    language     text,                                -- 分段语言识别结果
    text         text NOT NULL,
    text_edited  text,                                -- 用户编辑稿;原文保留
    confidence   real,
    UNIQUE (recording_id, seq)
);
CREATE INDEX idx_segments_fts ON transcript_segments USING gin (to_tsvector('simple', coalesce(text_edited, text)));

CREATE TABLE summary_templates (
    id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    key       text NOT NULL UNIQUE,   -- general/meeting/lecture/sales/interview/call/site/todo
    name_i18n jsonb NOT NULL,         -- {"zh":..,"en":..,"ar":..}
    prompt    text NOT NULL,
    builtin   boolean NOT NULL DEFAULT true,
    active    boolean NOT NULL DEFAULT true
);

CREATE TABLE summaries (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    recording_id uuid NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
    job_id       uuid REFERENCES transcription_jobs(id),
    template_id  uuid REFERENCES summary_templates(id),
    -- content.items[]: {type: conclusion/todo, text, confidence,
    --                   evidence: [{segment_id, start_ms, end_ms}]}
    -- 验收红线:关键待办 100% 有时间戳证据;无依据待办率 < 2%
    content      jsonb NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_summaries_recording ON summaries (recording_id, created_at DESC);

CREATE TABLE translations (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    recording_id uuid NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
    lang         text NOT NULL,                       -- zh / en / ar
    content      jsonb NOT NULL,                      -- 独立翻译层,不修改原始转写
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (recording_id, lang)
);

-- ============================================================ 商业化域

CREATE TABLE subscriptions (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 uuid NOT NULL REFERENCES users(id),
    platform                text NOT NULL,            -- apple / google
    product_id              text NOT NULL,
    original_transaction_id text NOT NULL UNIQUE,     -- 跨设备恢复购买的锚点
    status                  text NOT NULL,             -- active / grace / expired / refunded
    auto_renew              boolean NOT NULL DEFAULT true,
    started_at              timestamptz NOT NULL,
    expires_at              timestamptz NOT NULL,
    last_verified_at        timestamptz,
    created_at              timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_subscriptions_user ON subscriptions (user_id, expires_at DESC);

CREATE TABLE entitlements (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES users(id),
    bucket          text NOT NULL,                    -- free_monthly / gift / member / purchased / enterprise
    minutes_granted integer NOT NULL,
    effective_at    timestamptz NOT NULL DEFAULT now(),
    expires_at      timestamptz,                      -- NULL = 不过期(purchased 包)
    source_type     text NOT NULL,                    -- order / redeem / hardware / system / admin
    source_id       uuid,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_entitlements_user ON entitlements (user_id, expires_at);

-- 铁律:所有分钟增减唯一入口。余额 = SUM(delta_minutes) 按桶聚合,支持对账。
CREATE TABLE usage_ledger (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES users(id),
    entitlement_id  uuid REFERENCES entitlements(id),
    delta_minutes   integer NOT NULL,                 -- 授予为正,消耗为负,冲正为正
    reason          text NOT NULL,                    -- grant / consume / refund / expire / adjust
    job_id          uuid REFERENCES transcription_jobs(id),
    charge_generation integer NOT NULL DEFAULT 0,     -- 人工重试代次(0 = 首次扣费)
    order_id        uuid,
    idempotency_key text UNIQUE,                      -- 验收红线:重复扣费 = 0
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_ledger_user ON usage_ledger (user_id, created_at DESC);
CREATE INDEX idx_ledger_entitlement ON usage_ledger (entitlement_id);

CREATE TABLE orders (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        uuid NOT NULL REFERENCES users(id),
    platform       text NOT NULL,                     -- apple / google / redeem
    product_id     text NOT NULL,
    transaction_id text UNIQUE,                       -- 平台交易号,幂等锚点
    purchase_token text,                              -- Google
    status         text NOT NULL DEFAULT 'pending',   -- pending / verified / refunded / expired / failed
    amount_cents   integer,
    currency       text,
    raw_payload    jsonb,                             -- 平台原始凭证,审计留存
    verified_at    timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_orders_user ON orders (user_id, created_at DESC);

-- ============================================================ 运营域

CREATE TABLE audit_logs (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_type  text NOT NULL,                        -- user / admin / system
    actor_id    uuid,
    action      text NOT NULL,                        -- export_data / delete_recording / admin_adjust ...
    target_type text,
    target_id   uuid,
    detail      jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_logs_target ON audit_logs (target_type, target_id, created_at DESC);

-- Outbox(P0-5):与业务写入(扣分钟 + 建任务)同一事务落库,
-- Publisher 轮询未投递行 → 投递队列 → 标记 published_at(至少一次投递)。
CREATE TABLE outbox_events (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    topic        text NOT NULL,
    payload      jsonb NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz
);
CREATE INDEX idx_outbox_unpublished ON outbox_events (created_at) WHERE published_at IS NULL;

CREATE TABLE notification_jobs (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel      text NOT NULL,                       -- push / email / inapp
    type         text NOT NULL,                       -- job_completed / quota_low / ota_available / member_expiring / security
    payload      jsonb NOT NULL,
    status       text NOT NULL DEFAULT 'pending',     -- pending / sent / failed
    scheduled_at timestamptz NOT NULL DEFAULT now(),
    sent_at      timestamptz,
    error        text
);
CREATE INDEX idx_notification_jobs_due ON notification_jobs (status, scheduled_at);
