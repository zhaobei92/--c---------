//! Provider abstraction: normalized ROS observation operations.
//! Implementations: rclpy (preferred), CLI fallback (partial), fixture
//! (deterministic tests). Implementation details never leak into
//! doctor-domain — providers return normalized domain structures.

use crate::error::{RosError, RosErrorKind, RosResult};
use doctor_domain::ros::{
    RosClockObservation, RosDiagnosticStatus, RosGraphSnapshot, RosLifecycleState,
    RosTfQueryResult, RosTfSnapshot, RosTopicSample,
};

/// Operations a provider may support. A partial provider is valid.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProviderOp {
    Graph,
    TopicSample,
    Tf,
    TfQuery,
    Diagnostics,
    Lifecycle,
    Clock,
    QosCompat,
}

pub trait RosProvider: Send + Sync {
    fn name(&self) -> &'static str;
    fn supports(&self, op: ProviderOp) -> bool;

    fn graph_snapshot(&self, discovery_s: f64) -> RosResult<RosGraphSnapshot>;
    fn topic_sample(&self, topic: &str, duration_s: f64) -> RosResult<RosTopicSample>;
    fn tf_snapshot(&self, listen_s: f64) -> RosResult<RosTfSnapshot>;
    fn tf_query(&self, source: &str, target: &str, timeout_s: f64) -> RosResult<RosTfQueryResult>;
    fn diagnostics_snapshot(&self, window_s: f64) -> RosResult<Vec<RosDiagnosticStatus>>;
    fn lifecycle_snapshot(&self, timeout_s: f64) -> RosResult<Vec<RosLifecycleState>>;
    fn clock_snapshot(&self, window_s: f64) -> RosResult<RosClockObservation>;
    /// topic → per-pair QoS verdicts as JSON (rclpy only).
    fn qos_compat(&self, topic: &str) -> RosResult<serde_json::Value>;
}

pub fn unsupported<T>(provider: &str, what: &str) -> RosResult<T> {
    Err(RosError::new(
        RosErrorKind::CliUnavailable,
        format!("{what} is not supported by the {provider} provider"),
    ))
}

// ── fixture provider ───────────────────────────────────────────────────

/// Deterministic provider backed by a recorded normalized snapshot file.
/// Powers the fixture system (ros_empty, ros_basic_healthy, …) so
/// observation, persistence and UI paths are testable without a live
/// ROS system — including on Windows CI.
pub struct FixtureProvider {
    data: serde_json::Value,
}

impl FixtureProvider {
    pub fn load(fixtures_dir: &std::path::Path, name: &str) -> RosResult<Self> {
        // Fixture names are identifiers, never paths.
        if name.contains('/') || name.contains('\\') || name.contains("..") {
            return Err(RosError::new(
                RosErrorKind::ProviderError,
                format!("invalid fixture name '{name}'"),
            ));
        }
        let path = fixtures_dir.join(format!("{name}.json"));
        let text = std::fs::read_to_string(&path).map_err(|e| {
            RosError::new(
                RosErrorKind::ProviderError,
                format!("fixture '{name}' not found at {}: {e}", path.display()),
            )
        })?;
        let data = serde_json::from_str(&text).map_err(|e| {
            RosError::new(
                RosErrorKind::ProviderError,
                format!("fixture '{name}' is not valid JSON: {e}"),
            )
        })?;
        Ok(Self { data })
    }

    fn section<T: serde::de::DeserializeOwned>(&self, key: &str) -> RosResult<T> {
        let value = self.data.get(key).cloned().ok_or_else(|| {
            RosError::new(
                RosErrorKind::ProviderError,
                format!("fixture has no '{key}' section"),
            )
        })?;
        serde_json::from_value(value).map_err(|e| {
            RosError::new(
                RosErrorKind::ProviderError,
                format!("fixture section '{key}' malformed: {e}"),
            )
        })
    }
}

impl RosProvider for FixtureProvider {
    fn name(&self) -> &'static str {
        "fixture"
    }

    fn supports(&self, _op: ProviderOp) -> bool {
        true
    }

    fn graph_snapshot(&self, _discovery_s: f64) -> RosResult<RosGraphSnapshot> {
        let mut snapshot: RosGraphSnapshot = self.section("graph")?;
        snapshot.provider = "fixture".into();
        snapshot.captured_at = chrono::Utc::now();
        snapshot.normalize();
        Ok(snapshot)
    }

    fn topic_sample(&self, topic: &str, _duration_s: f64) -> RosResult<RosTopicSample> {
        // Per-topic simulated errors, e.g. custom_type_unavailable.
        if let Some(errors) = self.data.get("sample_errors").and_then(|v| v.as_object()) {
            if let Some(kind) = errors.get(topic).and_then(|v| v.as_str()) {
                return Err(RosError::new(
                    RosErrorKind::from_code(kind),
                    format!("fixture-simulated sampling failure for {topic}"),
                ));
            }
        }
        let samples: std::collections::BTreeMap<String, RosTopicSample> =
            self.section("samples").unwrap_or_default();
        samples.get(topic).cloned().ok_or_else(|| {
            RosError::new(
                RosErrorKind::ProviderError,
                format!("fixture has no sample for topic '{topic}'"),
            )
        })
    }

    fn tf_snapshot(&self, _listen_s: f64) -> RosResult<RosTfSnapshot> {
        self.section("tf")
    }

    fn tf_query(&self, source: &str, target: &str, _timeout_s: f64) -> RosResult<RosTfQueryResult> {
        // Answer from the fixture's TF connectivity.
        let tf: RosTfSnapshot = self.section("tf")?;
        let connected = tf
            .connected_components
            .iter()
            .any(|c| c.contains(&source.to_owned()) && c.contains(&target.to_owned()));
        Ok(RosTfQueryResult {
            source: source.to_owned(),
            target: target.to_owned(),
            status: if connected {
                "AVAILABLE"
            } else {
                "UNAVAILABLE"
            }
            .to_owned(),
            reason: (!connected).then(|| {
                format!("frames '{source}' and '{target}' are not in the same connected component")
            }),
            elapsed_ms: 1.0,
            stamp: None,
        })
    }

    fn diagnostics_snapshot(&self, _window_s: f64) -> RosResult<Vec<RosDiagnosticStatus>> {
        self.section("diagnostics").or_else(|_| Ok(vec![]))
    }

    fn lifecycle_snapshot(&self, _timeout_s: f64) -> RosResult<Vec<RosLifecycleState>> {
        self.section("lifecycle").or_else(|_| Ok(vec![]))
    }

    fn clock_snapshot(&self, _window_s: f64) -> RosResult<RosClockObservation> {
        self.section("clock").or_else(|_| {
            Ok(RosClockObservation {
                system_time: 0.0,
                ..Default::default()
            })
        })
    }

    fn qos_compat(&self, topic: &str) -> RosResult<serde_json::Value> {
        let compat = self.data.get("qos_compat").cloned().unwrap_or_default();
        Ok(compat.get(topic).cloned().unwrap_or(serde_json::json!({
            "topic": topic, "pairs": []
        })))
    }
}
