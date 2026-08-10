//! Plugin-independent comparison model (Phase C2).
//!
//! Plugins translate their own raw Observations into [`ComparisonEntity`]
//! values with **semantic** identities. doctor-core then performs generic
//! storage, diffing and expectation evaluation without understanding ROS,
//! NVIDIA or any other domain.
//!
//! Determinism is structural, not conventional: attributes live in a
//! `BTreeMap`, string sets in a `BTreeSet`. Reordering a plugin's internal
//! lists therefore cannot produce a spurious difference.

use crate::ids::{CheckId, EvidenceId, PluginId};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

/// Semantic identity of a comparable thing.
///
/// Never derived from array position, database row id or a transient
/// runtime handle (a DDS GID may appear as an *attribute*, never here).
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub struct EntityKey {
    /// Owning domain: `ros`, `system`, `network`, `nvidia`, …
    pub namespace: String,
    /// Entity type inside the namespace: `node`, `topic`, `tf_edge`, `gpu`…
    pub kind: String,
    /// Stable semantic key, e.g. `/scan|sensor_msgs/msg/LaserScan`.
    pub key: String,
}

impl EntityKey {
    pub fn new(
        namespace: impl Into<String>,
        kind: impl Into<String>,
        key: impl Into<String>,
    ) -> Self {
        Self {
            namespace: namespace.into(),
            kind: kind.into(),
            key: key.into(),
        }
    }

    /// Display/storage form, e.g. `ros.node:/lidar_driver`.
    pub fn canonical(&self) -> String {
        format!("{}.{}:{}", self.namespace, self.kind, self.key)
    }
}

impl std::fmt::Display for EntityKey {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.canonical())
    }
}

/// A typed attribute value. Deliberately small: no nested objects, so
/// comparison and constraint evaluation stay total and deterministic.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", content = "value", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum AttributeValue {
    Number(f64),
    Text(String),
    Bool(bool),
    /// Unordered set of strings — order-independence is structural.
    TextSet(BTreeSet<String>),
}

impl AttributeValue {
    pub fn text(value: impl Into<String>) -> Self {
        AttributeValue::Text(value.into())
    }

    pub fn set<I, S>(values: I) -> Self
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        AttributeValue::TextSet(values.into_iter().map(Into::into).collect())
    }

    pub fn as_number(&self) -> Option<f64> {
        match self {
            AttributeValue::Number(n) => Some(*n),
            _ => None,
        }
    }

    pub fn as_text(&self) -> Option<&str> {
        match self {
            AttributeValue::Text(s) => Some(s),
            _ => None,
        }
    }

    pub fn as_set(&self) -> Option<&BTreeSet<String>> {
        match self {
            AttributeValue::TextSet(s) => Some(s),
            _ => None,
        }
    }

    /// Human-readable rendering used in diffs and evaluation results.
    pub fn render(&self) -> String {
        match self {
            AttributeValue::Number(n) => {
                if n.fract() == 0.0 && n.abs() < 1e15 {
                    format!("{n:.0}")
                } else {
                    format!("{n:.4}")
                }
            }
            AttributeValue::Text(s) => s.clone(),
            AttributeValue::Bool(b) => b.to_string(),
            AttributeValue::TextSet(s) => s.iter().cloned().collect::<Vec<_>>().join(", "),
        }
    }

    /// Short type name for validation messages.
    pub fn type_name(&self) -> &'static str {
        match self {
            AttributeValue::Number(_) => "number",
            AttributeValue::Text(_) => "text",
            AttributeValue::Bool(_) => "bool",
            AttributeValue::TextSet(_) => "set",
        }
    }
}

/// One comparable thing observed on a device.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ComparisonEntity {
    pub key: EntityKey,
    pub display_name: String,
    /// Semantic attributes only. Volatile values (capture timestamps,
    /// discovery durations, request ids, endpoint ordering) are excluded
    /// by the producing plugin's projection.
    #[serde(default)]
    pub attributes: BTreeMap<String, AttributeValue>,
    pub source_plugin: PluginId,
    pub source_check: CheckId,
    #[serde(default)]
    pub source_evidence_ids: Vec<EvidenceId>,
}

impl ComparisonEntity {
    pub fn new(
        key: EntityKey,
        display_name: impl Into<String>,
        source_plugin: &PluginId,
        source_check: &CheckId,
    ) -> Self {
        Self {
            key,
            display_name: display_name.into(),
            attributes: BTreeMap::new(),
            source_plugin: source_plugin.clone(),
            source_check: source_check.clone(),
            source_evidence_ids: Vec::new(),
        }
    }

    pub fn with(mut self, name: &str, value: AttributeValue) -> Self {
        self.attributes.insert(name.to_owned(), value);
        self
    }

    pub fn with_evidence(mut self, ids: Vec<EvidenceId>) -> Self {
        self.source_evidence_ids = ids;
        self
    }

    pub fn attribute(&self, name: &str) -> Option<&AttributeValue> {
        self.attributes.get(name)
    }
}

/// A plugin's baseline-comparison projection for one check.
///
/// `observed` is the critical signal separating "we looked and the thing
/// genuinely is not there" from "we could not look at all":
///
/// - `observed = true`  → the entity list is authoritative for this
///   namespace; an empty list means the namespace is genuinely empty
///   (e.g. a machine with no NVIDIA GPU). Missing expectations may be
///   evaluated as UNSATISFIED.
/// - `observed = false` → the plugin could not determine the namespace's
///   state (runtime failed to initialize, permission denied, timeout).
///   Expectations must evaluate to UNKNOWN, never UNSATISFIED.
/// - no report at all   → the plugin does not implement projection;
///   the namespace is UNSUPPORTED for comparison.
///
/// `kinds` narrows those claims to the entity kinds this one check is
/// authoritative for. It is what makes "a machine with no GPU" (the
/// `nvidia` plugin claims `gpu` and reports zero of them) different from
/// "nobody looked at GPUs" — and it keeps one failing ROS check from
/// making every other ROS expectation UNKNOWN.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ProjectionReport {
    pub namespace: String,
    pub observed: bool,
    /// Entity kinds this report speaks for. Empty means "the whole
    /// namespace", which is only correct when a single check covers it.
    #[serde(default)]
    pub kinds: Vec<String>,
    #[serde(default)]
    pub entities: Vec<ComparisonEntity>,
    /// Why the namespace could not be observed (when `observed` is false).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
}

impl ProjectionReport {
    /// Observed, claiming authority for exactly the kinds present in
    /// `entities`. Use [`ProjectionReport::observed_kinds`] when an empty
    /// result must still count as "we looked".
    pub fn observed(namespace: impl Into<String>, entities: Vec<ComparisonEntity>) -> Self {
        let kinds = distinct_kinds(&entities);
        Self {
            namespace: namespace.into(),
            observed: true,
            kinds,
            entities,
            reason: None,
        }
    }

    /// Observed, explicitly claiming a fixed set of kinds. An empty entity
    /// list then means "none of these exist", not "we did not look".
    pub fn observed_kinds<I, S>(
        namespace: impl Into<String>,
        kinds: I,
        entities: Vec<ComparisonEntity>,
    ) -> Self
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        let mut declared: Vec<String> = kinds.into_iter().map(Into::into).collect();
        for kind in distinct_kinds(&entities) {
            if !declared.contains(&kind) {
                declared.push(kind);
            }
        }
        declared.sort();
        Self {
            namespace: namespace.into(),
            observed: true,
            kinds: declared,
            entities,
            reason: None,
        }
    }

    /// Could not observe the whole namespace.
    pub fn not_observed(namespace: impl Into<String>, reason: impl Into<String>) -> Self {
        Self {
            namespace: namespace.into(),
            observed: false,
            kinds: Vec::new(),
            entities: Vec::new(),
            reason: Some(reason.into()),
        }
    }

    /// Could not observe specific kinds, leaving the rest of the namespace
    /// to other checks.
    pub fn not_observed_kinds<I, S>(
        namespace: impl Into<String>,
        kinds: I,
        reason: impl Into<String>,
    ) -> Self
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        let mut declared: Vec<String> = kinds.into_iter().map(Into::into).collect();
        declared.sort();
        Self {
            namespace: namespace.into(),
            observed: false,
            kinds: declared,
            entities: Vec::new(),
            reason: Some(reason.into()),
        }
    }
}

fn distinct_kinds(entities: &[ComparisonEntity]) -> Vec<String> {
    let set: BTreeSet<String> = entities.iter().map(|e| e.key.kind.clone()).collect();
    set.into_iter().collect()
}

/// Whether a namespace could be compared for a given diagnostic run.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum NamespaceAvailability {
    /// At least one check successfully observed this namespace.
    Observed,
    /// The namespace was attempted but could not be observed.
    NotObserved,
    /// No plugin in the run projects this namespace at all.
    Unsupported,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn entity_key_is_semantic_and_canonical() {
        let key = EntityKey::new("ros", "topic", "/scan|sensor_msgs/msg/LaserScan");
        assert_eq!(key.canonical(), "ros.topic:/scan|sensor_msgs/msg/LaserScan");
        let back: EntityKey = serde_json::from_str(&serde_json::to_string(&key).unwrap()).unwrap();
        assert_eq!(back, key);
    }

    #[test]
    fn attribute_sets_are_order_independent_by_construction() {
        let a = AttributeValue::set(["b", "a", "c"]);
        let b = AttributeValue::set(["c", "b", "a"]);
        assert_eq!(a, b);
        assert_eq!(a.render(), "a, b, c");
    }

    #[test]
    fn attribute_maps_serialize_deterministically() {
        let plugin = PluginId::from("ros2");
        let check = CheckId::from("ros.graph");
        let build = |order: [&str; 3]| {
            let mut entity = ComparisonEntity::new(
                EntityKey::new("ros", "topic", "/scan"),
                "/scan",
                &plugin,
                &check,
            );
            for name in order {
                entity = entity.with(name, AttributeValue::Number(1.0));
            }
            serde_json::to_string(&entity.attributes).unwrap()
        };
        assert_eq!(build(["z", "a", "m"]), build(["m", "z", "a"]));
    }

    #[test]
    fn projection_report_distinguishes_absent_from_unobservable() {
        // A GPU-less machine: we looked at `gpu`, there are none.
        let absent = ProjectionReport::observed_kinds("nvidia", ["gpu"], vec![]);
        assert!(absent.observed);
        assert!(absent.entities.is_empty());
        assert_eq!(absent.kinds, vec!["gpu".to_owned()]);

        let blind = ProjectionReport::not_observed("ros", "ROS_NOT_INSTALLED");
        assert!(!blind.observed);
        assert!(
            blind.kinds.is_empty(),
            "whole-namespace failure claims no kind"
        );
        assert_eq!(blind.reason.as_deref(), Some("ROS_NOT_INSTALLED"));
    }

    #[test]
    fn observed_kinds_are_derived_and_merged() {
        let plugin = PluginId::from("ros2");
        let check = CheckId::from("ros.graph");
        let entity = |kind: &str| {
            ComparisonEntity::new(EntityKey::new("ros", kind, "/x"), "/x", &plugin, &check)
        };
        let report = ProjectionReport::observed("ros", vec![entity("topic"), entity("node")]);
        assert_eq!(report.kinds, vec!["node".to_owned(), "topic".to_owned()]);

        // Declared kinds survive even when nothing of that kind was found.
        let merged =
            ProjectionReport::observed_kinds("ros", ["service", "node"], vec![entity("topic")]);
        assert_eq!(
            merged.kinds,
            vec!["node".to_owned(), "service".to_owned(), "topic".to_owned()]
        );
    }
}
