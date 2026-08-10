//! User-triggered actions (remediation) and their results.

use crate::check::Platform;
use crate::ids::{ActionId, DeviceId, EvidenceId, PluginId};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// A runnable action a plugin offers, e.g. restart a service, record a bag.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ActionDefinition {
    pub id: ActionId,
    pub plugin_id: PluginId,
    pub name: String,
    pub description: String,
    /// Destructive actions change system state and always require explicit
    /// user confirmation; they are never run automatically.
    pub destructive: bool,
    pub timeout_ms: u64,
    #[serde(default)]
    pub platforms: Vec<Platform>,
}

/// Outcome of one action execution.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ActionStatus {
    Succeeded,
    Failed,
    Timeout,
    PermissionDenied,
    Unavailable,
}

/// Result of a user-triggered action.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ActionResult {
    pub action_id: ActionId,
    pub device_id: DeviceId,
    pub status: ActionStatus,
    pub started_at: DateTime<Utc>,
    pub duration_ms: u64,
    /// Human-readable outcome, e.g. "Service nav2 restarted (pid 4312)".
    pub message: String,
    /// Evidence captured while executing (command output etc.).
    #[serde(default)]
    pub evidence_ids: Vec<EvidenceId>,
    /// Action-specific structured output.
    #[serde(default)]
    pub output: BTreeMap<String, serde_json::Value>,
}
