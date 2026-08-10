//! Host-side runtime: spawns a plugin process, speaks the JSONL protocol,
//! enforces timeouts, detects crashes and supports restart.

use crate::manifest::LoadedManifest;
use crate::protocol::{HostMessage, PluginError, PluginErrorKind, PluginMessage, PROTOCOL_VERSION};
use doctor_domain::{
    ActionDeclaration, CheckDeclaration, CheckRequest, CheckResult, PluginCapability, PluginId,
};
use std::collections::HashMap;
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;
use thiserror::Error;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::{mpsc, oneshot, Mutex};

/// Grace added to a request's own timeout before the host gives up on it.
const HOST_TIMEOUT_GRACE: Duration = Duration::from_millis(1500);
/// Timeout for lifecycle requests (HELLO, CAPABILITIES, PING, SHUTDOWN).
const LIFECYCLE_TIMEOUT: Duration = Duration::from_secs(10);

#[derive(Debug, Error)]
pub enum HostError {
    #[error("failed to spawn plugin '{plugin}': {source}")]
    Spawn {
        plugin: String,
        #[source]
        source: std::io::Error,
    },
    #[error("plugin '{plugin}' exited or closed its pipe")]
    Crashed { plugin: String },
    #[error("plugin '{plugin}' did not answer within {timeout_ms} ms")]
    Timeout { plugin: String, timeout_ms: u64 },
    #[error("plugin '{plugin}' speaks protocol {plugin_version}, host supports {host_version}")]
    ProtocolMismatch {
        plugin: String,
        plugin_version: u32,
        host_version: u32,
    },
    #[error("plugin '{plugin}' returned error: {error:?}")]
    Plugin { plugin: String, error: PluginError },
    #[error("plugin '{plugin}' sent an unexpected response type")]
    UnexpectedResponse { plugin: String },
}

impl HostError {
    /// Map a host error to the typed plugin-error the engine records.
    pub fn as_plugin_error(&self) -> PluginError {
        match self {
            HostError::Plugin { error, .. } => error.clone(),
            HostError::Timeout { timeout_ms, .. } => PluginError {
                kind: PluginErrorKind::Timeout,
                message: format!("no response within {timeout_ms} ms"),
            },
            HostError::ProtocolMismatch { .. } => PluginError {
                kind: PluginErrorKind::ProtocolMismatch,
                message: self.to_string(),
            },
            other => PluginError {
                kind: PluginErrorKind::Internal,
                message: other.to_string(),
            },
        }
    }
}

/// Capabilities negotiated with a running plugin.
#[derive(Debug, Clone)]
pub struct NegotiatedCapabilities {
    pub capabilities: Vec<PluginCapability>,
    pub checks: Vec<CheckDeclaration>,
    pub actions: Vec<ActionDeclaration>,
}

type PendingMap = Arc<Mutex<HashMap<String, oneshot::Sender<PluginMessage>>>>;

/// A live connection to one running plugin process.
///
/// Cheap to clone; all clones talk to the same process.
#[derive(Clone)]
pub struct PluginHandle {
    plugin_id: PluginId,
    writer_tx: mpsc::Sender<String>,
    pending: PendingMap,
    alive: Arc<AtomicBool>,
    next_id: Arc<AtomicU64>,
    child: Arc<Mutex<Child>>,
}

impl PluginHandle {
    /// Spawn the plugin process and start reader/writer tasks.
    /// Does not perform the HELLO handshake — call [`PluginHandle::handshake`].
    pub fn spawn(loaded: &LoadedManifest) -> Result<Self, HostError> {
        let plugin_id = loaded.manifest.id.clone();
        let mut child = Command::new(&loaded.executable)
            .args(&loaded.manifest.args)
            .current_dir(&loaded.plugin_dir)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true)
            .spawn()
            .map_err(|source| HostError::Spawn {
                plugin: plugin_id.to_string(),
                source,
            })?;

        let stdin = child.stdin.take().expect("stdin piped");
        let stdout = child.stdout.take().expect("stdout piped");
        let stderr = child.stderr.take().expect("stderr piped");

        let pending: PendingMap = Arc::new(Mutex::new(HashMap::new()));
        let alive = Arc::new(AtomicBool::new(true));
        let (writer_tx, mut writer_rx) = mpsc::channel::<String>(64);

        // Writer task: serialize outbound lines.
        let w_alive = alive.clone();
        tokio::spawn(async move {
            let mut stdin = stdin;
            while let Some(line) = writer_rx.recv().await {
                if stdin.write_all(line.as_bytes()).await.is_err()
                    || stdin.write_all(b"\n").await.is_err()
                    || stdin.flush().await.is_err()
                {
                    w_alive.store(false, Ordering::SeqCst);
                    break;
                }
            }
        });

        // Stderr task: forward plugin stderr to tracing.
        let e_plugin = plugin_id.to_string();
        tokio::spawn(async move {
            let mut lines = BufReader::new(stderr).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                tracing::debug!(plugin = %e_plugin, "plugin stderr: {line}");
            }
        });

        // Reader task: dispatch inbound messages to pending requests.
        let r_pending = pending.clone();
        let r_alive = alive.clone();
        let r_plugin = plugin_id.to_string();
        tokio::spawn(async move {
            let mut lines = BufReader::new(stdout).lines();
            // Loop ends on EOF or broken pipe → crash/stop.
            while let Ok(Some(line)) = lines.next_line().await {
                let line = line.trim();
                if line.is_empty() {
                    continue;
                }
                match serde_json::from_str::<PluginMessage>(line) {
                    Ok(PluginMessage::Log { level, message }) => match level.as_str() {
                        "error" => tracing::error!(plugin = %r_plugin, "{message}"),
                        "warn" => tracing::warn!(plugin = %r_plugin, "{message}"),
                        _ => tracing::debug!(plugin = %r_plugin, "{message}"),
                    },
                    Ok(msg) => {
                        if let Some(id) = msg.id().map(str::to_owned) {
                            let sender = r_pending.lock().await.remove(&id);
                            if let Some(tx) = sender {
                                let _ = tx.send(msg);
                            } else {
                                tracing::warn!(
                                    plugin = %r_plugin,
                                    "response for unknown request id {id}"
                                );
                            }
                        } else {
                            tracing::warn!(
                                plugin = %r_plugin,
                                "plugin error without request id: {msg:?}"
                            );
                        }
                    }
                    Err(err) => {
                        tracing::warn!(
                            plugin = %r_plugin,
                            "unparseable plugin message ({err}): {line}"
                        );
                    }
                }
            }
            // Plugin is gone: fail everything still pending.
            r_alive.store(false, Ordering::SeqCst);
            r_pending.lock().await.clear();
        });

        Ok(Self {
            plugin_id,
            writer_tx,
            pending,
            alive,
            next_id: Arc::new(AtomicU64::new(1)),
            child: Arc::new(Mutex::new(child)),
        })
    }

    pub fn plugin_id(&self) -> &PluginId {
        &self.plugin_id
    }

    /// Whether the plugin process is still believed to be running.
    pub fn is_alive(&self) -> bool {
        self.alive.load(Ordering::SeqCst)
    }

    fn fresh_id(&self) -> String {
        self.next_id.fetch_add(1, Ordering::SeqCst).to_string()
    }

    /// Send one request and await its correlated response.
    async fn request(
        &self,
        msg: HostMessage,
        timeout: Duration,
    ) -> Result<PluginMessage, HostError> {
        if !self.is_alive() {
            return Err(HostError::Crashed {
                plugin: self.plugin_id.to_string(),
            });
        }
        let id = msg.id().to_owned();
        let (tx, rx) = oneshot::channel();
        self.pending.lock().await.insert(id.clone(), tx);

        let line = serde_json::to_string(&msg).expect("protocol messages serialize");
        if self.writer_tx.send(line).await.is_err() {
            self.pending.lock().await.remove(&id);
            return Err(HostError::Crashed {
                plugin: self.plugin_id.to_string(),
            });
        }

        match tokio::time::timeout(timeout, rx).await {
            Ok(Ok(PluginMessage::Error { error, .. })) => Err(HostError::Plugin {
                plugin: self.plugin_id.to_string(),
                error,
            }),
            Ok(Ok(msg)) => Ok(msg),
            // Sender dropped: reader task cleared pending on crash.
            Ok(Err(_)) => Err(HostError::Crashed {
                plugin: self.plugin_id.to_string(),
            }),
            Err(_) => {
                self.pending.lock().await.remove(&id);
                Err(HostError::Timeout {
                    plugin: self.plugin_id.to_string(),
                    timeout_ms: timeout.as_millis() as u64,
                })
            }
        }
    }

    /// HELLO: negotiate protocol version.
    pub async fn handshake(&self, host_version: &str) -> Result<(), HostError> {
        let msg = HostMessage::Hello {
            id: self.fresh_id(),
            protocol_version: PROTOCOL_VERSION,
            host_version: host_version.to_owned(),
        };
        match self.request(msg, LIFECYCLE_TIMEOUT).await? {
            PluginMessage::HelloAck {
                protocol_version, ..
            } => {
                if protocol_version != PROTOCOL_VERSION {
                    return Err(HostError::ProtocolMismatch {
                        plugin: self.plugin_id.to_string(),
                        plugin_version: protocol_version,
                        host_version: PROTOCOL_VERSION,
                    });
                }
                Ok(())
            }
            _ => Err(HostError::UnexpectedResponse {
                plugin: self.plugin_id.to_string(),
            }),
        }
    }

    /// CAPABILITIES: ask the running plugin what it can actually do here.
    pub async fn capabilities(&self) -> Result<NegotiatedCapabilities, HostError> {
        let msg = HostMessage::Capabilities {
            id: self.fresh_id(),
        };
        match self.request(msg, LIFECYCLE_TIMEOUT).await? {
            PluginMessage::CapabilitiesResult {
                capabilities,
                checks,
                actions,
                ..
            } => Ok(NegotiatedCapabilities {
                capabilities,
                checks,
                actions,
            }),
            _ => Err(HostError::UnexpectedResponse {
                plugin: self.plugin_id.to_string(),
            }),
        }
    }

    /// CHECK: run one check. The plugin gets `request.timeout_ms`; the host
    /// waits slightly longer before declaring a timeout itself.
    pub async fn run_check(&self, request: CheckRequest) -> Result<CheckResult, HostError> {
        let timeout = Duration::from_millis(request.timeout_ms) + HOST_TIMEOUT_GRACE;
        let msg = HostMessage::Check {
            id: self.fresh_id(),
            request,
        };
        match self.request(msg, timeout).await? {
            PluginMessage::CheckResult { result, .. } => Ok(result),
            _ => Err(HostError::UnexpectedResponse {
                plugin: self.plugin_id.to_string(),
            }),
        }
    }

    /// PING: liveness probe.
    pub async fn ping(&self) -> Result<(), HostError> {
        let msg = HostMessage::Ping {
            id: self.fresh_id(),
        };
        match self.request(msg, LIFECYCLE_TIMEOUT).await? {
            PluginMessage::Pong { .. } => Ok(()),
            _ => Err(HostError::UnexpectedResponse {
                plugin: self.plugin_id.to_string(),
            }),
        }
    }

    /// STOP: polite shutdown, then kill if the process lingers.
    pub async fn shutdown(&self) {
        let msg = HostMessage::Shutdown {
            id: self.fresh_id(),
        };
        let line = serde_json::to_string(&msg).expect("protocol messages serialize");
        let _ = self.writer_tx.send(line).await;
        let mut child = self.child.lock().await;
        match tokio::time::timeout(Duration::from_secs(3), child.wait()).await {
            Ok(_) => {}
            Err(_) => {
                let _ = child.kill().await;
            }
        }
        self.alive.store(false, Ordering::SeqCst);
    }
}

/// A plugin the host manages across crashes: respawns on demand.
pub struct ManagedPlugin {
    loaded: LoadedManifest,
    host_version: String,
    handle: Mutex<Option<PluginHandle>>,
    restarts: AtomicU64,
    /// Give up respawning after this many consecutive failures.
    max_restarts: u64,
}

impl ManagedPlugin {
    pub fn new(loaded: LoadedManifest, host_version: impl Into<String>) -> Self {
        Self {
            loaded,
            host_version: host_version.into(),
            handle: Mutex::new(None),
            restarts: AtomicU64::new(0),
            max_restarts: 3,
        }
    }

    pub fn manifest(&self) -> &doctor_domain::PluginManifest {
        &self.loaded.manifest
    }

    pub fn restart_count(&self) -> u64 {
        self.restarts.load(Ordering::SeqCst)
    }

    /// Get a live handle, spawning (or respawning after a crash) if needed.
    pub async fn ensure_running(&self) -> Result<PluginHandle, HostError> {
        let mut slot = self.handle.lock().await;
        if let Some(handle) = slot.as_ref() {
            if handle.is_alive() {
                return Ok(handle.clone());
            }
            // Crash detected → count the respawn attempt.
            let restarts = self.restarts.fetch_add(1, Ordering::SeqCst) + 1;
            tracing::warn!(
                plugin = %self.loaded.manifest.id,
                "plugin process died; respawn attempt {restarts}/{}",
                self.max_restarts
            );
            if restarts > self.max_restarts {
                return Err(HostError::Crashed {
                    plugin: self.loaded.manifest.id.to_string(),
                });
            }
        }
        let handle = PluginHandle::spawn(&self.loaded)?;
        handle.handshake(&self.host_version).await?;
        *slot = Some(handle.clone());
        Ok(handle)
    }

    /// Reset the crash counter (e.g. after a healthy period).
    pub fn reset_restarts(&self) {
        self.restarts.store(0, Ordering::SeqCst);
    }

    pub async fn shutdown(&self) {
        let slot = self.handle.lock().await;
        if let Some(handle) = slot.as_ref() {
            handle.shutdown().await;
        }
    }
}
