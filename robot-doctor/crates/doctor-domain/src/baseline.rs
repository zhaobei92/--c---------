//! Baselines: a captured known-good snapshot of a device, plus the diff
//! model used by "Compare baseline".

use crate::ids::{BaselineId, DeviceId};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// One captured item inside a baseline, keyed by a stable subject string
/// (e.g. `process:nav2`, `topic:/scan`, `disk:/`, `gpu:0`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BaselineEntry {
    /// Namespaced subject, e.g. `topic:/scan`.
    pub subject: String,
    /// Normalized captured state for that subject.
    pub state: serde_json::Value,
    /// Numeric reference values usable for DEGRADED comparisons,
    /// e.g. `{"hz": 10.2}` for a topic.
    #[serde(default)]
    pub metrics: BTreeMap<String, f64>,
}

/// A known-good snapshot of a device.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Baseline {
    pub id: BaselineId,
    pub device_id: DeviceId,
    pub name: String,
    pub captured_at: DateTime<Utc>,
    /// subject → entry
    pub entries: BTreeMap<String, BaselineEntry>,
}

/// Status of one subject when comparing baseline vs current.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum BaselineDiffStatus {
    /// Present now, absent in baseline.
    Added,
    /// Present in baseline, absent now.
    Removed,
    /// Present in both but state differs.
    Changed,
    /// Present in both, state equal, but metrics regressed
    /// (e.g. topic rate significantly below baseline).
    Degraded,
}

/// One line of a baseline comparison.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BaselineDiffEntry {
    pub subject: String,
    pub status: BaselineDiffStatus,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub baseline_state: Option<serde_json::Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub current_state: Option<serde_json::Value>,
    /// Human summary, e.g. "/scan rate 4.1 Hz vs baseline 10.2 Hz".
    pub summary: String,
}

/// Full result of Baseline vs Current.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BaselineDiff {
    pub baseline_id: BaselineId,
    pub device_id: DeviceId,
    pub compared_at: DateTime<Utc>,
    pub entries: Vec<BaselineDiffEntry>,
}
