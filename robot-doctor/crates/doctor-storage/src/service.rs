//! The storage service: one dedicated thread owns the SQLite connection and
//! serializes every read/write. Concurrent diagnostic workers never touch
//! SQLite directly — they send messages and await replies. A storage failure
//! is returned to the caller (and logged); it never corrupts the engine.

use crate::c2::read_entities;
use crate::schema;
use crate::types::{PluginSnapshot, RunFilter, RunSummaryRow, StoredRun};
use chrono::{DateTime, Utc};
use doctor_domain::{
    CheckError, CheckResult, CheckRun, CheckStatus, Device, DiagnosticMode, Evidence, EvidenceId,
    Finding, HealthState, Observation, RunId,
};
use rusqlite::{params, params_from_iter, Connection, OptionalExtension};
use serde::de::DeserializeOwned;
use serde::Serialize;
use std::path::PathBuf;
use thiserror::Error;
use tokio::sync::{mpsc, oneshot};

#[derive(Debug, Error)]
pub enum StorageError {
    #[error("sqlite error: {0}")]
    Sqlite(String),
    #[error("storage service is not running")]
    ServiceStopped,
    #[error("data corruption: {0}")]
    Corrupt(String),
}

impl From<rusqlite::Error> for StorageError {
    fn from(e: rusqlite::Error) -> Self {
        StorageError::Sqlite(e.to_string())
    }
}

type Reply<T> = oneshot::Sender<Result<T, StorageError>>;

enum Cmd {
    UpsertDevice(Device, Reply<()>),
    BeginRun {
        run_id: RunId,
        device_id: String,
        mode: DiagnosticMode,
        started_at: DateTime<Utc>,
        app_version: String,
        snapshots: Vec<PluginSnapshot>,
        reply: Reply<()>,
    },
    AppendResult {
        run_id: RunId,
        result: Box<CheckResult>,
        reply: Reply<()>,
    },
    FinishRun {
        run_id: RunId,
        health: HealthState,
        finished_at: DateTime<Utc>,
        reply: Reply<()>,
    },
    ListRuns(RunFilter, Reply<Vec<RunSummaryRow>>),
    GetRun(RunId, Reply<Option<StoredRun>>),
    DeleteRun(RunId, Reply<bool>),
    /// Run a typed closure on the storage thread. Keeps the Phase C2
    /// surface (baselines, profiles, evaluations) from doubling this
    /// message enum while preserving full type safety: the closure owns
    /// its own reply channel.
    Exec(Box<dyn FnOnce(&mut Connection) + Send>),
    GetSetting(String, Reply<Option<String>>),
    SetSetting(String, String, Reply<()>),
    Close(Reply<()>),
}

/// Async handle to the storage service. Cheap to clone.
#[derive(Clone)]
pub struct Storage {
    tx: mpsc::Sender<Cmd>,
}

impl Storage {
    /// Open (creating/migrating as needed) a database file and start the
    /// writer thread.
    pub async fn open(path: impl Into<PathBuf>) -> Result<Storage, StorageError> {
        Self::start(Some(path.into())).await
    }

    /// In-memory database (tests).
    pub async fn open_in_memory() -> Result<Storage, StorageError> {
        Self::start(None).await
    }

    async fn start(path: Option<PathBuf>) -> Result<Storage, StorageError> {
        let (tx, rx) = mpsc::channel::<Cmd>(256);
        let (init_tx, init_rx) = oneshot::channel();
        std::thread::Builder::new()
            .name("doctor-storage".into())
            .spawn(move || service_thread(path, rx, init_tx))
            .expect("spawn storage thread");
        init_rx.await.map_err(|_| StorageError::ServiceStopped)??;
        Ok(Storage { tx })
    }

    async fn call<T>(&self, build: impl FnOnce(Reply<T>) -> Cmd) -> Result<T, StorageError> {
        let (tx, rx) = oneshot::channel();
        self.tx
            .send(build(tx))
            .await
            .map_err(|_| StorageError::ServiceStopped)?;
        rx.await.map_err(|_| StorageError::ServiceStopped)?
    }

    /// Execute `work` on the storage thread and await its result.
    ///
    /// This is the extension point every Phase C2 operation uses: writes
    /// stay serialized on one connection, and a storage failure is
    /// returned to the caller instead of poisoning the engine.
    pub async fn with_conn<T, F>(&self, work: F) -> Result<T, StorageError>
    where
        F: FnOnce(&mut Connection) -> Result<T, StorageError> + Send + 'static,
        T: Send + 'static,
    {
        let (tx, rx) = oneshot::channel();
        self.tx
            .send(Cmd::Exec(Box::new(move |conn| {
                let _ = tx.send(work(conn));
            })))
            .await
            .map_err(|_| StorageError::ServiceStopped)?;
        rx.await.map_err(|_| StorageError::ServiceStopped)?
    }

    pub async fn upsert_device(&self, device: Device) -> Result<(), StorageError> {
        self.call(|r| Cmd::UpsertDevice(device, r)).await
    }

    pub async fn begin_run(
        &self,
        run: &CheckRun,
        app_version: &str,
        snapshots: Vec<PluginSnapshot>,
    ) -> Result<(), StorageError> {
        let (run_id, device_id, mode, started_at) = (
            run.id.clone(),
            run.device_id.to_string(),
            run.mode,
            run.started_at,
        );
        let app_version = app_version.to_owned();
        self.call(move |reply| Cmd::BeginRun {
            run_id,
            device_id,
            mode,
            started_at,
            app_version,
            snapshots,
            reply,
        })
        .await
    }

    pub async fn append_result(
        &self,
        run_id: &RunId,
        result: CheckResult,
    ) -> Result<(), StorageError> {
        let run_id = run_id.clone();
        self.call(move |reply| Cmd::AppendResult {
            run_id,
            result: Box::new(result),
            reply,
        })
        .await
    }

    pub async fn finish_run(
        &self,
        run_id: &RunId,
        health: HealthState,
        finished_at: DateTime<Utc>,
    ) -> Result<(), StorageError> {
        let run_id = run_id.clone();
        self.call(move |reply| Cmd::FinishRun {
            run_id,
            health,
            finished_at,
            reply,
        })
        .await
    }

    pub async fn list_runs(&self, filter: RunFilter) -> Result<Vec<RunSummaryRow>, StorageError> {
        self.call(|r| Cmd::ListRuns(filter, r)).await
    }

    pub async fn get_run(&self, run_id: &RunId) -> Result<Option<StoredRun>, StorageError> {
        let run_id = run_id.clone();
        self.call(|r| Cmd::GetRun(run_id, r)).await
    }

    pub async fn delete_run(&self, run_id: &RunId) -> Result<bool, StorageError> {
        let run_id = run_id.clone();
        self.call(|r| Cmd::DeleteRun(run_id, r)).await
    }

    pub async fn get_setting(&self, key: &str) -> Result<Option<String>, StorageError> {
        let key = key.to_owned();
        self.call(|r| Cmd::GetSetting(key, r)).await
    }

    pub async fn set_setting(&self, key: &str, value: &str) -> Result<(), StorageError> {
        let (key, value) = (key.to_owned(), value.to_owned());
        self.call(|r| Cmd::SetSetting(key, value, r)).await
    }

    /// Flush and stop the service thread.
    pub async fn close(&self) -> Result<(), StorageError> {
        self.call(Cmd::Close).await
    }
}

fn service_thread(
    path: Option<PathBuf>,
    mut rx: mpsc::Receiver<Cmd>,
    init_tx: oneshot::Sender<Result<(), StorageError>>,
) {
    let conn = match open_connection(path) {
        Ok(conn) => {
            let _ = init_tx.send(Ok(()));
            conn
        }
        Err(err) => {
            let _ = init_tx.send(Err(err));
            return;
        }
    };
    let mut conn = conn;
    while let Some(cmd) = rx.blocking_recv() {
        match cmd {
            Cmd::UpsertDevice(device, reply) => {
                let _ = reply.send(upsert_device(&conn, &device));
            }
            Cmd::BeginRun {
                run_id,
                device_id,
                mode,
                started_at,
                app_version,
                snapshots,
                reply,
            } => {
                let _ = reply.send(begin_run(
                    &mut conn,
                    &run_id,
                    &device_id,
                    mode,
                    started_at,
                    &app_version,
                    &snapshots,
                ));
            }
            Cmd::AppendResult {
                run_id,
                result,
                reply,
            } => {
                let _ = reply.send(append_result(&mut conn, &run_id, &result));
            }
            Cmd::FinishRun {
                run_id,
                health,
                finished_at,
                reply,
            } => {
                let _ = reply.send(finish_run(&conn, &run_id, health, finished_at));
            }
            Cmd::ListRuns(filter, reply) => {
                let _ = reply.send(list_runs(&conn, &filter));
            }
            Cmd::GetRun(run_id, reply) => {
                let _ = reply.send(get_run(&conn, &run_id));
            }
            Cmd::DeleteRun(run_id, reply) => {
                let _ = reply.send(delete_run(&conn, &run_id));
            }
            Cmd::Exec(work) => work(&mut conn),
            Cmd::GetSetting(key, reply) => {
                let _ = reply.send(get_setting(&conn, &key));
            }
            Cmd::SetSetting(key, value, reply) => {
                let _ = reply.send(set_setting(&conn, &key, &value));
            }
            Cmd::Close(reply) => {
                let _ = reply.send(Ok(()));
                break;
            }
        }
    }
}

fn open_connection(path: Option<PathBuf>) -> Result<Connection, StorageError> {
    let mut conn = match &path {
        Some(p) => {
            if let Some(parent) = p.parent() {
                std::fs::create_dir_all(parent)
                    .map_err(|e| StorageError::Sqlite(format!("create db dir: {e}")))?;
            }
            Connection::open(p)?
        }
        None => Connection::open_in_memory()?,
    };
    conn.pragma_update(None, "foreign_keys", "ON")?;
    conn.busy_timeout(std::time::Duration::from_millis(5000))?;
    if path.is_some() {
        // WAL improves desktop reliability/concurrency for file databases.
        let _mode: String = conn.pragma_query_value(None, "journal_mode", |row| row.get(0))?;
        conn.pragma_update(None, "journal_mode", "WAL")?;
        conn.pragma_update(None, "synchronous", "NORMAL")?;
    }
    schema::migrate(&mut conn)?;
    Ok(conn)
}

// ── serde helpers: enums as their wire strings ─────────────────────────

fn enum_str<T: Serialize>(value: &T) -> String {
    match serde_json::to_value(value) {
        Ok(serde_json::Value::String(s)) => s,
        other => panic!("expected string-serializable enum, got {other:?}"),
    }
}

fn enum_parse<T: DeserializeOwned>(s: &str) -> Result<T, StorageError> {
    serde_json::from_value(serde_json::Value::String(s.to_owned()))
        .map_err(|e| StorageError::Corrupt(format!("bad enum value '{s}': {e}")))
}

fn parse_ts(s: &str) -> Result<DateTime<Utc>, StorageError> {
    DateTime::parse_from_rfc3339(s)
        .map(|t| t.with_timezone(&Utc))
        .map_err(|e| StorageError::Corrupt(format!("bad timestamp '{s}': {e}")))
}

// ── writes ─────────────────────────────────────────────────────────────

fn upsert_device(conn: &Connection, device: &Device) -> Result<(), StorageError> {
    conn.execute(
        "INSERT INTO devices (id, name, endpoint, added_at) VALUES (?1, ?2, ?3, ?4)
         ON CONFLICT(id) DO UPDATE SET name = excluded.name, endpoint = excluded.endpoint",
        params![
            device.id.as_str(),
            device.name,
            serde_json::to_string(&device.endpoint).expect("endpoint serializes"),
            device.added_at.to_rfc3339(),
        ],
    )?;
    Ok(())
}

fn begin_run(
    conn: &mut Connection,
    run_id: &RunId,
    device_id: &str,
    mode: DiagnosticMode,
    started_at: DateTime<Utc>,
    app_version: &str,
    snapshots: &[PluginSnapshot],
) -> Result<(), StorageError> {
    let tx = conn.transaction()?;
    tx.execute(
        "INSERT INTO diagnostic_runs (id, device_id, mode, status, started_at, app_version)
         VALUES (?1, ?2, ?3, 'RUNNING', ?4, ?5)",
        params![
            run_id.as_str(),
            device_id,
            enum_str(&mode),
            started_at.to_rfc3339(),
            app_version,
        ],
    )?;
    for snap in snapshots {
        tx.execute(
            "INSERT INTO plugin_snapshots (run_id, plugin_id, version, api_version, capabilities)
             VALUES (?1, ?2, ?3, ?4, ?5)",
            params![
                run_id.as_str(),
                snap.plugin_id,
                snap.version,
                snap.api_version,
                serde_json::to_string(&snap.capabilities).expect("capabilities serialize"),
            ],
        )?;
    }
    tx.commit()?;
    Ok(())
}

fn result_health(result: &CheckResult) -> HealthState {
    let mut health = result.status.implied_health();
    for finding in &result.findings {
        health = health.worst(finding.implied_health());
    }
    health
}

fn result_summary(result: &CheckResult) -> String {
    if let Some(finding) = result.findings.first() {
        return finding.title.clone();
    }
    if let Some(err) = &result.error {
        return err.message.clone();
    }
    String::new()
}

fn append_result(
    conn: &mut Connection,
    run_id: &RunId,
    result: &CheckResult,
) -> Result<(), StorageError> {
    let result_id = uuid::Uuid::new_v4().to_string();
    let tx = conn.transaction()?;
    tx.execute(
        "INSERT INTO check_results
         (id, run_id, check_id, plugin_id, status, health, started_at, duration_ms,
          error_kind, error_message, summary,
          projection_namespace, projection_observed, projection_reason, projection_kinds)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)",
        params![
            result_id,
            run_id.as_str(),
            result.check_id.as_str(),
            result.plugin_id.as_str(),
            enum_str(&result.status),
            enum_str(&result_health(result)),
            result.started_at.to_rfc3339(),
            result.duration_ms as i64,
            result.error.as_ref().map(|e| enum_str(&e.status)),
            result.error.as_ref().map(|e| e.message.clone()),
            result_summary(result),
            result.projection.as_ref().map(|p| p.namespace.clone()),
            result.projection.as_ref().map(|p| i64::from(p.observed)),
            result.projection.as_ref().and_then(|p| p.reason.clone()),
            result
                .projection
                .as_ref()
                .map(|p| serde_json::to_string(&p.kinds).expect("kinds serialize")),
        ],
    )?;
    // Comparison entities (C2) are stored per run so baseline capture and
    // profile evaluation never need to relaunch plugins.
    if let Some(projection) = &result.projection {
        for entity in &projection.entities {
            tx.execute(
                "INSERT INTO run_entities
                 (id, run_id, result_id, namespace, kind, entity_key, display_name,
                  attributes, source_plugin, source_check, evidence_ids)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
                params![
                    uuid::Uuid::new_v4().to_string(),
                    run_id.as_str(),
                    result_id,
                    entity.key.namespace,
                    entity.key.kind,
                    entity.key.key,
                    entity.display_name,
                    serde_json::to_string(&entity.attributes).expect("attributes serialize"),
                    entity.source_plugin.as_str(),
                    entity.source_check.as_str(),
                    serde_json::to_string(&entity.source_evidence_ids).expect("ids serialize"),
                ],
            )?;
        }
    }
    for obs in &result.observations {
        tx.execute(
            "INSERT INTO observations (id, result_id, key, value, unit, evidence_ids, observed_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![
                obs.id.as_str(),
                result_id,
                obs.key,
                serde_json::to_string(&obs.value).expect("observation value serializes"),
                obs.unit,
                serde_json::to_string(&obs.evidence_ids).expect("ids serialize"),
                obs.observed_at.to_rfc3339(),
            ],
        )?;
    }
    for ev in &result.evidence {
        tx.execute(
            "INSERT INTO evidence (id, result_id, kind, source, summary, data, captured_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![
                ev.id.as_str(),
                result_id,
                enum_str(&ev.kind),
                ev.source,
                ev.summary,
                serde_json::to_string(&ev.data).expect("evidence data serializes"),
                ev.captured_at.to_rfc3339(),
            ],
        )?;
    }
    for finding in &result.findings {
        tx.execute(
            "INSERT INTO findings
             (id, result_id, device_id, check_id, rule_id, severity, code, title, detail,
              subject, detected_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
            params![
                finding.id.as_str(),
                result_id,
                finding.device_id.as_str(),
                finding.check_id.as_str(),
                finding.rule_id.as_ref().map(|r| r.as_str()),
                enum_str(&finding.severity),
                finding.code,
                finding.title,
                finding.detail,
                finding.subject,
                finding.detected_at.to_rfc3339(),
            ],
        )?;
        for evidence_id in &finding.evidence_ids {
            tx.execute(
                "INSERT OR IGNORE INTO finding_evidence (finding_id, evidence_id) VALUES (?1, ?2)",
                params![finding.id.as_str(), evidence_id.as_str()],
            )?;
        }
    }
    tx.commit()?;
    Ok(())
}

fn finish_run(
    conn: &Connection,
    run_id: &RunId,
    health: HealthState,
    finished_at: DateTime<Utc>,
) -> Result<(), StorageError> {
    conn.execute(
        "UPDATE diagnostic_runs SET status = 'COMPLETED', finished_at = ?2, overall_health = ?3
         WHERE id = ?1",
        params![run_id.as_str(), finished_at.to_rfc3339(), enum_str(&health)],
    )?;
    Ok(())
}

// ── reads ──────────────────────────────────────────────────────────────

fn list_runs(conn: &Connection, filter: &RunFilter) -> Result<Vec<RunSummaryRow>, StorageError> {
    let mut where_clauses: Vec<&str> = Vec::new();
    let mut args: Vec<String> = Vec::new();
    if let Some(device) = &filter.device_id {
        where_clauses.push("r.device_id = ?");
        args.push(device.clone());
    }
    if let Some(mode) = &filter.mode {
        where_clauses.push("r.mode = ?");
        args.push(enum_str(mode));
    }
    if let Some(health) = &filter.health {
        where_clauses.push("r.overall_health = ?");
        args.push(enum_str(health));
    }
    if let Some(from) = &filter.from {
        where_clauses.push("r.started_at >= ?");
        args.push(from.to_rfc3339());
    }
    if let Some(to) = &filter.to {
        where_clauses.push("r.started_at <= ?");
        args.push(to.to_rfc3339());
    }
    let where_sql = if where_clauses.is_empty() {
        String::new()
    } else {
        format!("WHERE {}", where_clauses.join(" AND "))
    };
    let limit = filter.limit.unwrap_or(50).min(500);
    let offset = filter.offset.unwrap_or(0);
    let sql = format!(
        "SELECT r.id, r.device_id, COALESCE(d.name, r.device_id), r.mode, r.status,
                r.started_at, r.finished_at, r.overall_health, r.app_version,
                (SELECT COUNT(*) FROM check_results c WHERE c.run_id = r.id),
                (SELECT COUNT(*) FROM findings f JOIN check_results c ON f.result_id = c.id
                  WHERE c.run_id = r.id)
         FROM diagnostic_runs r
         LEFT JOIN devices d ON d.id = r.device_id
         {where_sql}
         ORDER BY r.started_at DESC
         LIMIT {limit} OFFSET {offset}"
    );
    let mut stmt = conn.prepare(&sql)?;
    let rows = stmt.query_map(params_from_iter(args.iter()), |row| {
        let started_at: String = row.get(5)?;
        let finished_at: Option<String> = row.get(6)?;
        Ok(RunSummaryRow {
            id: row.get(0)?,
            device_id: row.get(1)?,
            device_name: row.get(2)?,
            mode: row.get(3)?,
            status: row.get(4)?,
            started_at: started_at.clone(),
            finished_at: finished_at.clone(),
            overall_health: row.get(7)?,
            app_version: row.get(8)?,
            duration_ms: match (&started_at, &finished_at) {
                (s, Some(f)) => match (
                    DateTime::parse_from_rfc3339(s),
                    DateTime::parse_from_rfc3339(f),
                ) {
                    (Ok(s), Ok(f)) => Some((f - s).num_milliseconds()),
                    _ => None,
                },
                _ => None,
            },
            check_count: row.get(9)?,
            finding_count: row.get(10)?,
        })
    })?;
    let mut out = Vec::new();
    for row in rows {
        out.push(row?);
    }
    Ok(out)
}

fn get_run(conn: &Connection, run_id: &RunId) -> Result<Option<StoredRun>, StorageError> {
    let header = conn
        .query_row(
            "SELECT device_id, mode, status, started_at, finished_at, overall_health, app_version
             FROM diagnostic_runs WHERE id = ?1",
            params![run_id.as_str()],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, Option<String>>(4)?,
                    row.get::<_, Option<String>>(5)?,
                    row.get::<_, String>(6)?,
                ))
            },
        )
        .optional()?;
    let Some((device_id, mode, status, started_at, finished_at, overall_health, app_version)) =
        header
    else {
        return Ok(None);
    };

    let mut run = CheckRun {
        id: run_id.clone(),
        device_id: device_id.as_str().into(),
        mode: enum_parse(&mode)?,
        started_at: parse_ts(&started_at)?,
        finished_at: finished_at.as_deref().map(parse_ts).transpose()?,
        results: Vec::new(),
    };

    // Check results (with their storage ids for child lookups).
    let mut stmt = conn.prepare(
        "SELECT id, check_id, plugin_id, status, started_at, duration_ms, error_kind, error_message,
                projection_namespace, projection_observed, projection_reason, projection_kinds
         FROM check_results WHERE run_id = ?1 ORDER BY started_at",
    )?;
    let result_rows: Vec<(String, CheckResult)> = stmt
        .query_map(params![run_id.as_str()], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
                row.get::<_, i64>(5)?,
                row.get::<_, Option<String>>(6)?,
                row.get::<_, Option<String>>(7)?,
                row.get::<_, Option<String>>(8)?,
                row.get::<_, Option<i64>>(9)?,
                row.get::<_, Option<String>>(10)?,
                row.get::<_, Option<String>>(11)?,
            ))
        })?
        .collect::<Result<Vec<_>, _>>()?
        .into_iter()
        .map(
            |(
                rid,
                check_id,
                plugin_id,
                status,
                started,
                duration,
                ekind,
                emsg,
                pns,
                pobs,
                prsn,
                pkinds,
            )| {
                let status: CheckStatus = enum_parse(&status)?;
                let error = match (ekind, emsg) {
                    (Some(kind), msg) => Some(CheckError {
                        status: enum_parse(&kind)?,
                        message: msg.unwrap_or_default(),
                    }),
                    _ => None,
                };
                // Entities are attached below; the header restores the
                // namespace/observed signal that decides UNKNOWN semantics.
                let kinds: Vec<String> = pkinds
                    .as_deref()
                    .and_then(|text| serde_json::from_str(text).ok())
                    .unwrap_or_default();
                let projection = pns.map(|namespace| doctor_domain::ProjectionReport {
                    namespace,
                    observed: pobs.unwrap_or(0) != 0,
                    kinds,
                    entities: vec![],
                    reason: prsn,
                });
                Ok((
                    rid,
                    CheckResult {
                        check_id: check_id.as_str().into(),
                        plugin_id: plugin_id.as_str().into(),
                        device_id: device_id.as_str().into(),
                        status,
                        started_at: parse_ts(&started)?,
                        duration_ms: duration as u64,
                        observations: vec![],
                        evidence: vec![],
                        findings: vec![],
                        error,
                        projection,
                    },
                ))
            },
        )
        .collect::<Result<Vec<_>, StorageError>>()?;

    let mut results = Vec::new();
    for (result_row_id, mut result) in result_rows {
        // Observations
        let mut stmt = conn.prepare(
            "SELECT id, key, value, unit, evidence_ids, observed_at
             FROM observations WHERE result_id = ?1",
        )?;
        let obs_rows = stmt
            .query_map(params![result_row_id], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, Option<String>>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, String>(5)?,
                ))
            })?
            .collect::<Result<Vec<_>, _>>()?;
        for (id, key, value, unit, evidence_ids, observed_at) in obs_rows {
            result.observations.push(Observation {
                id: id.as_str().into(),
                check_id: result.check_id.clone(),
                key,
                value: serde_json::from_str(&value)
                    .map_err(|e| StorageError::Corrupt(format!("observation value: {e}")))?,
                unit,
                evidence_ids: serde_json::from_str(&evidence_ids)
                    .map_err(|e| StorageError::Corrupt(format!("observation evidence: {e}")))?,
                observed_at: parse_ts(&observed_at)?,
            });
        }

        // Evidence
        let mut stmt = conn.prepare(
            "SELECT id, kind, source, summary, data, captured_at
             FROM evidence WHERE result_id = ?1",
        )?;
        let ev_rows = stmt
            .query_map(params![result_row_id], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, String>(5)?,
                ))
            })?
            .collect::<Result<Vec<_>, _>>()?;
        for (id, kind, source, summary, data, captured_at) in ev_rows {
            result.evidence.push(Evidence {
                id: id.as_str().into(),
                kind: enum_parse(&kind)?,
                source,
                summary,
                data: serde_json::from_str(&data)
                    .map_err(|e| StorageError::Corrupt(format!("evidence data: {e}")))?,
                captured_at: parse_ts(&captured_at)?,
            });
        }

        // Findings (+ evidence references from the join table)
        let mut stmt = conn.prepare(
            "SELECT id, device_id, check_id, rule_id, severity, code, title, detail, subject,
                    detected_at
             FROM findings WHERE result_id = ?1",
        )?;
        let f_rows = stmt
            .query_map(params![result_row_id], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, Option<String>>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, String>(5)?,
                    row.get::<_, String>(6)?,
                    row.get::<_, String>(7)?,
                    row.get::<_, String>(8)?,
                    row.get::<_, String>(9)?,
                ))
            })?
            .collect::<Result<Vec<_>, _>>()?;
        for (id, f_device, f_check, rule_id, severity, code, title, detail, subject, detected_at) in
            f_rows
        {
            let mut stmt =
                conn.prepare("SELECT evidence_id FROM finding_evidence WHERE finding_id = ?1")?;
            let evidence_ids: Vec<EvidenceId> = stmt
                .query_map(params![id], |row| row.get::<_, String>(0))?
                .collect::<Result<Vec<_>, _>>()?
                .into_iter()
                .map(|s| s.as_str().into())
                .collect();
            result.findings.push(Finding {
                id: id.as_str().into(),
                device_id: f_device.as_str().into(),
                check_id: f_check.as_str().into(),
                rule_id: rule_id.map(|r| r.as_str().into()),
                severity: enum_parse(&severity)?,
                code,
                title,
                detail,
                subject,
                evidence_ids,
                detected_at: parse_ts(&detected_at)?,
            });
        }

        // Comparison entities belonging to this result (C2).
        if let Some(projection) = result.projection.as_mut() {
            projection.entities = read_entities(conn, "result_id", &result_row_id)?;
        }

        results.push(result);
    }
    run.results = results;

    // Plugin snapshots
    let mut stmt = conn.prepare(
        "SELECT plugin_id, version, api_version, capabilities
         FROM plugin_snapshots WHERE run_id = ?1 ORDER BY plugin_id",
    )?;
    let snapshots = stmt
        .query_map(params![run_id.as_str()], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, i64>(2)?,
                row.get::<_, String>(3)?,
            ))
        })?
        .collect::<Result<Vec<_>, _>>()?
        .into_iter()
        .map(|(plugin_id, version, api_version, caps)| {
            Ok(PluginSnapshot {
                plugin_id,
                version,
                api_version: api_version as u32,
                capabilities: serde_json::from_str(&caps)
                    .map_err(|e| StorageError::Corrupt(format!("snapshot caps: {e}")))?,
            })
        })
        .collect::<Result<Vec<_>, StorageError>>()?;

    Ok(Some(StoredRun {
        run,
        status,
        overall_health: overall_health.as_deref().map(enum_parse).transpose()?,
        app_version,
        plugin_snapshots: snapshots,
    }))
}

fn delete_run(conn: &Connection, run_id: &RunId) -> Result<bool, StorageError> {
    let n = conn.execute(
        "DELETE FROM diagnostic_runs WHERE id = ?1",
        params![run_id.as_str()],
    )?;
    Ok(n > 0)
}

fn get_setting(conn: &Connection, key: &str) -> Result<Option<String>, StorageError> {
    Ok(conn
        .query_row(
            "SELECT value FROM settings WHERE key = ?1",
            params![key],
            |row| row.get(0),
        )
        .optional()?)
}

fn set_setting(conn: &Connection, key: &str, value: &str) -> Result<(), StorageError> {
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?1, ?2)
         ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        params![key, value],
    )?;
    Ok(())
}
