//! Measurement harness for Phase C2 (§59, §60).
//!
//! Not a benchmark suite and not an optimization exercise: it exists so
//! there is data before anyone sets a target. Each operation runs cold
//! once, then warm five times, and the cold time, median and p95 are
//! reported.
//!
//! Run with: `cargo run -p doctor-profile --bin measure --release`
//!
//! The one property this asserts rather than merely reports is §59's real
//! requirement: evaluating a persisted run must not need a plugin. That
//! holds by construction here — the whole harness runs on in-memory
//! projections with no engine, no process and no I/O.

use doctor_domain::baseline::{BaselineSource, RunProjection};
use doctor_domain::comparison::{
    AttributeValue, ComparisonEntity, EntityKey, NamespaceAvailability,
};
use doctor_domain::profile::{
    Constraint, Expectation, Profile, ProfileStatus, Requirement, Selector, PROFILE_SCHEMA_VERSION,
};
use doctor_domain::{BaselineId, CheckId, DeviceId, PluginId, ProfileId, RunId};
use doctor_profile::{capture, diff, draft, evaluate, to_yaml, CaptureInput, EvaluationContext};
use std::collections::BTreeMap;
use std::time::{Duration, Instant};

const WARM_RUNS: usize = 5;

struct Measurement {
    label: String,
    scale: String,
    cold: Duration,
    median: Duration,
    p95: Duration,
}

fn measure(label: &str, scale: &str, mut op: impl FnMut()) -> Measurement {
    let start = Instant::now();
    op();
    let cold = start.elapsed();

    let mut warm: Vec<Duration> = (0..WARM_RUNS)
        .map(|_| {
            let start = Instant::now();
            op();
            start.elapsed()
        })
        .collect();
    warm.sort();

    Measurement {
        label: label.to_owned(),
        scale: scale.to_owned(),
        cold,
        median: warm[warm.len() / 2],
        // With five samples p95 is the slowest; stated plainly rather
        // than dressed up as a percentile estimate it cannot support.
        p95: warm[warm.len() - 1],
    }
}

fn ms(duration: Duration) -> f64 {
    duration.as_secs_f64() * 1000.0
}

// ── synthetic data shaped like a real ROS graph ────────────────────────

fn entity(kind: &str, key: &str, index: usize) -> ComparisonEntity {
    let plugin = PluginId::from("ros2");
    let check = CheckId::from("ros.graph");
    ComparisonEntity::new(EntityKey::new("ros", kind, key), key, &plugin, &check)
        .with("index", AttributeValue::Number(index as f64))
        .with("name", AttributeValue::text(key))
        .with(
            "peers",
            AttributeValue::set([format!("/node_{index}"), format!("/node_{}", index + 1)]),
        )
        .with(
            "observed_hz",
            AttributeValue::Number(10.0 + (index % 7) as f64),
        )
}

/// A projection roughly shaped like a mid-sized robot: nodes, topics,
/// sampled rates and a TF chain.
fn projection(size: usize) -> RunProjection {
    let mut entities = Vec::with_capacity(size * 3 + 1);
    for i in 0..size {
        entities.push(entity("node", &format!("/node_{i}"), i));
        entities.push(entity(
            "topic",
            &format!("/topic_{i}|std_msgs/msg/String"),
            i,
        ));
        entities.push(entity("topic_rate", &format!("/topic_{i}"), i));
    }
    // A TF chain of the same depth, so path traversal is measured on a
    // realistic worst case rather than a two-edge toy.
    for i in 0..size {
        let from = if i == 0 {
            "map".to_owned()
        } else {
            format!("frame_{}", i - 1)
        };
        let to = format!("frame_{i}");
        entities.push(
            ComparisonEntity::new(
                EntityKey::new("ros", "tf_edge", format!("{from}->{to}")),
                format!("{from}->{to}"),
                &PluginId::from("ros2"),
                &CheckId::from("ros.tf"),
            )
            .with("from", AttributeValue::text(from))
            .with("to", AttributeValue::text(to)),
        );
    }

    let mut kinds = BTreeMap::new();
    for kind in ["node", "topic", "topic_rate", "tf_edge"] {
        kinds.insert(
            RunProjection::kind_key("ros", kind),
            NamespaceAvailability::Observed,
        );
    }
    RunProjection {
        run_id: RunId::from("perf-run"),
        device_id: DeviceId::from("local"),
        entities,
        namespaces: BTreeMap::from([("ros".to_owned(), NamespaceAvailability::Observed)]),
        kinds,
        ..Default::default()
    }
}

/// A profile with `count` expectations spread across every operator, so
/// the measurement is not dominated by one cheap code path.
fn profile(count: usize) -> Profile {
    let now = chrono::Utc::now();
    let expectations = (0..count)
        .map(|i| {
            let (kind, constraint, selector) = match i % 5 {
                0 => (
                    "node",
                    Constraint::Exists,
                    Selector {
                        key: Some(format!("/node_{i}")),
                        key_prefix: None,
                    },
                ),
                1 => (
                    "topic_rate",
                    Constraint::Min {
                        field: "observed_hz".into(),
                        value: 8.0,
                    },
                    Selector {
                        key: Some(format!("/topic_{i}")),
                        key_prefix: None,
                    },
                ),
                2 => (
                    "topic",
                    Constraint::Contains {
                        field: "name".into(),
                        value: "topic".into(),
                    },
                    Selector {
                        key: Some(format!("/topic_{i}|std_msgs/msg/String")),
                        key_prefix: None,
                    },
                ),
                3 => (
                    "node",
                    Constraint::CountMin { value: 1 },
                    Selector::default(),
                ),
                _ => (
                    "tf_edge",
                    Constraint::RelationshipExists {
                        from: "map".into(),
                        to: format!("frame_{i}"),
                        mode: doctor_domain::profile::RelationshipMode::Path,
                    },
                    Selector::default(),
                ),
            };
            Expectation {
                id: format!("expectation-{i:05}"),
                description: format!("synthetic expectation {i}"),
                namespace: "ros".into(),
                kind: kind.into(),
                selector,
                requirement: if i % 3 == 0 {
                    Requirement::Optional
                } else {
                    Requirement::Required
                },
                constraint,
                from_baseline: false,
            }
        })
        .collect();
    Profile {
        schema_version: PROFILE_SCHEMA_VERSION,
        id: ProfileId::from("perf-profile"),
        name: "performance profile".into(),
        description: String::new(),
        revision: 1,
        status: ProfileStatus::Active,
        created_at: now,
        updated_at: now,
        tags: vec![],
        expectations,
    }
}

fn capture_input(run: &str, projection: RunProjection) -> CaptureInput {
    CaptureInput {
        source: BaselineSource {
            run_id: RunId::from(run),
            run_started_at: chrono::Utc::now(),
            mode: "FULL".into(),
            overall_health: Some("HEALTHY".into()),
            manually_accepted: false,
        },
        projection,
    }
}

fn main() {
    let mut results = Vec::new();

    // Baseline capture, load and diff at realistic ROS graph sizes.
    // 300 → ~1200 entities, which is a large real robot.
    for size in [10usize, 100, 300] {
        let entities = size * 4;
        let scale = format!("{entities} entities");
        let single = projection(size);

        results.push(measure("baseline capture (1 run)", &scale, || {
            let baseline = capture(
                BaselineId::from("perf"),
                DeviceId::from("local"),
                "perf".into(),
                String::new(),
                "0.1.0".into(),
                BTreeMap::new(),
                vec![capture_input("r1", single.clone())],
            );
            std::hint::black_box(baseline);
        }));

        results.push(measure("baseline capture (5 runs)", &scale, || {
            let inputs = (0..5)
                .map(|i| capture_input(&format!("r{i}"), single.clone()))
                .collect();
            let baseline = capture(
                BaselineId::from("perf"),
                DeviceId::from("local"),
                "perf".into(),
                String::new(),
                "0.1.0".into(),
                BTreeMap::new(),
                inputs,
            );
            std::hint::black_box(baseline);
        }));

        let baseline = capture(
            BaselineId::from("perf"),
            DeviceId::from("local"),
            "perf".into(),
            String::new(),
            "0.1.0".into(),
            BTreeMap::new(),
            vec![capture_input("r1", single.clone())],
        );

        // "Load" here is deserialization from the stored JSON form, which
        // is what reading a baseline out of SQLite actually costs.
        let encoded = serde_json::to_string(&baseline).expect("baseline serializes");
        results.push(measure("baseline load (decode)", &scale, || {
            let decoded: doctor_domain::baseline::Baseline =
                serde_json::from_str(&encoded).expect("decodes");
            std::hint::black_box(decoded);
        }));

        results.push(measure("semantic diff", &scale, || {
            std::hint::black_box(diff(&baseline, &single));
        }));

        results.push(measure("profile draft", &scale, || {
            std::hint::black_box(draft(&baseline));
        }));
    }

    // Profile parsing and evaluation at the sizes the spec names.
    let run = projection(300);
    let context = EvaluationContext {
        app_version: "perf".into(),
        ..Default::default()
    };
    for count in [10usize, 100, 1000] {
        let scale = format!("{count} expectations");
        let profile = profile(count);
        let yaml = to_yaml(&profile);

        results.push(measure("profile parse (YAML)", &scale, || {
            let parsed = doctor_profile::parse_yaml(&yaml).expect("valid");
            std::hint::black_box(parsed);
        }));

        results.push(measure("profile validate", &scale, || {
            doctor_profile::validate(&profile).expect("valid");
        }));

        results.push(measure("profile evaluation", &scale, || {
            std::hint::black_box(evaluate(&profile, &run, &context));
        }));
    }

    // The payloads the desktop has to render (§60).
    let ui_run = projection(250); // 1000 entities
    let ui_profile = profile(1000);
    let ui_baseline = capture(
        BaselineId::from("perf"),
        DeviceId::from("local"),
        "perf".into(),
        String::new(),
        "0.1.0".into(),
        BTreeMap::new(),
        vec![capture_input("r1", ui_run.clone())],
    );
    results.push(measure(
        "UI payload: diff → JSON",
        "1000 entities",
        || {
            let payload = serde_json::to_string(&diff(&ui_baseline, &ui_run)).expect("serializes");
            std::hint::black_box(payload);
        },
    ));
    results.push(measure(
        "UI payload: evaluation → JSON",
        "1000 expectations",
        || {
            let evaluation = evaluate(&ui_profile, &ui_run, &context);
            let payload = serde_json::to_string(&evaluation).expect("serializes");
            std::hint::black_box(payload);
        },
    ));

    println!(
        "\nPhase C2 measurements — cold once, warm x{WARM_RUNS} (all times ms)\n\
         {:<32} {:<20} {:>9} {:>9} {:>9}",
        "operation", "scale", "cold", "median", "p95"
    );
    println!("{}", "-".repeat(83));
    for m in &results {
        println!(
            "{:<32} {:<20} {:>9.3} {:>9.3} {:>9.3}",
            m.label,
            m.scale,
            ms(m.cold),
            ms(m.median),
            ms(m.p95)
        );
    }
    println!(
        "\nNo plugin process is launched anywhere above: every operation reads an\n\
         already-persisted projection, which is what §59 requires.\n"
    );
}
