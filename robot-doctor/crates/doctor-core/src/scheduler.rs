//! Scheduler v2: dependency-aware, concurrency-bounded check execution.
//!
//! - Checks form a DAG (validated; cycles rejected with explicit results).
//! - Independent checks run concurrently, bounded by a global limit and a
//!   per-plugin limit (the plugin's negotiated `max_concurrency`).
//! - A prerequisite that does not PASS suppresses its dependents with an
//!   explicit DEPENDENCY_MISSING result — no cascade of secondary noise.
//! - Cancellation stops scheduling new checks; already-running checks are
//!   given up on with CANCELLED results.

use crate::registry::RegisteredPlugin;
use chrono::Utc;
use doctor_domain::{
    CheckDefinition, CheckError, CheckId, CheckRequest, CheckResult, CheckStatus, DeviceId,
    DiagnosticMode,
};
use doctor_plugin_host::{HostError, PluginErrorKind, PluginHandle};
use std::collections::{BTreeMap, HashMap, HashSet, VecDeque};
use std::sync::Arc;
use tokio::sync::{mpsc, Semaphore};
use tokio_util::sync::CancellationToken;

/// Where a scheduled check ended up.
#[derive(Debug, Clone)]
pub enum ScheduledOutcome {
    Started(CheckId),
    Completed(Box<CheckResult>),
    /// Not executed: prerequisite failed / cycle / cancelled before start.
    Skipped {
        check_id: CheckId,
        reason: String,
        prerequisite: Option<CheckId>,
        result: Box<CheckResult>,
    },
}

/// One plannable unit.
pub struct PlannedCheck {
    pub plugin: Arc<RegisteredPlugin>,
    pub definition: CheckDefinition,
}

/// Global scheduler limits.
#[derive(Debug, Clone)]
pub struct SchedulerLimits {
    pub global_concurrency: usize,
}

impl Default for SchedulerLimits {
    fn default() -> Self {
        Self {
            global_concurrency: 8,
        }
    }
}

fn synthetic_result(
    def: &CheckDefinition,
    device_id: &DeviceId,
    status: CheckStatus,
    message: String,
) -> CheckResult {
    CheckResult {
        check_id: def.id.clone(),
        plugin_id: def.plugin_id.clone(),
        device_id: device_id.clone(),
        status,
        started_at: Utc::now(),
        duration_ms: 0,
        observations: vec![],
        evidence: vec![],
        findings: vec![],
        error: Some(CheckError { status, message }),
    }
}

/// Validate the dependency graph. Returns (executable ids in no particular
/// order, checks rejected because of cycles or self-references).
fn validate_dag(defs: &HashMap<CheckId, &CheckDefinition>) -> (HashSet<CheckId>, HashSet<CheckId>) {
    // Kahn's algorithm over edges that stay inside the planned set;
    // dependencies pointing outside the plan are ignored (logged upstream).
    let mut indegree: HashMap<&CheckId, usize> = HashMap::new();
    let mut dependents: HashMap<&CheckId, Vec<&CheckId>> = HashMap::new();
    for (id, def) in defs {
        indegree.entry(id).or_insert(0);
        for dep in &def.depends_on {
            if defs.contains_key(dep) && dep != id {
                *indegree.entry(id).or_insert(0) += 1;
                dependents.entry(dep).or_default().push(id);
            }
            if dep == id {
                // Self-dependency is a cycle of length 1.
                *indegree.entry(id).or_insert(0) += 1;
            }
        }
    }
    let mut queue: VecDeque<&CheckId> = indegree
        .iter()
        .filter(|(_, d)| **d == 0)
        .map(|(id, _)| *id)
        .collect();
    let mut ordered: HashSet<CheckId> = HashSet::new();
    while let Some(id) = queue.pop_front() {
        ordered.insert(id.clone());
        for dependent in dependents.get(id).into_iter().flatten() {
            let d = indegree.get_mut(*dependent).expect("known node");
            *d -= 1;
            if *d == 0 {
                queue.push_back(dependent);
            }
        }
    }
    let cyclic: HashSet<CheckId> = defs
        .keys()
        .filter(|id| !ordered.contains(*id))
        .cloned()
        .collect();
    (ordered, cyclic)
}

/// Execute a planned set of checks, streaming outcomes through `out`.
///
/// `params_for` supplies per-check parameters (targets etc.).
#[allow(clippy::too_many_arguments)]
pub async fn execute(
    planned: Vec<PlannedCheck>,
    device_id: DeviceId,
    mode: DiagnosticMode,
    params_for: Arc<BTreeMap<String, serde_json::Value>>,
    limits: SchedulerLimits,
    cancel: CancellationToken,
    out: mpsc::Sender<ScheduledOutcome>,
) {
    let defs_owned: HashMap<CheckId, CheckDefinition> = planned
        .iter()
        .map(|p| (p.definition.id.clone(), p.definition.clone()))
        .collect();
    let plugins: HashMap<CheckId, Arc<RegisteredPlugin>> = planned
        .iter()
        .map(|p| (p.definition.id.clone(), p.plugin.clone()))
        .collect();
    let defs_ref: HashMap<CheckId, &CheckDefinition> =
        defs_owned.iter().map(|(k, v)| (k.clone(), v)).collect();

    // DAG validation.
    let (executable, cyclic) = validate_dag(&defs_ref);
    for id in &cyclic {
        let def = &defs_owned[id];
        let result = synthetic_result(
            def,
            &device_id,
            CheckStatus::Error,
            "dependency cycle detected — check cannot be scheduled".to_owned(),
        );
        let _ = out
            .send(ScheduledOutcome::Skipped {
                check_id: id.clone(),
                reason: "dependency cycle".to_owned(),
                prerequisite: None,
                result: Box::new(result),
            })
            .await;
    }

    // Per-plugin semaphores from negotiated max_concurrency.
    let mut plugin_limits: HashMap<String, Arc<Semaphore>> = HashMap::new();
    for p in &planned {
        let key = p.plugin.id().to_string();
        if let std::collections::hash_map::Entry::Vacant(entry) = plugin_limits.entry(key) {
            let max = p
                .plugin
                .cached_capabilities()
                .await
                .map(|c| c.max_concurrency.max(1) as usize)
                .unwrap_or(1);
            entry.insert(Arc::new(Semaphore::new(max)));
        }
    }
    let global = Arc::new(Semaphore::new(limits.global_concurrency.max(1)));

    // Dependency bookkeeping (edges inside the executable set only).
    let mut waiting_on: HashMap<CheckId, HashSet<CheckId>> = HashMap::new();
    let mut dependents: HashMap<CheckId, Vec<CheckId>> = HashMap::new();
    for id in &executable {
        let def = &defs_owned[id];
        let deps: HashSet<CheckId> = def
            .depends_on
            .iter()
            .filter(|d| executable.contains(*d))
            .cloned()
            .collect();
        for dep in &deps {
            dependents.entry(dep.clone()).or_default().push(id.clone());
        }
        waiting_on.insert(id.clone(), deps);
    }

    let (done_tx, mut done_rx) = mpsc::channel::<(CheckId, CheckResult)>(64);
    let mut remaining = executable.len();
    let mut passed: HashSet<CheckId> = HashSet::new();
    let mut finished: HashSet<CheckId> = HashSet::new();

    // Launch every check whose dependencies are already satisfied.
    let launch = |id: &CheckId,
                  defs: &HashMap<CheckId, CheckDefinition>,
                  plugins: &HashMap<CheckId, Arc<RegisteredPlugin>>| {
        let def = defs[id].clone();
        let plugin = plugins[id].clone();
        let device_id = device_id.clone();
        let global = global.clone();
        let per_plugin = plugin_limits[&def.plugin_id.to_string()].clone();
        let done_tx = done_tx.clone();
        let out = out.clone();
        let cancel = cancel.clone();
        let params = params_for.clone();
        tokio::spawn(async move {
            let _global_permit = global.acquire_owned().await.expect("semaphore open");
            let _plugin_permit = per_plugin.acquire_owned().await.expect("semaphore open");
            if cancel.is_cancelled() {
                let result = synthetic_result(
                    &def,
                    &device_id,
                    CheckStatus::Cancelled,
                    "run cancelled before this check started".to_owned(),
                );
                let _ = done_tx.send((def.id.clone(), result)).await;
                return;
            }
            let _ = out.send(ScheduledOutcome::Started(def.id.clone())).await;
            let result = tokio::select! {
                result = run_one(&plugin, &def, &device_id, mode, &params) => result,
                _ = cancel.cancelled() => synthetic_result(
                    &def,
                    &device_id,
                    CheckStatus::Cancelled,
                    "run cancelled while this check was executing".to_owned(),
                ),
            };
            let _ = done_tx.send((def.id.clone(), result)).await;
        });
    };

    let ready: Vec<CheckId> = waiting_on
        .iter()
        .filter(|(_, deps)| deps.is_empty())
        .map(|(id, _)| id.clone())
        .collect();
    for id in ready {
        launch(&id, &defs_owned, &plugins);
    }

    while remaining > 0 {
        let Some((check_id, result)) = done_rx.recv().await else {
            break;
        };
        remaining -= 1;
        finished.insert(check_id.clone());
        let check_passed = result.status == CheckStatus::Passed;
        if check_passed {
            passed.insert(check_id.clone());
        }
        let _ = out
            .send(ScheduledOutcome::Completed(Box::new(result)))
            .await;

        // Resolve dependents: launch when all deps passed; suppress
        // (recursively) when a dep finished without passing.
        let mut to_skip: VecDeque<(CheckId, CheckId)> = VecDeque::new(); // (check, failed prereq)
        for dependent in dependents.get(&check_id).cloned().unwrap_or_default() {
            let Some(deps) = waiting_on.get_mut(&dependent) else {
                continue;
            };
            deps.remove(&check_id);
            if !check_passed {
                to_skip.push_back((dependent.clone(), check_id.clone()));
            } else if deps.is_empty() && !finished.contains(&dependent) {
                launch(&dependent, &defs_owned, &plugins);
            }
        }
        while let Some((skip_id, prereq)) = to_skip.pop_front() {
            if finished.contains(&skip_id) {
                continue;
            }
            finished.insert(skip_id.clone());
            waiting_on.remove(&skip_id);
            remaining -= 1;
            let def = &defs_owned[&skip_id];
            let result = synthetic_result(
                def,
                &device_id,
                CheckStatus::DependencyMissing,
                format!("prerequisite check '{prereq}' did not pass — skipped"),
            );
            let _ = out
                .send(ScheduledOutcome::Skipped {
                    check_id: skip_id.clone(),
                    reason: format!("prerequisite '{prereq}' did not pass"),
                    prerequisite: Some(prereq.clone()),
                    result: Box::new(result),
                })
                .await;
            for dependent in dependents.get(&skip_id).cloned().unwrap_or_default() {
                to_skip.push_back((dependent, skip_id.clone()));
            }
        }
    }
}

/// Run one check through its plugin, converting every failure mode into a
/// well-formed `CheckResult` — plugin problems must never crash the engine.
async fn run_one(
    plugin: &RegisteredPlugin,
    def: &CheckDefinition,
    device_id: &DeviceId,
    mode: DiagnosticMode,
    params_for: &BTreeMap<String, serde_json::Value>,
) -> CheckResult {
    let started_at = Utc::now();
    let started = std::time::Instant::now();
    let params: BTreeMap<String, serde_json::Value> = params_for
        .get(def.id.as_str())
        .and_then(|v| v.as_object().cloned())
        .map(|m| m.into_iter().collect())
        .unwrap_or_default();
    let request = CheckRequest {
        check_id: def.id.clone(),
        device_id: device_id.clone(),
        mode: Some(mode),
        params,
        timeout_ms: def.timeout_ms,
    };

    let outcome = match plugin.connect().await {
        Ok((handle, _)) => run_via(&handle, request).await,
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

async fn run_via(handle: &PluginHandle, request: CheckRequest) -> Result<CheckResult, HostError> {
    handle.run_check(request).await
}

/// Map host/plugin errors onto the failure-semantics statuses.
pub fn status_for_host_error(err: &HostError) -> CheckStatus {
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
