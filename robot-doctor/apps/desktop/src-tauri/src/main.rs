//! Robot Doctor desktop shell: a thin Tauri 2 layer over the embedded
//! doctor-core engine. All diagnostics run in the core; the UI receives
//! progressive `diagnosis-event` events and renders real results.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use doctor_core::{Engine, PluginSummary};
use doctor_domain::{CheckRun, Device, DeviceId, DiagnosticMode, Platform, RunId};
use serde::Serialize;
use std::path::PathBuf;
use std::sync::Arc;
use tauri::{Emitter, Manager, State};

struct AppState {
    engine: Arc<Engine>,
    plugins_dir: PathBuf,
}

#[derive(Serialize)]
struct AppInfo {
    version: String,
    platform: String,
    plugins_dir: String,
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
        // Dev: walk up until we find a `plugins/` dir containing manifests.
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
fn list_plugins(state: State<'_, AppState>) -> Vec<PluginSummary> {
    state.engine.registry().summaries()
}

#[tauri::command]
fn get_app_info(state: State<'_, AppState>) -> AppInfo {
    AppInfo {
        version: env!("CARGO_PKG_VERSION").to_owned(),
        platform: format!("{:?}", Platform::current()).to_lowercase(),
        plugins_dir: state.plugins_dir.display().to_string(),
    }
}

#[tauri::command]
async fn run_diagnosis(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    device_id: String,
    mode: DiagnosticMode,
) -> Result<RunId, String> {
    let (run_id, mut rx) = state
        .engine
        .start_diagnosis(DeviceId::from(device_id.as_str()), mode)
        .await;
    // Forward progressive engine events to the UI; the run continues in the
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
async fn get_run(state: State<'_, AppState>, run_id: String) -> Result<Option<CheckRun>, String> {
    Ok(state
        .engine
        .store()
        .get_run(&RunId::from(run_id.as_str()))
        .await)
}

#[tauri::command]
async fn list_runs(state: State<'_, AppState>) -> Result<Vec<CheckRun>, String> {
    Ok(state.engine.store().list_runs().await)
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
            let engine = Arc::new(Engine::new(&plugins_dir));
            app.manage(AppState {
                engine,
                plugins_dir,
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            list_devices,
            list_plugins,
            get_app_info,
            run_diagnosis,
            get_run,
            list_runs
        ])
        .run(tauri::generate_context!())
        .expect("error while running Robot Doctor");
}
