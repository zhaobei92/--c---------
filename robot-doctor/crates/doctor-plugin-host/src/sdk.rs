//! Plugin-side SDK for Rust plugins: a stdio run loop that speaks the
//! JSONL protocol. Plugins in other languages implement the same wire
//! format directly (see `protocol.rs`).
//!
//! Concurrency model: the stdin reader dispatches CHECK/ACTION requests to
//! worker threads (bounded by [`PluginService::max_concurrency`]), so a slow
//! check cannot head-of-line-block a fast one. All responses go through one
//! synchronized stdout writer, so concurrent tasks never interleave bytes.
//! Lifecycle messages (HELLO/CAPABILITIES/PING/STOP) are answered inline.

use crate::protocol::{HostMessage, PluginError, PluginErrorKind, PluginMessage, PROTOCOL_VERSION};
use doctor_domain::{
    ActionDeclaration, ActionResult, ActionStatus, CheckDeclaration, CheckRequest, CheckResult,
    PluginCapability,
};
use std::collections::{BTreeMap, HashSet};
use std::io::{BufRead, Write};
use std::sync::{Arc, Condvar, Mutex};

/// Implemented by a plugin binary. Methods take `&self` because checks may
/// run concurrently; keep per-check state local or behind interior
/// mutability. Timeouts are enforced host-side.
pub trait PluginService: Send + Sync + 'static {
    fn plugin_id(&self) -> String;
    fn plugin_version(&self) -> String;
    fn api_version(&self) -> u32 {
        PROTOCOL_VERSION
    }
    /// How many requests this plugin can safely process in parallel.
    /// 1 (default) means strictly sequential.
    fn max_concurrency(&self) -> u32 {
        1
    }
    fn capabilities(&self) -> ServiceCapabilities;
    fn run_check(&self, request: &CheckRequest) -> CheckResult;
    /// Default: actions unavailable.
    fn run_action(
        &self,
        action_id: &str,
        _device_id: &str,
        _params: &BTreeMap<String, serde_json::Value>,
    ) -> Result<ActionResult, PluginError> {
        let _ = ActionStatus::Unavailable;
        Err(PluginError {
            kind: PluginErrorKind::Unavailable,
            message: format!("action '{action_id}' is not implemented by this plugin"),
        })
    }
}

#[derive(Default)]
pub struct ServiceCapabilities {
    pub capabilities: Vec<PluginCapability>,
    pub checks: Vec<CheckDeclaration>,
    pub actions: Vec<ActionDeclaration>,
    /// Whether this plugin emits baseline-comparison projections
    /// (Phase C2). Plugins without it stay valid; their namespaces are
    /// simply UNSUPPORTED for semantic comparison.
    pub baseline_projection: bool,
}

/// Collector used by plugin check implementations: accumulates evidence,
/// observations and findings, then finishes into a well-formed
/// [`CheckResult`]. Keeps every plugin's output shape identical.
pub struct CheckContext {
    pub check_id: doctor_domain::CheckId,
    pub device_id: doctor_domain::DeviceId,
    plugin_id: doctor_domain::PluginId,
    started_at: chrono::DateTime<chrono::Utc>,
    started: std::time::Instant,
    observations: Vec<doctor_domain::Observation>,
    evidence: Vec<doctor_domain::Evidence>,
    findings: Vec<doctor_domain::Finding>,
    projection: Option<doctor_domain::ProjectionReport>,
}

impl CheckContext {
    pub fn new(plugin_id: &str, request: &CheckRequest) -> Self {
        Self {
            check_id: request.check_id.clone(),
            device_id: request.device_id.clone(),
            plugin_id: doctor_domain::PluginId::from(plugin_id),
            started_at: chrono::Utc::now(),
            started: std::time::Instant::now(),
            observations: Vec::new(),
            evidence: Vec::new(),
            findings: Vec::new(),
            projection: None,
        }
    }

    /// Build a comparison entity attributed to this check (Phase C2).
    pub fn entity(
        &self,
        namespace: &str,
        kind: &str,
        key: impl Into<String>,
        display_name: impl Into<String>,
    ) -> doctor_domain::ComparisonEntity {
        doctor_domain::ComparisonEntity::new(
            doctor_domain::EntityKey::new(namespace, kind, key),
            display_name,
            &self.plugin_id,
            &self.check_id,
        )
    }

    /// Record that this check authoritatively observed `namespace`. An
    /// empty entity list then means "genuinely nothing there", which
    /// downstream may evaluate as UNSATISFIED.
    pub fn observed(&mut self, namespace: &str, entities: Vec<doctor_domain::ComparisonEntity>) {
        self.projection = Some(doctor_domain::ProjectionReport::observed(
            namespace, entities,
        ));
    }

    /// Record an authoritative observation of specific entity *kinds*.
    ///
    /// Prefer this over [`CheckContext::observed`] whenever an empty
    /// result is itself a fact — a machine with no GPU must still declare
    /// that `gpu` was looked at, or the expectation would be UNKNOWN
    /// instead of UNSATISFIED.
    pub fn observed_kinds(
        &mut self,
        namespace: &str,
        kinds: &[&str],
        entities: Vec<doctor_domain::ComparisonEntity>,
    ) {
        self.projection = Some(doctor_domain::ProjectionReport::observed_kinds(
            namespace,
            kinds.iter().copied(),
            entities,
        ));
    }

    /// Record that this check could *not* observe `namespace`, so absence
    /// proves nothing and expectations must evaluate to UNKNOWN.
    pub fn not_observed(&mut self, namespace: &str, reason: impl Into<String>) {
        self.projection = Some(doctor_domain::ProjectionReport::not_observed(
            namespace, reason,
        ));
    }

    /// Record that this check could not observe specific kinds, leaving
    /// the rest of the namespace to other checks.
    pub fn not_observed_kinds(
        &mut self,
        namespace: &str,
        kinds: &[&str],
        reason: impl Into<String>,
    ) {
        self.projection = Some(doctor_domain::ProjectionReport::not_observed_kinds(
            namespace,
            kinds.iter().copied(),
            reason,
        ));
    }

    pub fn evidence(
        &mut self,
        kind: doctor_domain::EvidenceKind,
        source: &str,
        summary: &str,
        data: serde_json::Value,
    ) -> doctor_domain::EvidenceId {
        let ev = doctor_domain::Evidence::new(kind, source, summary, data);
        let id = ev.id.clone();
        self.evidence.push(ev);
        id
    }

    pub fn number(&mut self, key: &str, value: f64, unit: &str, ev: &doctor_domain::EvidenceId) {
        self.observations.push(doctor_domain::Observation::number(
            &self.check_id,
            key,
            value,
            unit,
            vec![ev.clone()],
        ));
    }

    pub fn text(&mut self, key: &str, value: impl Into<String>, ev: &doctor_domain::EvidenceId) {
        self.observations.push(doctor_domain::Observation::text(
            &self.check_id,
            key,
            value,
            vec![ev.clone()],
        ));
    }

    pub fn json(&mut self, key: &str, value: serde_json::Value, ev: &doctor_domain::EvidenceId) {
        self.observations.push(doctor_domain::Observation::json(
            &self.check_id,
            key,
            value,
            vec![ev.clone()],
        ));
    }

    pub fn finding(
        &mut self,
        code: &str,
        severity: doctor_domain::Severity,
        subject: impl Into<String>,
        title: impl Into<String>,
        detail: impl Into<String>,
        evidence: Vec<doctor_domain::EvidenceId>,
    ) {
        self.findings.push(doctor_domain::Finding {
            id: doctor_domain::FindingId::generate(),
            device_id: self.device_id.clone(),
            check_id: self.check_id.clone(),
            rule_id: Some(doctor_domain::RuleId::from(code)),
            severity,
            code: code.to_owned(),
            title: title.into(),
            detail: detail.into(),
            subject: subject.into(),
            evidence_ids: evidence,
            detected_at: chrono::Utc::now(),
        });
    }

    pub fn has_findings(&self) -> bool {
        !self.findings.is_empty()
    }

    /// Observations collected so far — plugins build their comparison
    /// projection from the same normalized values the UI sees.
    pub fn observations(&self) -> &[doctor_domain::Observation] {
        &self.observations
    }

    /// Evidence ids collected so far, for attaching provenance to
    /// projected entities.
    pub fn evidence_ids(&self) -> Vec<doctor_domain::EvidenceId> {
        self.evidence.iter().map(|e| e.id.clone()).collect()
    }

    /// Build the final result. For non-evaluated statuses pass a message
    /// explaining why the check could not run.
    pub fn finish(
        self,
        plugin_id: &str,
        status: doctor_domain::CheckStatus,
        error_message: Option<String>,
    ) -> CheckResult {
        let error = if status.evaluated() {
            None
        } else {
            Some(doctor_domain::CheckError {
                status,
                message: error_message
                    .unwrap_or_else(|| "check could not evaluate its subject".to_owned()),
            })
        };
        CheckResult {
            check_id: self.check_id,
            plugin_id: doctor_domain::PluginId::from(plugin_id),
            device_id: self.device_id,
            status,
            started_at: self.started_at,
            duration_ms: self.started.elapsed().as_millis() as u64,
            observations: self.observations,
            evidence: self.evidence,
            findings: self.findings,
            error,
            projection: self.projection,
        }
    }
}

/// Serialized writes: one JSON line at a time, flushed.
#[derive(Clone)]
struct StdoutWriter {
    inner: Arc<Mutex<std::io::Stdout>>,
}

impl StdoutWriter {
    fn new() -> Self {
        Self {
            inner: Arc::new(Mutex::new(std::io::stdout())),
        }
    }

    fn send(&self, msg: &PluginMessage) {
        let line = serde_json::to_string(msg).expect("protocol messages serialize");
        let mut out = self.inner.lock().expect("stdout lock");
        // A broken pipe means the host is gone; workers just stop writing.
        let _ = out.write_all(line.as_bytes());
        let _ = out.write_all(b"\n");
        let _ = out.flush();
    }
}

/// Counting semaphore (std-only) bounding concurrent worker threads.
struct Semaphore {
    state: Mutex<u32>,
    cv: Condvar,
}

impl Semaphore {
    fn new(permits: u32) -> Self {
        Self {
            state: Mutex::new(permits),
            cv: Condvar::new(),
        }
    }

    fn acquire(&self) {
        let mut permits = self.state.lock().expect("semaphore lock");
        while *permits == 0 {
            permits = self.cv.wait(permits).expect("semaphore wait");
        }
        *permits -= 1;
    }

    fn release(&self) {
        *self.state.lock().expect("semaphore lock") += 1;
        self.cv.notify_one();
    }
}

/// Requests the host cancelled; responses for them are suppressed.
#[derive(Clone, Default)]
struct Cancelled(Arc<Mutex<HashSet<String>>>);

impl Cancelled {
    fn mark(&self, id: &str) {
        let mut set = self.0.lock().expect("cancel lock");
        // A cancel that arrives after the response was already sent would
        // stay marked forever; bound the set so stale marks cannot grow.
        if set.len() >= 256 {
            set.clear();
        }
        set.insert(id.to_owned());
    }

    /// Returns true (and forgets the id) when the request was cancelled.
    fn take(&self, id: &str) -> bool {
        self.0.lock().expect("cancel lock").remove(id)
    }
}

/// Run the plugin protocol over the process's stdin/stdout until STOP or EOF.
pub fn run_plugin_stdio<S: PluginService>(service: S) -> std::io::Result<()> {
    let service = Arc::new(service);
    let writer = StdoutWriter::new();
    let semaphore = Arc::new(Semaphore::new(service.max_concurrency().max(1)));
    let cancelled = Cancelled::default();

    let stdin = std::io::stdin();
    for line in stdin.lock().lines() {
        let line = line?;
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let msg: HostMessage = match serde_json::from_str(line) {
            Ok(m) => m,
            Err(err) => {
                writer.send(&PluginMessage::Error {
                    id: None,
                    error: PluginError {
                        kind: PluginErrorKind::InvalidRequest,
                        message: format!("unparseable host message: {err}"),
                    },
                });
                continue;
            }
        };
        match msg {
            HostMessage::Hello {
                id,
                protocol_version,
                ..
            } => {
                if protocol_version != PROTOCOL_VERSION {
                    writer.send(&PluginMessage::Error {
                        id: Some(id),
                        error: PluginError {
                            kind: PluginErrorKind::ProtocolMismatch,
                            message: format!(
                                "plugin supports protocol {PROTOCOL_VERSION}, host sent {protocol_version}"
                            ),
                        },
                    });
                    continue;
                }
                writer.send(&PluginMessage::HelloAck {
                    id,
                    protocol_version: PROTOCOL_VERSION,
                    plugin_id: service.plugin_id(),
                    plugin_version: service.plugin_version(),
                    api_version: service.api_version(),
                });
            }
            HostMessage::Capabilities { id } => {
                let caps = service.capabilities();
                writer.send(&PluginMessage::CapabilitiesResult {
                    id,
                    capabilities: caps.capabilities,
                    checks: caps.checks,
                    actions: caps.actions,
                    max_concurrency: service.max_concurrency().max(1),
                    baseline_projection: caps.baseline_projection,
                });
            }
            HostMessage::Check { id, request } => {
                dispatch(&service, &writer, &semaphore, &cancelled, id, move |svc| {
                    let result = svc.run_check(&request);
                    ResponseBody::Check(result)
                });
            }
            HostMessage::Action {
                id,
                action_id,
                device_id,
                params,
                ..
            } => {
                dispatch(
                    &service,
                    &writer,
                    &semaphore,
                    &cancelled,
                    id,
                    move |svc| match svc.run_action(&action_id, &device_id, &params) {
                        Ok(result) => ResponseBody::Action(result),
                        Err(error) => ResponseBody::Error(error),
                    },
                );
            }
            HostMessage::Ping { id } => {
                writer.send(&PluginMessage::Pong { id });
            }
            HostMessage::Cancel { request_id } => {
                cancelled.mark(&request_id);
            }
            HostMessage::Shutdown { id } => {
                writer.send(&PluginMessage::Pong { id });
                break;
            }
        }
    }
    Ok(())
}

enum ResponseBody {
    Check(CheckResult),
    Action(ActionResult),
    Error(PluginError),
}

/// Run one request on a worker thread (bounded by the semaphore), sending
/// its response through the synchronized writer unless it was cancelled.
fn dispatch<S: PluginService>(
    service: &Arc<S>,
    writer: &StdoutWriter,
    semaphore: &Arc<Semaphore>,
    cancelled: &Cancelled,
    id: String,
    work: impl FnOnce(&S) -> ResponseBody + Send + 'static,
) {
    // Acquire on the reader thread: when the plugin is saturated we simply
    // stop consuming stdin, which is honest backpressure (the host already
    // bounds in-flight requests via per-plugin concurrency).
    semaphore.acquire();
    let service = service.clone();
    let writer = writer.clone();
    let semaphore = semaphore.clone();
    let cancelled = cancelled.clone();
    std::thread::spawn(move || {
        let body = work(&service);
        if cancelled.take(&id) {
            // Host stopped waiting; a late response would be dropped there
            // anyway, but suppressing it keeps the wire quiet.
            semaphore.release();
            return;
        }
        let msg = match body {
            ResponseBody::Check(result) => PluginMessage::CheckResult { id, result },
            ResponseBody::Action(result) => PluginMessage::ActionResult { id, result },
            ResponseBody::Error(error) => PluginMessage::Error {
                id: Some(id),
                error,
            },
        };
        writer.send(&msg);
        semaphore.release();
    });
}
