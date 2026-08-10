//! Findings, root causes and remediation: the explainable conclusions layer.

use crate::health::{HealthState, Severity};
use crate::ids::{
    ActionId, CheckId, DeviceId, EvidenceId, FindingId, IncidentId, RootCauseId, RuleId,
};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

/// A single detected problem (or notable fact), always backed by evidence.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Finding {
    pub id: FindingId,
    pub device_id: DeviceId,
    /// Check that surfaced the finding.
    pub check_id: CheckId,
    /// Deterministic rule that produced it, when rule-derived
    /// (e.g. `TOPIC_RATE_LOW`). Checks may also emit findings directly.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub rule_id: Option<RuleId>,
    pub severity: Severity,
    /// Short, stable, machine-friendly code, e.g. `DISK_LOW`.
    pub code: String,
    /// Human title, e.g. "Disk / has only 3% free space".
    pub title: String,
    /// Longer explanation of what was observed and why it matters.
    pub detail: String,
    /// What this finding concerns, e.g. `disk:/`, `topic:/odom`, `process:nav2`.
    /// Used by the root-cause engine for dependency grouping.
    pub subject: String,
    /// Evidence backing the finding. Must not be empty for problem findings.
    pub evidence_ids: Vec<EvidenceId>,
    pub detected_at: DateTime<Utc>,
}

/// A remediation suggestion attached to a root cause or finding.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Remediation {
    /// Human description of what to do.
    pub description: String,
    /// Optional executable action the user can trigger from the UI.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub action_id: Option<ActionId>,
    /// Whether the action changes system state (never auto-run).
    pub destructive: bool,
}

/// A dependency-aware, high-level conclusion grouping related findings.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RootCause {
    pub id: RootCauseId,
    pub device_id: DeviceId,
    pub title: String,
    pub severity: Severity,
    /// 0.0–1.0 deterministic confidence (rule-derived, not ML).
    pub confidence: f64,
    /// The primary findings that constitute the cause itself.
    pub cause_finding_ids: Vec<FindingId>,
    /// Downstream findings explained by this cause (consequences).
    pub consequence_finding_ids: Vec<FindingId>,
    /// All evidence relevant to this conclusion.
    pub evidence_ids: Vec<EvidenceId>,
    /// What functionality is impacted, e.g. "Localization and navigation
    /// are unavailable".
    pub impact: String,
    /// Suggested follow-up checks to narrow down further.
    pub recommended_check_ids: Vec<CheckId>,
    pub remediations: Vec<Remediation>,
    pub detected_at: DateTime<Utc>,
}

/// A persisted problem episode: the same root cause observed over time.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Incident {
    pub id: IncidentId,
    pub device_id: DeviceId,
    pub title: String,
    pub severity: Severity,
    pub state: IncidentState,
    /// Stable fingerprint used to correlate recurring root causes
    /// (e.g. `PROCESS_MISSING:process:robot_localization`).
    pub fingerprint: String,
    pub root_cause_ids: Vec<RootCauseId>,
    pub first_seen: DateTime<Utc>,
    pub last_seen: DateTime<Utc>,
    pub resolved_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum IncidentState {
    Open,
    Resolved,
}

impl Finding {
    /// The health state this finding implies for its subject.
    pub fn implied_health(&self) -> HealthState {
        self.severity.implied_health()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finding_roundtrip_and_health() {
        let f = Finding {
            id: FindingId::generate(),
            device_id: DeviceId::from("local"),
            check_id: CheckId::from("system.disk"),
            rule_id: Some(RuleId::from("DISK_LOW")),
            severity: Severity::Critical,
            code: "DISK_LOW".into(),
            title: "Disk / almost full".into(),
            detail: "3% free".into(),
            subject: "disk:/".into(),
            evidence_ids: vec![EvidenceId::generate()],
            detected_at: Utc::now(),
        };
        assert_eq!(f.implied_health(), HealthState::Critical);
        let back: Finding = serde_json::from_str(&serde_json::to_string(&f).unwrap()).unwrap();
        assert_eq!(back, f);
    }
}
