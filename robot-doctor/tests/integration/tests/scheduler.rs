//! Scheduler v2 integration tests: concurrency, head-of-line blocking,
//! dependency ordering/suppression, cycles and cancellation — all against
//! real stub plugin processes.

use doctor_core::{DiagnosisEvent, Engine};
use doctor_domain::{CheckStatus, DeviceId, DiagnosticMode};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

fn stub_exe() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_stub-plugin"))
}

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
    let dir = std::env::temp_dir().join(format!("rd-sched-{tag}-{}", uuid::Uuid::new_v4()));
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

fn completion_order(events: &[DiagnosisEvent]) -> Vec<String> {
    events
        .iter()
        .filter_map(|ev| match ev {
            DiagnosisEvent::CheckCompleted { result, .. } => Some(result.check_id.to_string()),
            _ => None,
        })
        .collect()
}

/// REGRESSION (same-plugin head-of-line blocking): a plugin advertising
/// concurrency > 1 must answer a fast request while a slow request from
/// the same run is still executing. Responses route by request id.
#[tokio::test]
async fn fast_check_is_not_blocked_by_slow_check_in_same_concurrent_plugin() {
    let plugins_dir = temp_plugins_dir("hol");
    write_stub_manifest(&plugins_dir, "stub-concurrent", "concurrent");

    let engine = Arc::new(Engine::new(&plugins_dir));
    let started = std::time::Instant::now();
    let events = run_to_completion(&engine, DiagnosticMode::Full).await;
    let elapsed = started.elapsed();

    let order = completion_order(&events);
    assert_eq!(
        order.first().map(String::as_str),
        Some("stub.fast"),
        "fast check must complete before the sleeping one; order: {order:?}"
    );
    // Sanity: the sleeping check still completed (≈1.5 s), and the whole
    // run was not serialized (< 3 s total).
    assert!(order.contains(&"stub.sleep".to_string()));
    assert!(
        elapsed < std::time::Duration::from_secs(3),
        "run took {elapsed:?}, plugin concurrency not effective"
    );

    engine.shutdown().await;
    std::fs::remove_dir_all(&plugins_dir).ok();
}

/// Independent plugins must not block one another: a sleeping check in one
/// plugin does not delay another plugin's fast checks.
#[tokio::test]
async fn slow_plugin_does_not_block_unrelated_plugin() {
    let plugins_dir = temp_plugins_dir("cross");
    write_stub_manifest(&plugins_dir, "stub-concurrent", "concurrent"); // has stub.sleep
    write_stub_manifest(&plugins_dir, "stub-dag", "dag"); // fast checks

    let engine = Arc::new(Engine::new(&plugins_dir));
    let events = run_to_completion(&engine, DiagnosticMode::Full).await;
    let order = completion_order(&events);

    let sleep_pos = order.iter().position(|c| c == "stub.sleep").unwrap();
    let root_pos = order.iter().position(|c| c == "stub.root").unwrap();
    assert!(
        root_pos < sleep_pos,
        "unrelated plugin had to wait for the sleeping plugin: {order:?}"
    );

    engine.shutdown().await;
    std::fs::remove_dir_all(&plugins_dir).ok();
}

/// Dependencies execute in order; a failing prerequisite suppresses its
/// dependents (recursively) with explicit DEPENDENCY_MISSING results
/// instead of a cascade of meaningless failures.
#[tokio::test]
async fn dependency_failure_suppresses_downstream_checks() {
    let plugins_dir = temp_plugins_dir("dag");
    write_stub_manifest(&plugins_dir, "stub-dag", "dag");

    let engine = Arc::new(Engine::new(&plugins_dir));
    let events = run_to_completion(&engine, DiagnosticMode::Full).await;

    let mut statuses: HashMap<String, CheckStatus> = HashMap::new();
    let mut skipped: HashMap<String, Option<String>> = HashMap::new();
    for ev in &events {
        match ev {
            DiagnosisEvent::CheckCompleted { result, .. } => {
                statuses.insert(result.check_id.to_string(), result.status);
            }
            DiagnosisEvent::CheckSkipped {
                check_id,
                prerequisite,
                result,
                ..
            } => {
                statuses.insert(check_id.to_string(), result.status);
                skipped.insert(
                    check_id.to_string(),
                    prerequisite.as_ref().map(|p| p.to_string()),
                );
            }
            _ => {}
        }
    }

    // Ordering: child_ok ran after its passing prerequisite.
    let order = completion_order(&events);
    let root = order.iter().position(|c| c == "stub.root").unwrap();
    let child = order.iter().position(|c| c == "stub.child_ok").unwrap();
    assert!(root < child);
    assert_eq!(statuses["stub.child_ok"], CheckStatus::Passed);

    // Suppression: both downstream checks are DEPENDENCY_MISSING, each
    // naming its direct prerequisite.
    assert_eq!(statuses["stub.broken"], CheckStatus::Failed);
    assert_eq!(
        statuses["stub.child_suppressed"],
        CheckStatus::DependencyMissing
    );
    assert_eq!(
        statuses["stub.grandchild_suppressed"],
        CheckStatus::DependencyMissing
    );
    assert_eq!(
        skipped["stub.child_suppressed"].as_deref(),
        Some("stub.broken")
    );
    assert_eq!(
        skipped["stub.grandchild_suppressed"].as_deref(),
        Some("stub.child_suppressed")
    );

    engine.shutdown().await;
    std::fs::remove_dir_all(&plugins_dir).ok();
}

/// A dependency cycle is rejected explicitly; acyclic checks still run.
#[tokio::test]
async fn dependency_cycle_is_rejected_not_hung() {
    let plugins_dir = temp_plugins_dir("cycle");
    write_stub_manifest(&plugins_dir, "stub-cycle", "cycle");

    let engine = Arc::new(Engine::new(&plugins_dir));
    let events = run_to_completion(&engine, DiagnosticMode::Full).await;

    let mut cycle_skips = 0;
    let mut ok_passed = false;
    for ev in &events {
        match ev {
            DiagnosisEvent::CheckSkipped { reason, .. } if reason.contains("cycle") => {
                cycle_skips += 1;
            }
            DiagnosisEvent::CheckCompleted { result, .. }
                if result.check_id.as_str() == "stub.ok" =>
            {
                ok_passed = result.status == CheckStatus::Passed;
            }
            _ => {}
        }
    }
    assert_eq!(cycle_skips, 2, "both cyclic checks rejected");
    assert!(ok_passed, "acyclic check still ran");

    engine.shutdown().await;
    std::fs::remove_dir_all(&plugins_dir).ok();
}

/// Cancellation: the run ends promptly, in-flight work is marked CANCELLED,
/// and the engine remains usable.
#[tokio::test]
async fn cancellation_stops_the_run_promptly() {
    let plugins_dir = temp_plugins_dir("cancel");
    write_stub_manifest(&plugins_dir, "stub-concurrent", "concurrent");

    let engine = Arc::new(Engine::new(&plugins_dir));
    let (run_id, mut rx) = engine
        .start_diagnosis(DeviceId::from("local"), DiagnosticMode::Full)
        .await;

    // Wait for the sleeping check to actually start, then cancel.
    let mut events = Vec::new();
    while let Some(ev) = rx.recv().await {
        let started_sleep = matches!(
            &ev,
            DiagnosisEvent::CheckStarted { check_id, .. } if check_id.as_str() == "stub.sleep"
        );
        events.push(ev);
        if started_sleep {
            break;
        }
    }
    let cancelled_at = std::time::Instant::now();
    engine.cancel_run(&run_id).await;
    while let Some(ev) = rx.recv().await {
        let done = matches!(ev, DiagnosisEvent::RunCompleted { .. });
        events.push(ev);
        if done {
            break;
        }
    }
    assert!(
        cancelled_at.elapsed() < std::time::Duration::from_millis(1200),
        "run did not stop promptly after cancel"
    );
    assert!(
        events.iter().any(|ev| matches!(
            ev,
            DiagnosisEvent::CheckCompleted { result, .. }
                if result.status == CheckStatus::Cancelled
        )),
        "cancelled in-flight check must be marked CANCELLED"
    );

    // Engine still healthy: a follow-up run completes normally.
    let events = run_to_completion(&engine, DiagnosticMode::Full).await;
    assert!(events
        .iter()
        .any(|ev| matches!(ev, DiagnosisEvent::RunCompleted { .. })));

    engine.shutdown().await;
    std::fs::remove_dir_all(&plugins_dir).ok();
}
