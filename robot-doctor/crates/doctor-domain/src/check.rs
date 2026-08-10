//! Check definitions, requests, results and diagnostic runs.

use crate::evidence::{Evidence, Observation};
use crate::finding::Finding;
use crate::health::{CheckStatus, HealthState};
use crate::ids::{CheckId, DeviceId, PluginId, RunId};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// Relative execution cost of a check; the scheduler uses this to decide
/// what belongs in QUICK vs FULL diagnosis and how to order work.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum CheckCost {
    /// Sub-second, safe to run frequently.
    Fast,
    /// Seconds (samples over a window, probes network…).
    Medium,
    /// Potentially long (deep scans, rate measurements over long windows).
    Slow,
}

/// Diagnostic mode selected by the user.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum DiagnosticMode {
    Quick,
    Full,
    /// Continuous monitoring. Architecture-only for now.
    Watch,
}

/// Platforms a check or plugin supports.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Platform {
    Linux,
    Windows,
    Macos,
}

impl Platform {
    /// The platform this binary was compiled for.
    pub fn current() -> Platform {
        #[cfg(target_os = "linux")]
        {
            Platform::Linux
        }
        #[cfg(target_os = "windows")]
        {
            Platform::Windows
        }
        #[cfg(target_os = "macos")]
        {
            Platform::Macos
        }
    }
}

/// Static description of a check a plugin can perform.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CheckDefinition {
    pub id: CheckId,
    pub plugin_id: PluginId,
    pub name: String,
    pub description: String,
    pub cost: CheckCost,
    /// Hard per-execution timeout enforced by the plugin host.
    pub timeout_ms: u64,
    /// Platforms this check can run on. Empty = all platforms.
    #[serde(default)]
    pub platforms: Vec<Platform>,
    /// Checks that must complete successfully first.
    #[serde(default)]
    pub depends_on: Vec<CheckId>,
    /// Modes this check participates in.
    pub modes: Vec<DiagnosticMode>,
}

impl CheckDefinition {
    pub fn supports_platform(&self, platform: Platform) -> bool {
        self.platforms.is_empty() || self.platforms.contains(&platform)
    }

    pub fn in_mode(&self, mode: DiagnosticMode) -> bool {
        self.modes.contains(&mode)
    }
}

/// A request to execute one check.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CheckRequest {
    pub check_id: CheckId,
    pub device_id: DeviceId,
    /// Check-specific parameters (e.g. target hosts from a profile).
    #[serde(default)]
    pub params: BTreeMap<String, serde_json::Value>,
    pub timeout_ms: u64,
}

/// Typed error describing why a check could not evaluate its subject.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CheckError {
    /// Mirrors the non-evaluated `CheckStatus` variants.
    pub status: CheckStatus,
    pub message: String,
}

/// The complete result of one check execution.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CheckResult {
    pub check_id: CheckId,
    pub plugin_id: PluginId,
    pub device_id: DeviceId,
    pub status: CheckStatus,
    pub started_at: DateTime<Utc>,
    pub duration_ms: u64,
    #[serde(default)]
    pub observations: Vec<Observation>,
    #[serde(default)]
    pub evidence: Vec<Evidence>,
    #[serde(default)]
    pub findings: Vec<Finding>,
    /// Present when `status` is not Passed/Failed.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<CheckError>,
}

/// One diagnostic run: a set of check results for a device.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CheckRun {
    pub id: RunId,
    pub device_id: DeviceId,
    pub mode: DiagnosticMode,
    pub started_at: DateTime<Utc>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub finished_at: Option<DateTime<Utc>>,
    pub results: Vec<CheckResult>,
}

impl CheckRun {
    pub fn new(device_id: DeviceId, mode: DiagnosticMode) -> Self {
        Self {
            id: RunId::generate(),
            device_id,
            mode,
            started_at: Utc::now(),
            finished_at: None,
            results: Vec::new(),
        }
    }

    /// Overall health of the run: worst implied health across results,
    /// refined by finding severities where checks failed.
    pub fn overall_health(&self) -> HealthState {
        let mut health = if self.results.is_empty() {
            HealthState::Unknown
        } else {
            HealthState::Healthy
        };
        for result in &self.results {
            health = health.worst(result.status.implied_health());
            for finding in &result.findings {
                health = health.worst(finding.implied_health());
            }
        }
        health
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::health::Severity;
    use crate::ids::{EvidenceId, FindingId};

    fn result(status: CheckStatus, findings: Vec<Finding>) -> CheckResult {
        CheckResult {
            check_id: CheckId::from("system.cpu"),
            plugin_id: PluginId::from("system"),
            device_id: DeviceId::from("local"),
            status,
            started_at: Utc::now(),
            duration_ms: 5,
            observations: vec![],
            evidence: vec![],
            findings,
            error: None,
        }
    }

    fn finding(severity: Severity) -> Finding {
        Finding {
            id: FindingId::generate(),
            device_id: DeviceId::from("local"),
            check_id: CheckId::from("system.cpu"),
            rule_id: None,
            severity,
            code: "X".into(),
            title: "x".into(),
            detail: "x".into(),
            subject: "cpu".into(),
            evidence_ids: vec![EvidenceId::generate()],
            detected_at: Utc::now(),
        }
    }

    #[test]
    fn run_health_reflects_worst_finding() {
        let mut run = CheckRun::new(DeviceId::from("local"), DiagnosticMode::Quick);
        run.results.push(result(CheckStatus::Passed, vec![]));
        assert_eq!(run.overall_health(), HealthState::Healthy);

        run.results.push(result(
            CheckStatus::Failed,
            vec![finding(Severity::Critical)],
        ));
        assert_eq!(run.overall_health(), HealthState::Critical);
    }

    #[test]
    fn unavailable_checks_keep_run_healthy() {
        let mut run = CheckRun::new(DeviceId::from("local"), DiagnosticMode::Quick);
        run.results.push(result(CheckStatus::Passed, vec![]));
        let mut unavailable = result(CheckStatus::Unavailable, vec![]);
        unavailable.error = Some(CheckError {
            status: CheckStatus::Unavailable,
            message: "ROS 2 not installed".into(),
        });
        run.results.push(unavailable);
        assert_eq!(run.overall_health(), HealthState::Healthy);
    }

    #[test]
    fn empty_platforms_means_all() {
        let def = CheckDefinition {
            id: CheckId::from("system.cpu"),
            plugin_id: PluginId::from("system"),
            name: "CPU".into(),
            description: "".into(),
            cost: CheckCost::Fast,
            timeout_ms: 5000,
            platforms: vec![],
            depends_on: vec![],
            modes: vec![DiagnosticMode::Quick, DiagnosticMode::Full],
        };
        assert!(def.supports_platform(Platform::Linux));
        assert!(def.supports_platform(Platform::Windows));
    }
}
