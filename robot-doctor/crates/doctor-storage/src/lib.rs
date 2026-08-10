//! # doctor-storage
//!
//! SQLite persistence for diagnostic history, hidden behind an async
//! service handle. One dedicated thread owns the connection (single
//! controlled writer); concurrent diagnostic workers send messages and
//! await replies. Schema migrations run on open. Storage failures are
//! returned to callers and logged — they never crash the engine.

pub mod c2;
pub mod schema;
mod service;
mod types;

pub use service::{Storage, StorageError};
pub use types::{
    BaselineSummaryRow, PluginSnapshot, ProfileRevisionRow, ProfileRow, RunFilter, RunSummaryRow,
    StoredRun,
};

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Utc;
    use doctor_domain::{
        CheckError, CheckId, CheckResult, CheckRun, CheckStatus, Device, DeviceId, DiagnosticMode,
        Evidence, EvidenceKind, Finding, FindingId, HealthState, Observation, PluginId, RuleId,
        Severity,
    };

    fn sample_result(check: &str, with_finding: bool) -> CheckResult {
        let check_id = CheckId::from(check);
        let ev = Evidence::new(
            EvidenceKind::Api,
            "test",
            "sampled",
            serde_json::json!({"n": 1}),
        );
        let obs = Observation::number(&check_id, "test.value", 42.0, "count", vec![ev.id.clone()]);
        let findings = if with_finding {
            vec![Finding {
                id: FindingId::generate(),
                device_id: DeviceId::from("local"),
                check_id: check_id.clone(),
                rule_id: Some(RuleId::from("TEST_RULE")),
                severity: Severity::Warning,
                code: "TEST_RULE".into(),
                title: "test finding".into(),
                detail: "detail".into(),
                subject: "test:subject".into(),
                evidence_ids: vec![ev.id.clone()],
                detected_at: Utc::now(),
            }]
        } else {
            vec![]
        };
        // Every sample result also projects one comparison entity, so
        // the C2 persistence path is exercised by the ordinary
        // round-trip tests rather than only by a dedicated one.
        let projection = doctor_domain::ProjectionReport::observed_kinds(
            "test",
            ["widget", "gadget"],
            vec![doctor_domain::ComparisonEntity::new(
                doctor_domain::EntityKey::new("test", "widget", check),
                check,
                &PluginId::from("test"),
                &check_id,
            )
            .with("value", doctor_domain::AttributeValue::Number(42.0))
            .with_evidence(vec![ev.id.clone()])],
        );
        CheckResult {
            check_id,
            plugin_id: PluginId::from("test"),
            device_id: DeviceId::from("local"),
            status: if with_finding {
                CheckStatus::Failed
            } else {
                CheckStatus::Passed
            },
            started_at: Utc::now(),
            duration_ms: 12,
            observations: vec![obs],
            evidence: vec![ev],
            findings,
            error: None,
            projection: Some(projection),
        }
    }

    async fn seed_run(storage: &Storage, mode: DiagnosticMode, with_finding: bool) -> CheckRun {
        let device = Device::local();
        storage.upsert_device(device.clone()).await.unwrap();
        let mut run = CheckRun::new(device.id.clone(), mode);
        storage
            .begin_run(
                &run,
                "0.1.0-test",
                vec![PluginSnapshot {
                    plugin_id: "test".into(),
                    version: "9.9.9".into(),
                    api_version: 1,
                    capabilities: vec!["test".into()],
                }],
            )
            .await
            .unwrap();
        let r1 = sample_result("test.alpha", false);
        let r2 = sample_result("test.beta", with_finding);
        storage.append_result(&run.id, r1.clone()).await.unwrap();
        storage.append_result(&run.id, r2.clone()).await.unwrap();
        run.results = vec![r1, r2];
        let health = run.overall_health();
        run.finished_at = Some(Utc::now());
        storage
            .finish_run(&run.id, health, run.finished_at.unwrap())
            .await
            .unwrap();
        run
    }

    #[tokio::test]
    async fn write_read_roundtrip_preserves_ids_and_references() {
        let storage = Storage::open_in_memory().await.unwrap();
        let run = seed_run(&storage, DiagnosticMode::Full, true).await;

        let stored = storage.get_run(&run.id).await.unwrap().unwrap();
        assert_eq!(stored.run.results.len(), 2);
        assert_eq!(stored.status, "COMPLETED");
        assert_eq!(stored.overall_health, Some(HealthState::Degraded));
        assert_eq!(stored.app_version, "0.1.0-test");

        // Stable IDs survive the round trip.
        let orig = &run.results[1];
        let back = stored
            .run
            .results
            .iter()
            .find(|r| r.check_id == orig.check_id)
            .unwrap();
        assert_eq!(back.observations[0].id, orig.observations[0].id);
        assert_eq!(back.evidence[0].id, orig.evidence[0].id);
        assert_eq!(back.findings[0].id, orig.findings[0].id);
        // Finding → evidence references intact and resolvable.
        assert_eq!(back.findings[0].evidence_ids, orig.findings[0].evidence_ids);
        assert!(back.findings[0]
            .evidence_ids
            .iter()
            .all(|id| back.evidence.iter().any(|e| &e.id == id)));

        // Plugin snapshot persisted.
        assert_eq!(stored.plugin_snapshots.len(), 1);
        assert_eq!(stored.plugin_snapshots[0].version, "9.9.9");
    }

    #[tokio::test]
    async fn error_results_roundtrip_typed() {
        let storage = Storage::open_in_memory().await.unwrap();
        let device = Device::local();
        storage.upsert_device(device.clone()).await.unwrap();
        let run = CheckRun::new(device.id.clone(), DiagnosticMode::Quick);
        storage.begin_run(&run, "t", vec![]).await.unwrap();
        let mut result = sample_result("test.gone", false);
        result.status = CheckStatus::Unavailable;
        result.error = Some(CheckError {
            status: CheckStatus::Unavailable,
            message: "no ROS".into(),
        });
        storage.append_result(&run.id, result).await.unwrap();
        let stored = storage.get_run(&run.id).await.unwrap().unwrap();
        let back = &stored.run.results[0];
        assert_eq!(back.status, CheckStatus::Unavailable);
        assert_eq!(back.error.as_ref().unwrap().message, "no ROS");
    }

    #[tokio::test]
    async fn list_filters_and_pagination() {
        let storage = Storage::open_in_memory().await.unwrap();
        for _ in 0..3 {
            seed_run(&storage, DiagnosticMode::Quick, false).await;
        }
        seed_run(&storage, DiagnosticMode::Full, true).await;

        let all = storage.list_runs(RunFilter::default()).await.unwrap();
        assert_eq!(all.len(), 4);
        assert_eq!(all[0].check_count, 2);

        let full_only = storage
            .list_runs(RunFilter {
                mode: Some(DiagnosticMode::Full),
                ..Default::default()
            })
            .await
            .unwrap();
        assert_eq!(full_only.len(), 1);
        assert_eq!(full_only[0].finding_count, 1);

        let degraded = storage
            .list_runs(RunFilter {
                health: Some(HealthState::Degraded),
                ..Default::default()
            })
            .await
            .unwrap();
        assert_eq!(degraded.len(), 1);

        let page = storage
            .list_runs(RunFilter {
                limit: Some(2),
                offset: Some(2),
                ..Default::default()
            })
            .await
            .unwrap();
        assert_eq!(page.len(), 2);
    }

    #[tokio::test]
    async fn delete_run_cascades() {
        let storage = Storage::open_in_memory().await.unwrap();
        let run = seed_run(&storage, DiagnosticMode::Quick, true).await;
        assert!(storage.delete_run(&run.id).await.unwrap());
        assert!(storage.get_run(&run.id).await.unwrap().is_none());
        assert!(!storage.delete_run(&run.id).await.unwrap());
        let remaining = storage.list_runs(RunFilter::default()).await.unwrap();
        assert!(remaining.is_empty());
    }

    #[tokio::test]
    async fn reopen_preserves_history() {
        let dir = std::env::temp_dir().join(format!("rd-storage-{}", uuid::Uuid::new_v4()));
        let db = dir.join("history.db");
        let storage = Storage::open(&db).await.unwrap();
        let run = seed_run(&storage, DiagnosticMode::Quick, false).await;
        storage.close().await.unwrap();

        // Reopen: previous runs must still be there (restart survival).
        let storage = Storage::open(&db).await.unwrap();
        let rows = storage.list_runs(RunFilter::default()).await.unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].id, run.id.to_string());
        let stored = storage.get_run(&run.id).await.unwrap().unwrap();
        assert_eq!(stored.run.results.len(), 2);
        storage.close().await.unwrap();
        std::fs::remove_dir_all(&dir).ok();
    }

    #[tokio::test]
    async fn concurrent_writers_serialize_through_the_service() {
        let storage = Storage::open_in_memory().await.unwrap();
        let device = Device::local();
        storage.upsert_device(device.clone()).await.unwrap();
        let run = CheckRun::new(device.id.clone(), DiagnosticMode::Full);
        storage.begin_run(&run, "t", vec![]).await.unwrap();

        let mut handles = Vec::new();
        for i in 0..16 {
            let storage = storage.clone();
            let run_id = run.id.clone();
            handles.push(tokio::spawn(async move {
                storage
                    .append_result(&run_id, sample_result(&format!("test.c{i}"), false))
                    .await
            }));
        }
        for handle in handles {
            handle.await.unwrap().unwrap();
        }
        let stored = storage.get_run(&run.id).await.unwrap().unwrap();
        assert_eq!(stored.run.results.len(), 16);
    }

    #[tokio::test]
    async fn foreign_keys_reject_orphan_results() {
        let storage = Storage::open_in_memory().await.unwrap();
        let err = storage
            .append_result(
                &doctor_domain::RunId::from("missing-run"),
                sample_result("test.orphan", false),
            )
            .await;
        assert!(matches!(err, Err(StorageError::Sqlite(_))));
    }

    #[tokio::test]
    async fn settings_roundtrip() {
        let storage = Storage::open_in_memory().await.unwrap();
        assert!(storage.get_setting("targets").await.unwrap().is_none());
        storage.set_setting("targets", "[1,2]").await.unwrap();
        storage.set_setting("targets", "[3]").await.unwrap();
        assert_eq!(
            storage.get_setting("targets").await.unwrap().as_deref(),
            Some("[3]")
        );
    }

    #[tokio::test]
    async fn migrations_are_idempotent_across_reopen() {
        let dir = std::env::temp_dir().join(format!("rd-migrate-{}", uuid::Uuid::new_v4()));
        let db = dir.join("history.db");
        for _ in 0..3 {
            let storage = Storage::open(&db).await.unwrap();
            storage.close().await.unwrap();
        }
        std::fs::remove_dir_all(&dir).ok();
    }

    #[tokio::test]
    async fn invalid_database_file_fails_open_without_panicking() {
        let dir = std::env::temp_dir().join(format!("rd-bad-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        let db = dir.join("history.db");
        std::fs::write(&db, b"this is definitely not a sqlite database____").unwrap();
        let result = Storage::open(&db).await;
        assert!(result.is_err(), "corrupt file must error, not panic");
        std::fs::remove_dir_all(&dir).ok();
    }
}
