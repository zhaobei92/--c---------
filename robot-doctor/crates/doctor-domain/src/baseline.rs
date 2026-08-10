//! Baselines: immutable known-good references, and the semantic diff
//! between a baseline and a current diagnostic run (Phase C2).
//!
//! A baseline is *not* a serialized diagnostic run. It stores its own
//! immutable semantic projection so the originating run may later be
//! deleted without destroying the baseline.
//!
//! Nothing here decides health. `ADDED`/`REMOVED`/`CHANGED` are facts;
//! whether a fact is a problem is decided by explicit Profile
//! expectations (and, from Phase C3, by rules).

use crate::comparison::{AttributeValue, ComparisonEntity, EntityKey, NamespaceAvailability};
use crate::ids::{BaselineId, DeviceId, RunId};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

/// A diagnostic run a baseline was captured from.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BaselineSource {
    pub run_id: RunId,
    pub run_started_at: DateTime<Utc>,
    pub mode: String,
    /// Overall health of that run at capture time (recorded, not judged).
    pub overall_health: Option<String>,
    /// True when the user explicitly accepted a run with warnings.
    #[serde(default)]
    pub manually_accepted: bool,
}

/// How consistently an entity appeared across a baseline's source runs.
///
/// Descriptive only — never a health state.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum EntityStability {
    /// Present in every source run.
    Stable,
    /// Present in some but not all source runs.
    Variable,
    /// Seen in exactly one run of a multi-run baseline.
    TransientCandidate,
}

/// Descriptive aggregation of one numeric attribute across source runs.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct NumericSummary {
    pub min: f64,
    pub max: f64,
    pub median: f64,
    pub samples: u32,
}

/// One entity captured in a baseline, with cross-run aggregation.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BaselineEntity {
    pub key: EntityKey,
    pub display_name: String,
    /// Attributes of the most recent source run that contained the entity.
    #[serde(default)]
    pub attributes: BTreeMap<String, AttributeValue>,
    /// How many source runs contained this entity.
    pub presence_count: u32,
    /// `presence_count / source_count`, in [0, 1].
    pub presence_ratio: f64,
    pub stability: EntityStability,
    /// Descriptive numeric aggregation per numeric attribute.
    #[serde(default)]
    pub numeric_summaries: BTreeMap<String, NumericSummary>,
    /// Distinct values observed for non-numeric attributes.
    #[serde(default)]
    pub observed_values: BTreeMap<String, BTreeSet<String>>,
    pub source_plugin: String,
    pub source_check: String,
    /// Evidence from the source runs, kept for provenance.
    #[serde(default)]
    pub source_evidence_ids: Vec<String>,
}

/// Immutable known-good snapshot of a device.
///
/// A "BaselineSet" is simply a baseline with more than one source run:
/// per-entity presence/stability aggregation is always computed, so a
/// single-run baseline is the degenerate case of the same model.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Baseline {
    pub id: BaselineId,
    pub device_id: DeviceId,
    /// User-editable label (editing metadata never mutates the snapshot).
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub tags: Vec<String>,
    pub created_at: DateTime<Utc>,
    /// Runs this baseline was derived from (1..N).
    pub sources: Vec<BaselineSource>,
    pub app_version: String,
    /// plugin id → version, as used when the baseline was captured.
    #[serde(default)]
    pub plugin_versions: BTreeMap<String, String>,
    /// Namespace availability at capture time, so a later diff can tell
    /// "was never captured" from "was captured and is now gone".
    #[serde(default)]
    pub namespaces: BTreeMap<String, NamespaceAvailability>,
    /// The immutable semantic projection.
    pub entities: Vec<BaselineEntity>,
}

impl Baseline {
    pub fn source_count(&self) -> u32 {
        self.sources.len() as u32
    }

    pub fn entity(&self, key: &EntityKey) -> Option<&BaselineEntity> {
        self.entities.iter().find(|e| &e.key == key)
    }

    /// Entities that appeared in every source run.
    pub fn stable_entities(&self) -> impl Iterator<Item = &BaselineEntity> {
        self.entities
            .iter()
            .filter(|e| e.stability == EntityStability::Stable)
    }
}

// ── diff ───────────────────────────────────────────────────────────────

/// State of one entity when comparing a baseline to a current run.
///
/// Deliberately excludes DEGRADED/CRITICAL/FAILED: a baseline diff is a
/// statement of difference, not a health diagnosis.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum EntityDiffState {
    Unchanged,
    Added,
    Removed,
    Changed,
    /// In the baseline, but its namespace could not be observed now, so
    /// absence proves nothing.
    Unavailable,
    /// The namespace is not projected by any plugin in the current run.
    Unknown,
}

/// One attribute difference inside a CHANGED entity.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BaselineAttributeDiff {
    pub attribute: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub baseline_value: Option<AttributeValue>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub current_value: Option<AttributeValue>,
    /// `current - baseline` for numeric attributes.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub absolute_delta: Option<f64>,
    /// `(current - baseline) / baseline` for numeric attributes with a
    /// non-zero baseline. No judgement attached to the magnitude.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub relative_delta: Option<f64>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BaselineDiffEntity {
    pub key: EntityKey,
    pub display_name: String,
    pub state: EntityDiffState,
    #[serde(default)]
    pub attribute_diffs: Vec<BaselineAttributeDiff>,
    /// Baseline presence ratio, so the UI can show that a REMOVED entity
    /// was only present in some known-good runs anyway.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub baseline_presence_ratio: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
}

/// How comparable the current run is against the baseline.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum BaselineCompatibility {
    Compatible,
    PartiallyCompatible,
    Incompatible,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CompatibilityNote {
    pub code: String,
    pub message: String,
}

/// Result of Baseline vs current diagnostic run.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BaselineDiff {
    pub baseline_id: BaselineId,
    pub device_id: DeviceId,
    pub diagnostic_run_id: RunId,
    pub compared_at: DateTime<Utc>,
    pub compatibility: BaselineCompatibility,
    #[serde(default)]
    pub compatibility_notes: Vec<CompatibilityNote>,
    pub entities: Vec<BaselineDiffEntity>,
}

impl BaselineDiff {
    pub fn count(&self, state: EntityDiffState) -> usize {
        self.entities.iter().filter(|e| e.state == state).count()
    }

    /// Entities in a given state, in canonical key order.
    pub fn in_state(&self, state: EntityDiffState) -> Vec<&BaselineDiffEntity> {
        self.entities.iter().filter(|e| e.state == state).collect()
    }
}

/// A current run projected for comparison: entities plus which namespaces
/// could actually be observed.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct RunProjection {
    pub run_id: RunId,
    pub device_id: DeviceId,
    #[serde(default)]
    pub entities: Vec<ComparisonEntity>,
    #[serde(default)]
    pub namespaces: BTreeMap<String, NamespaceAvailability>,
    /// Finer-grained availability keyed by [`RunProjection::kind_key`].
    /// A namespace can be partially observable: the ROS graph may be
    /// readable while TF times out.
    #[serde(default)]
    pub kinds: BTreeMap<String, NamespaceAvailability>,
    #[serde(default)]
    pub namespace_reasons: BTreeMap<String, String>,
    /// Reasons keyed the same way as `kinds`.
    #[serde(default)]
    pub kind_reasons: BTreeMap<String, String>,
}

impl RunProjection {
    /// Composite key for the per-kind maps.
    pub fn kind_key(namespace: &str, kind: &str) -> String {
        format!("{namespace}/{kind}")
    }

    pub fn availability(&self, namespace: &str) -> NamespaceAvailability {
        self.namespaces
            .get(namespace)
            .copied()
            .unwrap_or(NamespaceAvailability::Unsupported)
    }

    /// Availability of one entity kind.
    ///
    /// Falls back to the namespace only when the namespace as a whole
    /// could not be observed — otherwise a kind nobody claimed is
    /// UNSUPPORTED, so an expectation about it yields UNKNOWN rather than
    /// a fabricated UNSATISFIED.
    pub fn availability_for(&self, namespace: &str, kind: &str) -> NamespaceAvailability {
        if let Some(state) = self.kinds.get(&Self::kind_key(namespace, kind)) {
            return *state;
        }
        match self.availability(namespace) {
            NamespaceAvailability::NotObserved => NamespaceAvailability::NotObserved,
            _ => NamespaceAvailability::Unsupported,
        }
    }

    /// Why a kind (or its namespace) could not be observed.
    pub fn reason_for(&self, namespace: &str, kind: &str) -> Option<&str> {
        self.kind_reasons
            .get(&Self::kind_key(namespace, kind))
            .or_else(|| self.namespace_reasons.get(namespace))
            .map(String::as_str)
    }

    pub fn entity(&self, key: &EntityKey) -> Option<&ComparisonEntity> {
        self.entities.iter().find(|e| &e.key == key)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn diff_states_are_difference_not_health() {
        // Guard against anyone re-introducing health words into the diff
        // vocabulary; health belongs to findings (C3), not to baselines.
        for state in [
            EntityDiffState::Unchanged,
            EntityDiffState::Added,
            EntityDiffState::Removed,
            EntityDiffState::Changed,
            EntityDiffState::Unavailable,
            EntityDiffState::Unknown,
        ] {
            let rendered = serde_json::to_string(&state).unwrap();
            for forbidden in ["DEGRADED", "CRITICAL", "FAILED"] {
                assert!(
                    !rendered.contains(forbidden),
                    "{rendered} contains {forbidden}"
                );
            }
        }
    }

    #[test]
    fn baseline_roundtrip_preserves_stability() {
        let baseline = Baseline {
            id: BaselineId::from("b1"),
            device_id: DeviceId::from("local"),
            name: "known good".into(),
            description: String::new(),
            tags: vec![],
            created_at: Utc::now(),
            sources: vec![BaselineSource {
                run_id: RunId::from("r1"),
                run_started_at: Utc::now(),
                mode: "FULL".into(),
                overall_health: Some("HEALTHY".into()),
                manually_accepted: false,
            }],
            app_version: "0.1.0".into(),
            plugin_versions: BTreeMap::new(),
            namespaces: BTreeMap::new(),
            entities: vec![BaselineEntity {
                key: EntityKey::new("ros", "node", "/talker"),
                display_name: "/talker".into(),
                attributes: BTreeMap::new(),
                presence_count: 1,
                presence_ratio: 1.0,
                stability: EntityStability::Stable,
                numeric_summaries: BTreeMap::new(),
                observed_values: BTreeMap::new(),
                source_plugin: "ros2".into(),
                source_check: "ros.graph".into(),
                source_evidence_ids: vec![],
            }],
        };
        let back: Baseline =
            serde_json::from_str(&serde_json::to_string(&baseline).unwrap()).unwrap();
        assert_eq!(back, baseline);
        assert_eq!(back.stable_entities().count(), 1);
    }
}
