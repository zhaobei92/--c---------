//! Robot Doctor desktop shell: a thin Tauri 2 layer over the embedded
//! doctor-core engine. All diagnostics run in the core; the UI receives
//! progressive `diagnosis-event` events and renders real results. History
//! is served from SQLite through the storage service — never rebuilt by
//! re-running checks.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use doctor_core::{c2, CaptureCandidate, ComparisonView, Engine, PluginSummary};
use doctor_domain::baseline::{Baseline, BaselineDiff};
use doctor_domain::evaluation::EvaluationRun;
use doctor_domain::profile::{DeviceProfileAssignment, Profile, ProfileStatus};
use doctor_domain::{
    BaselineId, CheckResult, CheckRun, Device, DeviceId, DiagnosticMode, Platform, ProfileId, RunId,
};
use doctor_storage::{
    BaselineSummaryRow, ProfileRevisionRow, ProfileRow, RunFilter, RunSummaryRow, Storage, StoredRun,
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::Arc;
use tauri::{Emitter, Manager, State};

const NETWORK_TARGETS_KEY: &str = "network.targets";
const ROS_RUNTIME_KEY: &str = "ros.runtime";

struct AppState {
    engine: Arc<Engine>,
    plugins_dir: PathBuf,
    db_path: Option<PathBuf>,
}

#[derive(Serialize)]
struct AppInfo {
    version: String,
    platform: String,
    plugins_dir: String,
    database_path: Option<String>,
    storage_ok: bool,
}

/// A user-configured network target (host + optional port).
#[derive(Debug, Clone, Serialize, Deserialize)]
struct NetworkTarget {
    host: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    port: Option<u16>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    timeout_ms: Option<u64>,
}

/// Find the plugins directory:
/// 1. `$ROBOT_DOCTOR_PLUGINS_DIR`
/// 2. `plugins/` next to the executable (installed layout)
/// 3. walking up from the executable to the repository root (dev layout)
fn locate_plugins_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("ROBOT_DOCTOR_PLUGINS_DIR") {
        return PathBuf::from(dir);
    }
    let exe = std::env::current_exe().unwrap_or_default();
    if let Some(exe_dir) = exe.parent() {
        let installed = exe_dir.join("plugins");
        if installed.is_dir() {
            return installed;
        }
        let mut cursor = Some(exe_dir);
        while let Some(dir) = cursor {
            let candidate = dir.join("plugins");
            if candidate.is_dir()
                && std::fs::read_dir(&candidate)
                    .map(|mut entries| {
                        entries.any(|e| {
                            e.map(|e| e.path().join("plugin.yaml").exists())
                                .unwrap_or(false)
                        })
                    })
                    .unwrap_or(false)
            {
                return candidate;
            }
            cursor = dir.parent();
        }
    }
    PathBuf::from("plugins")
}

#[tauri::command]
async fn list_devices(state: State<'_, AppState>) -> Result<Vec<Device>, String> {
    Ok(state.engine.devices().await)
}

#[tauri::command]
async fn list_plugins(state: State<'_, AppState>) -> Result<Vec<PluginSummary>, String> {
    Ok(state.engine.registry().summaries().await)
}

#[tauri::command]
fn get_app_info(state: State<'_, AppState>) -> AppInfo {
    AppInfo {
        version: env!("CARGO_PKG_VERSION").to_owned(),
        platform: format!("{:?}", Platform::current()).to_lowercase(),
        plugins_dir: state.plugins_dir.display().to_string(),
        database_path: state.db_path.as_ref().map(|p| p.display().to_string()),
        storage_ok: state.engine.storage().is_some(),
    }
}

#[tauri::command]
async fn get_network_targets(state: State<'_, AppState>) -> Result<Vec<NetworkTarget>, String> {
    let Some(storage) = state.engine.storage() else {
        return Ok(vec![]);
    };
    let raw = storage
        .get_setting(NETWORK_TARGETS_KEY)
        .await
        .map_err(|e| e.to_string())?;
    Ok(raw
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default())
}

#[tauri::command]
async fn set_network_targets(
    state: State<'_, AppState>,
    targets: Vec<NetworkTarget>,
) -> Result<(), String> {
    let Some(storage) = state.engine.storage() else {
        return Err("storage is not available".into());
    };
    storage
        .set_setting(
            NETWORK_TARGETS_KEY,
            &serde_json::to_string(&targets).map_err(|e| e.to_string())?,
        )
        .await
        .map_err(|e| e.to_string())
}

async fn network_params(engine: &Engine) -> BTreeMap<String, serde_json::Value> {
    let mut params = BTreeMap::new();
    if let Some(storage) = engine.storage() {
        if let Ok(Some(raw)) = storage.get_setting(NETWORK_TARGETS_KEY).await {
            if let Ok(targets) = serde_json::from_str::<Vec<NetworkTarget>>(&raw) {
                if !targets.is_empty() {
                    let value = serde_json::json!({ "targets": targets });
                    for check in [
                        "network.reachability",
                        "network.tcp_port",
                        "network.latency",
                    ] {
                        params.insert(check.to_owned(), value.clone());
                    }
                }
            }
        }
        // Selected ROS runtime flows into every ros.* check.
        if let Ok(Some(raw)) = storage.get_setting(ROS_RUNTIME_KEY).await {
            if let Ok(runtime) = serde_json::from_str::<serde_json::Value>(&raw) {
                let value = serde_json::json!({ "runtime": runtime });
                for check in [
                    "ros.environment",
                    "ros.graph",
                    "ros.clock",
                    "ros.diagnostics",
                    "ros.doctor",
                    "ros.qos",
                    "ros.tf",
                    "ros.lifecycle",
                ] {
                    params.insert(check.to_owned(), value.clone());
                }
            }
        }
    }
    params
}

#[tauri::command]
async fn get_ros_runtime(state: State<'_, AppState>) -> Result<Option<serde_json::Value>, String> {
    let Some(storage) = state.engine.storage() else {
        return Ok(None);
    };
    let raw = storage
        .get_setting(ROS_RUNTIME_KEY)
        .await
        .map_err(|e| e.to_string())?;
    Ok(raw.and_then(|s| serde_json::from_str(&s).ok()))
}

#[tauri::command]
async fn set_ros_runtime(
    state: State<'_, AppState>,
    runtime: serde_json::Value,
) -> Result<(), String> {
    let Some(storage) = state.engine.storage() else {
        return Err("storage is not available".into());
    };
    storage
        .set_setting(
            ROS_RUNTIME_KEY,
            &serde_json::to_string(&runtime).map_err(|e| e.to_string())?,
        )
        .await
        .map_err(|e| e.to_string())
}

/// Execute one check on demand (SAMPLE TOPIC, TF query, runtime discovery,
/// graph refresh). If no explicit `runtime` param is given, the stored ROS
/// runtime selection is injected for ros.* checks.
#[tauri::command]
async fn run_single_check(
    state: State<'_, AppState>,
    check_id: String,
    params: serde_json::Value,
) -> Result<CheckResult, String> {
    let mut params = params;
    if check_id.starts_with("ros.") && params.get("runtime").is_none() {
        if let Some(storage) = state.engine.storage() {
            if let Ok(Some(raw)) = storage.get_setting(ROS_RUNTIME_KEY).await {
                if let Ok(runtime) = serde_json::from_str::<serde_json::Value>(&raw) {
                    if let Some(map) = params.as_object_mut() {
                        map.insert("runtime".into(), runtime);
                    } else {
                        params = serde_json::json!({ "runtime": runtime });
                    }
                }
            }
        }
    }
    state
        .engine
        .run_check_now(DeviceId::from("local"), &check_id, params)
        .await
}

#[tauri::command]
async fn run_diagnosis(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    device_id: String,
    mode: DiagnosticMode,
) -> Result<RunId, String> {
    let params = network_params(&state.engine).await;
    let (run_id, mut rx) = state
        .engine
        .start_diagnosis_with_params(DeviceId::from(device_id.as_str()), mode, params)
        .await;
    // Forward progressive engine events; the run continues in the
    // background so the interface never blocks on a deep diagnostic.
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            if let Err(err) = app.emit("diagnosis-event", &event) {
                tracing::warn!("failed to emit diagnosis event: {err}");
            }
        }
    });
    Ok(run_id)
}

#[tauri::command]
async fn cancel_diagnosis(state: State<'_, AppState>, run_id: String) -> Result<(), String> {
    state.engine.cancel_run(&RunId::from(run_id.as_str())).await;
    Ok(())
}

#[tauri::command]
async fn get_run(state: State<'_, AppState>, run_id: String) -> Result<Option<CheckRun>, String> {
    Ok(state
        .engine
        .store()
        .get_run(&RunId::from(run_id.as_str()))
        .await)
}

#[tauri::command]
async fn list_history(
    state: State<'_, AppState>,
    filter: RunFilter,
) -> Result<Vec<RunSummaryRow>, String> {
    match state.engine.storage() {
        Some(storage) => storage.list_runs(filter).await.map_err(|e| e.to_string()),
        None => Ok(vec![]),
    }
}

#[tauri::command]
async fn get_history_run(
    state: State<'_, AppState>,
    run_id: String,
) -> Result<Option<StoredRun>, String> {
    match state.engine.storage() {
        Some(storage) => storage
            .get_run(&RunId::from(run_id.as_str()))
            .await
            .map_err(|e| e.to_string()),
        None => Ok(None),
    }
}

#[tauri::command]
async fn delete_history_run(state: State<'_, AppState>, run_id: String) -> Result<bool, String> {
    match state.engine.storage() {
        Some(storage) => storage
            .delete_run(&RunId::from(run_id.as_str()))
            .await
            .map_err(|e| e.to_string()),
        None => Ok(false),
    }
}

// ── Phase C2: baselines, profiles, evaluation ──────────────────────────
//
// These commands are thin: every decision lives in doctor-core, so the
// desktop shell and any future remote agent behave identically.

/// Machine-readable error payload, so the UI can show *which* expectation
/// or field a profile problem belongs to instead of one opaque string.
#[derive(Serialize)]
struct C2ErrorPayload {
    message: String,
    /// Populated for profile validation failures.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    validation_errors: Vec<ValidationErrorPayload>,
}

#[derive(Serialize)]
struct ValidationErrorPayload {
    code: String,
    path: String,
    message: String,
}

impl From<c2::C2Error> for C2ErrorPayload {
    fn from(err: c2::C2Error) -> Self {
        let validation_errors = match &err {
            c2::C2Error::Profile(doctor_profile::ProfileError::Invalid(errors)) => errors
                .iter()
                .map(|e| ValidationErrorPayload {
                    code: e.code.clone(),
                    path: e.path.clone(),
                    message: e.message.clone(),
                })
                .collect(),
            _ => Vec::new(),
        };
        Self {
            message: err.to_string(),
            validation_errors,
        }
    }
}

fn c2_err(err: c2::C2Error) -> String {
    serde_json::to_string(&C2ErrorPayload::from(err))
        .unwrap_or_else(|e| format!(r#"{{"message":"{e}","validation_errors":[]}}"#))
}

#[tauri::command]
async fn list_baseline_candidates(
    state: State<'_, AppState>,
    limit: Option<u32>,
) -> Result<Vec<CaptureCandidate>, String> {
    c2::baseline_candidates(&state.engine, None, limit.unwrap_or(20))
        .await
        .map_err(c2_err)
}

#[tauri::command]
async fn capture_baseline(
    state: State<'_, AppState>,
    name: String,
    description: String,
    run_ids: Vec<String>,
    manually_accepted: bool,
) -> Result<Baseline, String> {
    let run_ids = run_ids.iter().map(|id| RunId::from(id.as_str())).collect();
    c2::capture_baseline(
        &state.engine,
        name,
        description,
        run_ids,
        manually_accepted,
    )
    .await
    .map_err(c2_err)
}

#[tauri::command]
async fn list_baselines(state: State<'_, AppState>) -> Result<Vec<BaselineSummaryRow>, String> {
    c2::list_baselines(&state.engine, None).await.map_err(c2_err)
}

#[tauri::command]
async fn get_baseline(state: State<'_, AppState>, baseline_id: String) -> Result<Baseline, String> {
    c2::get_baseline(&state.engine, &BaselineId::from(baseline_id.as_str()))
        .await
        .map_err(c2_err)
}

#[tauri::command]
async fn update_baseline(
    state: State<'_, AppState>,
    baseline_id: String,
    name: String,
    description: String,
    tags: Vec<String>,
) -> Result<bool, String> {
    c2::update_baseline_metadata(
        &state.engine,
        BaselineId::from(baseline_id.as_str()),
        name,
        description,
        tags,
    )
    .await
    .map_err(c2_err)
}

#[tauri::command]
async fn delete_baseline(state: State<'_, AppState>, baseline_id: String) -> Result<bool, String> {
    c2::delete_baseline(&state.engine, BaselineId::from(baseline_id.as_str()))
        .await
        .map_err(c2_err)
}

#[tauri::command]
async fn diff_run_against_baseline(
    state: State<'_, AppState>,
    baseline_id: String,
    run_id: String,
) -> Result<BaselineDiff, String> {
    c2::diff_run(
        &state.engine,
        &BaselineId::from(baseline_id.as_str()),
        &RunId::from(run_id.as_str()),
    )
    .await
    .map_err(c2_err)
}

/// Suggestion payload for the draft review screen. Flattened for the UI so
/// it never has to understand the Rust enum shapes.
#[derive(Serialize)]
struct DraftSuggestionPayload {
    expectation: doctor_domain::profile::Expectation,
    selected_by_default: bool,
    rationale: String,
    suggested_threshold: bool,
}

#[derive(Serialize)]
struct DraftReportPayload {
    suggestions: Vec<DraftSuggestionPayload>,
    skipped: Vec<SkippedPayload>,
}

#[derive(Serialize)]
struct SkippedPayload {
    key: String,
    reason: String,
}

#[tauri::command]
async fn draft_profile(
    state: State<'_, AppState>,
    baseline_id: String,
) -> Result<DraftReportPayload, String> {
    let report = c2::draft_from_baseline(&state.engine, &BaselineId::from(baseline_id.as_str()))
        .await
        .map_err(c2_err)?;
    Ok(DraftReportPayload {
        suggestions: report
            .suggestions
            .into_iter()
            .map(|s| DraftSuggestionPayload {
                expectation: s.expectation,
                selected_by_default: s.selected_by_default,
                rationale: s.rationale,
                suggested_threshold: s.suggested_threshold,
            })
            .collect(),
        skipped: report
            .skipped
            .into_iter()
            .map(|s| SkippedPayload {
                key: s.key,
                reason: s.reason,
            })
            .collect(),
    })
}

#[tauri::command]
async fn save_profile(state: State<'_, AppState>, profile: Profile) -> Result<Profile, String> {
    c2::save_profile(&state.engine, profile)
        .await
        .map_err(c2_err)
}

#[tauri::command]
async fn import_profile(
    state: State<'_, AppState>,
    yaml: String,
    profile_id: Option<String>,
) -> Result<Profile, String> {
    c2::import_profile_yaml(
        &state.engine,
        &yaml,
        profile_id.map(|id| ProfileId::from(id.as_str())),
    )
    .await
    .map_err(c2_err)
}

#[tauri::command]
async fn export_profile(
    state: State<'_, AppState>,
    profile_id: String,
    revision: u32,
) -> Result<String, String> {
    c2::export_profile_yaml(
        &state.engine,
        &ProfileId::from(profile_id.as_str()),
        revision,
    )
    .await
    .map_err(c2_err)
}

#[tauri::command]
async fn list_profiles(state: State<'_, AppState>) -> Result<Vec<ProfileRow>, String> {
    c2::list_profiles(&state.engine).await.map_err(c2_err)
}

#[tauri::command]
async fn list_profile_revisions(
    state: State<'_, AppState>,
    profile_id: String,
) -> Result<Vec<ProfileRevisionRow>, String> {
    c2::list_profile_revisions(&state.engine, ProfileId::from(profile_id.as_str()))
        .await
        .map_err(c2_err)
}

#[tauri::command]
async fn get_profile(
    state: State<'_, AppState>,
    profile_id: String,
    revision: u32,
) -> Result<Profile, String> {
    c2::get_profile(
        &state.engine,
        &ProfileId::from(profile_id.as_str()),
        revision,
    )
    .await
    .map_err(c2_err)
}

#[tauri::command]
async fn set_profile_status(
    state: State<'_, AppState>,
    profile_id: String,
    revision: u32,
    status: ProfileStatus,
) -> Result<bool, String> {
    c2::set_profile_status(
        &state.engine,
        ProfileId::from(profile_id.as_str()),
        revision,
        status,
    )
    .await
    .map_err(c2_err)
}

#[tauri::command]
async fn delete_profile_revision(
    state: State<'_, AppState>,
    profile_id: String,
    revision: u32,
) -> Result<bool, String> {
    c2::delete_profile_revision(
        &state.engine,
        ProfileId::from(profile_id.as_str()),
        revision,
    )
    .await
    .map_err(c2_err)
}

#[tauri::command]
async fn activate_profile(
    state: State<'_, AppState>,
    profile_id: String,
    revision: u32,
    runtime_id: Option<String>,
) -> Result<DeviceProfileAssignment, String> {
    c2::assign_profile(
        &state.engine,
        DeviceId::from("local"),
        ProfileId::from(profile_id.as_str()),
        revision,
        runtime_id,
    )
    .await
    .map_err(c2_err)
}

#[tauri::command]
async fn get_active_profile(
    state: State<'_, AppState>,
) -> Result<Option<DeviceProfileAssignment>, String> {
    c2::assignment(&state.engine, DeviceId::from("local"))
        .await
        .map_err(c2_err)
}

#[tauri::command]
async fn get_evaluation(
    state: State<'_, AppState>,
    run_id: String,
) -> Result<Option<EvaluationRun>, String> {
    c2::evaluation_for_run(&state.engine, RunId::from(run_id.as_str()))
        .await
        .map_err(c2_err)
}

/// Re-evaluate a stored run against the currently active profile. The
/// original evaluation is kept — evaluations are history, not a cache.
#[tauri::command]
async fn evaluate_run(
    state: State<'_, AppState>,
    run_id: String,
) -> Result<Option<EvaluationRun>, String> {
    c2::evaluate_run(&state.engine, &RunId::from(run_id.as_str()))
        .await
        .map_err(c2_err)
}

#[tauri::command]
async fn get_comparison(
    state: State<'_, AppState>,
    run_id: String,
    baseline_id: Option<String>,
) -> Result<ComparisonView, String> {
    c2::comparison_view(
        &state.engine,
        &RunId::from(run_id.as_str()),
        baseline_id.map(|id| BaselineId::from(id.as_str())),
    )
    .await
    .map_err(c2_err)
}

fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info,doctor_core=debug,doctor_plugin_host=debug".into()),
        )
        .init();

    tauri::Builder::default()
        .setup(|app| {
            let plugins_dir = locate_plugins_dir();
            tracing::info!("plugins directory: {}", plugins_dir.display());

            // Storage lives in the per-user app data dir. A storage failure
            // is degraded mode (no history), never a startup crash.
            let db_path = app
                .path()
                .app_data_dir()
                .ok()
                .map(|dir| dir.join("history.db"));
            let storage = db_path.as_ref().and_then(|path| {
                match tauri::async_runtime::block_on(Storage::open(path)) {
                    Ok(storage) => Some(storage),
                    Err(err) => {
                        tracing::error!("failed to open history database: {err}");
                        None
                    }
                }
            });
            let engine = Arc::new(match storage {
                Some(storage) => Engine::with_storage(&plugins_dir, storage),
                None => Engine::new(&plugins_dir),
            });
            app.manage(AppState {
                engine,
                plugins_dir,
                db_path,
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            list_devices,
            list_plugins,
            get_app_info,
            get_network_targets,
            set_network_targets,
            get_ros_runtime,
            set_ros_runtime,
            run_single_check,
            run_diagnosis,
            cancel_diagnosis,
            get_run,
            list_history,
            get_history_run,
            delete_history_run,
            list_baseline_candidates,
            capture_baseline,
            list_baselines,
            get_baseline,
            update_baseline,
            delete_baseline,
            diff_run_against_baseline,
            draft_profile,
            save_profile,
            import_profile,
            export_profile,
            list_profiles,
            list_profile_revisions,
            get_profile,
            set_profile_status,
            delete_profile_revision,
            activate_profile,
            get_active_profile,
            get_evaluation,
            evaluate_run,
            get_comparison
        ])
        .run(tauri::generate_context!())
        .expect("error while running Robot Doctor");
}
