//! Real ROS 2 integration smoke (Linux): launches actual ROS nodes and
//! verifies the RclpyProvider (plus CLI parity) against them through the
//! full engine → plugin → provider path. Covers C1 acceptance scenarios
//! B–G and reports cold/warm performance per the C1 methodology.
//!
//! Requirements: a ROS 2 installation. Configuration via env:
//!   RD_ROS_SETUP   — setup script (default /opt/ros/jazzy/setup.bash)
//!   RD_ROS_EXTRA_PATH — optional PATH prepend (e.g. python shim dir)
//!   ROBOT_DOCTOR_PYTHON — optional explicit interpreter
//!
//! Usage: cargo run -p integration-tests --bin smoke-ros -- [plugins_dir]

use doctor_domain::ros::{RosGraphSnapshot, RosRuntimeConfig, RosRuntimeMode};
use doctor_domain::{CheckResult, CheckStatus, DeviceId};
use plugin_ros2::bootstrap::{bootstrap_env, which_in, EnvMap};
use std::collections::BTreeMap;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Arc;
use std::time::Instant;

fn expect(cond: bool, what: &str) {
    if cond {
        println!("  OK   {what}");
    } else {
        // Panic (not exit) so Drop impls still kill spawned ROS nodes.
        panic!("SMOKE FAIL: {what}");
    }
}

fn expect_check(result: &CheckResult, what: &str) {
    if result.status != CheckStatus::Passed {
        let detail = result
            .error
            .as_ref()
            .map(|e| e.message.clone())
            .unwrap_or_default();
        panic!("SMOKE FAIL: {what} — status {:?}: {detail}", result.status);
    }
    println!("  OK   {what}");
}

fn runtime_config() -> RosRuntimeConfig {
    let setup =
        std::env::var("RD_ROS_SETUP").unwrap_or_else(|_| "/opt/ros/jazzy/setup.bash".into());
    let mut extra_env = BTreeMap::new();
    if let Ok(prepend) = std::env::var("RD_ROS_EXTRA_PATH") {
        extra_env.insert("PATH".to_owned(), format!("{prepend}:${{PATH}}"));
    }
    if let Ok(python) = std::env::var("ROBOT_DOCTOR_PYTHON") {
        extra_env.insert("ROBOT_DOCTOR_PYTHON".to_owned(), python);
    }
    RosRuntimeConfig {
        id: "smoke:ros".into(),
        name: "smoke runtime".into(),
        mode: RosRuntimeMode::Configured,
        setup_scripts: vec![setup],
        domain_id: Some(57), // isolated domain for deterministic testing
        rmw: None,
        extra_env,
        fixture: None,
        preferred_provider: None,
    }
}

struct TestNodes {
    children: Vec<Child>,
    env: EnvMap,
    ros2: String,
}

impl TestNodes {
    fn spawn(env: EnvMap) -> Self {
        let ros2 = which_in(&env, "ros2").expect("ros2 CLI in bootstrapped PATH");
        Self {
            children: Vec::new(),
            env,
            ros2,
        }
    }

    fn start(&mut self, args: &[&str]) -> usize {
        let mut cmd = Command::new(&self.ros2);
        cmd.args(args)
            .env_clear()
            .envs(&self.env)
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        // `ros2 run` wraps the real node process; put each launch in its
        // own process group so killing takes the whole tree down.
        #[cfg(unix)]
        std::os::unix::process::CommandExt::process_group(&mut cmd, 0);
        let child = cmd
            .spawn()
            .unwrap_or_else(|e| panic!("spawn ros2 {args:?}: {e}"));
        self.children.push(child);
        self.children.len() - 1
    }

    fn kill(&mut self, index: usize) {
        if let Some(child) = self.children.get_mut(index) {
            kill_tree(child);
        }
    }
}

fn kill_tree(child: &mut Child) {
    #[cfg(unix)]
    {
        // Negative pid = process group (set via process_group(0) at spawn).
        let _ = Command::new("kill")
            .args(["-KILL", "--", &format!("-{}", child.id())])
            .status();
    }
    let _ = child.kill();
    let _ = child.wait();
}

impl Drop for TestNodes {
    fn drop(&mut self) {
        for child in &mut self.children {
            kill_tree(child);
        }
    }
}

fn obs_json(result: &CheckResult, key: &str) -> serde_json::Value {
    result
        .observations
        .iter()
        .find(|o| o.key == key)
        .map(|o| serde_json::to_value(&o.value).expect("serializes"))
        .map(|v| v.get("value").cloned().unwrap_or(v))
        .unwrap_or_else(|| panic!("observation '{key}' missing"))
}

fn median(mut xs: Vec<f64>) -> f64 {
    xs.sort_by(f64::total_cmp);
    xs[xs.len() / 2]
}

fn p95(mut xs: Vec<f64>) -> f64 {
    xs.sort_by(f64::total_cmp);
    xs[((xs.len() as f64 - 1.0) * 0.95).round() as usize]
}

#[tokio::main]
async fn main() {
    let plugins_dir = std::env::args()
        .nth(1)
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("plugins"));
    let config = runtime_config();
    let runtime_json = serde_json::to_value(&config).unwrap();

    // ── SCENARIO B: bootstrap without a sourced parent shell ───────────
    println!("SCENARIO B: environment bootstrap from unsourced process");
    expect(
        std::env::var("ROS_DISTRO").is_err(),
        "smoke process itself has no ROS environment (unsourced)",
    );
    let env = bootstrap_env(&config).expect("bootstrap env");
    expect(
        env.contains_key("ROS_DISTRO"),
        "setup chain yields ROS_DISTRO",
    );
    println!("       distro: {}", env.get("ROS_DISTRO").unwrap());

    // Launch the real test graph on the isolated domain.
    let mut nodes = TestNodes::spawn(env.clone());
    nodes.start(&[
        "run",
        "demo_nodes_cpp",
        "talker",
        "--ros-args",
        "-r",
        "__node:=smoke_talker",
    ]);
    nodes.start(&[
        "run",
        "demo_nodes_py",
        "listener",
        "--ros-args",
        "-r",
        "__node:=smoke_listener",
    ]);
    let _slow = nodes.start(&[
        "topic",
        "pub",
        "-r",
        "2",
        "/slow_chatter",
        "std_msgs/msg/String",
        "{data: tick}",
    ]);
    nodes.start(&[
        "run",
        "tf2_ros",
        "static_transform_publisher",
        "--frame-id",
        "map",
        "--child-frame-id",
        "odom",
    ]);
    let odom_base = nodes.start(&[
        "run",
        "tf2_ros",
        "static_transform_publisher",
        "--frame-id",
        "odom",
        "--child-frame-id",
        "base_link",
    ]);
    // Diagnostics via a real rclpy node (`ros2 topic pub` cannot express
    // the byte-typed `level` field reliably).
    {
        let python = env
            .get("ROBOT_DOCTOR_PYTHON")
            .cloned()
            .or_else(|| which_in(&env, "python3"))
            .expect("python in runtime env");
        let script = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("ros_helpers/diag_pub.py");
        let mut cmd = Command::new(python);
        cmd.arg(script)
            .env_clear()
            .envs(&env)
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        #[cfg(unix)]
        std::os::unix::process::CommandExt::process_group(&mut cmd, 0);
        nodes.children.push(cmd.spawn().expect("spawn diag_pub"));
    }
    std::thread::sleep(std::time::Duration::from_secs(3)); // let the graph form

    // ── engine setup with persistence (SCENARIO G groundwork) ──────────
    let db_dir = std::env::temp_dir().join(format!("rd-ros-smoke-{}", std::process::id()));
    let db_path = db_dir.join("history.db");
    let storage = doctor_storage::Storage::open(&db_path)
        .await
        .expect("storage");
    let engine = Arc::new(doctor_core::Engine::with_storage(
        &plugins_dir,
        storage.clone(),
    ));

    let t_start = Instant::now();
    let env_result = engine
        .run_check_now(
            DeviceId::from("local"),
            "ros.environment",
            serde_json::json!({"runtime": runtime_json}),
        )
        .await
        .expect("ros.environment routed");
    let cold_env_ms = t_start.elapsed().as_millis();
    expect_check(
        &env_result,
        "ros.environment PASSED via bootstrapped runtime",
    );
    let info = obs_json(&env_result, "ros.env.info");
    expect(info["rclpy_available"] == true, "RclpyProvider started");
    expect(
        info["provider"] == "rclpy",
        "rclpy is the negotiated provider",
    );
    println!("PERF ros_plugin_cold_start_env_ms={cold_env_ms}");

    // ── SCENARIO C: talker/listener graph observation ──────────────────
    println!("\nSCENARIO C: talker/listener graph");
    let graph_result = engine
        .run_check_now(
            DeviceId::from("local"),
            "ros.graph",
            serde_json::json!({"runtime": runtime_json}),
        )
        .await
        .expect("ros.graph routed");
    expect_check(&graph_result, "ros.graph PASSED");
    let snapshot: RosGraphSnapshot =
        serde_json::from_value(obs_json(&graph_result, "ros.graph.snapshot")).unwrap();
    let names: Vec<&str> = snapshot
        .nodes
        .iter()
        .map(|n| n.full_name.as_str())
        .collect();
    expect(names.contains(&"/smoke_talker"), "talker node observed");
    expect(names.contains(&"/smoke_listener"), "listener node observed");
    let chatter = snapshot.topics.iter().find(|t| t.name == "/chatter");
    let chatter = chatter.expect("chatter topic observed");
    expect(
        chatter.types == vec!["std_msgs/msg/String"],
        "message type observed",
    );
    expect(chatter.publisher_count == 1, "one publisher");
    expect(chatter.subscriber_count == 1, "one subscriber");
    expect(
        chatter.publishers[0].qos.reliability != doctor_domain::ros::QosReliability::Unknown,
        "endpoint QoS captured",
    );
    println!("PERF dds_discovery_ms={}", snapshot.discovery_ms);

    // ── PROVIDER PARITY: CLI fallback sees the same fundamental facts ──
    println!("\nPROVIDER PARITY: rclpy vs cli");
    let mut cli_config = runtime_config();
    cli_config.id = "smoke:ros-cli".into();
    cli_config.preferred_provider = Some("cli".into());
    let cli_result = engine
        .run_check_now(
            DeviceId::from("local"),
            "ros.graph",
            serde_json::json!({"runtime": serde_json::to_value(&cli_config).unwrap()}),
        )
        .await
        .expect("cli graph routed");
    expect_check(&cli_result, "CLI provider graph PASSED");
    let cli_snapshot: RosGraphSnapshot =
        serde_json::from_value(obs_json(&cli_result, "ros.graph.snapshot")).unwrap();
    expect(cli_snapshot.provider == "cli", "cli provider was used");
    let cli_names: Vec<&str> = cli_snapshot
        .nodes
        .iter()
        .map(|n| n.full_name.as_str())
        .collect();
    expect(
        cli_names.contains(&"/smoke_talker"),
        "parity: node existence",
    );
    let cli_chatter = cli_snapshot.topics.iter().find(|t| t.name == "/chatter");
    let cli_chatter = cli_chatter.expect("parity: topic existence");
    expect(
        cli_chatter.types == chatter.types,
        "parity: topic types agree",
    );
    expect(
        cli_chatter.publisher_count == chatter.publisher_count,
        "parity: publisher count agrees",
    );
    if cli_snapshot.services.is_empty() != snapshot.services.is_empty() {
        println!(
            "  NOTE provider difference: service lists differ (cli={}, rclpy={})",
            cli_snapshot.services.len(),
            snapshot.services.len()
        );
    }
    println!(
        "  NOTE recorded differences: cli has no endpoint QoS/GIDs (documented partial provider)"
    );

    // ── SCENARIO D: controlled low-rate topic sampling ─────────────────
    println!("\nSCENARIO D: low-rate sampling (2 Hz publisher)");
    let sample_result = engine
        .run_check_now(
            DeviceId::from("local"),
            "ros.topic_rate",
            serde_json::json!({
                "runtime": runtime_json,
                "topics": ["/slow_chatter"],
                "duration_s": 3.0,
            }),
        )
        .await
        .expect("sampling routed");
    expect_check(&sample_result, "sampling PASSED");
    let hz = sample_result
        .observations
        .iter()
        .find(|o| o.key == "ros.topic_rate./slow_chatter.hz")
        .and_then(|o| o.as_number())
        .expect("hz observation");
    expect(
        (1.0..=3.5).contains(&hz),
        &format!("measured rate ≈ 2 Hz (got {hz:.2})"),
    );
    expect(
        sample_result.findings.is_empty(),
        "C1 does not call a slow topic unhealthy",
    );

    // ── SCENARIO E: TF available, then broken ──────────────────────────
    println!("\nSCENARIO E: TF map -> base_link");
    let tf_ok = engine
        .run_check_now(
            DeviceId::from("local"),
            "ros.tf",
            serde_json::json!({
                "runtime": runtime_json,
                "tf_queries": [{"source": "map", "target": "base_link", "timeout_s": 3.0}],
            }),
        )
        .await
        .expect("tf routed");
    let query = obs_json(&tf_ok, "ros.tf.query.map->base_link");
    expect(query["status"] == "AVAILABLE", "map -> base_link AVAILABLE");

    nodes.kill(odom_base);
    // Let DDS discovery unregister the dead writer before re-querying.
    std::thread::sleep(std::time::Duration::from_millis(1500));
    let tf_broken = engine
        .run_check_now(
            DeviceId::from("local"),
            "ros.tf",
            serde_json::json!({
                "runtime": runtime_json,
                "tf_queries": [{"source": "map", "target": "base_link", "timeout_s": 2.0}],
            }),
        )
        .await
        .expect("tf routed");
    let query = obs_json(&tf_broken, "ros.tf.query.map->base_link");
    expect(
        query["status"] == "UNAVAILABLE" || query["status"] == "TIMEOUT",
        &format!("map -> base_link now unavailable ({})", query["status"]),
    );
    expect(
        !tf_broken.evidence.is_empty(),
        "unavailability has evidence",
    );
    expect(
        tf_broken.findings.is_empty(),
        "C1 does not call a missing TF a robot fault",
    );

    // ── SCENARIO F: /diagnostics ingestion ─────────────────────────────
    println!("\nSCENARIO F: diagnostics ingestion");
    let diag_result = engine
        .run_check_now(
            DeviceId::from("local"),
            "ros.diagnostics",
            serde_json::json!({"runtime": runtime_json, "window_s": 2.5}),
        )
        .await
        .expect("diagnostics routed");
    expect_check(&diag_result, "diagnostics PASSED");
    let statuses = obs_json(&diag_result, "ros.diagnostics.statuses");
    let found = statuses
        .as_array()
        .map(|a| {
            a.iter()
                .any(|s| s["name"] == "smoke_component" && s["level"] == 1)
        })
        .unwrap_or(false);
    expect(
        found,
        "smoke_component WARN status with source level preserved",
    );
    expect(
        diag_result.findings.is_empty(),
        "no root-cause interpretation in C1",
    );

    // ── SCENARIO G: persistence across restart ─────────────────────────
    println!("\nSCENARIO G: snapshot persistence");
    let (_, mut rx) = engine
        .start_diagnosis_with_params(
            DeviceId::from("local"),
            doctor_domain::DiagnosticMode::Quick,
            BTreeMap::from([
                (
                    "ros.environment".to_owned(),
                    serde_json::json!({"runtime": runtime_json}),
                ),
                (
                    "ros.graph".to_owned(),
                    serde_json::json!({"runtime": runtime_json}),
                ),
                (
                    "ros.clock".to_owned(),
                    serde_json::json!({"runtime": runtime_json}),
                ),
                (
                    "ros.diagnostics".to_owned(),
                    serde_json::json!({"runtime": runtime_json}),
                ),
            ]),
        )
        .await;
    let mut run_id = None;
    while let Some(event) = rx.recv().await {
        if let doctor_core::DiagnosisEvent::RunCompleted {
            run_id: id,
            persisted,
            ..
        } = event
        {
            expect(persisted, "QUICK run with ROS observation persisted");
            run_id = Some(id);
            break;
        }
    }
    let run_id = run_id.expect("run completed");

    engine.shutdown().await;
    storage.close().await.unwrap();
    // Reopen: the historical run must contain the actual ROS snapshot —
    // never rebuilt from the (now different) live graph.
    let storage = doctor_storage::Storage::open(&db_path)
        .await
        .expect("reopen");
    let stored = storage
        .get_run(&run_id)
        .await
        .expect("query")
        .expect("historical run exists");
    let ros_graph = stored
        .run
        .results
        .iter()
        .find(|r| r.check_id.as_str() == "ros.graph")
        .expect("ros.graph result persisted");
    let persisted_snapshot: RosGraphSnapshot = serde_json::from_value(
        ros_graph
            .observations
            .iter()
            .find(|o| o.key == "ros.graph.snapshot")
            .map(|o| serde_json::to_value(&o.value).unwrap()["value"].clone())
            .expect("snapshot observation persisted"),
    )
    .unwrap();
    expect(
        persisted_snapshot
            .nodes
            .iter()
            .any(|n| n.full_name == "/smoke_talker"),
        "historical snapshot still shows the graph as observed",
    );
    expect(!ros_graph.evidence.is_empty(), "historical evidence intact");
    storage.close().await.ok();

    // ── PERFORMANCE (cold above; warm x5, median/p95) ──────────────────
    println!("\nPERFORMANCE: warm runs");
    let engine = Arc::new(doctor_core::Engine::new(&plugins_dir));
    let mut graph_ms = Vec::new();
    let mut clock_ms = Vec::new();
    for _ in 0..5 {
        let t = Instant::now();
        let _ = engine
            .run_check_now(
                DeviceId::from("local"),
                "ros.graph",
                serde_json::json!({"runtime": runtime_json, "discovery_s": 1.0}),
            )
            .await;
        graph_ms.push(t.elapsed().as_millis() as f64);
        let t = Instant::now();
        let _ = engine
            .run_check_now(
                DeviceId::from("local"),
                "ros.clock",
                serde_json::json!({"runtime": runtime_json, "window_s": 0.5}),
            )
            .await;
        clock_ms.push(t.elapsed().as_millis() as f64);
    }
    println!(
        "PERF ros_graph_warm_ms median={} p95={}",
        median(graph_ms.clone()),
        p95(graph_ms)
    );
    println!(
        "PERF ros_clock_warm_ms median={} p95={}",
        median(clock_ms.clone()),
        p95(clock_ms)
    );
    engine.shutdown().await;
    std::fs::remove_dir_all(&db_dir).ok();

    println!(
        "\nROS SMOKE PASSED (distro: {})",
        env.get("ROS_DISTRO").unwrap()
    );
}
