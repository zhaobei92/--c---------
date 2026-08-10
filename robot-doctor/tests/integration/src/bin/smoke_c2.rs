//! Real ROS 2 acceptance scenarios for Phase C2 (§52–§57), Linux.
//!
//! Runs against actual ROS nodes through the full engine → plugin →
//! provider → SQLite path. Nothing is stubbed: the baselines compared
//! here were captured from real diagnostic runs of a real ROS graph, and
//! the expectations are evaluated from what those runs persisted.
//!
//! Requirements: a ROS 2 installation. Configuration via env:
//!   RD_ROS_SETUP        — setup script (default /opt/ros/jazzy/setup.bash)
//!   RD_ROS_EXTRA_PATH   — optional PATH prepend (e.g. python shim dir)
//!   ROBOT_DOCTOR_PYTHON — optional explicit interpreter
//!
//! Usage: cargo run -p integration-tests --bin smoke-c2 -- [plugins_dir]

use doctor_core::{c2, DiagnosisEvent, Engine};
use doctor_domain::baseline::EntityDiffState;
use doctor_domain::evaluation::{EvaluationRun, ExpectationStatus};
use doctor_domain::profile::{
    Constraint, Expectation, Profile, ProfileStatus, RelationshipMode, Requirement, Selector,
    PROFILE_SCHEMA_VERSION,
};
use doctor_domain::ros::{RosRuntimeConfig, RosRuntimeMode};
use doctor_domain::{DeviceId, DiagnosticMode, ProfileId, RunId};
use plugin_ros2::bootstrap::{bootstrap_env, which_in, EnvMap};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Arc;
use std::time::Duration;

// ── harness ────────────────────────────────────────────────────────────

fn expect(cond: bool, what: &str) {
    if cond {
        println!("  OK   {what}");
    } else {
        // Panic (not exit) so Drop impls still kill spawned ROS nodes.
        panic!("SMOKE FAIL: {what}");
    }
}

fn expect_status(
    evaluation: &EvaluationRun,
    id: &str,
    wanted: ExpectationStatus,
    what: &str,
) -> ExpectationStatus {
    let result = evaluation
        .results
        .iter()
        .find(|r| r.expectation_id == id)
        .unwrap_or_else(|| panic!("SMOKE FAIL: no result for expectation '{id}'"));
    if result.status != wanted {
        panic!(
            "SMOKE FAIL: {what} — {id} is {:?}, expected {wanted:?} (reason: {})",
            result.status, result.reason
        );
    }
    println!("  OK   {what} ({id} = {:?})", result.status);
    if result.status != ExpectationStatus::Satisfied {
        println!("       reason: {}", result.reason);
    }
    result.status
}

/// C2 states outcomes; it must never produce a diagnosis.
fn expect_no_diagnosis(evaluation: &EvaluationRun, what: &str) {
    let json = serde_json::to_string(evaluation)
        .expect("serializes")
        .to_lowercase();
    for forbidden in ["finding", "root_cause", "severity", "score", "remediation"] {
        if json.contains(forbidden) {
            panic!("SMOKE FAIL: {what} — evaluation leaked '{forbidden}'");
        }
    }
    println!("  OK   {what}");
}

fn runtime_config(domain: u32, fixture_free: bool) -> RosRuntimeConfig {
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
        id: "smoke:c2".into(),
        // Scenario 5 needs a runtime that genuinely cannot initialize.
        setup_scripts: if fixture_free {
            vec![setup]
        } else {
            vec!["/nonexistent/ros/setup.bash".to_owned()]
        },
        name: "c2 smoke runtime".into(),
        mode: RosRuntimeMode::Configured,
        domain_id: Some(domain),
        rmw: None,
        extra_env,
        fixture: None,
        preferred_provider: None,
    }
}

struct TestNodes {
    children: Vec<Option<Child>>,
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
        // `ros2 run` wraps the real node; its own process group makes the
        // kill take down the whole tree rather than orphaning the node.
        #[cfg(unix)]
        std::os::unix::process::CommandExt::process_group(&mut cmd, 0);
        let child = cmd
            .spawn()
            .unwrap_or_else(|e| panic!("spawn ros2 {args:?}: {e}"));
        self.children.push(Some(child));
        self.children.len() - 1
    }

    fn kill(&mut self, index: usize) {
        if let Some(slot) = self.children.get_mut(index) {
            if let Some(child) = slot.as_mut() {
                kill_tree(child);
            }
            *slot = None;
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
        for slot in &mut self.children {
            if let Some(child) = slot.as_mut() {
                kill_tree(child);
            }
        }
    }
}

fn settle(seconds: f64) {
    std::thread::sleep(Duration::from_secs_f64(seconds));
}

// ── engine plumbing ────────────────────────────────────────────────────

fn ros_params(config: &RosRuntimeConfig, topics: &[&str]) -> BTreeMap<String, serde_json::Value> {
    let runtime = serde_json::to_value(config).expect("runtime serializes");
    let mut params = BTreeMap::new();
    for check in [
        "ros.environment",
        "ros.graph",
        "ros.clock",
        "ros.diagnostics",
        "ros.qos",
        "ros.tf",
        "ros.lifecycle",
    ] {
        params.insert(check.to_owned(), serde_json::json!({ "runtime": runtime }));
    }
    // Sampling is always explicit — never an automatic scan of every topic.
    for check in ["ros.topic_rate", "ros.topic_age"] {
        params.insert(
            check.to_owned(),
            serde_json::json!({ "runtime": runtime, "topics": topics, "duration_s": 4.0 }),
        );
    }
    params
}

async fn run_full(
    engine: &Arc<Engine>,
    config: &RosRuntimeConfig,
    topics: &[&str],
) -> (RunId, Option<EvaluationRun>) {
    let (run_id, mut rx) = engine
        .start_diagnosis_with_params(
            DeviceId::from("local"),
            DiagnosticMode::Full,
            ros_params(config, topics),
        )
        .await;
    let mut evaluated = false;
    while let Some(event) = rx.recv().await {
        if matches!(event, DiagnosisEvent::EvaluationCompleted { .. }) {
            evaluated = true;
        }
    }
    let evaluation = if evaluated {
        c2::evaluation_for_run(engine, run_id.clone())
            .await
            .expect("evaluation readable")
    } else {
        None
    };
    (run_id, evaluation)
}

async fn open_engine(plugins_dir: &Path, db_path: &Path) -> Arc<Engine> {
    let storage = doctor_storage::Storage::open(db_path)
        .await
        .expect("storage opens");
    Arc::new(Engine::with_storage(plugins_dir, storage))
}

fn expectation(
    id: &str,
    kind: &str,
    selector: Selector,
    constraint: Constraint,
    requirement: Requirement,
) -> Expectation {
    Expectation {
        id: id.into(),
        description: format!("ros.{kind} {id}"),
        namespace: "ros".into(),
        kind: kind.into(),
        selector,
        requirement,
        constraint,
        from_baseline: false,
    }
}

fn key(k: &str) -> Selector {
    Selector {
        key: Some(k.to_owned()),
        key_prefix: None,
    }
}

fn profile(id: &str, expectations: Vec<Expectation>) -> Profile {
    let now = chrono::Utc::now();
    Profile {
        schema_version: PROFILE_SCHEMA_VERSION,
        id: ProfileId::from(id),
        name: format!("{id} profile"),
        description: "real ROS acceptance profile".into(),
        revision: 0,
        status: ProfileStatus::Draft,
        created_at: now,
        updated_at: now,
        tags: vec!["smoke".into()],
        expectations,
    }
}

async fn activate(engine: &Arc<Engine>, profile: Profile) -> Profile {
    let saved = c2::save_profile(engine, profile)
        .await
        .unwrap_or_else(|e| panic!("SMOKE FAIL: saving profile: {e}"));
    c2::assign_profile(
        engine,
        DeviceId::from("local"),
        saved.id.clone(),
        saved.revision,
        Some("smoke:c2".into()),
    )
    .await
    .unwrap_or_else(|e| panic!("SMOKE FAIL: activating profile: {e}"));
    saved
}

#[tokio::main(flavor = "multi_thread")]
async fn main() {
    let plugins_dir = std::env::args()
        .nth(1)
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("plugins"));
    let config = runtime_config(58, true);

    expect(
        std::env::var("ROS_DISTRO").is_err(),
        "smoke process itself has no ROS environment (unsourced)",
    );
    let env = bootstrap_env(&config).expect("bootstrap env");
    let distro = env
        .get("ROS_DISTRO")
        .cloned()
        .unwrap_or_else(|| "unknown".into());
    println!("distro: {distro}\n");

    // ── the known-good graph ───────────────────────────────────────────
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
    // /scan at ~10 Hz is the known-good rate for scenario 3.
    let scan_fast = nodes.start(&[
        "topic",
        "pub",
        "-r",
        "10",
        "/scan",
        "std_msgs/msg/String",
        "{data: scan}",
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
        nodes
            .children
            .push(Some(cmd.spawn().expect("spawn diag_pub")));
    }
    settle(3.0);

    let db_dir = std::env::temp_dir().join(format!("rd-c2-smoke-{}", std::process::id()));
    std::fs::create_dir_all(&db_dir).expect("temp dir");
    let db_path = db_dir.join("history.db");

    // ── SCENARIO 1: healthy baseline, surviving a restart (§52) ────────
    println!("\nSCENARIO 1: capture a known-good baseline and reopen it after restart");
    let engine = open_engine(&plugins_dir, &db_path).await;
    let (run_1, _) = run_full(&engine, &config, &["/scan"]).await;

    if let Some(run) = engine.store().get_run(&run_1).await {
        for result in &run.results {
            println!(
                "       {:<18} {:?}{}",
                result.check_id.as_str(),
                result.status,
                result
                    .error
                    .as_ref()
                    .map(|e| format!(" — {}", e.message))
                    .unwrap_or_default()
            );
        }
    }
    let projection = c2::run_projection(&engine, &run_1)
        .await
        .expect("run projected");
    let node_keys: Vec<String> = projection
        .entities
        .iter()
        .filter(|e| e.key.kind == "node")
        .map(|e| e.key.key.clone())
        .collect();
    expect(
        node_keys.iter().any(|k| k.contains("smoke_talker")),
        "the run observed /smoke_talker",
    );
    expect(
        projection.entities.iter().any(|e| e.key.kind == "tf_edge"),
        "the run observed TF edges",
    );

    let baseline_1 = c2::capture_baseline(
        &engine,
        "known good".into(),
        "scenario 1".into(),
        vec![run_1.clone()],
        false,
    )
    .await
    .expect("baseline captured");
    let entity_count = baseline_1.entities.len();
    println!("       baseline entities: {entity_count}");
    expect(entity_count > 0, "baseline captured a semantic projection");

    // "Stop Robot Doctor": shut the engine and storage down completely,
    // then start a fresh process-equivalent from the same database.
    engine.shutdown().await;
    drop(engine);
    settle(0.5);

    let engine = open_engine(&plugins_dir, &db_path).await;
    let reloaded = c2::get_baseline(&engine, &baseline_1.id)
        .await
        .expect("baseline reloaded after restart");
    if reloaded != baseline_1 {
        // Show the first structural difference rather than just failing.
        let a = serde_json::to_value(&baseline_1).unwrap();
        let b = serde_json::to_value(&reloaded).unwrap();
        for (key, left) in a.as_object().unwrap() {
            let right = b.get(key);
            if Some(left) != right {
                println!("       DIFFERS at '{key}':\n         captured: {left}\n         reloaded: {right:?}");
            }
        }
        panic!("SMOKE FAIL: baseline is byte-identical after restart");
    }
    println!("  OK   baseline is byte-identical after restart");
    expect(
        reloaded.entities.len() == entity_count,
        "no live ROS query was needed to reconstruct it",
    );

    // ── SCENARIO 2: a removed node (§53) ───────────────────────────────
    println!("\nSCENARIO 2: stop the talker and diff against the known-good baseline");
    let talker_profile = profile(
        "talker-required",
        vec![expectation(
            "talker-present",
            "node",
            key("/smoke_talker"),
            Constraint::Exists,
            Requirement::Required,
        )],
    );
    activate(&engine, talker_profile).await;

    nodes.kill(0); // stop /smoke_talker
    settle(2.0);

    let (run_2, evaluation_2) = run_full(&engine, &config, &["/scan"]).await;
    let diff_2 = c2::diff_run(&engine, &baseline_1.id, &run_2)
        .await
        .expect("diff computed");
    let removed: Vec<String> = diff_2
        .in_state(EntityDiffState::Removed)
        .iter()
        .map(|e| e.key.canonical())
        .collect();
    println!("       removed: {removed:?}");
    expect(
        removed.iter().any(|k| k.contains("smoke_talker")),
        "baseline diff reports REMOVED ros.node:/smoke_talker",
    );

    let evaluation_2 = evaluation_2.expect("profile evaluated");
    expect_status(
        &evaluation_2,
        "talker-present",
        ExpectationStatus::Unsatisfied,
        "an explicitly required, observably absent node is UNSATISFIED",
    );
    expect_no_diagnosis(&evaluation_2, "no Finding and no RootCause were produced");

    // ── SCENARIO 3: topic rate (§54) ───────────────────────────────────
    println!("\nSCENARIO 3: a baseline set around 10 Hz, then publish at 2 Hz");
    // Two more known-good runs at ~10 Hz make a real multi-run baseline set.
    let (rate_run_a, _) = run_full(&engine, &config, &["/scan"]).await;
    let (rate_run_b, _) = run_full(&engine, &config, &["/scan"]).await;
    let baseline_set = c2::capture_baseline(
        &engine,
        "scan rate known good".into(),
        "scenario 3".into(),
        vec![run_1.clone(), rate_run_a, rate_run_b],
        true, // earlier runs are missing the talker; capture is the user's call
    )
    .await
    .expect("baseline set captured");
    expect(
        baseline_set.source_count() == 3,
        "baseline set aggregates three known-good runs",
    );
    let baseline_hz = baseline_set
        .entities
        .iter()
        .find(|e| e.key.kind == "topic_rate" && e.key.key == "/scan")
        .and_then(|e| e.numeric_summaries.get("observed_hz").cloned())
        .expect("baseline recorded a /scan rate");
    println!(
        "       baseline /scan hz: min {:.2} max {:.2} median {:.2} over {} samples",
        baseline_hz.min, baseline_hz.max, baseline_hz.median, baseline_hz.samples
    );
    expect(
        baseline_hz.samples >= 2,
        "the rate summary aggregates several known-good samples",
    );

    // The threshold is stated by the user, never derived silently.
    activate(
        &engine,
        profile(
            "scan-rate",
            vec![expectation(
                "scan-min-hz",
                "topic_rate",
                key("/scan"),
                Constraint::Min {
                    field: "observed_hz".into(),
                    value: 8.0,
                },
                Requirement::Required,
            )],
        ),
    )
    .await;

    nodes.kill(scan_fast);
    settle(1.0);
    let scan_slow = nodes.start(&[
        "topic",
        "pub",
        "-r",
        "2",
        "/scan",
        "std_msgs/msg/String",
        "{data: scan}",
    ]);
    settle(2.0);

    let (run_3, evaluation_3) = run_full(&engine, &config, &["/scan"]).await;
    let evaluation_3 = evaluation_3.expect("profile evaluated");
    expect_status(
        &evaluation_3,
        "scan-min-hz",
        ExpectationStatus::Unsatisfied,
        "2 Hz against a stated minimum of 8 Hz is UNSATISFIED",
    );
    let actual = evaluation_3
        .results
        .iter()
        .find(|r| r.expectation_id == "scan-min-hz")
        .and_then(|r| r.actual.as_ref())
        .and_then(|v| v.as_number())
        .expect("an observed rate was recorded");
    println!("       observed /scan hz: {actual:.2}");
    expect(
        (0.5..4.0).contains(&actual),
        "the recorded actual value is the measured ~2 Hz",
    );

    let diff_3 = c2::diff_run(&engine, &baseline_set.id, &run_3)
        .await
        .expect("diff computed");
    let rate_change = diff_3
        .entities
        .iter()
        .find(|e| e.key.kind == "topic_rate" && e.key.key == "/scan")
        .expect("/scan appears in the diff");
    expect(
        rate_change.state == EntityDiffState::Changed,
        "baseline diff reports the rate as a numeric CHANGE",
    );
    expect_no_diagnosis(&evaluation_3, "still no Finding for a low topic rate");

    // ── SCENARIO 4: TF path (§55) ──────────────────────────────────────
    println!("\nSCENARIO 4: TF path map -> base_link, then break the chain");
    activate(
        &engine,
        profile(
            "tf-path",
            vec![
                expectation(
                    "map-to-base",
                    "tf_edge",
                    Selector::default(),
                    Constraint::RelationshipExists {
                        from: "map".into(),
                        to: "base_link".into(),
                        mode: RelationshipMode::Path,
                    },
                    Requirement::Required,
                ),
                expectation(
                    "map-to-base-direct",
                    "tf_edge",
                    Selector::default(),
                    Constraint::RelationshipExists {
                        from: "map".into(),
                        to: "base_link".into(),
                        mode: RelationshipMode::DirectEdge,
                    },
                    Requirement::Optional,
                ),
            ],
        ),
    )
    .await;

    let (_, evaluation_4a) = run_full(&engine, &config, &["/scan"]).await;
    let evaluation_4a = evaluation_4a.expect("profile evaluated");
    expect_status(
        &evaluation_4a,
        "map-to-base",
        ExpectationStatus::Satisfied,
        "a PATH through map -> odom -> base_link is SATISFIED",
    );
    expect_status(
        &evaluation_4a,
        "map-to-base-direct",
        ExpectationStatus::NotApplicable,
        "the same pair as a DIRECT_EDGE is not satisfied by a path",
    );

    nodes.kill(odom_base);
    settle(2.0);

    let (_, evaluation_4b) = run_full(&engine, &config, &["/scan"]).await;
    let evaluation_4b = evaluation_4b.expect("profile evaluated");
    expect_status(
        &evaluation_4b,
        "map-to-base",
        ExpectationStatus::Unsatisfied,
        "removing odom -> base_link makes the PATH UNSATISFIED",
    );
    expect_no_diagnosis(
        &evaluation_4b,
        "no causal interpretation of the broken chain",
    );

    // ── SCENARIO 5: UNKNOWN, not UNSATISFIED (§56) ─────────────────────
    println!("\nSCENARIO 5: ROS runtime unavailable — the hard acceptance requirement");
    activate(
        &engine,
        profile(
            "scan-required",
            vec![expectation(
                "scan-topic",
                "topic",
                key("/scan|std_msgs/msg/String"),
                Constraint::Exists,
                Requirement::Required,
            )],
        ),
    )
    .await;

    // A runtime whose setup chain does not exist: the plugin cannot
    // observe anything, which is different from observing nothing.
    let broken = runtime_config(58, false);
    let (run_5, evaluation_5) = run_full(&engine, &broken, &["/scan"]).await;
    let projection_5 = c2::run_projection(&engine, &run_5)
        .await
        .expect("run projected");
    println!(
        "       ros availability: {:?} (reason: {:?})",
        projection_5.availability("ros"),
        projection_5.namespace_reasons.get("ros")
    );
    expect(
        projection_5.availability("ros")
            == doctor_domain::comparison::NamespaceAvailability::NotObserved,
        "the ROS namespace is recorded as NOT_OBSERVED, not as empty",
    );

    let evaluation_5 = evaluation_5.expect("profile evaluated");
    expect_status(
        &evaluation_5,
        "scan-topic",
        ExpectationStatus::Unknown,
        "a required topic is UNKNOWN when ROS could not be observed at all",
    );
    expect_no_diagnosis(
        &evaluation_5,
        "an unobservable runtime produces no diagnosis",
    );

    // ── SCENARIO 6: historical revisions (§57) ─────────────────────────
    println!("\nSCENARIO 6: a historical evaluation keeps the revision that produced it");
    nodes.kill(scan_slow);
    settle(0.5);
    let scan_again = nodes.start(&[
        "topic",
        "pub",
        "-r",
        "10",
        "/scan",
        "std_msgs/msg/String",
        "{data: scan}",
    ]);
    settle(2.0);

    let rev1 = activate(
        &engine,
        profile(
            "revisioned",
            vec![expectation(
                "scan-min-hz",
                "topic_rate",
                key("/scan"),
                Constraint::Min {
                    field: "observed_hz".into(),
                    value: 8.0,
                },
                Requirement::Required,
            )],
        ),
    )
    .await;
    expect(rev1.revision == 1, "the profile starts at revision 1");

    let (run_6, evaluation_6) = run_full(&engine, &config, &["/scan"]).await;
    let evaluation_6 = evaluation_6.expect("profile evaluated");
    expect(
        evaluation_6.profile_revision == 1,
        "the run was evaluated under revision 1",
    );
    let original_status = expect_status(
        &evaluation_6,
        "scan-min-hz",
        ExpectationStatus::Satisfied,
        "10 Hz satisfies the revision 1 minimum of 8 Hz",
    );

    // Revision 2 raises the bar past the observed rate, so the two
    // revisions genuinely disagree about this exact run.
    let rev2 = activate(
        &engine,
        profile(
            "revisioned",
            vec![expectation(
                "scan-min-hz",
                "topic_rate",
                key("/scan"),
                Constraint::Min {
                    field: "observed_hz".into(),
                    value: 9.0,
                },
                Requirement::Required,
            )],
        ),
    )
    .await;
    expect(rev2.revision == 2, "saving allocates revision 2");

    let historical = c2::evaluation_for_run(&engine, run_6.clone())
        .await
        .expect("evaluation readable")
        .expect("historical evaluation still present");
    expect(
        historical.profile_revision == 1,
        "the historical evaluation still reports revision 1",
    );
    expect(
        historical.id == evaluation_6.id && historical.results == evaluation_6.results,
        "its original result was not recalculated under revision 2",
    );
    expect(
        historical
            .results
            .iter()
            .all(|r| r.status == original_status),
        "the stored verdict is unchanged",
    );
    expect(
        historical.results[0].expected.contains('8'),
        "and it still shows the threshold revision 1 actually used",
    );

    nodes.kill(scan_again);
    engine.shutdown().await;
    drop(nodes);
    let _ = std::fs::remove_dir_all(&db_dir);

    println!("\nC2 ROS SMOKE PASSED (distro: {distro})");
    println!(
        "  baselines captured: 2 (1 single-run, 1 three-run set) · profile revisions: 2 \
         · scenarios: 6"
    );
}
