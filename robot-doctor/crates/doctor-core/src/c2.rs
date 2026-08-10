//! Baseline and profile operations exposed to the UI (Phase C2).
//!
//! Everything here works from *persisted* run projections. Capturing a
//! baseline, diffing, drafting and evaluating never relaunch a plugin and
//! never re-observe the machine: what the user approved is exactly what
//! is stored, and a historical evaluation always reflects the run it was
//! computed from.

use crate::engine::Engine;
use chrono::Utc;
use doctor_domain::baseline::{Baseline, BaselineDiff, BaselineSource, RunProjection};
use doctor_domain::evaluation::EvaluationRun;
use doctor_domain::profile::{DeviceProfileAssignment, Profile, ProfileStatus};
use doctor_domain::{BaselineId, DeviceId, ProfileId, RunId};
use doctor_profile::{
    draft, evaluate, validate, CaptureInput, DraftReport, EligibilityWarning, ProfileError,
};
use doctor_storage::{
    c2 as store, BaselineSummaryRow, ProfileRevisionRow, ProfileRow, Storage, StorageError,
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// Failure modes the UI must distinguish.
#[derive(Debug, thiserror::Error)]
pub enum C2Error {
    /// Baselines and profiles are meaningless without history.
    #[error("this engine runs without persistence, so baselines and profiles are unavailable")]
    NoStorage,
    #[error("{0}")]
    Storage(#[from] StorageError),
    #[error("{0}")]
    Profile(#[from] ProfileError),
    #[error("{what} '{id}' does not exist")]
    NotFound { what: &'static str, id: String },
}

/// Everything the Baseline page needs to show one capture candidate.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CaptureCandidate {
    pub run_id: RunId,
    pub device_id: DeviceId,
    pub started_at: String,
    pub mode: String,
    pub overall_health: Option<String>,
    pub entity_count: usize,
    /// Namespaces that could not be observed in this run, so the user can
    /// see what a baseline captured from it would be missing.
    pub unobserved_namespaces: Vec<String>,
    pub warnings: Vec<CaptureWarning>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CaptureWarning {
    pub code: String,
    pub message: String,
}

impl From<EligibilityWarning> for CaptureWarning {
    fn from(w: EligibilityWarning) -> Self {
        Self {
            code: w.code,
            message: w.message,
        }
    }
}

/// A run's diff against a baseline, plus the evaluation of the active
/// profile, as one payload for the comparison view.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ComparisonView {
    pub run_id: RunId,
    pub baseline: Option<BaselineDiff>,
    pub evaluation: Option<EvaluationRun>,
}

fn storage(engine: &Engine) -> Result<&Storage, C2Error> {
    engine.storage().ok_or(C2Error::NoStorage)
}

// ── projections ────────────────────────────────────────────────────────

/// The stored comparison projection of one diagnostic run.
pub async fn run_projection(engine: &Engine, run_id: &RunId) -> Result<RunProjection, C2Error> {
    let storage = storage(engine)?;
    let run_id = run_id.clone();
    Ok(storage
        .with_conn(move |conn| store::run_projection(conn, &run_id))
        .await?)
}

/// Inspect a run for capture eligibility without capturing anything.
pub async fn capture_candidate(
    engine: &Engine,
    run_id: &RunId,
) -> Result<CaptureCandidate, C2Error> {
    let storage = storage(engine)?;
    let stored = storage
        .get_run(run_id)
        .await?
        .ok_or_else(|| C2Error::NotFound {
            what: "run",
            id: run_id.to_string(),
        })?;
    let projection = run_projection(engine, run_id).await?;

    // A run is persisted iff storage returned it, which is what the
    // eligibility check means by "persisted".
    let warnings = doctor_profile::eligibility_warnings(&stored.run, &projection, true);
    let unobserved = projection
        .namespaces
        .iter()
        .filter(|(_, state)| {
            **state == doctor_domain::comparison::NamespaceAvailability::NotObserved
        })
        .map(|(name, _)| name.clone())
        .collect();

    Ok(CaptureCandidate {
        run_id: run_id.clone(),
        device_id: projection.device_id.clone(),
        started_at: stored.run.started_at.to_rfc3339(),
        mode: format!("{:?}", stored.run.mode).to_uppercase(),
        overall_health: stored
            .overall_health
            .map(|h| format!("{h:?}").to_uppercase()),
        entity_count: projection.entities.len(),
        unobserved_namespaces: unobserved,
        warnings: warnings.into_iter().map(Into::into).collect(),
    })
}

// ── baselines ──────────────────────────────────────────────────────────

/// Capture a baseline from one or more approved runs.
///
/// Passing several runs produces the "baseline set" behaviour: per-entity
/// presence and stability are aggregated across them, so a later diff can
/// say that a missing entity was only present in half the known-good runs
/// anyway.
pub async fn capture_baseline(
    engine: &Engine,
    name: String,
    description: String,
    run_ids: Vec<RunId>,
    manually_accepted: bool,
) -> Result<Baseline, C2Error> {
    let storage = storage(engine)?;
    if run_ids.is_empty() {
        return Err(C2Error::NotFound {
            what: "run",
            id: "<none selected>".into(),
        });
    }

    let mut inputs = Vec::new();
    let mut device_id = None;
    let mut plugin_versions: BTreeMap<String, String> = BTreeMap::new();
    for run_id in &run_ids {
        let stored = storage
            .get_run(run_id)
            .await?
            .ok_or_else(|| C2Error::NotFound {
                what: "run",
                id: run_id.to_string(),
            })?;
        let projection = run_projection(engine, run_id).await?;
        device_id.get_or_insert(projection.device_id.clone());
        for snapshot in &stored.plugin_snapshots {
            plugin_versions.insert(snapshot.plugin_id.clone(), snapshot.version.clone());
        }
        inputs.push(CaptureInput {
            source: BaselineSource {
                run_id: run_id.clone(),
                run_started_at: stored.run.started_at,
                mode: format!("{:?}", stored.run.mode).to_uppercase(),
                overall_health: stored
                    .overall_health
                    .map(|h| format!("{h:?}").to_uppercase()),
                manually_accepted,
            },
            projection,
        });
    }

    let baseline = doctor_profile::capture(
        BaselineId::from(uuid::Uuid::new_v4().to_string().as_str()),
        device_id.expect("at least one run"),
        name,
        description,
        engine.app_version().to_owned(),
        plugin_versions,
        inputs,
    );

    let to_store = baseline.clone();
    storage
        .with_conn(move |conn| store::insert_baseline(conn, &to_store))
        .await?;
    Ok(baseline)
}

pub async fn list_baselines(
    engine: &Engine,
    device_id: Option<DeviceId>,
) -> Result<Vec<BaselineSummaryRow>, C2Error> {
    let storage = storage(engine)?;
    Ok(storage
        .with_conn(move |conn| store::list_baselines(conn, device_id.as_ref().map(|d| d.as_str())))
        .await?)
}

pub async fn get_baseline(engine: &Engine, id: &BaselineId) -> Result<Baseline, C2Error> {
    let storage = storage(engine)?;
    let wanted = id.clone();
    storage
        .with_conn(move |conn| store::get_baseline(conn, &wanted))
        .await?
        .ok_or_else(|| C2Error::NotFound {
            what: "baseline",
            id: id.to_string(),
        })
}

/// Rename or re-tag a baseline. The captured snapshot itself is immutable.
pub async fn update_baseline_metadata(
    engine: &Engine,
    id: BaselineId,
    name: String,
    description: String,
    tags: Vec<String>,
) -> Result<bool, C2Error> {
    let storage = storage(engine)?;
    Ok(storage
        .with_conn(move |conn| {
            store::update_baseline_metadata(conn, &id, &name, &description, &tags)
        })
        .await?)
}

pub async fn delete_baseline(engine: &Engine, id: BaselineId) -> Result<bool, C2Error> {
    let storage = storage(engine)?;
    Ok(storage
        .with_conn(move |conn| store::delete_baseline(conn, &id))
        .await?)
}

/// Diff a run against a baseline and persist the result.
pub async fn diff_run(
    engine: &Engine,
    baseline_id: &BaselineId,
    run_id: &RunId,
) -> Result<BaselineDiff, C2Error> {
    let storage = storage(engine)?;
    let baseline = get_baseline(engine, baseline_id).await?;
    let projection = run_projection(engine, run_id).await?;
    let diff = doctor_profile::diff(&baseline, &projection);

    let to_store = diff.clone();
    let id = uuid::Uuid::new_v4().to_string();
    storage
        .with_conn(move |conn| store::insert_diff(conn, &id, &to_store))
        .await?;
    Ok(diff)
}

// ── profiles ───────────────────────────────────────────────────────────

/// Suggest a profile from a baseline. Nothing is saved or activated.
pub async fn draft_from_baseline(
    engine: &Engine,
    baseline_id: &BaselineId,
) -> Result<DraftReport, C2Error> {
    let baseline = get_baseline(engine, baseline_id).await?;
    Ok(draft::draft(&baseline))
}

/// Save a profile as a new revision.
///
/// Revisions are immutable: saving always allocates the next number
/// rather than editing history, so an evaluation recorded against
/// revision 3 keeps meaning what it meant.
pub async fn save_profile(engine: &Engine, mut profile: Profile) -> Result<Profile, C2Error> {
    let storage = storage(engine)?;

    // The revision is allocated before validation, so callers submit a
    // profile without guessing the next number and still get it checked
    // exactly as it will be stored.
    let profile_id = profile.id.clone();
    let latest = storage
        .with_conn(move |conn| store::latest_revision(conn, &profile_id))
        .await?;
    profile.revision = latest.unwrap_or(0) + 1;
    profile.updated_at = Utc::now();
    validate::validate(&profile)?;

    let yaml = validate::to_yaml(&profile);
    let to_store = profile.clone();
    storage
        .with_conn(move |conn| store::upsert_profile_revision(conn, &to_store, &yaml))
        .await?;
    Ok(profile)
}

/// Import a profile from YAML text. Parsing is pure data: the document
/// can declare expectations, never behaviour.
pub async fn import_profile_yaml(
    engine: &Engine,
    yaml: &str,
    id: Option<ProfileId>,
) -> Result<Profile, C2Error> {
    let mut profile = validate::parse_yaml(yaml)?;
    if let Some(id) = id {
        profile.id = id;
    }
    // An imported profile always lands as a draft: importing must never
    // silently start judging a device.
    profile.status = ProfileStatus::Draft;
    save_profile(engine, profile).await
}

pub async fn export_profile_yaml(
    engine: &Engine,
    profile_id: &ProfileId,
    revision: u32,
) -> Result<String, C2Error> {
    let profile = get_profile(engine, profile_id, revision).await?;
    Ok(validate::to_yaml(&profile))
}

pub async fn list_profiles(engine: &Engine) -> Result<Vec<ProfileRow>, C2Error> {
    let storage = storage(engine)?;
    Ok(storage.with_conn(|conn| store::list_profiles(conn)).await?)
}

pub async fn list_profile_revisions(
    engine: &Engine,
    profile_id: ProfileId,
) -> Result<Vec<ProfileRevisionRow>, C2Error> {
    let storage = storage(engine)?;
    Ok(storage
        .with_conn(move |conn| store::list_profile_revisions(conn, &profile_id))
        .await?)
}

pub async fn get_profile(
    engine: &Engine,
    profile_id: &ProfileId,
    revision: u32,
) -> Result<Profile, C2Error> {
    let storage = storage(engine)?;
    let wanted = profile_id.clone();
    storage
        .with_conn(move |conn| store::get_profile_revision(conn, &wanted, revision))
        .await?
        .ok_or_else(|| C2Error::NotFound {
            what: "profile revision",
            id: format!("{profile_id}@{revision}"),
        })
}

pub async fn set_profile_status(
    engine: &Engine,
    profile_id: ProfileId,
    revision: u32,
    status: ProfileStatus,
) -> Result<bool, C2Error> {
    let storage = storage(engine)?;
    Ok(storage
        .with_conn(move |conn| store::set_revision_status(conn, &profile_id, revision, status))
        .await?)
}

pub async fn delete_profile_revision(
    engine: &Engine,
    profile_id: ProfileId,
    revision: u32,
) -> Result<bool, C2Error> {
    let storage = storage(engine)?;
    Ok(storage
        .with_conn(move |conn| store::delete_profile_revision(conn, &profile_id, revision))
        .await?)
}

/// Activate a profile revision on a device. Activation is what makes a
/// profile start being evaluated after every run.
pub async fn assign_profile(
    engine: &Engine,
    device_id: DeviceId,
    profile_id: ProfileId,
    revision: u32,
    runtime_id: Option<String>,
) -> Result<DeviceProfileAssignment, C2Error> {
    let storage = storage(engine)?;
    // Refuse to activate something that does not exist.
    let _ = get_profile(engine, &profile_id, revision).await?;

    // An assignment references a device row, which normally appears the
    // first time a diagnosis runs. Assigning a profile on a fresh install
    // is legitimate, so make sure the device exists first rather than
    // surfacing a foreign-key error.
    for device in engine.devices().await {
        if device.id == device_id {
            storage.upsert_device(device).await?;
        }
    }
    let assignment = DeviceProfileAssignment {
        device_id,
        profile_id: profile_id.clone(),
        profile_revision: revision,
        activated_at: Utc::now(),
        runtime_id,
    };
    let to_store = assignment.clone();
    storage
        .with_conn(move |conn| store::set_assignment(conn, &to_store))
        .await?;
    storage
        .with_conn(move |conn| {
            store::set_revision_status(conn, &profile_id, revision, ProfileStatus::Active)
        })
        .await?;
    Ok(assignment)
}

pub async fn assignment(
    engine: &Engine,
    device_id: DeviceId,
) -> Result<Option<DeviceProfileAssignment>, C2Error> {
    let storage = storage(engine)?;
    Ok(storage
        .with_conn(move |conn| store::get_assignment(conn, &device_id))
        .await?)
}

// ── evaluation ─────────────────────────────────────────────────────────

/// Evaluate the device's active profile against one run and persist the
/// result. Returns `None` when no profile is assigned.
pub async fn evaluate_run(
    engine: &Engine,
    run_id: &RunId,
) -> Result<Option<EvaluationRun>, C2Error> {
    let storage = storage(engine)?;
    let projection = run_projection(engine, run_id).await?;
    let Some(assignment) = assignment(engine, projection.device_id.clone()).await? else {
        return Ok(None);
    };
    let profile = get_profile(engine, &assignment.profile_id, assignment.profile_revision).await?;

    let plugin_versions = storage
        .get_run(run_id)
        .await?
        .map(|stored| {
            stored
                .plugin_snapshots
                .iter()
                .map(|s| (s.plugin_id.clone(), s.version.clone()))
                .collect::<BTreeMap<_, _>>()
        })
        .unwrap_or_default();

    let context = evaluate::EvaluationContext {
        baseline_id: None,
        app_version: engine.app_version().to_owned(),
        plugin_versions,
    };
    let evaluation = evaluate::evaluate(&profile, &projection, &context);

    let to_store = evaluation.clone();
    storage
        .with_conn(move |conn| store::insert_evaluation(conn, &to_store))
        .await?;
    Ok(Some(evaluation))
}

/// The stored evaluation for a run, if one exists.
pub async fn evaluation_for_run(
    engine: &Engine,
    run_id: RunId,
) -> Result<Option<EvaluationRun>, C2Error> {
    let storage = storage(engine)?;
    Ok(storage
        .with_conn(move |conn| store::evaluation_for_run(conn, &run_id))
        .await?)
}

/// Diff + evaluation for one run, as the comparison view needs them.
pub async fn comparison_view(
    engine: &Engine,
    run_id: &RunId,
    baseline_id: Option<BaselineId>,
) -> Result<ComparisonView, C2Error> {
    let baseline = match baseline_id {
        Some(id) => Some(diff_run(engine, &id, run_id).await?),
        None => None,
    };
    Ok(ComparisonView {
        run_id: run_id.clone(),
        baseline,
        evaluation: evaluation_for_run(engine, run_id.clone()).await?,
    })
}

/// Runs eligible to become a baseline, newest first.
pub async fn baseline_candidates(
    engine: &Engine,
    device_id: Option<DeviceId>,
    limit: u32,
) -> Result<Vec<CaptureCandidate>, C2Error> {
    let storage = storage(engine)?;
    let rows = storage
        .list_runs(doctor_storage::RunFilter {
            device_id: device_id.map(|d| d.to_string()),
            limit: Some(limit),
            ..Default::default()
        })
        .await?;
    let mut out = Vec::new();
    for row in rows {
        let run_id = RunId::from(row.id.as_str());
        // A run that failed to persist its projection simply has none;
        // skip it rather than failing the whole listing.
        if let Ok(candidate) = capture_candidate(engine, &run_id).await {
            out.push(candidate);
        }
    }
    Ok(out)
}
