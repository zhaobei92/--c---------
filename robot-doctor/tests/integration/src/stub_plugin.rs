//! Deterministic stub plugin for integration tests.
//!
//! `stub-plugin good`   → checks: stub.ok (passes), stub.unavailable
//!                        (UNAVAILABLE), stub.slow (sleeps 3 s).
//! `stub-plugin crashy` → check: stub.crash (exits the process abruptly).

use chrono::Utc;
use doctor_domain::{
    CheckCost, CheckDeclaration, CheckError, CheckId, CheckRequest, CheckResult, CheckStatus,
    DiagnosticMode, Evidence, EvidenceKind, Observation, PluginCapability, PluginId,
};
use doctor_plugin_host::{run_plugin_stdio, PluginService, ServiceCapabilities};

struct StubPlugin {
    mode: String,
}

fn decl(id: &str, timeout_ms: u64) -> CheckDeclaration {
    CheckDeclaration {
        id: CheckId::from(id),
        name: id.to_owned(),
        description: String::new(),
        cost: CheckCost::Fast,
        timeout_ms,
        platforms: vec![],
        depends_on: vec![],
        modes: vec![DiagnosticMode::Quick, DiagnosticMode::Full],
    }
}

impl PluginService for StubPlugin {
    fn plugin_id(&self) -> String {
        format!("stub-{}", self.mode)
    }

    fn plugin_version(&self) -> String {
        "0.1.0".to_owned()
    }

    fn capabilities(&mut self) -> ServiceCapabilities {
        let checks = if self.mode == "crashy" {
            vec![decl("stub.crash", 5_000)]
        } else {
            vec![
                decl("stub.ok", 5_000),
                decl("stub.unavailable", 5_000),
                decl("stub.slow", 300),
            ]
        };
        ServiceCapabilities {
            capabilities: vec![PluginCapability("stub".to_owned())],
            checks,
            actions: vec![],
        }
    }

    fn run_check(&mut self, request: &CheckRequest) -> CheckResult {
        let started_at = Utc::now();
        let base = |status: CheckStatus, error: Option<CheckError>| CheckResult {
            check_id: request.check_id.clone(),
            plugin_id: PluginId::from(self.plugin_id().as_str()),
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
            "stub.ok" => {
                let ev = Evidence::new(
                    EvidenceKind::Api,
                    "stub",
                    "stub evidence",
                    serde_json::json!({"ok": true}),
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
    run_plugin_stdio(&mut StubPlugin { mode })
}
