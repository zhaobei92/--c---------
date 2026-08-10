//! Schema migrations. Applied in order; `PRAGMA user_version` tracks the
//! current level. Never edit a shipped migration — append a new one.

use rusqlite::Connection;

pub const MIGRATIONS: &[&str] = &[
    // v1: initial diagnostic-history schema.
    r#"
    CREATE TABLE devices (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        endpoint    TEXT NOT NULL,           -- JSON DeviceEndpoint
        added_at    TEXT NOT NULL
    );

    CREATE TABLE diagnostic_runs (
        id             TEXT PRIMARY KEY,
        device_id      TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
        mode           TEXT NOT NULL,        -- QUICK/FULL/WATCH
        status         TEXT NOT NULL,        -- RUNNING/COMPLETED
        started_at     TEXT NOT NULL,
        finished_at    TEXT,
        overall_health TEXT,                 -- HEALTHY/DEGRADED/…
        app_version    TEXT NOT NULL
    );
    CREATE INDEX idx_runs_device_started ON diagnostic_runs(device_id, started_at DESC);
    CREATE INDEX idx_runs_started ON diagnostic_runs(started_at DESC);

    CREATE TABLE check_results (
        id            TEXT PRIMARY KEY,      -- storage-generated stable id
        run_id        TEXT NOT NULL REFERENCES diagnostic_runs(id) ON DELETE CASCADE,
        check_id      TEXT NOT NULL,
        plugin_id     TEXT NOT NULL,
        status        TEXT NOT NULL,
        health        TEXT NOT NULL,         -- implied health incl. findings
        started_at    TEXT NOT NULL,
        duration_ms   INTEGER NOT NULL,
        error_kind    TEXT,                  -- status name when not evaluated
        error_message TEXT,
        summary       TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX idx_results_run ON check_results(run_id);

    CREATE TABLE observations (
        id          TEXT PRIMARY KEY,        -- domain ObservationId
        result_id   TEXT NOT NULL REFERENCES check_results(id) ON DELETE CASCADE,
        key         TEXT NOT NULL,
        value       TEXT NOT NULL,           -- JSON ObservationValue
        unit        TEXT,
        evidence_ids TEXT NOT NULL,          -- JSON array of EvidenceId
        observed_at TEXT NOT NULL
    );
    CREATE INDEX idx_obs_result ON observations(result_id);

    CREATE TABLE evidence (
        id          TEXT PRIMARY KEY,        -- domain EvidenceId
        result_id   TEXT NOT NULL REFERENCES check_results(id) ON DELETE CASCADE,
        kind        TEXT NOT NULL,
        source      TEXT NOT NULL,
        summary     TEXT NOT NULL,
        data        TEXT NOT NULL,           -- JSON payload
        captured_at TEXT NOT NULL
    );
    CREATE INDEX idx_evidence_result ON evidence(result_id);

    CREATE TABLE findings (
        id          TEXT PRIMARY KEY,        -- domain FindingId
        result_id   TEXT NOT NULL REFERENCES check_results(id) ON DELETE CASCADE,
        device_id   TEXT NOT NULL,
        check_id    TEXT NOT NULL,
        rule_id     TEXT,
        severity    TEXT NOT NULL,
        code        TEXT NOT NULL,
        title       TEXT NOT NULL,
        detail      TEXT NOT NULL,
        subject     TEXT NOT NULL,
        detected_at TEXT NOT NULL
    );
    CREATE INDEX idx_findings_result ON findings(result_id);

    -- Explicit finding → evidence references (must survive round trips).
    CREATE TABLE finding_evidence (
        finding_id  TEXT NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
        evidence_id TEXT NOT NULL,
        PRIMARY KEY (finding_id, evidence_id)
    );

    -- Which plugin versions produced a run (behavior can change between
    -- plugin versions; history must stay interpretable).
    CREATE TABLE plugin_snapshots (
        run_id       TEXT NOT NULL REFERENCES diagnostic_runs(id) ON DELETE CASCADE,
        plugin_id    TEXT NOT NULL,
        version      TEXT NOT NULL,
        api_version  INTEGER NOT NULL,
        capabilities TEXT NOT NULL,          -- JSON array of capability names
        PRIMARY KEY (run_id, plugin_id)
    );

    -- Small key/value store for app settings (e.g. network targets).
    CREATE TABLE settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    "#,
];

/// Apply all pending migrations inside one transaction per migration.
pub fn migrate(conn: &mut Connection) -> Result<(), rusqlite::Error> {
    let current: i64 = conn.pragma_query_value(None, "user_version", |row| row.get(0))?;
    for (index, sql) in MIGRATIONS.iter().enumerate() {
        let version = (index + 1) as i64;
        if version <= current {
            continue;
        }
        let tx = conn.transaction()?;
        tx.execute_batch(sql)?;
        tx.pragma_update(None, "user_version", version)?;
        tx.commit()?;
        tracing::info!("applied storage migration v{version}");
    }
    Ok(())
}
