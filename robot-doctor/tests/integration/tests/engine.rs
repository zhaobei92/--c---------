//! Engine-level integration tests using the deterministic stub plugin:
//! progressive streaming, failure semantics, crash containment, restart,
//! runtime capability discovery and persistence wiring.

use doctor_core::{DiagnosisEvent, Engine};
use doctor_domain::{CheckStatus, DeviceId, DiagnosticMode, HealthState};
use doctor_storage::{RunFilter, Storage};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

fn stub_exe() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_stub-plugin"))
}

/// Write a bootstrap-only manifest (no checks — those come from the
/// running plugin's CAPABILITIES answer).
fn write_stub_manifest(plugins_dir: &Path, id: &str, mode: &str) {
    let dir = plugins_dir.join(id);
    std::fs::create_dir_all(&dir).unwrap();
    let exe = stub_exe().display().to_string();
    let yaml = format!(
        r#"
id: {id}
name: Stub ({mode})
version: 0.1.0
api_version: 1
platforms: [linux, windows]
executable:
  linux: '{exe}'
  windows: '{exe}'
args: [{mode}]
"#
    );
    std::fs::write(dir.join("plugin.yaml"), yaml).unwrap();
}

fn temp_plugins_dir(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("rd-it-{tag}-{}", uuid::Uuid::new_v4()));
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

async fn run_to_completion(engine: &Arc<Engine>, mode: DiagnosticMode) -> Vec<DiagnosisEvent> {
    let (_run_id, mut rx) = engine.start_diagnosis(DeviceId::from("local"), mode).await;
    let mut events = Vec::new();
    while let Some(ev) = rx.recv().await {
        let done = matches!(ev, DiagnosisEvent::RunCompleted { .. });
        events.push(ev);
        if done {
            break;
        }
    }
    events
}

fn results_by_check(events: &[DiagnosisEvent]) -> HashMap<String, CheckStatus> {
    events
        .iter()
        .filter_map(|ev| match ev {
            DiagnosisEvent::CheckCompleted { result, .. } => {
                Some((result.check_id.to_string(), result.status))
            }
            DiagnosisEvent::CheckSkipped { result, .. } => {
                Some((result.check_id.to_string(), result.status))
            }
            _ => None,
        })
        .collect()
}

#[tokio::test]
async fn checks_are_discovered_from_the_running_plugin_not_the_manifest() {
    let plugins_dir = temp_plugins_dir("discovery");
    // Bootstrap manifest with NO check declarations at all.
    write_stub_manifest(&plugins_dir, "stub-good", "good");

    let engine = Arc::new(Engine::new(&plugins_dir));
    // Before contact: no runtime capabilities known yet.
    let before = engine.registry().summaries().await;
    assert!(!before[0].contacted);
    assert!(before[0].checks.is_empty());

    let events = run_to_completion(&engine, DiagnosticMode::Full).await;
    match &events[0] {
        DiagnosisEvent::RunStarted { planned, .. } => {
            assert_eq!(planned.len(), 3, "3 checks discovered from the process");
        }
        other => panic!("first event was {other:?}"),
    }

    // After contact: capability list reflects the live negotiation.
    let after = engine.registry().summaries().await;
    assert!(after[0].contacted);
    assert_eq!(after[0].checks.len(), 3);
    assert_eq!(after[0].capabilities, vec!["stub".to_string()]);

    engine.shutdown().await;
    std::fs::remove_dir_all(&plugins_dir).ok();
}

#[tokio::test]
async fn full_diagnosis_streams_progressive_results_with_failure_semantics() {
    let plugins_dir = temp_plugins_dir("good");
    write_stub_manifest(&plugins_dir, "stub-good", "good");

    let engine = Arc::new(Engine::new(&plugins_dir));
    let events = run_to_completion(&engine, DiagnosticMode::Full).await;

    let statuses = results_by_check(&events);
    assert_eq!(statuses["stub.ok"], CheckStatus::Passed);
    assert_eq!(statuses["stub.unavailable"], CheckStatus::Unavailable);
    assert_eq!(statuses["stub.slow"], CheckStatus::Timeout);

    // Progressive event shape: queued before started before completed.
    let first_queued = events
        .iter()
        .position(|e| matches!(e, DiagnosisEvent::CheckQueued { .. }))
        .unwrap();
    let first_started = events
        .iter()
        .position(|e| matches!(e, DiagnosisEvent::CheckStarted { .. }))
        .unwrap();
    assert!(first_queued < first_started);

    match events.last().unwrap() {
        DiagnosisEvent::RunCompleted { health, .. } => {
            assert_eq!(*health, HealthState::Unknown); // timeout → UNKNOWN
        }
        other => panic!("last event was {other:?}"),
    }

    engine.shutdown().await;
    std::fs::remove_dir_all(&plugins_dir).ok();
}

#[tokio::test]
async fn quick_mode_excludes_full_only_checks() {
    let plugins_dir = temp_plugins_dir("quick");
    write_stub_manifest(&plugins_dir, "stub-good", "good");

    let engine = Arc::new(Engine::new(&plugins_dir));
    let events = run_to_completion(&engine, DiagnosticMode::Quick).await;
    let statuses = results_by_check(&events);
    assert!(statuses.contains_key("stub.ok"));
    assert!(!statuses.contains_key("stub.slow")); // FULL-only

    engine.shutdown().await;
    std::fs::remove_dir_all(&plugins_dir).ok();
}

#[tokio::test]
async fn plugin_crash_is_contained_and_process_restarted() {
    let plugins_dir = temp_plugins_dir("crashy");
    write_stub_manifest(&plugins_dir, "stub-crashy", "crashy");

    let engine = Arc::new(Engine::new(&plugins_dir));

    let events = run_to_completion(&engine, DiagnosticMode::Full).await;
    let statuses = results_by_check(&events);
    assert_eq!(statuses["stub.crash"], CheckStatus::Error);

    let events = run_to_completion(&engine, DiagnosticMode::Full).await;
    let statuses = results_by_check(&events);
    assert_eq!(statuses["stub.crash"], CheckStatus::Error);

    let summaries = engine.registry().summaries().await;
    let stub = summaries.iter().find(|s| s.id == "stub-crashy").unwrap();
    assert!(stub.restarts >= 1);

    engine.shutdown().await;
    std::fs::remove_dir_all(&plugins_dir).ok();
}

#[tokio::test]
async fn broken_manifest_never_blocks_other_plugins() {
    let plugins_dir = temp_plugins_dir("mixed");
    write_stub_manifest(&plugins_dir, "stub-good", "good");
    let broken = plugins_dir.join("broken");
    std::fs::create_dir_all(&broken).unwrap();
    std::fs::write(broken.join("plugin.yaml"), "{{{{ not yaml").unwrap();

    let engine = Arc::new(Engine::new(&plugins_dir));
    let summaries = engine.registry().summaries().await;
    assert!(summaries.iter().any(|s| s.error.is_some()));

    let events = run_to_completion(&engine, DiagnosticMode::Quick).await;
    let statuses = results_by_check(&events);
    assert_eq!(statuses["stub.ok"], CheckStatus::Passed);

    engine.shutdown().await;
    std::fs::remove_dir_all(&plugins_dir).ok();
}

#[tokio::test]
async fn devices_reflect_latest_run_health() {
    let plugins_dir = temp_plugins_dir("devices");
    write_stub_manifest(&plugins_dir, "stub-good", "good");

    let engine = Arc::new(Engine::new(&plugins_dir));
    let devices = engine.devices().await;
    assert_eq!(devices.len(), 1);
    assert_eq!(devices[0].health, HealthState::Unknown);

    run_to_completion(&engine, DiagnosticMode::Quick).await;
    let devices = engine.devices().await;
    assert_eq!(devices[0].health, HealthState::Healthy);

    engine.shutdown().await;
    std::fs::remove_dir_all(&plugins_dir).ok();
}

#[tokio::test]
async fn engine_persists_runs_to_storage_and_history_survives() {
    let plugins_dir = temp_plugins_dir("persist");
    write_stub_manifest(&plugins_dir, "stub-good", "good");

    let dir = std::env::temp_dir().join(format!("rd-engine-db-{}", uuid::Uuid::new_v4()));
    let db_path = dir.join("history.db");
    let storage = Storage::open(&db_path).await.unwrap();
    let engine = Arc::new(Engine::with_storage(&plugins_dir, storage.clone()));

    let events = run_to_completion(&engine, DiagnosticMode::Quick).await;
    match events.last().unwrap() {
        DiagnosisEvent::RunCompleted { persisted, .. } => assert!(*persisted),
        other => panic!("last event was {other:?}"),
    }
    engine.shutdown().await;
    storage.close().await.unwrap();

    // Fresh storage handle (simulated restart): history still there,
    // including the plugin snapshot taken at run time.
    let storage = Storage::open(&db_path).await.unwrap();
    let rows = storage.list_runs(RunFilter::default()).await.unwrap();
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].check_count, 2); // quick: ok + unavailable
    let run_id = doctor_domain::RunId::from(rows[0].id.as_str());
    let stored = storage.get_run(&run_id).await.unwrap().unwrap();
    assert_eq!(stored.plugin_snapshots.len(), 1);
    assert_eq!(stored.plugin_snapshots[0].plugin_id, "stub-good");
    assert_eq!(stored.plugin_snapshots[0].capabilities, vec!["stub"]);
    storage.close().await.unwrap();
    std::fs::remove_dir_all(&dir).ok();
    std::fs::remove_dir_all(&plugins_dir).ok();
}
