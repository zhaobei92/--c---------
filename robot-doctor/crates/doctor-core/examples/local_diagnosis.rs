//! Headless smoke run of the embedded engine against the real plugins dir:
//! `cargo run -p doctor-core --example local_diagnosis [plugins_dir] [quick|full]`
//!
//! Prints progressive check results and the final health verdict — the same
//! code path the desktop app drives.

use doctor_core::{DiagnosisEvent, Engine};
use doctor_domain::{DeviceId, DiagnosticMode};
use std::path::PathBuf;
use std::sync::Arc;

#[tokio::main]
async fn main() {
    let plugins_dir = std::env::args()
        .nth(1)
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("plugins"));
    let mode = match std::env::args().nth(2).as_deref() {
        Some("full") => DiagnosticMode::Full,
        _ => DiagnosticMode::Quick,
    };

    let engine = Arc::new(Engine::new(&plugins_dir));
    for summary in engine.registry().summaries() {
        match &summary.error {
            None => println!(
                "plugin {:<10} v{} ({} checks)",
                summary.id, summary.version, summary.check_count
            ),
            Some(err) => println!("plugin {:<10} LOAD FAILED: {err}", summary.id),
        }
    }

    let (run_id, mut rx) = engine.start_diagnosis(DeviceId::from("local"), mode).await;
    println!("run {run_id} started ({mode:?})\n");

    while let Some(event) = rx.recv().await {
        match event {
            DiagnosisEvent::RunStarted { planned, .. } => {
                println!("planned {} checks", planned.len());
            }
            DiagnosisEvent::CheckStarted { .. } => {}
            DiagnosisEvent::CheckCompleted { result, .. } => {
                println!(
                    "  {:<18} {:>6} ms  {:?}  obs={} ev={} findings={}",
                    result.check_id,
                    result.duration_ms,
                    result.status,
                    result.observations.len(),
                    result.evidence.len(),
                    result.findings.len()
                );
                for finding in &result.findings {
                    println!("      ! [{:?}] {}", finding.severity, finding.title);
                }
            }
            DiagnosisEvent::RunCompleted { health, .. } => {
                println!("\noverall health: {health:?}");
                break;
            }
        }
    }
    engine.shutdown().await;
}
