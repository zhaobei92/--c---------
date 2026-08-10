//! The diagnosis engine: plans checks from runtime plugin capabilities,
//! executes them through the dependency-aware scheduler and streams
//! progressive events. Persists runs to storage when configured — a storage
//! failure is surfaced but never stops a diagnosis.

use crate::registry::PluginRegistry;
use crate::scheduler::{self, PlannedCheck, ScheduledOutcome, SchedulerLimits};
use crate::store::RunStore;
use chrono::Utc;
use doctor_domain::{
    CheckDefinition, CheckRun, Device, DeviceId, DiagnosticMode, HealthState, Platform, RunId,
};
use doctor_storage::{PluginSnapshot, Storage};
use serde::Serialize;
use std::collections::{BTreeMap, HashMap};
use std::path::Path;
use std::sync::Arc;
use tokio::sync::{mpsc, Mutex};
use tokio_util::sync::CancellationToken;

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
    PluginStarted {
        run_id: RunId,
        plugin_id: String,
    },
    CheckQueued {
        run_id: RunId,
        check_id: doctor_domain::CheckId,
    },
    CheckStarted {
        run_id: RunId,
        check_id: doctor_domain::CheckId,
    },
    CheckCompleted {
        run_id: RunId,
        result: doctor_domain::CheckResult,
    },
    CheckSkipped {
        run_id: RunId,
        check_id: doctor_domain::CheckId,
        reason: String,
        prerequisite: Option<doctor_domain::CheckId>,
        result: doctor_domain::CheckResult,
    },
    PluginCompleted {
        run_id: RunId,
        plugin_id: String,
    },
    RunCompleted {
        run_id: RunId,
        health: HealthState,
        finished_at: chrono::DateTime<Utc>,
        /// False when persistence failed — the run exists only in memory.
        persisted: bool,
    },
}

/// The embedded diagnosis engine used by the desktop app (local device)
/// and by the remote agent (same code, different transport).
pub struct Engine {
    registry: PluginRegistry,
    store: Arc<RunStore>,
    storage: Option<Storage>,
    app_version: String,
    limits: SchedulerLimits,
    active: Mutex<HashMap<RunId, CancellationToken>>,
}

impl Engine {
    /// Engine without persistence (tests, ephemeral tools).
    pub fn new(plugins_dir: &Path) -> Self {
        Self::build(plugins_dir, None)
    }

    /// Engine persisting diagnostic history to `storage`.
    pub fn with_storage(plugins_dir: &Path, storage: Storage) -> Self {
        Self::build(plugins_dir, Some(storage))
    }

    fn build(plugins_dir: &Path, storage: Option<Storage>) -> Self {
        Self {
            registry: PluginRegistry::discover(plugins_dir),
            store: Arc::new(RunStore::new()),
            storage,
            app_version: env!("CARGO_PKG_VERSION").to_owned(),
            limits: SchedulerLimits::default(),
            active: Mutex::new(HashMap::new()),
        }
    }

    pub fn registry(&self) -> &PluginRegistry {
        &self.registry
    }

    pub fn store(&self) -> Arc<RunStore> {
        self.store.clone()
    }

    pub fn storage(&self) -> Option<&Storage> {
        self.storage.as_ref()
    }

    /// The devices this engine can diagnose. Phase B: the local machine.
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
            if let Some(negotiated) = plugin.cached_capabilities().await {
                for c in &negotiated.capabilities {
                    caps.push(doctor_domain::Capability {
                        name: c.0.clone(),
                        available: true,
                    });
                }
            }
        }
        caps
    }

    /// Execute one check immediately (outside a diagnostic run) — used for
    /// explicitly user-triggered operations like SAMPLE TOPIC, TF queries
    /// and runtime discovery. Bounded by the check's declared timeout.
    pub async fn run_check_now(
        &self,
        device_id: DeviceId,
        check_id: &str,
        params: serde_json::Value,
    ) -> Result<doctor_domain::CheckResult, String> {
        for plugin in self.registry.plugins() {
            let defs = match plugin.check_definitions().await {
                Ok(defs) => defs,
                Err(_) => continue,
            };
            if let Some(def) = defs.iter().find(|d| d.id.as_str() == check_id) {
                let request = doctor_domain::CheckRequest {
                    check_id: def.id.clone(),
                    device_id,
                    mode: Some(DiagnosticMode::Full),
                    params: params
                        .as_object()
                        .map(|m| m.clone().into_iter().collect())
                        .unwrap_or_default(),
                    timeout_ms: def.timeout_ms,
                };
                let (handle, _) = plugin.connect().await.map_err(|e| e.to_string())?;
                return match handle.run_check(request).await {
                    Ok(result) => Ok(result),
                    Err(err) => {
                        let status = crate::scheduler::status_for_host_error(&err);
                        Ok(doctor_domain::CheckResult {
                            check_id: def.id.clone(),
                            plugin_id: def.plugin_id.clone(),
                            device_id: DeviceId::from("local"),
                            status,
                            started_at: Utc::now(),
                            duration_ms: 0,
                            observations: vec![],
                            evidence: vec![],
                            findings: vec![],
                            error: Some(doctor_domain::CheckError {
                                status,
                                message: err.to_string(),
                            }),
                            projection: None,
                        })
                    }
                };
            }
        }
        Err(format!("no plugin provides check '{check_id}'"))
    }

    /// Cancel a running diagnosis (idempotent).
    pub async fn cancel_run(&self, run_id: &RunId) {
        if let Some(token) = self.active.lock().await.get(run_id) {
            token.cancel();
        }
    }

    /// Start a diagnosis. Returns the run id and a receiver of progressive
    /// events; execution continues in the background.
    pub async fn start_diagnosis(
        self: &Arc<Self>,
        device_id: DeviceId,
        mode: DiagnosticMode,
    ) -> (RunId, mpsc::Receiver<DiagnosisEvent>) {
        self.start_diagnosis_with_params(device_id, mode, BTreeMap::new())
            .await
    }

    /// Start a diagnosis with per-check parameters
    /// (`check id → params object`, e.g. network targets).
    pub async fn start_diagnosis_with_params(
        self: &Arc<Self>,
        device_id: DeviceId,
        mode: DiagnosticMode,
        params: BTreeMap<String, serde_json::Value>,
    ) -> (RunId, mpsc::Receiver<DiagnosisEvent>) {
        let (tx, rx) = mpsc::channel(512);
        let run = CheckRun::new(device_id.clone(), mode);
        let run_id = run.id.clone();
        self.store.insert_run(run).await;

        let token = CancellationToken::new();
        self.active
            .lock()
            .await
            .insert(run_id.clone(), token.clone());

        let engine = self.clone();
        let run_id_bg = run_id.clone();
        tokio::spawn(async move {
            engine
                .execute_run(run_id_bg.clone(), device_id, mode, params, token, tx)
                .await;
            engine.active.lock().await.remove(&run_id_bg);
        });
        (run_id, rx)
    }

    /// Plan the checks for a mode on the current platform from runtime
    /// plugin capabilities. Plugins that fail to start are skipped (their
    /// error is visible in plugin summaries).
    async fn plan(&self, mode: DiagnosticMode) -> (Vec<PlannedCheck>, Vec<PluginSnapshot>) {
        let platform = Platform::current();
        let mut planned = Vec::new();
        let mut snapshots = Vec::new();
        for plugin in self.registry.plugins() {
            match plugin.check_definitions().await {
                Ok(defs) => {
                    let caps = plugin.cached_capabilities().await;
                    snapshots.push(PluginSnapshot {
                        plugin_id: plugin.id().to_string(),
                        version: plugin.managed.manifest().version.clone(),
                        api_version: plugin.managed.manifest().api_version,
                        capabilities: caps
                            .map(|c| c.capabilities.iter().map(|x| x.0.clone()).collect())
                            .unwrap_or_default(),
                    });
                    for def in defs {
                        if def.in_mode(mode) && def.supports_platform(platform) {
                            planned.push(PlannedCheck {
                                plugin: plugin.clone(),
                                definition: def,
                            });
                        }
                    }
                }
                Err(err) => {
                    tracing::warn!(plugin = %plugin.id(), "plugin unavailable for this run: {err}");
                }
            }
        }
        (planned, snapshots)
    }

    async fn execute_run(
        &self,
        run_id: RunId,
        device_id: DeviceId,
        mode: DiagnosticMode,
        params: BTreeMap<String, serde_json::Value>,
        cancel: CancellationToken,
        tx: mpsc::Sender<DiagnosisEvent>,
    ) {
        let (planned, snapshots) = self.plan(mode).await;
        let planned_defs: Vec<CheckDefinition> =
            planned.iter().map(|p| p.definition.clone()).collect();
        let _ = tx
            .send(DiagnosisEvent::RunStarted {
                run_id: run_id.clone(),
                device_id: device_id.clone(),
                mode,
                planned: planned_defs.clone(),
            })
            .await;
        for def in &planned_defs {
            let _ = tx
                .send(DiagnosisEvent::CheckQueued {
                    run_id: run_id.clone(),
                    check_id: def.id.clone(),
                })
                .await;
        }
        let mut planned_plugins: Vec<String> = planned_defs
            .iter()
            .map(|d| d.plugin_id.to_string())
            .collect();
        planned_plugins.sort();
        planned_plugins.dedup();
        for plugin_id in &planned_plugins {
            let _ = tx
                .send(DiagnosisEvent::PluginStarted {
                    run_id: run_id.clone(),
                    plugin_id: plugin_id.clone(),
                })
                .await;
        }

        // Persist run header (failure is surfaced, not fatal).
        let mut persisted = true;
        if let Some(storage) = &self.storage {
            let run = self.store.get_run(&run_id).await.expect("run inserted");
            if let Err(err) = storage.upsert_device(Device::local()).await {
                tracing::error!("storage: device upsert failed: {err}");
            }
            if let Err(err) = storage.begin_run(&run, &self.app_version, snapshots).await {
                tracing::error!("storage: begin_run failed: {err}");
                persisted = false;
            }
        }

        // Track per-plugin outstanding counts for PluginCompleted events.
        let mut outstanding: HashMap<String, usize> = HashMap::new();
        for def in &planned_defs {
            *outstanding.entry(def.plugin_id.to_string()).or_insert(0) += 1;
        }

        let (sched_tx, mut sched_rx) = mpsc::channel::<ScheduledOutcome>(256);
        let params = Arc::new(params);
        let limits = self.limits.clone();
        let sched_device = device_id.clone();
        let sched_cancel = cancel.clone();
        tokio::spawn(async move {
            scheduler::execute(
                planned,
                sched_device,
                mode,
                params,
                limits,
                sched_cancel,
                sched_tx,
            )
            .await;
        });

        while let Some(outcome) = sched_rx.recv().await {
            match outcome {
                ScheduledOutcome::Started(check_id) => {
                    let _ = tx
                        .send(DiagnosisEvent::CheckStarted {
                            run_id: run_id.clone(),
                            check_id,
                        })
                        .await;
                }
                ScheduledOutcome::Completed(result) => {
                    let result = *result;
                    self.record_result(&run_id, &result, &mut persisted).await;
                    self.note_plugin_done(
                        &run_id,
                        &result.plugin_id.to_string(),
                        &mut outstanding,
                        &tx,
                    )
                    .await;
                    let _ = tx
                        .send(DiagnosisEvent::CheckCompleted {
                            run_id: run_id.clone(),
                            result,
                        })
                        .await;
                }
                ScheduledOutcome::Skipped {
                    check_id,
                    reason,
                    prerequisite,
                    result,
                } => {
                    let result = *result;
                    self.record_result(&run_id, &result, &mut persisted).await;
                    self.note_plugin_done(
                        &run_id,
                        &result.plugin_id.to_string(),
                        &mut outstanding,
                        &tx,
                    )
                    .await;
                    let _ = tx
                        .send(DiagnosisEvent::CheckSkipped {
                            run_id: run_id.clone(),
                            check_id,
                            reason,
                            prerequisite,
                            result,
                        })
                        .await;
                }
            }
        }

        let finished_at = Utc::now();
        let health = self.store.finish_run(&run_id, finished_at).await;
        if let Some(storage) = &self.storage {
            if let Err(err) = storage.finish_run(&run_id, health, finished_at).await {
                tracing::error!("storage: finish_run failed: {err}");
                persisted = false;
            }
        }
        let _ = tx
            .send(DiagnosisEvent::RunCompleted {
                run_id,
                health,
                finished_at,
                persisted,
            })
            .await;
    }

    async fn record_result(
        &self,
        run_id: &RunId,
        result: &doctor_domain::CheckResult,
        persisted: &mut bool,
    ) {
        self.store.append_result(run_id, result.clone()).await;
        if let Some(storage) = &self.storage {
            if let Err(err) = storage.append_result(run_id, result.clone()).await {
                tracing::error!("storage: append_result failed: {err}");
                *persisted = false;
            }
        }
    }

    async fn note_plugin_done(
        &self,
        run_id: &RunId,
        plugin_id: &str,
        outstanding: &mut HashMap<String, usize>,
        tx: &mpsc::Sender<DiagnosisEvent>,
    ) {
        if let Some(count) = outstanding.get_mut(plugin_id) {
            *count -= 1;
            if *count == 0 {
                let _ = tx
                    .send(DiagnosisEvent::PluginCompleted {
                        run_id: run_id.clone(),
                        plugin_id: plugin_id.to_owned(),
                    })
                    .await;
            }
        }
    }

    pub async fn shutdown(&self) {
        self.registry.shutdown().await;
    }
}
