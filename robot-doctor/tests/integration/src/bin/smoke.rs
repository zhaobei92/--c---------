//! Real-execution smoke test, run on Linux AND Windows CI:
//! spawns the real system/network/nvidia plugins, executes QUICK and FULL
//! diagnoses against the local machine, exercises the TCP open/closed
//! scenario with a temporary local server, persists everything to SQLite,
//! then reopens the database and verifies history survived.
//!
//! Usage: `cargo run -p integration-tests --bin smoke -- [plugins_dir]`
//! Exits non-zero on any failed expectation. No public Internet is used.

use doctor_core::{DiagnosisEvent, Engine};
use doctor_domain::{CheckStatus, DeviceId, DiagnosticMode, RunId};
use doctor_storage::{RunFilter, Storage};
use std::collections::BTreeMap;
use std::collections::HashMap;
use std::net::TcpListener;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Instant;

struct RunOutcome {
    statuses: HashMap<String, CheckStatus>,
    findings: Vec<(String, String)>, // (code, check)
    persisted: bool,
    duration_ms: u128,
}

async fn run(
    engine: &Arc<Engine>,
    mode: DiagnosticMode,
    params: BTreeMap<String, serde_json::Value>,
) -> (RunId, RunOutcome) {
    let started = Instant::now();
    let (run_id, mut rx) = engine
        .start_diagnosis_with_params(DeviceId::from("local"), mode, params)
        .await;
    let mut statuses = HashMap::new();
    let mut findings = Vec::new();
    let mut persisted = false;
    while let Some(event) = rx.recv().await {
        match event {
            DiagnosisEvent::CheckCompleted { result, .. }
            | DiagnosisEvent::CheckSkipped { result, .. } => {
                for f in &result.findings {
                    findings.push((f.code.clone(), result.check_id.to_string()));
                }
                statuses.insert(result.check_id.to_string(), result.status);
            }
            DiagnosisEvent::RunCompleted { persisted: p, .. } => {
                persisted = p;
                break;
            }
            _ => {}
        }
    }
    (
        run_id,
        RunOutcome {
            statuses,
            findings,
            persisted,
            duration_ms: started.elapsed().as_millis(),
        },
    )
}

fn expect(cond: bool, what: &str) {
    if cond {
        println!("  OK   {what}");
    } else {
        eprintln!("  FAIL {what}");
        std::process::exit(1);
    }
}

#[tokio::main]
async fn main() {
    let plugins_dir = std::env::args()
        .nth(1)
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("plugins"));
    let db_dir = std::env::temp_dir().join(format!("rd-smoke-{}", std::process::id()));
    let db_path = db_dir.join("history.db");

    // ── startup ────────────────────────────────────────────────────────
    let t_start = Instant::now();
    let storage = Storage::open(&db_path).await.expect("open storage");
    let engine = Arc::new(Engine::with_storage(&plugins_dir, storage.clone()));
    let startup_ms = t_start.elapsed().as_millis();
    println!("SMOKE: engine + storage ready in {startup_ms} ms");

    // Temporary local TCP server for the port scenario (no Internet).
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind temp server");
    let port = listener.local_addr().unwrap().port();
    let net_params = |targets: serde_json::Value| {
        let mut params = BTreeMap::new();
        for check in [
            "network.reachability",
            "network.tcp_port",
            "network.latency",
        ] {
            params.insert(
                check.to_owned(),
                serde_json::json!({"targets": targets.clone()}),
            );
        }
        params
    };
    let loopback_targets =
        serde_json::json!([{ "host": "127.0.0.1", "port": port, "timeout_ms": 1000 }]);

    // ── SCENARIO A: quick diagnosis on the healthy local machine ───────
    println!("\nSCENARIO A: QUICK diagnosis");
    let (_, quick) = run(&engine, DiagnosticMode::Quick, BTreeMap::new()).await;
    expect(
        quick.statuses["system.identity"] == CheckStatus::Passed,
        "system.identity PASSED",
    );
    expect(
        quick.statuses["system.cpu"] == CheckStatus::Passed,
        "system.cpu PASSED",
    );
    expect(
        matches!(
            quick.statuses["system.memory"],
            CheckStatus::Passed | CheckStatus::Failed
        ),
        "system.memory evaluated",
    );
    expect(
        quick.statuses["network.interfaces"] == CheckStatus::Passed,
        "network.interfaces PASSED",
    );
    expect(
        !quick.statuses.contains_key("network.tcp_port"),
        "QUICK excludes FULL-only checks (tcp_port)",
    );
    expect(quick.persisted, "QUICK run persisted to SQLite");
    println!("PERF quick_diagnosis_ms={}", quick.duration_ms);

    // ── SCENARIO C1: FULL with the local port OPEN ─────────────────────
    println!("\nSCENARIO C: FULL diagnosis, temp TCP server OPEN (port {port})");
    let (full_run_id, full) = run(
        &engine,
        DiagnosticMode::Full,
        net_params(loopback_targets.clone()),
    )
    .await;
    expect(
        full.statuses["system.disk"] == CheckStatus::Passed
            || full.statuses["system.disk"] == CheckStatus::Failed,
        "system.disk evaluated",
    );
    expect(
        full.statuses["network.tcp_port"] == CheckStatus::Passed,
        "tcp_port OPEN",
    );
    expect(
        full.statuses["network.reachability"] == CheckStatus::Passed,
        "loopback reachability PASSED",
    );
    expect(
        full.statuses["network.latency"] == CheckStatus::Passed,
        "latency sampling PASSED",
    );
    expect(full.persisted, "FULL run persisted to SQLite");
    println!("PERF full_diagnosis_ms={}", full.duration_ms);

    // ── SCENARIO D: NVIDIA absence (or presence) is a normal state ─────
    println!("\nSCENARIO D: NVIDIA semantics");
    let nvidia_statuses: Vec<(&String, &CheckStatus)> = full
        .statuses
        .iter()
        .filter(|(k, _)| k.starts_with("nvidia."))
        .collect();
    expect(!nvidia_statuses.is_empty(), "nvidia checks were scheduled");
    for (check, status) in &nvidia_statuses {
        expect(
            !matches!(status, CheckStatus::Error),
            &format!("{check} is typed ({status:?}), not generic ERROR"),
        );
    }
    // Machine health must not be degraded merely because NVIDIA is absent.
    let devices = engine.devices().await;
    if nvidia_statuses
        .iter()
        .all(|(_, s)| matches!(s, CheckStatus::Unavailable))
    {
        expect(
            devices[0].health != doctor_domain::HealthState::Critical
                && devices[0].health != doctor_domain::HealthState::Degraded
                || full.findings.iter().any(|(_, c)| !c.starts_with("nvidia.")),
            "NVIDIA absence alone does not degrade machine health",
        );
    }

    // ── SCENARIO C2: FULL with the port CLOSED ─────────────────────────
    println!("\nSCENARIO C2: temp TCP server CLOSED");
    drop(listener);
    let (_, closed) = run(&engine, DiagnosticMode::Full, net_params(loopback_targets)).await;
    expect(
        closed.statuses["network.tcp_port"] == CheckStatus::Failed,
        "tcp_port now FAILED",
    );
    expect(
        closed
            .findings
            .iter()
            .any(|(code, _)| code == "PORT_UNREACHABLE"),
        "PORT_UNREACHABLE finding produced",
    );

    // ── SCENARIO B: bad target produces a precise failure type ─────────
    println!("\nSCENARIO B: unresolvable target");
    let bad = serde_json::json!([{ "host": "no-such-host-smoke.invalid", "timeout_ms": 500 }]);
    let mut params = BTreeMap::new();
    params.insert(
        "network.reachability".to_owned(),
        serde_json::json!({"targets": bad}),
    );
    let (_, badrun) = run(&engine, DiagnosticMode::Full, params).await;
    expect(
        badrun.statuses["network.reachability"] == CheckStatus::Failed,
        "reachability FAILED for invalid host",
    );
    expect(
        badrun
            .findings
            .iter()
            .any(|(code, _)| code == "TARGET_UNREACHABLE"),
        "TARGET_UNREACHABLE finding produced (typed, Robot Doctor still running)",
    );

    engine.shutdown().await;
    let t_write = Instant::now();
    storage.close().await.expect("close storage");
    println!(
        "PERF storage_flush_close_ms={}",
        t_write.elapsed().as_millis()
    );

    // ── SCENARIO E: persistence across restart ─────────────────────────
    println!("\nSCENARIO E: restart + history reload");
    let t_reopen = Instant::now();
    let storage = Storage::open(&db_path).await.expect("reopen storage");
    let rows = storage
        .list_runs(RunFilter::default())
        .await
        .expect("list history");
    println!(
        "PERF history_reopen_query_ms={}",
        t_reopen.elapsed().as_millis()
    );
    expect(
        rows.len() == 4,
        &format!("4 historical runs found (got {})", rows.len()),
    );
    let stored = storage
        .get_run(&full_run_id)
        .await
        .expect("get run")
        .expect("full run exists after restart");
    expect(!stored.run.results.is_empty(), "historical run has results");
    expect(
        stored.run.results.iter().any(|r| !r.evidence.is_empty()),
        "historical run has evidence",
    );
    let all_evidence_resolvable = stored.run.results.iter().all(|r| {
        r.findings.iter().all(|f| {
            f.evidence_ids
                .iter()
                .all(|id| r.evidence.iter().any(|e| &e.id == id))
        })
    });
    expect(
        all_evidence_resolvable,
        "finding → evidence references intact",
    );
    expect(
        !stored.plugin_snapshots.is_empty(),
        "plugin snapshot stored with the run",
    );
    storage.close().await.ok();
    std::fs::remove_dir_all(&db_dir).ok();

    println!("\nSMOKE PASSED on {:?}", doctor_domain::Platform::current());
}
