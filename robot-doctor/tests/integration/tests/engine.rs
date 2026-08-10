//! Engine-level integration tests using the deterministic stub plugin:
//! progressive streaming, failure semantics, crash containment and restart.

use doctor_core::{DiagnosisEvent, Engine};
use doctor_domain::{CheckStatus, DeviceId, DiagnosticMode, HealthState};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

fn stub_exe() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_stub-plugin"))
}

fn write_stub_manifest(plugins_dir: &Path, id: &str, mode: &str, checks_yaml: &str) {
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
capabilities: [stub]
executable:
  linux: '{exe}'
  windows: '{exe}'
args: [{mode}]
checks:
{checks_yaml}
"#
    );
    std::fs::write(dir.join("plugin.yaml"), yaml).unwrap();
}

fn good_checks_yaml() -> &'static str {
    r#"  - { id: stub.ok, name: ok, cost: FAST, timeout_ms: 5000, modes: [QUICK, FULL] }
  - { id: stub.unavailable, name: unavailable, cost: FAST, timeout_ms: 5000, modes: [QUICK, FULL] }
  - { id: stub.slow, name: slow, cost: FAST, timeout_ms: 300, modes: [FULL] }"#
}

fn crashy_checks_yaml() -> &'static str {
    r#"  - { id: stub.crash, name: crash, cost: FAST, timeout_ms: 5000, modes: [QUICK, FULL] }"#
}

fn temp_plugins_dir(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("rd-it-{tag}-{}", uuid::Uuid::new_v4()));
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

/// Drive one diagnosis to completion, returning all events.
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
            _ => None,
        })
        .collect()
}

#[tokio::test]
async fn full_diagnosis_streams_progressive_results_with_failure_semantics() {
    let plugins_dir = temp_plugins_dir("good");
    write_stub_manifest(&plugins_dir, "stub-good", "good", good_checks_yaml());

    let engine = Arc::new(Engine::new(&plugins_dir));
    let events = run_to_completion(&engine, DiagnosticMode::Full).await;

    // First event is the plan.
    match &events[0] {
        DiagnosisEvent::RunStarted { planned, .. } => {
            assert_eq!(planned.len(), 3, "expected 3 planned checks");
        }
        other => panic!("first event was {other:?}"),
    }

    let statuses = results_by_check(&events);
    assert_eq!(statuses["stub.ok"], CheckStatus::Passed);
    // Missing dependency is UNAVAILABLE, not a generic error.
    assert_eq!(statuses["stub.unavailable"], CheckStatus::Unavailable);
    // A hanging check hits its timeout instead of stalling the run.
    assert_eq!(statuses["stub.slow"], CheckStatus::Timeout);

    // UNAVAILABLE alone must not degrade health; TIMEOUT makes it UNKNOWN.
    match events.last().unwrap() {
        DiagnosisEvent::RunCompleted { health, .. } => {
            assert_eq!(*health, HealthState::Unknown);
        }
        other => panic!("last event was {other:?}"),
    }

    // The run is persisted with all results and a finish timestamp.
    let runs = engine.store().list_runs().await;
    assert_eq!(runs.len(), 1);
    assert_eq!(runs[0].results.len(), 3);
    assert!(runs[0].finished_at.is_some());

    engine.shutdown().await;
    std::fs::remove_dir_all(&plugins_dir).ok();
}

#[tokio::test]
async fn quick_mode_excludes_full_only_checks() {
    let plugins_dir = temp_plugins_dir("quick");
    write_stub_manifest(&plugins_dir, "stub-good", "good", good_checks_yaml());

    let engine = Arc::new(Engine::new(&plugins_dir));
    let events = run_to_completion(&engine, DiagnosticMode::Quick).await;
    let statuses = results_by_check(&events);
    assert!(statuses.contains_key("stub.ok"));
    // stub.slow is FULL-only in its manifest.
    assert!(!statuses.contains_key("stub.slow"));

    engine.shutdown().await;
    std::fs::remove_dir_all(&plugins_dir).ok();
}

#[tokio::test]
async fn plugin_crash_is_contained_and_process_restarted() {
    let plugins_dir = temp_plugins_dir("crashy");
    write_stub_manifest(&plugins_dir, "stub-crashy", "crashy", crashy_checks_yaml());

    let engine = Arc::new(Engine::new(&plugins_dir));

    // First run: the plugin dies mid-check. Robot Doctor keeps going and
    // records a typed ERROR result for that check.
    let events = run_to_completion(&engine, DiagnosticMode::Full).await;
    let statuses = results_by_check(&events);
    assert_eq!(statuses["stub.crash"], CheckStatus::Error);

    // Second run: the managed plugin is respawned automatically.
    let events = run_to_completion(&engine, DiagnosticMode::Full).await;
    let statuses = results_by_check(&events);
    assert_eq!(statuses["stub.crash"], CheckStatus::Error);

    let summaries = engine.registry().summaries();
    let stub = summaries.iter().find(|s| s.id == "stub-crashy").unwrap();
    assert!(
        stub.restarts >= 1,
        "expected at least one recorded restart, got {}",
        stub.restarts
    );

    engine.shutdown().await;
    std::fs::remove_dir_all(&plugins_dir).ok();
}

#[tokio::test]
async fn broken_manifest_never_blocks_other_plugins() {
    let plugins_dir = temp_plugins_dir("mixed");
    write_stub_manifest(&plugins_dir, "stub-good", "good", good_checks_yaml());
    // A plugin directory with an unparseable manifest.
    let broken = plugins_dir.join("broken");
    std::fs::create_dir_all(&broken).unwrap();
    std::fs::write(broken.join("plugin.yaml"), "{{{{ not yaml").unwrap();

    let engine = Arc::new(Engine::new(&plugins_dir));
    let summaries = engine.registry().summaries();
    assert!(
        summaries.iter().any(|s| s.error.is_some()),
        "broken plugin reported"
    );
    assert!(summaries
        .iter()
        .any(|s| s.id == "stub-good" && s.error.is_none()));

    let events = run_to_completion(&engine, DiagnosticMode::Quick).await;
    let statuses = results_by_check(&events);
    assert_eq!(statuses["stub.ok"], CheckStatus::Passed);

    engine.shutdown().await;
    std::fs::remove_dir_all(&plugins_dir).ok();
}

#[tokio::test]
async fn devices_reflect_latest_run_health() {
    let plugins_dir = temp_plugins_dir("devices");
    write_stub_manifest(&plugins_dir, "stub-good", "good", good_checks_yaml());

    let engine = Arc::new(Engine::new(&plugins_dir));
    let devices = engine.devices().await;
    assert_eq!(devices.len(), 1);
    assert_eq!(devices[0].health, HealthState::Unknown);

    run_to_completion(&engine, DiagnosticMode::Quick).await;
    let devices = engine.devices().await;
    // Quick mode: ok=PASSED, unavailable=UNAVAILABLE → overall HEALTHY.
    assert_eq!(devices[0].health, HealthState::Healthy);

    engine.shutdown().await;
    std::fs::remove_dir_all(&plugins_dir).ok();
}
