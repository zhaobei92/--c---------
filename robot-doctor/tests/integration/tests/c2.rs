//! Phase C2 end-to-end: run → projection → baseline → diff → profile →
//! evaluation, through the real engine, the real scheduler and real
//! SQLite persistence. No component is stubbed except the plugin itself,
//! which is a real process speaking the real protocol.

use doctor_core::{c2, DiagnosisEvent, Engine};
use doctor_domain::baseline::EntityDiffState;
use doctor_domain::comparison::NamespaceAvailability;
use doctor_domain::evaluation::ExpectationStatus;
use doctor_domain::profile::{
    Constraint, Expectation, Profile, ProfileStatus, Requirement, Selector, PROFILE_SCHEMA_VERSION,
};
use doctor_domain::{DeviceId, DiagnosticMode, ProfileId, RunId};
use doctor_storage::Storage;
use std::path::{Path, PathBuf};
use std::sync::Arc;

fn stub_exe() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_stub-plugin"))
}

fn write_stub_manifest(plugins_dir: &Path, id: &str, mode: &str) {
    let dir = plugins_dir.join(id);
    std::fs::create_dir_all(&dir).unwrap();
    let exe = stub_exe().display().to_string();
    std::fs::write(
        dir.join("plugin.yaml"),
        format!(
            r#"
id: {id}
name: Stub ({mode})
version: 0.1.0
api_version: 1
platforms: [linux, windows]
executable:
  linux: '{exe}'
  windows: '{exe}'
args: [{mode}]
"#
        ),
    )
    .unwrap();
}

fn temp_plugins_dir(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("rd-c2-{tag}-{}", uuid::Uuid::new_v4()));
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

async fn engine_with_storage(tag: &str) -> Arc<Engine> {
    let plugins_dir = temp_plugins_dir(tag);
    write_stub_manifest(&plugins_dir, "stub-good", "good");
    let storage = Storage::open_in_memory().await.unwrap();
    Arc::new(Engine::with_storage(&plugins_dir, storage))
}

/// Run to completion, draining every event including the ones that arrive
/// after RUN_COMPLETED (evaluation is deliberately reported last).
async fn run_all_events(
    engine: &Arc<Engine>,
    mode: DiagnosticMode,
) -> (RunId, Vec<DiagnosisEvent>) {
    let (run_id, mut rx) = engine.start_diagnosis(DeviceId::from("local"), mode).await;
    let mut events = Vec::new();
    while let Some(ev) = rx.recv().await {
        events.push(ev);
    }
    (run_id, events)
}

fn expectation(
    id: &str,
    kind: &str,
    constraint: Constraint,
    requirement: Requirement,
) -> Expectation {
    Expectation {
        id: id.into(),
        description: format!("stub {kind} expectation"),
        namespace: "stub".into(),
        kind: kind.into(),
        selector: Selector::default(),
        requirement,
        constraint,
        from_baseline: false,
    }
}

fn profile_with(id: &str, expectations: Vec<Expectation>) -> Profile {
    let now = chrono::Utc::now();
    Profile {
        schema_version: PROFILE_SCHEMA_VERSION,
        id: ProfileId::from(id),
        name: "stub profile".into(),
        description: String::new(),
        revision: 0,
        status: ProfileStatus::Draft,
        created_at: now,
        updated_at: now,
        tags: vec![],
        expectations,
    }
}

#[tokio::test]
async fn run_projection_records_what_was_and_was_not_observed() {
    let engine = engine_with_storage("projection").await;
    let (run_id, _) = run_all_events(&engine, DiagnosticMode::Full).await;

    let projection = c2::run_projection(&engine, &run_id).await.unwrap();
    assert_eq!(projection.device_id, DeviceId::from("local"));
    assert!(
        !projection.entities.is_empty(),
        "the stub plugin projects comparison entities"
    );

    // stub.ok observed widgets; stub.unavailable could not observe
    // gadgets. Both facts survive persistence.
    assert_eq!(
        projection.availability_for("stub", "widget"),
        NamespaceAvailability::Observed
    );
    assert_eq!(
        projection.availability_for("stub", "gadget"),
        NamespaceAvailability::NotObserved
    );
    // A kind nobody reported on is not silently treated as empty.
    assert_eq!(
        projection.availability_for("stub", "sprocket"),
        NamespaceAvailability::Unsupported
    );
    assert_eq!(
        projection.reason_for("stub", "gadget"),
        Some("simulated missing dependency")
    );

    engine.shutdown().await;
}

#[tokio::test]
async fn baseline_capture_diff_and_metadata_editing() {
    let engine = engine_with_storage("baseline").await;
    let (first, _) = run_all_events(&engine, DiagnosticMode::Full).await;
    let (second, _) = run_all_events(&engine, DiagnosticMode::Full).await;

    let candidate = c2::capture_candidate(&engine, &first).await.unwrap();
    assert_eq!(candidate.run_id, first);
    assert!(candidate.entity_count > 0);
    assert_eq!(candidate.unobserved_namespaces, Vec::<String>::new());

    // Two runs → a baseline set: presence is aggregated across both.
    let baseline = c2::capture_baseline(
        &engine,
        "known good".into(),
        "captured by test".into(),
        vec![first.clone(), second.clone()],
        false,
    )
    .await
    .unwrap();
    assert_eq!(baseline.source_count(), 2);
    assert!(baseline.stable_entities().count() > 0);
    assert!(baseline
        .entities
        .iter()
        .all(|e| (e.presence_ratio - 1.0).abs() < f64::EPSILON));

    // Diffing the same device against its own baseline finds no change.
    let diff = c2::diff_run(&engine, &baseline.id, &second).await.unwrap();
    assert_eq!(diff.count(EntityDiffState::Added), 0);
    assert_eq!(diff.count(EntityDiffState::Removed), 0);
    assert_eq!(diff.count(EntityDiffState::Changed), 0);
    assert!(diff.count(EntityDiffState::Unchanged) > 0);

    // Metadata is editable; the captured snapshot is not.
    assert!(c2::update_baseline_metadata(
        &engine,
        baseline.id.clone(),
        "renamed".into(),
        "new description".into(),
        vec!["fleet-a".into()],
    )
    .await
    .unwrap());
    let reloaded = c2::get_baseline(&engine, &baseline.id).await.unwrap();
    assert_eq!(reloaded.name, "renamed");
    assert_eq!(reloaded.tags, vec!["fleet-a".to_owned()]);
    assert_eq!(reloaded.entities, baseline.entities);

    let listed = c2::list_baselines(&engine, None).await.unwrap();
    assert_eq!(listed.len(), 1);
    assert_eq!(listed[0].source_count, 2);

    assert!(c2::delete_baseline(&engine, baseline.id.clone())
        .await
        .unwrap());
    engine.shutdown().await;
}

#[tokio::test]
async fn active_profile_is_evaluated_after_every_run() {
    let engine = engine_with_storage("evaluate").await;

    let profile = profile_with(
        "stub-profile",
        vec![
            // Observed and present → SATISFIED.
            expectation(
                "widget-present",
                "widget",
                Constraint::Exists,
                Requirement::Required,
            ),
            // The gadget kind could not be observed → UNKNOWN, never
            // UNSATISFIED. This is the §25 rule.
            expectation(
                "gadget-present",
                "gadget",
                Constraint::Exists,
                Requirement::Required,
            ),
            // Observed, and genuinely absent → UNSATISFIED (§43).
            {
                let mut e = expectation(
                    "missing-widget",
                    "widget",
                    Constraint::Exists,
                    Requirement::Required,
                );
                e.selector = Selector {
                    key: Some("stub.nonexistent".into()),
                    key_prefix: None,
                };
                e
            },
            // Optional and absent → NOT_APPLICABLE.
            {
                let mut e = expectation(
                    "optional-widget",
                    "widget",
                    Constraint::Exists,
                    Requirement::Optional,
                );
                e.selector = Selector {
                    key: Some("stub.also_nonexistent".into()),
                    key_prefix: None,
                };
                e
            },
        ],
    );

    let saved = c2::save_profile(&engine, profile).await.unwrap();
    assert_eq!(saved.revision, 1, "saving allocates the next revision");

    c2::assign_profile(
        &engine,
        DeviceId::from("local"),
        saved.id.clone(),
        saved.revision,
        None,
    )
    .await
    .unwrap();

    let (run_id, events) = run_all_events(&engine, DiagnosticMode::Full).await;

    // The engine reports the evaluation as its own event, after the run.
    let evaluation_event = events
        .iter()
        .find_map(|ev| match ev {
            DiagnosisEvent::EvaluationCompleted {
                satisfied,
                unsatisfied,
                unknown,
                not_applicable,
                profile_revision,
                ..
            } => Some((
                *satisfied,
                *unsatisfied,
                *unknown,
                *not_applicable,
                *profile_revision,
            )),
            _ => None,
        })
        .expect("EVALUATION_COMPLETED is emitted for an assigned profile");
    assert_eq!(evaluation_event, (1, 1, 1, 1, 1));

    // And it is persisted against the exact revision that was active.
    let stored = c2::evaluation_for_run(&engine, run_id.clone())
        .await
        .unwrap()
        .expect("evaluation persisted");
    assert_eq!(stored.diagnostic_run_id, run_id);
    assert_eq!(stored.profile_revision, 1);

    let status = |id: &str| {
        stored
            .results
            .iter()
            .find(|r| r.expectation_id == id)
            .unwrap_or_else(|| panic!("missing result for {id}"))
            .status
    };
    assert_eq!(status("widget-present"), ExpectationStatus::Satisfied);
    assert_eq!(status("gadget-present"), ExpectationStatus::Unknown);
    assert_eq!(status("missing-widget"), ExpectationStatus::Unsatisfied);
    assert_eq!(status("optional-widget"), ExpectationStatus::NotApplicable);

    // Every non-satisfied outcome explains itself, so UNKNOWN can never
    // be mistaken for a fault.
    let unknown = stored
        .results
        .iter()
        .find(|r| r.expectation_id == "gadget-present")
        .unwrap();
    assert!(
        unknown.reason.contains("could not be observed"),
        "unhelpful UNKNOWN reason: {}",
        unknown.reason
    );

    // C2 states outcomes, never diagnoses.
    let json = serde_json::to_string(&stored).unwrap().to_lowercase();
    for forbidden in ["finding", "root_cause", "severity", "score"] {
        assert!(!json.contains(forbidden), "evaluation leaked '{forbidden}'");
    }

    engine.shutdown().await;
}

#[tokio::test]
async fn revisions_are_immutable_and_referenced_ones_cannot_be_deleted() {
    let engine = engine_with_storage("revisions").await;

    let profile = profile_with(
        "versioned",
        vec![expectation(
            "widget-present",
            "widget",
            Constraint::Exists,
            Requirement::Required,
        )],
    );
    let v1 = c2::save_profile(&engine, profile.clone()).await.unwrap();
    let v2 = c2::save_profile(&engine, profile).await.unwrap();
    assert_eq!((v1.revision, v2.revision), (1, 2));

    c2::assign_profile(
        &engine,
        DeviceId::from("local"),
        v1.id.clone(),
        v1.revision,
        None,
    )
    .await
    .unwrap();
    run_all_events(&engine, DiagnosticMode::Quick).await;

    // Revision 1 now has history, so it may be archived but never deleted.
    let err = c2::delete_profile_revision(&engine, v1.id.clone(), 1)
        .await
        .expect_err("a referenced revision must not be deletable");
    assert!(err.to_string().contains("archive"), "{err}");

    // An unreferenced revision may be removed.
    assert!(c2::delete_profile_revision(&engine, v1.id.clone(), 2)
        .await
        .unwrap());

    let revisions = c2::list_profile_revisions(&engine, v1.id.clone())
        .await
        .unwrap();
    assert_eq!(revisions.len(), 1);
    assert_eq!(revisions[0].revision, 1);
    assert!(revisions[0].evaluation_count > 0);

    engine.shutdown().await;
}

#[tokio::test]
async fn yaml_round_trip_through_storage_is_stable_and_inert() {
    let engine = engine_with_storage("yaml").await;

    // A profile document is data. Anything that looks like code is just
    // text in a description, and stays text.
    let yaml = r#"
schema_version: 1
id: imported
name: Imported profile
description: "$(rm -rf /) ${env:SECRET} <%= 7*7 %>"
tags: [imported]
expectations:
  - id: widget-present
    description: widget exists
    namespace: stub
    kind: widget
    requirement: required
    constraint:
      operator: exists
  - id: widget-count
    namespace: stub
    kind: widget
    requirement: optional
    constraint:
      operator: count_min
      value: 1
"#;

    let imported = c2::import_profile_yaml(&engine, yaml, None).await.unwrap();
    assert_eq!(imported.expectations.len(), 2);
    // Importing never activates: a fresh import cannot start judging.
    assert_eq!(imported.status, ProfileStatus::Draft);
    assert!(imported.description.contains("rm -rf"));

    let exported = c2::export_profile_yaml(&engine, &imported.id, imported.revision)
        .await
        .unwrap();
    // Deterministic export: re-importing and re-exporting is a fixpoint,
    // so profiles produce clean diffs in version control.
    let reimported = doctor_profile::parse_yaml(&exported).unwrap();
    assert_eq!(doctor_profile::to_yaml(&reimported), exported);
    assert!(
        exported.starts_with("schema_version:"),
        "export should lead with the schema version:\n{exported}"
    );

    engine.shutdown().await;
}

#[tokio::test]
async fn drafting_from_a_baseline_suggests_without_committing() {
    let engine = engine_with_storage("draft").await;
    let (first, _) = run_all_events(&engine, DiagnosticMode::Full).await;
    let (second, _) = run_all_events(&engine, DiagnosticMode::Full).await;

    let baseline = c2::capture_baseline(
        &engine,
        "draft source".into(),
        String::new(),
        vec![first, second],
        false,
    )
    .await
    .unwrap();

    let report = c2::draft_from_baseline(&engine, &baseline.id)
        .await
        .unwrap();
    assert!(!report.suggestions.is_empty());
    assert!(
        report
            .suggestions
            .iter()
            .all(|s| s.expectation.from_baseline),
        "drafted expectations must record their provenance"
    );

    let accepted: Vec<String> = report
        .selected()
        .map(|s| s.expectation.id.clone())
        .collect();
    assert!(!accepted.is_empty(), "stable entities are pre-selected");

    let drafted = report.into_profile(
        ProfileId::from("drafted"),
        "Drafted".into(),
        String::new(),
        &accepted,
    );
    // A draft is a proposal, never an active judgement.
    assert_eq!(drafted.status, ProfileStatus::Draft);
    assert!(c2::assignment(&engine, DeviceId::from("local"))
        .await
        .unwrap()
        .is_none());

    engine.shutdown().await;
}
