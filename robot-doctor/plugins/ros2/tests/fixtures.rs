//! Fixture-driven ros2 plugin tests: observation normalization, failure
//! semantics and the read-only contract — no live ROS system required,
//! so these run identically on Linux and Windows CI.

use doctor_domain::ros::{RosGraphSnapshot, RosRuntimeConfig, RosRuntimeMode, RosTfSnapshot};
use doctor_domain::{CheckId, CheckRequest, CheckStatus, DeviceId, DiagnosticMode};
use plugin_ros2::{run_check, RosPlugin};
use std::collections::BTreeMap;
use std::path::PathBuf;

fn plugin() -> RosPlugin {
    RosPlugin::new(PathBuf::from(env!("CARGO_MANIFEST_DIR")))
}

fn fixture_runtime(fixture: &str) -> serde_json::Value {
    serde_json::to_value(RosRuntimeConfig {
        id: format!("fixture:{fixture}"),
        name: format!("Fixture {fixture}"),
        mode: RosRuntimeMode::Fixture,
        setup_scripts: vec![],
        domain_id: None,
        rmw: None,
        extra_env: BTreeMap::new(),
        fixture: Some(fixture.to_owned()),
        preferred_provider: None,
    })
    .unwrap()
}

fn request(check: &str, fixture: &str, extra: serde_json::Value) -> CheckRequest {
    let mut params: BTreeMap<String, serde_json::Value> = BTreeMap::new();
    params.insert("runtime".into(), fixture_runtime(fixture));
    if let serde_json::Value::Object(map) = extra {
        params.extend(map);
    }
    CheckRequest {
        check_id: CheckId::from(check),
        device_id: DeviceId::from("local"),
        mode: Some(DiagnosticMode::Full),
        params,
        timeout_ms: 30_000,
    }
}

fn observation_json(result: &doctor_domain::CheckResult, key: &str) -> serde_json::Value {
    let obs = result
        .observations
        .iter()
        .find(|o| o.key == key)
        .unwrap_or_else(|| panic!("observation '{key}' missing"));
    match &obs.value {
        doctor_domain::ObservationValue::Json(v) => v.clone(),
        other => panic!("observation '{key}' is not JSON: {other:?}"),
    }
}

#[test]
fn graph_snapshot_normalizes_identities_and_endpoints() {
    let plugin = plugin();
    let result = run_check(
        &plugin,
        &request("ros.graph", "ros_basic_healthy", serde_json::json!({})),
    );
    assert_eq!(result.status, CheckStatus::Passed);
    let snapshot: RosGraphSnapshot =
        serde_json::from_value(observation_json(&result, "ros.graph.snapshot")).unwrap();
    // Node identity: namespace + name → fully qualified.
    let talker = snapshot
        .nodes
        .iter()
        .find(|n| n.full_name == "/talker")
        .unwrap();
    assert_eq!(talker.name, "talker");
    // Topic identity + endpoint normalization with QoS.
    let chatter = snapshot
        .topics
        .iter()
        .find(|t| t.name == "/chatter")
        .unwrap();
    assert_eq!(chatter.types, vec!["std_msgs/msg/String"]);
    assert_eq!(chatter.publisher_count, 1);
    assert_eq!(chatter.subscriber_count, 1);
    assert_eq!(chatter.publishers[0].node, "/talker");
    assert_eq!(
        chatter.publishers[0].qos.reliability,
        doctor_domain::ros::QosReliability::Reliable
    );
    // Findings must NOT be generated from graph facts in C1.
    assert!(result.findings.is_empty());
}

#[test]
fn zero_publisher_topic_is_an_observation_not_a_finding() {
    let plugin = plugin();
    let result = run_check(
        &plugin,
        &request("ros.graph", "ros_topic_no_publisher", serde_json::json!({})),
    );
    assert_eq!(result.status, CheckStatus::Passed);
    let snapshot: RosGraphSnapshot =
        serde_json::from_value(observation_json(&result, "ros.graph.snapshot")).unwrap();
    let orphan = snapshot
        .topics
        .iter()
        .find(|t| t.name == "/orphan")
        .unwrap();
    assert_eq!(orphan.publisher_count, 0);
    assert!(
        result.findings.is_empty(),
        "C1 must not emit TOPIC_* findings"
    );
}

#[test]
fn slow_topic_rate_is_reported_without_judgement() {
    let plugin = plugin();
    let result = run_check(
        &plugin,
        &request(
            "ros.topic_rate",
            "ros_topic_rate_slow",
            serde_json::json!({"topics": ["/chatter"]}),
        ),
    );
    assert_eq!(result.status, CheckStatus::Passed);
    let hz = result
        .observations
        .iter()
        .find(|o| o.key == "ros.topic_rate./chatter.hz")
        .and_then(|o| o.as_number())
        .unwrap();
    assert!((hz - 1.2).abs() < 0.01);
    assert!(result.findings.is_empty(), "no TOPIC_RATE_LOW in C1");
}

#[test]
fn custom_type_missing_is_dependency_missing_with_evidence() {
    let plugin = plugin();
    let result = run_check(
        &plugin,
        &request(
            "ros.topic_rate",
            "custom_type_unavailable",
            serde_json::json!({"topics": ["/custom"]}),
        ),
    );
    assert_eq!(result.status, CheckStatus::DependencyMissing);
    let err = result.error.as_ref().unwrap();
    assert!(
        err.message.contains("TYPE_SUPPORT_MISSING"),
        "{}",
        err.message
    );
    // Precise evidence exists; the topic itself is not marked broken.
    assert!(!result.evidence.is_empty());
    assert!(result.findings.is_empty());
}

#[test]
fn tf_connected_and_disconnected_are_observations() {
    let plugin = plugin();
    let connected = run_check(
        &plugin,
        &request(
            "ros.tf",
            "ros_tf_connected",
            serde_json::json!({"tf_queries": [{"source": "map", "target": "base_link"}]}),
        ),
    );
    assert_eq!(connected.status, CheckStatus::Passed);
    let q = observation_json(&connected, "ros.tf.query.map->base_link");
    assert_eq!(q["status"], "AVAILABLE");

    let disconnected = run_check(
        &plugin,
        &request(
            "ros.tf",
            "ros_tf_disconnected",
            serde_json::json!({"tf_queries": [{"source": "map", "target": "base_link"}]}),
        ),
    );
    assert_eq!(
        disconnected.status,
        CheckStatus::Passed,
        "disconnection is not a defect in C1"
    );
    let q = observation_json(&disconnected, "ros.tf.query.map->base_link");
    assert_eq!(q["status"], "UNAVAILABLE");
    assert!(q["reason"]
        .as_str()
        .unwrap()
        .contains("connected component"));
    let snapshot: RosTfSnapshot =
        serde_json::from_value(observation_json(&disconnected, "ros.tf.snapshot")).unwrap();
    assert_eq!(snapshot.connected_components.len(), 2);
    assert!(disconnected.findings.is_empty(), "no TF_PATH_MISSING in C1");
}

#[test]
fn diagnostics_levels_are_preserved_not_reinterpreted() {
    let plugin = plugin();
    let result = run_check(
        &plugin,
        &request(
            "ros.diagnostics",
            "ros_diagnostics_error",
            serde_json::json!({}),
        ),
    );
    assert_eq!(result.status, CheckStatus::Passed);
    let error_count = result
        .observations
        .iter()
        .find(|o| o.key == "ros.diagnostics.error_count")
        .and_then(|o| o.as_number())
        .unwrap();
    let stale_count = result
        .observations
        .iter()
        .find(|o| o.key == "ros.diagnostics.stale_count")
        .and_then(|o| o.as_number())
        .unwrap();
    assert_eq!(error_count, 1.0);
    assert_eq!(stale_count, 1.0);
    // Source levels visible; no Robot Doctor findings derived from them.
    assert!(result.findings.is_empty());
}

#[test]
fn qos_mismatch_is_an_observation_verdict_not_a_finding() {
    let plugin = plugin();
    let result = run_check(
        &plugin,
        &request("ros.qos", "ros_qos_mismatch", serde_json::json!({})),
    );
    assert_eq!(result.status, CheckStatus::Passed);
    let verdicts = observation_json(&result, "ros.qos./scan");
    assert_eq!(verdicts["pairs"][0]["verdict"], "INCOMPATIBLE");
    assert!(result.findings.is_empty(), "no QOS finding in C1");
}

#[test]
fn lifecycle_states_are_read_only_observations() {
    let plugin = plugin();
    let result = run_check(
        &plugin,
        &request(
            "ros.lifecycle",
            "ros_lifecycle_inactive",
            serde_json::json!({}),
        ),
    );
    assert_eq!(result.status, CheckStatus::Passed);
    let states = observation_json(&result, "ros.lifecycle.states");
    assert_eq!(states[0]["state_label"], "inactive");
    assert!(result.findings.is_empty());
}

#[test]
fn empty_graph_fixture_is_valid() {
    let plugin = plugin();
    let result = run_check(
        &plugin,
        &request("ros.graph", "ros_empty", serde_json::json!({})),
    );
    assert_eq!(result.status, CheckStatus::Passed);
    let count = result
        .observations
        .iter()
        .find(|o| o.key == "ros.graph.node_count")
        .and_then(|o| o.as_number())
        .unwrap();
    assert_eq!(count, 0.0);
}

#[test]
fn unknown_fixture_is_a_typed_provider_error() {
    let plugin = plugin();
    let result = run_check(
        &plugin,
        &request("ros.graph", "nope_does_not_exist", serde_json::json!({})),
    );
    // Provider could not start; typed unavailability, never a crash.
    assert!(matches!(
        result.status,
        CheckStatus::Unavailable | CheckStatus::Error
    ));
    assert!(result.error.is_some());
}

#[cfg(unix)]
#[test]
fn configured_runtime_that_cannot_initialize_is_the_one_allowed_finding() {
    let plugin = plugin();
    let runtime = serde_json::to_value(RosRuntimeConfig {
        id: "broken".into(),
        name: "Broken".into(),
        mode: RosRuntimeMode::Configured,
        setup_scripts: vec!["/does/not/exist/setup.bash".into()],
        domain_id: None,
        rmw: None,
        extra_env: BTreeMap::new(),
        fixture: None,
        preferred_provider: None,
    })
    .unwrap();
    let mut params = BTreeMap::new();
    params.insert("runtime".to_owned(), runtime);
    let result = run_check(
        &plugin,
        &CheckRequest {
            check_id: CheckId::from("ros.environment"),
            device_id: DeviceId::from("local"),
            mode: Some(DiagnosticMode::Quick),
            params,
            timeout_ms: 30_000,
        },
    );
    assert_eq!(result.status, CheckStatus::Failed);
    assert_eq!(result.findings[0].code, "ROS_RUNTIME_INIT_FAILED");
    assert!(!result.findings[0].evidence_ids.is_empty());
}

/// SCENARIO A semantics: a system without any ROS runtime yields a clean
/// typed UNAVAILABLE — never a crash, never generic ERROR. Runs on both
/// Linux and Windows CI (neither test env has an inherited ROS).
#[test]
fn ros_absence_is_unavailable_not_error() {
    if std::env::var("ROS_DISTRO").is_ok() {
        return; // only meaningful without an inherited ROS environment
    }
    let plugin = plugin();
    let runtime = serde_json::to_value(RosRuntimeConfig {
        id: "auto".into(),
        name: "Auto".into(),
        mode: RosRuntimeMode::Auto,
        setup_scripts: vec![],
        domain_id: None,
        rmw: None,
        extra_env: BTreeMap::new(),
        fixture: None,
        preferred_provider: None,
    })
    .unwrap();
    let mut params = BTreeMap::new();
    params.insert("runtime".to_owned(), runtime);
    for check in ["ros.environment", "ros.graph", "ros.tf", "ros.diagnostics"] {
        let result = run_check(
            &plugin,
            &CheckRequest {
                check_id: CheckId::from(check),
                device_id: DeviceId::from("local"),
                mode: Some(DiagnosticMode::Full),
                params: params.clone(),
                timeout_ms: 30_000,
            },
        );
        assert_eq!(
            result.status,
            CheckStatus::Unavailable,
            "{check} must be UNAVAILABLE without ROS"
        );
        assert!(result
            .error
            .as_ref()
            .unwrap()
            .message
            .contains("ROS_NOT_INSTALLED"));
    }
}

#[test]
fn manifest_is_bootstrap_only() {
    let manifest_path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("plugin.yaml");
    let manifest: doctor_domain::PluginManifest =
        serde_yaml::from_str(&std::fs::read_to_string(manifest_path).unwrap()).unwrap();
    assert!(manifest.checks.is_empty());
}
