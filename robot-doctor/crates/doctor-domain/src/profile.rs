//! Device profiles: human-authored YAML describing the *expected* state of
//! a robot. Robot-specific requirements live here, never in rules or code.

use crate::ids::ProfileId;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// Expectation for one topic.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct TopicExpectation {
    #[serde(default)]
    pub required: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub min_hz: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_hz: Option<f64>,
    /// Maximum age of the newest message, in seconds.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_age_s: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expected_publishers: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expected_subscribers: Option<u32>,
}

/// A required TF transform path.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TfPathExpectation {
    pub from: String,
    pub to: String,
}

/// Expectation for a network target the device must reach.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct NetworkTargetExpectation {
    pub host: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub port: Option<u16>,
    #[serde(default)]
    pub description: String,
}

/// Expectations grouped under `ros:` in the profile YAML.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct RosExpectations {
    #[serde(default)]
    pub required_nodes: Vec<String>,
    #[serde(default)]
    pub optional_nodes: Vec<String>,
    /// Topic name → expectation.
    #[serde(default)]
    pub topics: BTreeMap<String, TopicExpectation>,
    #[serde(default)]
    pub required_tf: Vec<TfPathExpectation>,
    #[serde(default)]
    pub required_services: Vec<String>,
    #[serde(default)]
    pub required_actions: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expected_domain_id: Option<u32>,
}

/// A device profile: the expected state of a robot/workstation.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Profile {
    pub id: ProfileId,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub required_processes: Vec<String>,
    /// TCP ports that must be listening locally.
    #[serde(default)]
    pub required_ports: Vec<u16>,
    #[serde(default)]
    pub network_targets: Vec<NetworkTargetExpectation>,
    #[serde(default)]
    pub ros: RosExpectations,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn profile_yaml_parses() {
        let yaml = r#"
id: nav2_robot
name: Nav2 robot
required_processes: [robot_state_publisher]
required_ports: [8765]
network_targets:
  - host: 192.168.1.1
    description: gateway
ros:
  required_nodes: [/controller_server]
  topics:
    /scan:
      required: true
      min_hz: 8
    /odom:
      required: true
      min_hz: 20
  required_tf:
    - { from: map, to: odom }
    - { from: odom, to: base_link }
"#;
        let p: Profile = serde_yaml::from_str(yaml).unwrap();
        assert_eq!(p.id, ProfileId::from("nav2_robot"));
        assert_eq!(p.ros.topics["/scan"].min_hz, Some(8.0));
        assert_eq!(p.ros.required_tf.len(), 2);
        assert!(p.ros.topics["/odom"].required);
    }

    #[test]
    fn shipped_example_profile_parses() {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../profiles/examples/nav2_robot.yaml");
        let p: Profile = serde_yaml::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
        assert_eq!(p.id, ProfileId::from("nav2_robot"));
        assert_eq!(p.ros.required_tf.len(), 3);
        assert_eq!(p.ros.topics["/odom"].max_age_s, Some(0.5));
        assert_eq!(p.required_ports, vec![8765]);
    }
}
