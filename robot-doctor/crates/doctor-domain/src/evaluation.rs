//! Expectation evaluation results (Phase C2).
//!
//! An evaluation answers "are the explicit expectations satisfied?" — it
//! never answers "why is it broken?". There is no severity, no finding
//! and no root cause here, and no aggregate compliance score.

use crate::comparison::{AttributeValue, EntityKey};
use crate::ids::{BaselineId, DeviceId, ProfileId, RunId};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// Outcome of one expectation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ExpectationStatus {
    /// The constraint held.
    Satisfied,
    /// The subject was observed and the constraint did not hold.
    Unsatisfied,
    /// Robot Doctor did not observe enough to decide. Never a fault.
    Unknown,
    /// The expectation does not apply (optional subject simply absent).
    NotApplicable,
}

/// Result of evaluating one expectation against one diagnostic run.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ExpectationResult {
    pub expectation_id: String,
    pub description: String,
    pub namespace: String,
    pub kind: String,
    pub status: ExpectationStatus,
    /// Entities the expectation was evaluated against.
    #[serde(default)]
    pub evaluated_entities: Vec<EntityKey>,
    /// Observed value that decided the outcome, when there is one.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub actual: Option<AttributeValue>,
    /// Human rendering of the constraint, e.g. `observed_hz >= 8`.
    pub expected: String,
    /// Why this status was produced — always populated for non-satisfied
    /// outcomes so UNKNOWN can never be mistaken for a fault.
    pub reason: String,
    #[serde(default)]
    pub evidence_ids: Vec<String>,
    pub evaluated_at: DateTime<Utc>,
}

/// One persisted evaluation of a profile revision against a run.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct EvaluationRun {
    pub id: String,
    pub device_id: DeviceId,
    pub diagnostic_run_id: RunId,
    pub profile_id: ProfileId,
    /// The exact revision used. Historical evaluations are never
    /// recomputed with a newer revision.
    pub profile_revision: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub baseline_id: Option<BaselineId>,
    pub started_at: DateTime<Utc>,
    pub finished_at: DateTime<Utc>,
    pub app_version: String,
    /// plugin id → version at evaluation time (provenance for C3).
    #[serde(default)]
    pub plugin_versions: BTreeMap<String, String>,
    pub results: Vec<ExpectationResult>,
}

impl EvaluationRun {
    pub fn count(&self, status: ExpectationStatus) -> usize {
        self.results.iter().filter(|r| r.status == status).count()
    }

    pub fn satisfied(&self) -> usize {
        self.count(ExpectationStatus::Satisfied)
    }

    pub fn unsatisfied(&self) -> usize {
        self.count(ExpectationStatus::Unsatisfied)
    }

    pub fn unknown(&self) -> usize {
        self.count(ExpectationStatus::Unknown)
    }

    pub fn not_applicable(&self) -> usize {
        self.count(ExpectationStatus::NotApplicable)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn evaluation_counts_every_status() {
        let result = |status: ExpectationStatus, id: &str| ExpectationResult {
            expectation_id: id.into(),
            description: String::new(),
            namespace: "ros".into(),
            kind: "topic".into(),
            status,
            evaluated_entities: vec![],
            actual: None,
            expected: "exists".into(),
            reason: String::new(),
            evidence_ids: vec![],
            evaluated_at: Utc::now(),
        };
        let run = EvaluationRun {
            id: "e1".into(),
            device_id: DeviceId::from("local"),
            diagnostic_run_id: RunId::from("r1"),
            profile_id: ProfileId::from("p1"),
            profile_revision: 1,
            baseline_id: None,
            started_at: Utc::now(),
            finished_at: Utc::now(),
            app_version: "0.1.0".into(),
            plugin_versions: BTreeMap::new(),
            results: vec![
                result(ExpectationStatus::Satisfied, "a"),
                result(ExpectationStatus::Unsatisfied, "b"),
                result(ExpectationStatus::Unknown, "c"),
                result(ExpectationStatus::NotApplicable, "d"),
            ],
        };
        assert_eq!(run.satisfied(), 1);
        assert_eq!(run.unsatisfied(), 1);
        assert_eq!(run.unknown(), 1);
        assert_eq!(run.not_applicable(), 1);
        // No compliance score exists by construction.
        let json = serde_json::to_string(&run).unwrap();
        assert!(!json.contains("score"));
    }
}
