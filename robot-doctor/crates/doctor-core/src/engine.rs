//! The diagnosis engine: executes checks concurrently across plugins and
//! streams results progressively.

use crate::registry::{PluginRegistry, RegisteredPlugin};
use crate::store::RunStore;
use chrono::Utc;
use doctor_domain::{
    CheckDefinition, CheckError, CheckRequest, CheckResult, CheckRun, CheckStatus, Device,
    DeviceId, DiagnosticMode, HealthState, Platform, RunId,
};
use doctor_plugin_host::{HostError, PluginErrorKind};
use serde::Serialize;
use std::collections::BTreeMap;
use std::path::Path;
use std::sync::Arc;
use tokio::sync::mpsc;
use tokio::task::JoinSet;

/// Progressive events emitted while a diagnosis runs.
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "type", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum DiagnosisEvent {
    RunStarted {
        run_id: RunId,
        device_id: DeviceId,
        mode: DiagnosticMode,
        /// Checks planned for this run, so the UI can render progress.
        planned: Vec<CheckDefinition>,
    },
    CheckStarted {
        run_id: RunId,
        check_id: doctor_domain::CheckId,
    },
    CheckCompleted {
        run_id: RunId,
        result: CheckResult,
    },
    RunCompleted {
        run_id: RunId,
        health: HealthState,
        finished_at: chrono::DateTime<Utc>,
    },
}

/// The embedded diagnosis engine used by the desktop app (local device)
/// and by the remote agent (same code, different transport).
pub struct Engine {
    registry: PluginRegistry,
    store: Arc<RunStore>,
}

impl Engine {
    /// Create an engine discovering plugins in `plugins_dir`.
    pub fn new(plugins_dir: &Path) -> Self {
        Self {
            registry: PluginRegistry::discover(plugins_dir),
            store: Arc::new(RunStore::new()),
        }
    }

    pub fn registry(&self) -> &PluginRegistry {
        &self.registry
    }

    pub fn store(&self) -> Arc<RunStore> {
        self.store.clone()
    }

    /// The devices this engine can diagnose. Phase A: the local machine.
    pub async fn devices(&self) -> Vec<Device> {
        let mut local = Device::local();
        if let Some(run) = self.store.latest_run(&local.id).await {
            local.health = run.overall_health();
        }
        local.capabilities = self.capability_list().await;
        vec![local]
    }

    async fn capability_list(&self) -> Vec<doctor_domain::Capability> {
        let mut caps = Vec::new();
        for plugin in self.registry.plugins() {
            for c in &plugin.managed.manifest().capabilities {
                caps.push(doctor_domain::Capability {
                    name: c.0.clone(),
                    available: true,
                });
            }
        }
        caps
    }

    /// Start a diagnosis. Returns the run id and a receiver of progressive
    /// events; execution continues in the background.
    pub async fn start_diagnosis(
        self: &Arc<Self>,
        device_id: DeviceId,
        mode: DiagnosticMode,
    ) -> (RunId, mpsc::Receiver<DiagnosisEvent>) {
        let (tx, rx) = mpsc::channel(256);
        let run = CheckRun::new(device_id.clone(), mode);
        let run_id = run.id.clone();
        self.store.insert_run(run).await;

        let engine = self.clone();
        let run_id_bg = run_id.clone();
        tokio::spawn(async move {
            engine.execute_run(run_id_bg, device_id, mode, tx).await;
        });
        (run_id, rx)
    }

    /// Plan the checks for a mode on the current platform.
    async fn plan(&self, mode: DiagnosticMode) -> Vec<(Arc<RegisteredPlugin>, CheckDefinition)> {
        let platform = Platform::current();
        let mut planned = Vec::new();
        for plugin in self.registry.plugins() {
            for def in plugin.check_definitions().await {
                if def.in_mode(mode) && def.supports_platform(platform) {
                    planned.push((plugin.clone(), def));
                }
            }
        }
        planned
    }

    async fn execute_run(
        &self,
        run_id: RunId,
        device_id: DeviceId,
        mode: DiagnosticMode,
        tx: mpsc::Sender<DiagnosisEvent>,
    ) {
        let planned = self.plan(mode).await;
        let _ = tx
            .send(DiagnosisEvent::RunStarted {
                run_id: run_id.clone(),
                device_id: device_id.clone(),
                mode,
                planned: planned.iter().map(|(_, d)| d.clone()).collect(),
            })
            .await;

        let mut tasks: JoinSet<CheckResult> = JoinSet::new();
        for (plugin, def) in planned {
            let device_id = device_id.clone();
            let tx = tx.clone();
            let run_id = run_id.clone();
            tasks.spawn(async move {
                let _ = tx
                    .send(DiagnosisEvent::CheckStarted {
                        run_id,
                        check_id: def.id.clone(),
                    })
                    .await;
                run_one_check(&plugin, &def, &device_id).await
            });
        }

        while let Some(joined) = tasks.join_next().await {
            let result = match joined {
                Ok(result) => result,
                Err(join_err) => {
                    tracing::error!("check task panicked: {join_err}");
                    continue;
                }
            };
            self.store.append_result(&run_id, result.clone()).await;
            let _ = tx
                .send(DiagnosisEvent::CheckCompleted {
                    run_id: run_id.clone(),
                    result,
                })
                .await;
        }

        let finished_at = Utc::now();
        let health = self.store.finish_run(&run_id, finished_at).await;
        let _ = tx
            .send(DiagnosisEvent::RunCompleted {
                run_id,
                health,
                finished_at,
            })
            .await;
    }

    pub async fn shutdown(&self) {
        self.registry.shutdown().await;
    }
}

/// Run one check through its plugin, converting every failure mode into a
/// well-formed `CheckResult` — plugin problems must never crash the engine.
async fn run_one_check(
    plugin: &RegisteredPlugin,
    def: &CheckDefinition,
    device_id: &DeviceId,
) -> CheckResult {
    let started_at = Utc::now();
    let started = std::time::Instant::now();
    let request = CheckRequest {
        check_id: def.id.clone(),
        device_id: device_id.clone(),
        params: BTreeMap::new(),
        timeout_ms: def.timeout_ms,
    };

    let outcome = match plugin.connect().await {
        Ok(handle) => handle.run_check(request).await,
        Err(err) => Err(err),
    };

    match outcome {
        Ok(result) => result,
        Err(err) => {
            let status = status_for_host_error(&err);
            let plugin_error = err.as_plugin_error();
            CheckResult {
                check_id: def.id.clone(),
                plugin_id: def.plugin_id.clone(),
                device_id: device_id.clone(),
                status,
                started_at,
                duration_ms: started.elapsed().as_millis() as u64,
                observations: vec![],
                evidence: vec![],
                findings: vec![],
                error: Some(CheckError {
                    status,
                    message: plugin_error.message,
                }),
            }
        }
    }
}

/// Map host/plugin errors onto the failure-semantics statuses.
fn status_for_host_error(err: &HostError) -> CheckStatus {
    match err {
        HostError::Timeout { .. } => CheckStatus::Timeout,
        HostError::Plugin { error, .. } => match error.kind {
            PluginErrorKind::Unavailable => CheckStatus::Unavailable,
            PluginErrorKind::Unsupported => CheckStatus::Unsupported,
            PluginErrorKind::Timeout => CheckStatus::Timeout,
            PluginErrorKind::PermissionDenied => CheckStatus::PermissionDenied,
            PluginErrorKind::DependencyMissing => CheckStatus::DependencyMissing,
            PluginErrorKind::InvalidRequest
            | PluginErrorKind::Internal
            | PluginErrorKind::ProtocolMismatch => CheckStatus::Error,
        },
        _ => CheckStatus::Error,
    }
}
