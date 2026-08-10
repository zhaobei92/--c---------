//! Deterministic stub plugin for integration tests.
//!
//! `stub-plugin good`       → sequential (max_concurrency 1); checks:
//!                            stub.ok (passes), stub.unavailable
//!                            (UNAVAILABLE), stub.slow (sleeps 3 s).
//! `stub-plugin crashy`     → check stub.crash (exits the process abruptly).
//! `stub-plugin concurrent` → max_concurrency 4; checks: stub.sleep
//!                            (sleeps 1.5 s), stub.fast (immediate) — used
//!                            by the head-of-line-blocking regression test.
//! `stub-plugin dag`        → checks with dependencies: stub.root (passes),
//!                            stub.broken (fails), stub.child_ok (depends on
//!                            root), stub.child_suppressed (depends on broken).

use chrono::Utc;
use doctor_domain::{
    CheckCost, CheckDeclaration, CheckError, CheckId, CheckRequest, CheckResult, CheckStatus,
    DiagnosticMode, Evidence, EvidenceKind, Finding, FindingId, Observation, PluginCapability,
    PluginId, RuleId, Severity,
};
use doctor_plugin_host::{run_plugin_stdio, PluginService, ServiceCapabilities};

struct StubPlugin {
    mode: String,
}

fn decl(
    id: &str,
    timeout_ms: u64,
    depends_on: &[&str],
    modes: &[DiagnosticMode],
) -> CheckDeclaration {
    CheckDeclaration {
        id: CheckId::from(id),
        name: id.to_owned(),
        description: String::new(),
        cost: CheckCost::Fast,
        timeout_ms,
        platforms: vec![],
        depends_on: depends_on.iter().map(|d| CheckId::from(*d)).collect(),
        modes: modes.to_vec(),
    }
}

const BOTH: &[DiagnosticMode] = &[DiagnosticMode::Quick, DiagnosticMode::Full];

impl PluginService for StubPlugin {
    fn plugin_id(&self) -> String {
        format!("stub-{}", self.mode)
    }

    fn plugin_version(&self) -> String {
        "0.1.0".to_owned()
    }

    fn max_concurrency(&self) -> u32 {
        if self.mode == "concurrent" {
            4
        } else {
            1
        }
    }

    fn capabilities(&self) -> ServiceCapabilities {
        let checks = match self.mode.as_str() {
            "crashy" => vec![decl("stub.crash", 5_000, &[], BOTH)],
            "concurrent" => vec![
                decl("stub.sleep", 8_000, &[], BOTH),
                decl("stub.fast", 5_000, &[], BOTH),
            ],
            "dag" => vec![
                decl("stub.root", 5_000, &[], BOTH),
                decl("stub.broken", 5_000, &[], BOTH),
                decl("stub.child_ok", 5_000, &["stub.root"], BOTH),
                decl("stub.child_suppressed", 5_000, &["stub.broken"], BOTH),
                decl(
                    "stub.grandchild_suppressed",
                    5_000,
                    &["stub.child_suppressed"],
                    BOTH,
                ),
            ],
            "cycle" => vec![
                decl("stub.cyc_a", 5_000, &["stub.cyc_b"], BOTH),
                decl("stub.cyc_b", 5_000, &["stub.cyc_a"], BOTH),
                decl("stub.ok", 5_000, &[], BOTH),
            ],
            _ => vec![
                decl("stub.ok", 5_000, &[], BOTH),
                decl("stub.unavailable", 5_000, &[], BOTH),
                decl("stub.slow", 300, &[], &[DiagnosticMode::Full]),
            ],
        };
        ServiceCapabilities {
            capabilities: vec![PluginCapability("stub".to_owned())],
            checks,
            actions: vec![],
        }
    }

    fn run_check(&self, request: &CheckRequest) -> CheckResult {
        let started_at = Utc::now();
        let plugin_id = PluginId::from(self.plugin_id().as_str());
        let base = |status: CheckStatus, error: Option<CheckError>| CheckResult {
            check_id: request.check_id.clone(),
            plugin_id: plugin_id.clone(),
            device_id: request.device_id.clone(),
            status,
            started_at,
            duration_ms: 1,
            observations: vec![],
            evidence: vec![],
            findings: vec![],
            error,
        };
        match request.check_id.as_str() {
            "stub.ok" | "stub.root" | "stub.child_ok" | "stub.fast" => {
                let ev = Evidence::new(
                    EvidenceKind::Api,
                    "stub",
                    "stub evidence",
                    serde_json::json!({"ok": true, "check": request.check_id.as_str()}),
                );
                let obs = Observation::number(
                    &request.check_id,
                    "stub.value",
                    1.0,
                    "count",
                    vec![ev.id.clone()],
                );
                let mut r = base(CheckStatus::Passed, None);
                r.evidence.push(ev);
                r.observations.push(obs);
                r
            }
            "stub.broken" => {
                let ev = Evidence::new(
                    EvidenceKind::Api,
                    "stub",
                    "simulated failure evidence",
                    serde_json::json!({"broken": true}),
                );
                let finding = Finding {
                    id: FindingId::generate(),
                    device_id: request.device_id.clone(),
                    check_id: request.check_id.clone(),
                    rule_id: Some(RuleId::from("STUB_BROKEN")),
                    severity: Severity::Critical,
                    code: "STUB_BROKEN".into(),
                    title: "Simulated broken subsystem".into(),
                    detail: "deterministic failure for dependency tests".into(),
                    subject: "stub:broken".into(),
                    evidence_ids: vec![ev.id.clone()],
                    detected_at: Utc::now(),
                };
                let mut r = base(CheckStatus::Failed, None);
                r.evidence.push(ev);
                r.findings.push(finding);
                r
            }
            "stub.unavailable" => base(
                CheckStatus::Unavailable,
                Some(CheckError {
                    status: CheckStatus::Unavailable,
                    message: "simulated missing dependency (e.g. ROS not installed)".to_owned(),
                }),
            ),
            "stub.slow" => {
                std::thread::sleep(std::time::Duration::from_secs(3));
                base(CheckStatus::Passed, None)
            }
            "stub.sleep" => {
                std::thread::sleep(std::time::Duration::from_millis(1500));
                base(CheckStatus::Passed, None)
            }
            "stub.crash" => {
                // Simulate a hard plugin crash mid-request.
                std::process::exit(17);
            }
            _ => base(
                CheckStatus::Unsupported,
                Some(CheckError {
                    status: CheckStatus::Unsupported,
                    message: "unknown stub check".to_owned(),
                }),
            ),
        }
    }
}

fn main() -> std::io::Result<()> {
    let mode = std::env::args().nth(1).unwrap_or_else(|| "good".to_owned());
    run_plugin_stdio(StubPlugin { mode })
}
