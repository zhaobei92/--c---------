//! Plugin-side SDK for Rust plugins: a blocking stdio run loop that speaks
//! the JSONL protocol. Plugins in other languages implement the same wire
//! format directly (see `protocol.rs`).

use crate::protocol::{HostMessage, PluginError, PluginErrorKind, PluginMessage, PROTOCOL_VERSION};
use doctor_domain::{
    ActionDeclaration, ActionResult, ActionStatus, CheckDeclaration, CheckRequest, CheckResult,
    PluginCapability,
};
use std::collections::BTreeMap;
use std::io::{BufRead, Write};

/// Implemented by a plugin binary. Called sequentially from the run loop;
/// timeouts are enforced host-side.
pub trait PluginService {
    fn plugin_id(&self) -> String;
    fn plugin_version(&self) -> String;
    fn api_version(&self) -> u32 {
        PROTOCOL_VERSION
    }
    fn capabilities(&mut self) -> ServiceCapabilities;
    fn run_check(&mut self, request: &CheckRequest) -> CheckResult;
    /// Default: actions unavailable.
    fn run_action(
        &mut self,
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

pub struct ServiceCapabilities {
    pub capabilities: Vec<PluginCapability>,
    pub checks: Vec<CheckDeclaration>,
    pub actions: Vec<ActionDeclaration>,
}

fn write_msg(out: &mut impl Write, msg: &PluginMessage) -> std::io::Result<()> {
    let line = serde_json::to_string(msg).expect("protocol messages serialize");
    out.write_all(line.as_bytes())?;
    out.write_all(b"\n")?;
    out.flush()
}

/// Run the plugin protocol over the process's stdin/stdout until STOP or EOF.
pub fn run_plugin_stdio(service: &mut dyn PluginService) -> std::io::Result<()> {
    let stdin = std::io::stdin();
    let stdout = std::io::stdout();
    let mut out = stdout.lock();

    for line in stdin.lock().lines() {
        let line = line?;
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let msg: HostMessage = match serde_json::from_str(line) {
            Ok(m) => m,
            Err(err) => {
                write_msg(
                    &mut out,
                    &PluginMessage::Error {
                        id: None,
                        error: PluginError {
                            kind: PluginErrorKind::InvalidRequest,
                            message: format!("unparseable host message: {err}"),
                        },
                    },
                )?;
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
                    write_msg(
                        &mut out,
                        &PluginMessage::Error {
                            id: Some(id),
                            error: PluginError {
                                kind: PluginErrorKind::ProtocolMismatch,
                                message: format!(
                                    "plugin supports protocol {PROTOCOL_VERSION}, host sent {protocol_version}"
                                ),
                            },
                        },
                    )?;
                    continue;
                }
                write_msg(
                    &mut out,
                    &PluginMessage::HelloAck {
                        id,
                        protocol_version: PROTOCOL_VERSION,
                        plugin_id: service.plugin_id(),
                        plugin_version: service.plugin_version(),
                        api_version: service.api_version(),
                    },
                )?;
            }
            HostMessage::Capabilities { id } => {
                let caps = service.capabilities();
                write_msg(
                    &mut out,
                    &PluginMessage::CapabilitiesResult {
                        id,
                        capabilities: caps.capabilities,
                        checks: caps.checks,
                        actions: caps.actions,
                    },
                )?;
            }
            HostMessage::Check { id, request } => {
                let result = service.run_check(&request);
                write_msg(&mut out, &PluginMessage::CheckResult { id, result })?;
            }
            HostMessage::Action {
                id,
                action_id,
                device_id,
                params,
                ..
            } => match service.run_action(&action_id, &device_id, &params) {
                Ok(result) => write_msg(&mut out, &PluginMessage::ActionResult { id, result })?,
                Err(error) => write_msg(
                    &mut out,
                    &PluginMessage::Error {
                        id: Some(id),
                        error,
                    },
                )?,
            },
            HostMessage::Ping { id } => {
                write_msg(&mut out, &PluginMessage::Pong { id })?;
            }
            HostMessage::Shutdown { id } => {
                write_msg(&mut out, &PluginMessage::Pong { id })?;
                break;
            }
        }
    }
    Ok(())
}
