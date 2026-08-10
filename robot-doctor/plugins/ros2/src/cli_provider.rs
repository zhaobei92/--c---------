//! CliFallbackProvider: partial provider built on the `ros2` CLI.
//! Used when rclpy is broken/missing but the CLI works. Text parsing is
//! confined to this module and limited to stable list outputs; deeper
//! operations report themselves as unsupported instead of guessing.

use crate::bootstrap::{run_bounded, which_in, EnvMap};
use crate::error::{RosError, RosErrorKind, RosResult};
use crate::provider::{unsupported, ProviderOp, RosProvider};
use doctor_domain::ros::{
    RosActionInfo, RosClockObservation, RosDiagnosticStatus, RosGraphSnapshot, RosLifecycleState,
    RosNodeInfo, RosServiceInfo, RosTfQueryResult, RosTfSnapshot, RosTopicInfo, RosTopicSample,
};
use std::process::Command;
use std::time::Duration;

/// How many topics get a `ros2 topic info` counting query per snapshot.
const MAX_TOPIC_INFO_QUERIES: usize = 30;

pub struct CliFallbackProvider {
    ros2: String,
    env: EnvMap,
}

impl CliFallbackProvider {
    pub fn new(env: EnvMap) -> RosResult<Self> {
        let ros2 = which_in(&env, "ros2").ok_or_else(|| {
            RosError::new(
                RosErrorKind::CliUnavailable,
                "ros2 executable not found in the runtime PATH",
            )
        })?;
        Ok(Self { ros2, env })
    }

    fn run(&self, args: &[&str], timeout: Duration) -> RosResult<String> {
        let mut cmd = Command::new(&self.ros2);
        cmd.args(args).env_clear().envs(&self.env);
        let (code, stdout, stderr, _) = run_bounded(cmd, timeout)?;
        if code != 0 {
            return Err(RosError::new(
                RosErrorKind::ProviderError,
                format!("ros2 {} exited {code}: {}", args.join(" "), stderr.trim()),
            ));
        }
        Ok(stdout)
    }

    /// `name [type1, type2]` lines → (name, types).
    fn parse_typed_list(output: &str) -> Vec<(String, Vec<String>)> {
        output
            .lines()
            .filter_map(|line| {
                let line = line.trim();
                if line.is_empty() {
                    return None;
                }
                match line.split_once(" [") {
                    Some((name, types)) => Some((
                        name.trim().to_owned(),
                        types
                            .trim_end_matches(']')
                            .split(',')
                            .map(|t| t.trim().to_owned())
                            .filter(|t| !t.is_empty())
                            .collect(),
                    )),
                    None => Some((line.to_owned(), vec![])),
                }
            })
            .collect()
    }
}

impl RosProvider for CliFallbackProvider {
    fn name(&self) -> &'static str {
        "cli"
    }

    fn supports(&self, op: ProviderOp) -> bool {
        matches!(op, ProviderOp::Graph)
    }

    fn graph_snapshot(&self, _discovery_s: f64) -> RosResult<RosGraphSnapshot> {
        let nodes_out = self.run(&["node", "list"], Duration::from_secs(20))?;
        let topics_out = self.run(&["topic", "list", "-t"], Duration::from_secs(20))?;
        let services_out = self.run(&["service", "list", "-t"], Duration::from_secs(20))?;
        let actions_out = self
            .run(&["action", "list", "-t"], Duration::from_secs(20))
            .unwrap_or_default();

        let nodes: Vec<RosNodeInfo> = nodes_out
            .lines()
            .map(str::trim)
            .filter(|l| !l.is_empty() && !l.contains("robot_doctor_observer"))
            .map(|full| {
                let (namespace, name) = match full.rsplit_once('/') {
                    Some((ns, n)) if !ns.is_empty() => (ns.to_owned(), n.to_owned()),
                    _ => ("/".to_owned(), full.trim_start_matches('/').to_owned()),
                };
                RosNodeInfo {
                    name,
                    namespace,
                    full_name: full.to_owned(),
                    publishers: vec![],
                    subscriptions: vec![],
                    services: vec![],
                    actions: vec![],
                }
            })
            .collect();

        let mut topics: Vec<RosTopicInfo> = Self::parse_typed_list(&topics_out)
            .into_iter()
            .map(|(name, types)| RosTopicInfo {
                name,
                types,
                publisher_count: 0,
                subscriber_count: 0,
                publishers: vec![],
                subscribers: vec![],
            })
            .collect();

        // Bounded pub/sub counting; beyond the cap counts stay 0 and the
        // snapshot notes the truncation via `discovery_ms` evidence upstream.
        for topic in topics.iter_mut().take(MAX_TOPIC_INFO_QUERIES) {
            if let Ok(info) = self.run(&["topic", "info", &topic.name], Duration::from_secs(10)) {
                for line in info.lines() {
                    let line = line.trim();
                    if let Some(rest) = line.strip_prefix("Publisher count:") {
                        topic.publisher_count = rest.trim().parse().unwrap_or(0);
                    } else if let Some(rest) = line.strip_prefix("Subscription count:") {
                        topic.subscriber_count = rest.trim().parse().unwrap_or(0);
                    }
                }
            }
        }

        let services: Vec<RosServiceInfo> = Self::parse_typed_list(&services_out)
            .into_iter()
            .filter(|(name, _)| !name.contains("/_action/"))
            .map(|(name, types)| RosServiceInfo {
                name,
                types,
                providers: vec![],
            })
            .collect();

        let actions: Vec<RosActionInfo> = Self::parse_typed_list(&actions_out)
            .into_iter()
            .map(|(name, types)| RosActionInfo {
                name,
                types,
                servers: vec![],
            })
            .collect();

        let mut snapshot = RosGraphSnapshot {
            runtime_id: String::new(),
            provider: "cli".into(),
            captured_at: chrono::Utc::now(),
            discovery_ms: 0,
            nodes,
            topics,
            services,
            actions,
        };
        snapshot.normalize();
        Ok(snapshot)
    }

    fn topic_sample(&self, _topic: &str, _duration_s: f64) -> RosResult<RosTopicSample> {
        unsupported(self.name(), "bounded topic sampling")
    }

    fn tf_snapshot(&self, _listen_s: f64) -> RosResult<RosTfSnapshot> {
        unsupported(self.name(), "TF observation")
    }

    fn tf_query(&self, _s: &str, _t: &str, _timeout_s: f64) -> RosResult<RosTfQueryResult> {
        unsupported(self.name(), "TF query")
    }

    fn diagnostics_snapshot(&self, _window_s: f64) -> RosResult<Vec<RosDiagnosticStatus>> {
        unsupported(self.name(), "diagnostics ingestion")
    }

    fn lifecycle_snapshot(&self, _timeout_s: f64) -> RosResult<Vec<RosLifecycleState>> {
        unsupported(self.name(), "lifecycle inspection")
    }

    fn clock_snapshot(&self, _window_s: f64) -> RosResult<RosClockObservation> {
        unsupported(self.name(), "clock observation")
    }

    fn qos_compat(&self, _topic: &str) -> RosResult<serde_json::Value> {
        unsupported(self.name(), "QoS compatibility evaluation")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn typed_list_parsing_handles_types_and_bare_names() {
        let parsed = CliFallbackProvider::parse_typed_list(
            "/chatter [std_msgs/msg/String]\n/rosout [rcl_interfaces/msg/Log]\n/bare\n",
        );
        assert_eq!(parsed.len(), 3);
        assert_eq!(parsed[0].0, "/chatter");
        assert_eq!(parsed[0].1, vec!["std_msgs/msg/String"]);
        assert!(parsed[2].1.is_empty());
    }
}
