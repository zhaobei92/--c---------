//! Normalized ROS 2 observation model.
//!
//! These are *observations* of a live ROS system — never judgements.
//! Whether an observed state is good or bad is decided later by Profiles
//! and the rule engine (Phase C2/C3). No provider implementation details
//! (rclpy objects, CLI text) appear here.

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

// ── runtime configuration ──────────────────────────────────────────────

/// How a ROS runtime's environment is obtained.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum RosRuntimeMode {
    /// Conservative discovery (inherited env, standard install locations).
    Auto,
    /// Explicit user-configured setup script chain.
    Configured,
    /// Use the environment Robot Doctor itself was launched with.
    Inherited,
    /// Deterministic fixture provider (tests, demos). Never auto-selected.
    Fixture,
}

/// A stored ROS runtime configuration. A device may have several
/// (different distros/workspaces/domains); one is selected per diagnosis.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RosRuntimeConfig {
    /// Stable id, e.g. `auto:/opt/ros/jazzy` or a user-chosen name.
    pub id: String,
    pub name: String,
    pub mode: RosRuntimeMode,
    /// Setup scripts sourced in order (base ROS → overlay A → overlay B).
    #[serde(default)]
    pub setup_scripts: Vec<String>,
    /// Explicit ROS_DOMAIN_ID override (None = whatever the env yields).
    #[serde(default)]
    pub domain_id: Option<u32>,
    /// Explicit RMW_IMPLEMENTATION override.
    #[serde(default)]
    pub rmw: Option<String>,
    /// Extra environment variables applied after sourcing.
    #[serde(default)]
    pub extra_env: BTreeMap<String, String>,
    /// Fixture name when mode == Fixture.
    #[serde(default)]
    pub fixture: Option<String>,
    /// Force a provider (`rclpy` or `cli`); None = negotiate (rclpy first).
    #[serde(default)]
    pub preferred_provider: Option<String>,
}

/// What a runtime's environment actually looked like once bootstrapped.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct RosEnvironmentInfo {
    pub distro: Option<String>,
    pub ros_version: Option<String>,
    pub domain_id: Option<u32>,
    pub rmw_implementation: Option<String>,
    pub localhost_only: Option<bool>,
    pub install_prefix: Option<String>,
    pub python_executable: Option<String>,
    pub python_version: Option<String>,
    pub rclpy_available: bool,
    pub cli_available: bool,
    /// Overlay paths from AMENT_PREFIX_PATH beyond the base install.
    #[serde(default)]
    pub overlays: Vec<String>,
    /// Provider that will serve graph queries: `rclpy`, `cli`, `fixture`.
    pub provider: Option<String>,
}

// ── QoS ────────────────────────────────────────────────────────────────

macro_rules! qos_enum {
    ($name:ident { $($variant:ident),+ $(,)? }) => {
        #[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
        #[serde(rename_all = "SCREAMING_SNAKE_CASE")]
        pub enum $name {
            $($variant,)+
            SystemDefault,
            #[default]
            Unknown,
        }
    };
}

qos_enum!(QosReliability {
    Reliable,
    BestEffort
});
qos_enum!(QosDurability {
    Volatile,
    TransientLocal
});
qos_enum!(QosHistory { KeepLast, KeepAll });
qos_enum!(QosLiveliness {
    Automatic,
    ManualByTopic
});

/// A duration-valued QoS property. `unspecified` covers infinite/default.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize, Default)]
pub struct QosDuration {
    #[serde(default)]
    pub seconds: Option<f64>,
    #[serde(default)]
    pub unspecified: bool,
}

/// Normalized QoS profile of one endpoint.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct RosQosProfile {
    #[serde(default)]
    pub reliability: QosReliability,
    #[serde(default)]
    pub durability: QosDurability,
    #[serde(default)]
    pub history: QosHistory,
    #[serde(default)]
    pub depth: Option<u32>,
    #[serde(default)]
    pub deadline: QosDuration,
    #[serde(default)]
    pub lifespan: QosDuration,
    #[serde(default)]
    pub liveliness: QosLiveliness,
    #[serde(default)]
    pub lease_duration: QosDuration,
}

/// Deterministic publisher/subscriber QoS compatibility verdict.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum QosCompatibility {
    Compatible,
    Incompatible,
    Warning,
    Unknown,
}

// ── graph ──────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum RosEndpointType {
    Publisher,
    Subscription,
}

/// One pub/sub endpoint on a topic.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RosEndpointInfo {
    /// Fully qualified node name (`/ns/node`).
    pub node: String,
    pub endpoint_type: RosEndpointType,
    pub topic_type: String,
    /// Stable runtime identity (DDS GID hex) where available.
    #[serde(default)]
    pub gid: Option<String>,
    #[serde(default)]
    pub qos: RosQosProfile,
}

/// A node as observed in the graph. Identity = namespace + name.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RosNodeInfo {
    pub name: String,
    pub namespace: String,
    /// `/namespace/name`, normalized (single leading slash).
    pub full_name: String,
    #[serde(default)]
    pub publishers: Vec<String>,
    #[serde(default)]
    pub subscriptions: Vec<String>,
    #[serde(default)]
    pub services: Vec<String>,
    #[serde(default)]
    pub actions: Vec<String>,
}

/// A topic as observed. Identity = fully qualified name (+ types).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RosTopicInfo {
    pub name: String,
    /// One or more observed message types (multiple = type conflict fact).
    pub types: Vec<String>,
    pub publisher_count: u32,
    pub subscriber_count: u32,
    #[serde(default)]
    pub publishers: Vec<RosEndpointInfo>,
    #[serde(default)]
    pub subscribers: Vec<RosEndpointInfo>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RosServiceInfo {
    pub name: String,
    pub types: Vec<String>,
    #[serde(default)]
    pub providers: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RosActionInfo {
    pub name: String,
    pub types: Vec<String>,
    #[serde(default)]
    pub servers: Vec<String>,
}

/// A complete normalized snapshot of the observable ROS graph.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
#[serde(default)]
pub struct RosGraphSnapshot {
    /// Which runtime produced this (RosRuntimeConfig id).
    pub runtime_id: String,
    pub provider: String,
    pub captured_at: chrono::DateTime<chrono::Utc>,
    /// How long DDS discovery was allowed to stabilize before capture.
    pub discovery_ms: u64,
    #[serde(default)]
    pub nodes: Vec<RosNodeInfo>,
    #[serde(default)]
    pub topics: Vec<RosTopicInfo>,
    #[serde(default)]
    pub services: Vec<RosServiceInfo>,
    #[serde(default)]
    pub actions: Vec<RosActionInfo>,
}

impl RosGraphSnapshot {
    /// Sort all collections so snapshot equality/diffing does not depend
    /// on discovery order.
    pub fn normalize(&mut self) {
        self.nodes.sort_by(|a, b| a.full_name.cmp(&b.full_name));
        self.topics.sort_by(|a, b| a.name.cmp(&b.name));
        self.services.sort_by(|a, b| a.name.cmp(&b.name));
        self.actions.sort_by(|a, b| a.name.cmp(&b.name));
        for topic in &mut self.topics {
            topic.types.sort();
            topic.publishers.sort_by(|a, b| a.node.cmp(&b.node));
            topic.subscribers.sort_by(|a, b| a.node.cmp(&b.node));
        }
    }
}

// ── TF ─────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RosTfEdge {
    pub parent: String,
    pub child: String,
    /// True when seen on /tf_static (or provider says static).
    #[serde(default)]
    pub is_static: Option<bool>,
    /// Most recent transform timestamp (ROS time, seconds).
    #[serde(default)]
    pub last_stamp: Option<f64>,
    /// Node that most recently broadcast this edge, where known.
    #[serde(default)]
    pub broadcaster: Option<String>,
}

/// Normalized TF structure snapshot.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct RosTfSnapshot {
    pub frames: Vec<String>,
    pub edges: Vec<RosTfEdge>,
    /// Frame groups that are mutually connected (observation, not defect).
    pub connected_components: Vec<Vec<String>>,
    /// How long TF data was collected for this snapshot, ms.
    pub listen_ms: u64,
}

/// Result of an explicit `can_transform` query.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RosTfQueryResult {
    pub source: String,
    pub target: String,
    /// AVAILABLE / UNAVAILABLE / TIMEOUT
    pub status: String,
    #[serde(default)]
    pub reason: Option<String>,
    /// Query round-trip in ms.
    pub elapsed_ms: f64,
    /// Timestamp of the transform used, when available.
    #[serde(default)]
    pub stamp: Option<f64>,
}

// ── diagnostics ────────────────────────────────────────────────────────

/// One component status from /diagnostics, level preserved verbatim.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RosDiagnosticStatus {
    pub name: String,
    pub hardware_id: String,
    /// Source level: 0 OK, 1 WARN, 2 ERROR, 3 STALE.
    pub level: u8,
    pub message: String,
    #[serde(default)]
    pub values: BTreeMap<String, String>,
    /// ROS time of the containing DiagnosticArray, seconds.
    #[serde(default)]
    pub stamp: Option<f64>,
    /// `/diagnostics` or `/diagnostics_agg`.
    pub source_topic: String,
}

impl RosDiagnosticStatus {
    pub fn level_label(&self) -> &'static str {
        match self.level {
            0 => "OK",
            1 => "WARN",
            2 => "ERROR",
            3 => "STALE",
            _ => "UNKNOWN",
        }
    }
}

// ── lifecycle ──────────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RosLifecycleState {
    pub node: String,
    pub state_id: u8,
    pub state_label: String,
    /// Whether the node's transition services were reachable.
    #[serde(default)]
    pub services_available: bool,
}

// ── clock ──────────────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct RosClockObservation {
    /// System wall time at observation (unix seconds).
    pub system_time: f64,
    /// ROS time as seen by the provider (unix-like seconds).
    #[serde(default)]
    pub ros_time: Option<f64>,
    /// Whether /clock exists in the graph.
    pub clock_topic_present: bool,
    /// use_sim_time where discoverable (per provider node context).
    #[serde(default)]
    pub use_sim_time: Option<bool>,
    /// Observed /clock progression over the sample window (seconds), when
    /// /clock is present. None = not sampled.
    #[serde(default)]
    pub clock_advance: Option<f64>,
}

// ── topic sampling ─────────────────────────────────────────────────────

/// Bounded topic sampling result. Distinguishes header-timestamp age from
/// receive age (time since last observed message).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RosTopicSample {
    pub topic: String,
    pub message_type: Option<String>,
    pub sample_count: u32,
    pub sampling_duration_s: f64,
    #[serde(default)]
    pub observed_hz: Option<f64>,
    #[serde(default)]
    pub period_min_ms: Option<f64>,
    #[serde(default)]
    pub period_max_ms: Option<f64>,
    #[serde(default)]
    pub period_mean_ms: Option<f64>,
    #[serde(default)]
    pub period_stddev_ms: Option<f64>,
    /// Age of the newest message's header stamp vs ROS clock, seconds.
    /// None when messages carry no usable timestamp — never invented.
    #[serde(default)]
    pub message_stamp_age_s: Option<f64>,
    /// Time between last received message and end of sampling, seconds.
    #[serde(default)]
    pub receive_age_s: Option<f64>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn qos_defaults_are_unknown_not_guessed() {
        let qos = RosQosProfile::default();
        assert_eq!(qos.reliability, QosReliability::Unknown);
        assert_eq!(qos.durability, QosDurability::Unknown);
        let json = serde_json::to_string(&qos).unwrap();
        assert!(json.contains("UNKNOWN"));
    }

    #[test]
    fn snapshot_normalization_is_order_independent() {
        let node = |n: &str| RosNodeInfo {
            name: n.trim_start_matches('/').to_owned(),
            namespace: "/".into(),
            full_name: n.to_owned(),
            publishers: vec![],
            subscriptions: vec![],
            services: vec![],
            actions: vec![],
        };
        let mut a = RosGraphSnapshot {
            nodes: vec![node("/b"), node("/a")],
            ..Default::default()
        };
        let mut b = RosGraphSnapshot {
            nodes: vec![node("/a"), node("/b")],
            ..Default::default()
        };
        a.normalize();
        b.normalize();
        assert_eq!(a.nodes, b.nodes);
    }

    #[test]
    fn runtime_config_roundtrip() {
        let cfg = RosRuntimeConfig {
            id: "configured:jazzy".into(),
            name: "Jazzy + workspace".into(),
            mode: RosRuntimeMode::Configured,
            setup_scripts: vec![
                "/opt/ros/jazzy/setup.bash".into(),
                "/home/robot/ws/install/setup.bash".into(),
            ],
            domain_id: Some(7),
            rmw: Some("rmw_cyclonedds_cpp".into()),
            extra_env: BTreeMap::new(),
            fixture: None,
            preferred_provider: None,
        };
        let back: RosRuntimeConfig =
            serde_json::from_str(&serde_json::to_string(&cfg).unwrap()).unwrap();
        assert_eq!(back, cfg);
    }

    #[test]
    fn diagnostic_levels_preserved() {
        let status = RosDiagnosticStatus {
            name: "lidar".into(),
            hardware_id: "hokuyo".into(),
            level: 3,
            message: "no data".into(),
            values: BTreeMap::new(),
            stamp: Some(123.4),
            source_topic: "/diagnostics".into(),
        };
        assert_eq!(status.level_label(), "STALE");
        let back: RosDiagnosticStatus =
            serde_json::from_str(&serde_json::to_string(&status).unwrap()).unwrap();
        assert_eq!(back.level, 3);
    }
}
