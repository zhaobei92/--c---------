//! Data-driven Phase C2 fixtures.
//!
//! Each file in `fixtures/` is a self-contained case: inputs plus the
//! outcome they must produce. They are deterministic and plugin-free, so
//! they run identically on Linux and Windows and pin the semantics that
//! are easy to break by accident — order independence, UNKNOWN
//! propagation, and the difference between "absent" and "unobservable".
//!
//! Adding a case means adding a JSON file; the harness discovers it.

use doctor_domain::baseline::{
    Baseline, BaselineSource, EntityDiffState, EntityStability, RunProjection,
};
use doctor_domain::evaluation::ExpectationStatus;
use doctor_domain::profile::Profile;
use doctor_profile::{capture, diff, draft, evaluate, validate, CaptureInput, EvaluationContext};
use serde::Deserialize;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

#[derive(Debug, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
enum Fixture {
    BaselineDiff {
        description: String,
        baseline: Baseline,
        current: RunProjection,
        expect: DiffExpectation,
    },
    ProfileEvaluation {
        description: String,
        projection: RunProjection,
        profile: Profile,
        /// A second revision evaluated against the same run, to prove the
        /// revision is what decides the outcome.
        #[serde(default)]
        profile_next: Option<Profile>,
        expect: EvaluationExpectation,
    },
    BaselineCapture {
        description: String,
        sources: Vec<CaptureSource>,
        expect: CaptureExpectation,
    },
}

#[derive(Debug, Deserialize)]
struct CaptureSource {
    source: BaselineSource,
    projection: RunProjection,
}

#[derive(Debug, Deserialize)]
struct DiffExpectation {
    compatibility: String,
    /// canonical entity key → expected diff state
    states: BTreeMap<String, String>,
    /// canonical entity key → attribute → expected absolute delta
    #[serde(default)]
    attribute_deltas: BTreeMap<String, BTreeMap<String, f64>>,
}

#[derive(Debug, Deserialize)]
struct EvaluationExpectation {
    statuses: BTreeMap<String, String>,
    #[serde(default)]
    next_statuses: BTreeMap<String, String>,
    #[serde(default)]
    reason_contains: BTreeMap<String, String>,
    #[serde(default)]
    actual_numbers: BTreeMap<String, f64>,
}

#[derive(Debug, Deserialize)]
struct CaptureExpectation {
    /// canonical entity key → expected aggregation
    entities: BTreeMap<String, EntityExpectation>,
    /// canonical entity key → expected draft treatment
    #[serde(default)]
    draft: BTreeMap<String, DraftExpectation>,
    /// suggestion id → expected numeric bound
    #[serde(default)]
    suggested_thresholds: BTreeMap<String, f64>,
}

#[derive(Debug, Deserialize)]
struct EntityExpectation {
    stability: String,
    presence_count: u32,
    #[serde(default)]
    presence_ratio: Option<f64>,
}

#[derive(Debug, Deserialize)]
struct DraftExpectation {
    requirement: String,
    selected_by_default: bool,
}

fn fixtures_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures")
}

fn load_all() -> Vec<(String, Fixture)> {
    let mut entries: Vec<PathBuf> = std::fs::read_dir(fixtures_dir())
        .expect("fixtures directory exists")
        .map(|e| e.expect("readable dir entry").path())
        .filter(|p| p.extension().is_some_and(|ext| ext == "json"))
        .collect();
    entries.sort();
    assert!(!entries.is_empty(), "no fixtures found");
    entries
        .into_iter()
        .map(|path| {
            let name = path
                .file_stem()
                .expect("file stem")
                .to_string_lossy()
                .into_owned();
            let text = std::fs::read_to_string(&path).expect("fixture readable");
            let fixture: Fixture = serde_json::from_str(&text)
                .unwrap_or_else(|e| panic!("fixture {name} does not parse: {e}"));
            (name, fixture)
        })
        .collect()
}

fn enum_name<T: serde::Serialize>(value: &T) -> String {
    match serde_json::to_value(value) {
        Ok(serde_json::Value::String(s)) => s,
        other => panic!("expected a string-serializable enum, got {other:?}"),
    }
}

fn status_of(status: &str) -> ExpectationStatus {
    match status {
        "SATISFIED" => ExpectationStatus::Satisfied,
        "UNSATISFIED" => ExpectationStatus::Unsatisfied,
        "UNKNOWN" => ExpectationStatus::Unknown,
        "NOT_APPLICABLE" => ExpectationStatus::NotApplicable,
        other => panic!("unknown expectation status '{other}' in fixture"),
    }
}

fn diff_state_of(state: &str) -> EntityDiffState {
    match state {
        "UNCHANGED" => EntityDiffState::Unchanged,
        "ADDED" => EntityDiffState::Added,
        "REMOVED" => EntityDiffState::Removed,
        "CHANGED" => EntityDiffState::Changed,
        "UNAVAILABLE" => EntityDiffState::Unavailable,
        "UNKNOWN" => EntityDiffState::Unknown,
        other => panic!("unknown diff state '{other}' in fixture"),
    }
}

fn stability_of(value: &str) -> EntityStability {
    match value {
        "STABLE" => EntityStability::Stable,
        "VARIABLE" => EntityStability::Variable,
        "TRANSIENT_CANDIDATE" => EntityStability::TransientCandidate,
        other => panic!("unknown stability '{other}' in fixture"),
    }
}

fn check_diff(name: &str, baseline: &Baseline, current: &RunProjection, expect: &DiffExpectation) {
    let result = diff(baseline, current);
    assert_eq!(
        enum_name(&result.compatibility),
        expect.compatibility,
        "[{name}] compatibility"
    );

    for (key, expected) in &expect.states {
        let entity = result
            .entities
            .iter()
            .find(|e| e.key.canonical() == *key)
            .unwrap_or_else(|| {
                panic!(
                    "[{name}] no diff entry for {key}; got {:?}",
                    result
                        .entities
                        .iter()
                        .map(|e| e.key.canonical())
                        .collect::<Vec<_>>()
                )
            });
        assert_eq!(
            entity.state,
            diff_state_of(expected),
            "[{name}] state of {key} (reason: {:?})",
            entity.reason
        );
    }

    for (key, attributes) in &expect.attribute_deltas {
        let entity = result
            .entities
            .iter()
            .find(|e| e.key.canonical() == *key)
            .unwrap_or_else(|| panic!("[{name}] no diff entry for {key}"));
        for (attribute, expected) in attributes {
            let change = entity
                .attribute_diffs
                .iter()
                .find(|d| d.attribute == *attribute)
                .unwrap_or_else(|| panic!("[{name}] no attribute diff for {key}.{attribute}"));
            let actual = change
                .absolute_delta
                .unwrap_or_else(|| panic!("[{name}] {key}.{attribute} has no numeric delta"));
            assert!(
                (actual - expected).abs() < 1e-9,
                "[{name}] {key}.{attribute} delta {actual} != {expected}"
            );
        }
    }

    // A diff is a statement of difference. If health vocabulary ever
    // leaks into it, these fixtures are where it must fail.
    let json = serde_json::to_string(&result).unwrap();
    for forbidden in ["DEGRADED", "CRITICAL", "finding", "root_cause", "severity"] {
        assert!(
            !json.contains(forbidden),
            "[{name}] diff leaked diagnosis vocabulary '{forbidden}'"
        );
    }
}

fn check_evaluation(
    name: &str,
    projection: &RunProjection,
    profile: &Profile,
    next: Option<&Profile>,
    expect: &EvaluationExpectation,
) {
    validate(profile).unwrap_or_else(|e| panic!("[{name}] fixture profile is invalid: {e}"));
    let context = EvaluationContext {
        app_version: "fixture".into(),
        ..Default::default()
    };
    let run = evaluate(profile, projection, &context);

    let result_of = |run: &doctor_domain::evaluation::EvaluationRun, id: &str| {
        run.results
            .iter()
            .find(|r| r.expectation_id == id)
            .cloned()
            .unwrap_or_else(|| panic!("[{name}] no result for expectation '{id}'"))
    };

    for (id, expected) in &expect.statuses {
        let result = result_of(&run, id);
        assert_eq!(
            result.status,
            status_of(expected),
            "[{name}] {id} (reason: {})",
            result.reason
        );
        // Every non-satisfied outcome must explain itself, so UNKNOWN can
        // never be mistaken for a fault.
        if result.status != ExpectationStatus::Satisfied {
            assert!(!result.reason.is_empty(), "[{name}] {id} has no reason");
        }
    }

    for (id, needle) in &expect.reason_contains {
        let result = result_of(&run, id);
        assert!(
            result.reason.contains(needle.as_str()),
            "[{name}] {id} reason '{}' does not mention '{needle}'",
            result.reason
        );
    }

    for (id, expected) in &expect.actual_numbers {
        let result = result_of(&run, id);
        let actual = result
            .actual
            .as_ref()
            .and_then(|v| v.as_number())
            .unwrap_or_else(|| panic!("[{name}] {id} recorded no numeric actual value"));
        assert!(
            (actual - expected).abs() < 1e-9,
            "[{name}] {id} actual {actual} != {expected}"
        );
    }

    if !expect.next_statuses.is_empty() {
        let next = next.expect("fixture declares next_statuses but no profile_next");
        let later = evaluate(next, projection, &context);
        assert_ne!(
            run.profile_revision, later.profile_revision,
            "[{name}] revisions must differ for this fixture to mean anything"
        );
        for (id, expected) in &expect.next_statuses {
            assert_eq!(
                result_of(&later, id).status,
                status_of(expected),
                "[{name}] {id} under revision {}",
                later.profile_revision
            );
        }
    }

    // §40: C2 answers whether expectations hold, never why something broke.
    let json = serde_json::to_string(&run).unwrap().to_lowercase();
    for forbidden in ["finding", "root_cause", "severity", "score", "remediation"] {
        assert!(
            !json.contains(forbidden),
            "[{name}] evaluation leaked diagnosis vocabulary '{forbidden}'"
        );
    }
}

fn check_capture(name: &str, sources: &[CaptureSource], expect: &CaptureExpectation) {
    let inputs: Vec<CaptureInput> = sources
        .iter()
        .map(|s| CaptureInput {
            source: s.source.clone(),
            projection: s.projection.clone(),
        })
        .collect();
    let source_count = inputs.len() as u32;
    let baseline = capture(
        doctor_domain::BaselineId::from("fixture"),
        doctor_domain::DeviceId::from("local"),
        name.to_owned(),
        String::new(),
        "fixture".into(),
        BTreeMap::new(),
        inputs,
    );
    assert_eq!(
        baseline.source_count(),
        source_count,
        "[{name}] source count"
    );

    for (key, expected) in &expect.entities {
        let entity = baseline
            .entities
            .iter()
            .find(|e| e.key.canonical() == *key)
            .unwrap_or_else(|| panic!("[{name}] baseline has no entity {key}"));
        assert_eq!(
            entity.stability,
            stability_of(&expected.stability),
            "[{name}] stability of {key}"
        );
        assert_eq!(
            entity.presence_count, expected.presence_count,
            "[{name}] presence count of {key}"
        );
        if let Some(ratio) = expected.presence_ratio {
            assert!(
                (entity.presence_ratio - ratio).abs() < 1e-9,
                "[{name}] presence ratio of {key}: {} != {ratio}",
                entity.presence_ratio
            );
        }
    }

    let report = draft(&baseline);
    for (key, expected) in &expect.draft {
        let suggestion = report
            .suggestions
            .iter()
            .find(|s| s.expectation.id == *key)
            .unwrap_or_else(|| panic!("[{name}] draft has no suggestion for {key}"));
        assert_eq!(
            enum_name(&suggestion.expectation.requirement),
            expected.requirement,
            "[{name}] requirement for {key}"
        );
        assert_eq!(
            suggestion.selected_by_default, expected.selected_by_default,
            "[{name}] pre-selection for {key}"
        );
    }

    for (id, expected) in &expect.suggested_thresholds {
        let suggestion = report
            .suggestions
            .iter()
            .find(|s| s.expectation.id == *id)
            .unwrap_or_else(|| panic!("[{name}] draft has no threshold suggestion '{id}'"));
        assert!(
            suggestion.suggested_threshold,
            "[{name}] {id} must be marked as a suggested threshold"
        );
        assert!(
            !suggestion.selected_by_default,
            "[{name}] a derived threshold must never be pre-accepted"
        );
        let value = match &suggestion.expectation.constraint {
            doctor_domain::profile::Constraint::Min { value, .. } => *value,
            other => panic!("[{name}] {id} is not a min constraint: {other:?}"),
        };
        assert!(
            (value - expected).abs() < 1e-9,
            "[{name}] {id} bound {value} != {expected}"
        );
    }
}

#[test]
fn every_fixture_holds() {
    let fixtures = load_all();
    // Guards against a fixture being silently dropped from the tree.
    assert!(
        fixtures.len() >= 19,
        "expected the full fixture set, found {}",
        fixtures.len()
    );

    for (name, fixture) in fixtures {
        match fixture {
            Fixture::BaselineDiff {
                description,
                baseline,
                current,
                expect,
            } => {
                assert!(!description.is_empty(), "[{name}] needs a description");
                check_diff(&name, &baseline, &current, &expect);
            }
            Fixture::ProfileEvaluation {
                description,
                projection,
                profile,
                profile_next,
                expect,
            } => {
                assert!(!description.is_empty(), "[{name}] needs a description");
                check_evaluation(&name, &projection, &profile, profile_next.as_ref(), &expect);
            }
            Fixture::BaselineCapture {
                description,
                sources,
                expect,
            } => {
                assert!(!description.is_empty(), "[{name}] needs a description");
                check_capture(&name, &sources, &expect);
            }
        }
    }
}

/// Every fixture the specification names must exist, by that exact name.
#[test]
fn the_named_fixture_set_is_present() {
    let present: Vec<String> = load_all().into_iter().map(|(name, _)| name).collect();
    for required in [
        "baseline_identical",
        "baseline_entity_added",
        "baseline_entity_removed",
        "baseline_attribute_changed",
        "baseline_order_changed_only",
        "baseline_source_unavailable",
        "profile_all_satisfied",
        "profile_required_missing",
        "profile_optional_missing",
        "profile_numeric_violation",
        "profile_unknown_source",
        "profile_revision_history",
        "profile_multi_run_stable",
        "profile_multi_run_variable",
        "ros_profile_node_required",
        "ros_profile_topic_rate",
        "ros_profile_tf_path",
        "ros_profile_qos",
        "ros_profile_lifecycle",
    ] {
        assert!(
            present.iter().any(|name| name == required),
            "missing fixture '{required}'"
        );
    }
}

/// Order independence, proven by shuffling rather than by construction:
/// reversing both the baseline's and the run's entity lists must produce
/// byte-identical diff output.
#[test]
fn diff_is_order_independent_under_shuffling() {
    for (name, fixture) in load_all() {
        let Fixture::BaselineDiff {
            baseline, current, ..
        } = fixture
        else {
            continue;
        };
        let straight = serde_json::to_string(&diff(&baseline, &current)).unwrap();

        let mut shuffled_baseline = baseline.clone();
        shuffled_baseline.entities.reverse();
        let mut shuffled_current = current.clone();
        shuffled_current.entities.reverse();
        let reversed = serde_json::to_string(&diff(&shuffled_baseline, &shuffled_current)).unwrap();

        // `compared_at` is a timestamp; compare everything else.
        let strip = |text: &str| {
            let mut value: serde_json::Value = serde_json::from_str(text).unwrap();
            value.as_object_mut().unwrap().remove("compared_at");
            value.to_string()
        };
        assert_eq!(
            strip(&straight),
            strip(&reversed),
            "[{name}] diff is order-dependent"
        );
    }
}

/// Evaluating the same inputs twice must produce the same statuses and
/// the same reasons — an evaluation is a record, not an opinion.
#[test]
fn evaluation_is_reproducible() {
    for (name, fixture) in load_all() {
        let Fixture::ProfileEvaluation {
            projection,
            profile,
            ..
        } = fixture
        else {
            continue;
        };
        let context = EvaluationContext::default();
        let first = evaluate(&profile, &projection, &context);
        let second = evaluate(&profile, &projection, &context);
        let strip = |run: &doctor_domain::evaluation::EvaluationRun| {
            run.results
                .iter()
                .map(|r| (r.expectation_id.clone(), r.status, r.reason.clone()))
                .collect::<Vec<_>>()
        };
        assert_eq!(
            strip(&first),
            strip(&second),
            "[{name}] evaluation is unstable"
        );
    }
}

/// Every fixture profile survives a YAML round trip unchanged, and the
/// exported document is a fixpoint so profiles diff cleanly in git.
#[test]
fn fixture_profiles_round_trip_through_yaml() {
    for (name, fixture) in load_all() {
        let Fixture::ProfileEvaluation { profile, .. } = fixture else {
            continue;
        };
        let yaml = doctor_profile::to_yaml(&profile);
        let parsed = doctor_profile::parse_yaml(&yaml)
            .unwrap_or_else(|e| panic!("[{name}] exported YAML does not parse: {e}"));
        assert_eq!(
            doctor_profile::to_yaml(&parsed),
            yaml,
            "[{name}] YAML export is not a fixpoint"
        );

        let mut expected = profile.expectations.clone();
        expected.sort_by(|a, b| a.id.cmp(&b.id));
        assert_eq!(
            parsed.expectations, expected,
            "[{name}] expectations changed across a YAML round trip"
        );
        assert_eq!(parsed.schema_version, profile.schema_version);
        assert_eq!(parsed.revision, profile.revision);
    }
}
