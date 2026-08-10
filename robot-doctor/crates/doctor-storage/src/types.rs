//! Query/filter and row types exposed by the storage service.

use chrono::{DateTime, Utc};
use doctor_domain::{CheckRun, DiagnosticMode, HealthState};
use serde::{Deserialize, Serialize};

/// Which plugin versions produced a diagnostic run.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PluginSnapshot {
    pub plugin_id: String,
    pub version: String,
    pub api_version: u32,
    pub capabilities: Vec<String>,
}

/// Filter + pagination for history listing.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct RunFilter {
    #[serde(default)]
    pub device_id: Option<String>,
    #[serde(default)]
    pub mode: Option<DiagnosticMode>,
    #[serde(default)]
    pub health: Option<HealthState>,
    #[serde(default)]
    pub from: Option<DateTime<Utc>>,
    #[serde(default)]
    pub to: Option<DateTime<Utc>>,
    /// Page size; clamped to a sane maximum server-side.
    #[serde(default)]
    pub limit: Option<u32>,
    #[serde(default)]
    pub offset: Option<u32>,
}

/// One row in the History list. Cheap to load — no observations/evidence.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RunSummaryRow {
    pub id: String,
    pub device_id: String,
    pub device_name: String,
    pub mode: String,
    pub status: String,
    pub started_at: String,
    pub finished_at: Option<String>,
    pub overall_health: Option<String>,
    pub app_version: String,
    pub duration_ms: Option<i64>,
    pub check_count: i64,
    pub finding_count: i64,
}

/// A fully reconstructed historical run.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct StoredRun {
    pub run: CheckRun,
    pub status: String,
    pub overall_health: Option<HealthState>,
    pub app_version: String,
    pub plugin_snapshots: Vec<PluginSnapshot>,
}
