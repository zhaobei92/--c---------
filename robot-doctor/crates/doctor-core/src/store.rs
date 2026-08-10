//! In-memory run store. Phase B replaces the persistence side with SQLite;
//! the API is already shaped so the engine does not care where runs live.

use chrono::{DateTime, Utc};
use doctor_domain::{CheckResult, CheckRun, DeviceId, HealthState, RunId};
use std::collections::HashMap;
use tokio::sync::RwLock;

#[derive(Default)]
pub struct RunStore {
    runs: RwLock<HashMap<RunId, CheckRun>>,
    /// Insertion order for history listing (newest last).
    order: RwLock<Vec<RunId>>,
}

impl RunStore {
    pub fn new() -> Self {
        Self::default()
    }

    pub async fn insert_run(&self, run: CheckRun) {
        let id = run.id.clone();
        self.runs.write().await.insert(id.clone(), run);
        self.order.write().await.push(id);
    }

    pub async fn append_result(&self, run_id: &RunId, result: CheckResult) {
        if let Some(run) = self.runs.write().await.get_mut(run_id) {
            run.results.push(result);
        }
    }

    /// Mark a run finished and return its overall health.
    pub async fn finish_run(&self, run_id: &RunId, finished_at: DateTime<Utc>) -> HealthState {
        let mut runs = self.runs.write().await;
        match runs.get_mut(run_id) {
            Some(run) => {
                run.finished_at = Some(finished_at);
                run.overall_health()
            }
            None => HealthState::Unknown,
        }
    }

    pub async fn get_run(&self, run_id: &RunId) -> Option<CheckRun> {
        self.runs.read().await.get(run_id).cloned()
    }

    /// Most recent run for a device, if any.
    pub async fn latest_run(&self, device_id: &DeviceId) -> Option<CheckRun> {
        let order = self.order.read().await;
        let runs = self.runs.read().await;
        order
            .iter()
            .rev()
            .filter_map(|id| runs.get(id))
            .find(|run| &run.device_id == device_id)
            .cloned()
    }

    /// All runs, newest first.
    pub async fn list_runs(&self) -> Vec<CheckRun> {
        let order = self.order.read().await;
        let runs = self.runs.read().await;
        order
            .iter()
            .rev()
            .filter_map(|id| runs.get(id))
            .cloned()
            .collect()
    }
}
