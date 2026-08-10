//! Robot Doctor desktop shell: a thin Tauri 2 layer over the embedded
//! doctor-core engine. All diagnostics run in the core; the UI receives
//! progressive `diagnosis-event` events and renders real results. History
//! is served from SQLite through the storage service — never rebuilt by
//! re-running checks.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use doctor_core::{Engine, PluginSummary};
use doctor_domain::{CheckResult, CheckRun, Device, DeviceId, DiagnosticMode, Platform, RunId};
use doctor_storage::{RunFilter, RunSummaryRow, Storage, StoredRun};
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
            delete_history_run
        ])
        .run(tauri::generate_context!())
        .expect("error while running Robot Doctor");
}
