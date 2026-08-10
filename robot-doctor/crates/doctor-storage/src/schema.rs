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
    // v2: Phase C2 — comparison entities, baselines, profiles, evaluations.
    r#"
    -- Per-check baseline-comparison projection.
    ALTER TABLE check_results ADD COLUMN projection_namespace TEXT;
    ALTER TABLE check_results ADD COLUMN projection_observed INTEGER;
    ALTER TABLE check_results ADD COLUMN projection_reason TEXT;
    -- JSON array of the entity kinds this check is authoritative for.
    ALTER TABLE check_results ADD COLUMN projection_kinds TEXT;

    -- Normalized comparison entities produced during a diagnostic run.
    CREATE TABLE run_entities (
        id            TEXT PRIMARY KEY,
        run_id        TEXT NOT NULL REFERENCES diagnostic_runs(id) ON DELETE CASCADE,
        result_id     TEXT NOT NULL REFERENCES check_results(id) ON DELETE CASCADE,
        namespace     TEXT NOT NULL,
        kind          TEXT NOT NULL,
        entity_key    TEXT NOT NULL,
        display_name  TEXT NOT NULL,
        attributes    TEXT NOT NULL,   -- JSON: name -> AttributeValue
        source_plugin TEXT NOT NULL,
        source_check  TEXT NOT NULL,
        evidence_ids  TEXT NOT NULL    -- JSON array
    );
    CREATE INDEX idx_run_entities_run ON run_entities(run_id);
    CREATE INDEX idx_run_entities_key ON run_entities(run_id, namespace, kind, entity_key);

    -- Immutable known-good snapshots. Baselines own their semantic
    -- projection so the originating run may later be deleted.
    CREATE TABLE baselines (
        id              TEXT PRIMARY KEY,
        device_id       TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
        name            TEXT NOT NULL,
        description     TEXT NOT NULL DEFAULT '',
        tags            TEXT NOT NULL DEFAULT '[]',
        created_at      TEXT NOT NULL,
        app_version     TEXT NOT NULL,
        plugin_versions TEXT NOT NULL DEFAULT '{}',
        namespaces      TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX idx_baselines_device ON baselines(device_id, created_at DESC);

    -- Provenance: which runs a baseline was captured from (1..N).
    -- No FK to diagnostic_runs: history may be deleted without harming
    -- the baseline, by design.
    CREATE TABLE baseline_sources (
        baseline_id       TEXT NOT NULL REFERENCES baselines(id) ON DELETE CASCADE,
        run_id            TEXT NOT NULL,
        run_started_at    TEXT NOT NULL,
        mode              TEXT NOT NULL,
        overall_health    TEXT,
        manually_accepted INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (baseline_id, run_id)
    );

    CREATE TABLE baseline_entities (
        baseline_id       TEXT NOT NULL REFERENCES baselines(id) ON DELETE CASCADE,
        namespace         TEXT NOT NULL,
        kind              TEXT NOT NULL,
        entity_key        TEXT NOT NULL,
        display_name      TEXT NOT NULL,
        attributes        TEXT NOT NULL,
        presence_count    INTEGER NOT NULL,
        presence_ratio    REAL NOT NULL,
        stability         TEXT NOT NULL,
        numeric_summaries TEXT NOT NULL DEFAULT '{}',
        observed_values   TEXT NOT NULL DEFAULT '{}',
        source_plugin     TEXT NOT NULL,
        source_check      TEXT NOT NULL,
        evidence_ids      TEXT NOT NULL DEFAULT '[]',
        PRIMARY KEY (baseline_id, namespace, kind, entity_key)
    );

    -- Profiles are reusable across devices; revisions are immutable.
    CREATE TABLE profiles (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        tags        TEXT NOT NULL DEFAULT '[]',
        created_at  TEXT NOT NULL
    );

    CREATE TABLE profile_revisions (
        profile_id  TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        revision    INTEGER NOT NULL,
        status      TEXT NOT NULL,          -- DRAFT/ACTIVE/ARCHIVED
        document    TEXT NOT NULL,          -- canonical JSON of the Profile
        yaml        TEXT NOT NULL,          -- deterministic YAML export
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL,
        PRIMARY KEY (profile_id, revision)
    );

    CREATE TABLE device_profile_assignments (
        device_id        TEXT PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
        profile_id       TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        profile_revision INTEGER NOT NULL,
        activated_at     TEXT NOT NULL,
        runtime_id       TEXT
    );

    CREATE TABLE evaluation_runs (
        id                TEXT PRIMARY KEY,
        device_id         TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
        diagnostic_run_id TEXT NOT NULL,
        profile_id        TEXT NOT NULL,
        profile_revision  INTEGER NOT NULL,
        baseline_id       TEXT,
        started_at        TEXT NOT NULL,
        finished_at       TEXT NOT NULL,
        app_version       TEXT NOT NULL,
        plugin_versions   TEXT NOT NULL DEFAULT '{}',
        satisfied         INTEGER NOT NULL,
        unsatisfied       INTEGER NOT NULL,
        unknown           INTEGER NOT NULL,
        not_applicable    INTEGER NOT NULL
    );
    CREATE INDEX idx_eval_run ON evaluation_runs(diagnostic_run_id);
    CREATE INDEX idx_eval_device ON evaluation_runs(device_id, started_at DESC);

    CREATE TABLE expectation_results (
        evaluation_run_id  TEXT NOT NULL REFERENCES evaluation_runs(id) ON DELETE CASCADE,
        expectation_id     TEXT NOT NULL,
        description        TEXT NOT NULL DEFAULT '',
        namespace          TEXT NOT NULL,
        kind               TEXT NOT NULL,
        status             TEXT NOT NULL,
        evaluated_entities TEXT NOT NULL DEFAULT '[]',
        actual             TEXT,
        expected           TEXT NOT NULL,
        reason             TEXT NOT NULL DEFAULT '',
        evidence_ids       TEXT NOT NULL DEFAULT '[]',
        evaluated_at       TEXT NOT NULL,
        PRIMARY KEY (evaluation_run_id, expectation_id)
    );

    CREATE TABLE comparison_diffs (
        id                TEXT PRIMARY KEY,
        device_id         TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
        baseline_id       TEXT NOT NULL REFERENCES baselines(id) ON DELETE CASCADE,
        diagnostic_run_id TEXT NOT NULL,
        compared_at       TEXT NOT NULL,
        compatibility     TEXT NOT NULL,
        notes             TEXT NOT NULL DEFAULT '[]',
        entities          TEXT NOT NULL   -- JSON array of BaselineDiffEntity
    );
    CREATE INDEX idx_diffs_run ON comparison_diffs(diagnostic_run_id);
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
