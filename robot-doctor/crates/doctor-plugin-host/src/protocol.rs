//! Wire protocol between the plugin host and process plugins.
//!
//! Transport: one JSON object per line (JSONL) over the plugin's
//! stdin (host → plugin) and stdout (plugin → host). stderr is free-form
//! and captured as log output.
//!
//! Lifecycle: START (spawn) → HELLO → CAPABILITIES → CHECK/ACTION/PING… → STOP.

use doctor_domain::{
    ActionDeclaration, ActionResult, CheckDeclaration, CheckRequest, CheckResult, PluginCapability,
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// Current plugin protocol version. Bump on breaking wire changes;
/// host and plugin negotiate during HELLO.
pub const PROTOCOL_VERSION: u32 = 1;

/// Message sent from host to plugin. `id` correlates the response.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum HostMessage {
    Hello {
        id: String,
        protocol_version: u32,
        host_version: String,
    },
    Capabilities {
        id: String,
    },
    Check {
        id: String,
        request: CheckRequest,
    },
    Action {
        id: String,
        action_id: String,
        device_id: String,
        #[serde(default)]
        params: BTreeMap<String, serde_json::Value>,
        timeout_ms: u64,
    },
    Ping {
        id: String,
    },
    Shutdown {
        id: String,
    },
}

impl HostMessage {
    pub fn id(&self) -> &str {
        match self {
            HostMessage::Hello { id, .. }
            | HostMessage::Capabilities { id }
            | HostMessage::Check { id, .. }
            | HostMessage::Action { id, .. }
            | HostMessage::Ping { id }
            | HostMessage::Shutdown { id } => id,
        }
    }
}

/// Typed error a plugin can return instead of a result.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PluginError {
    pub kind: PluginErrorKind,
    pub message: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum PluginErrorKind {
    /// The capability's dependency is absent on this system (no ROS, no GPU…).
    Unavailable,
    /// Request not applicable on this platform.
    Unsupported,
    /// The plugin could not finish in time (self-reported).
    Timeout,
    PermissionDenied,
    DependencyMissing,
    /// Malformed or unknown request.
    InvalidRequest,
    /// Plugin-internal failure.
    Internal,
    /// HELLO version negotiation failed.
    ProtocolMismatch,
}

/// Message sent from plugin to host.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum PluginMessage {
    HelloAck {
        id: String,
        protocol_version: u32,
        plugin_id: String,
        plugin_version: String,
        api_version: u32,
    },
    CapabilitiesResult {
        id: String,
        #[serde(default)]
        capabilities: Vec<PluginCapability>,
        #[serde(default)]
        checks: Vec<CheckDeclaration>,
        #[serde(default)]
        actions: Vec<ActionDeclaration>,
    },
    CheckResult {
        id: String,
        result: CheckResult,
    },
    ActionResult {
        id: String,
        result: ActionResult,
    },
    Pong {
        id: String,
    },
    /// Typed failure for the request `id`. `id` may be null for fatal
    /// protocol-level errors not tied to a request.
    Error {
        #[serde(default)]
        id: Option<String>,
        error: PluginError,
    },
    /// Free-form log line forwarded to the host's tracing output.
    Log {
        level: String,
        message: String,
    },
}

impl PluginMessage {
    /// The request id this message answers, if any.
    pub fn id(&self) -> Option<&str> {
        match self {
            PluginMessage::HelloAck { id, .. }
            | PluginMessage::CapabilitiesResult { id, .. }
            | PluginMessage::CheckResult { id, .. }
            | PluginMessage::ActionResult { id, .. }
            | PluginMessage::Pong { id } => Some(id),
            PluginMessage::Error { id, .. } => id.as_deref(),
            PluginMessage::Log { .. } => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use doctor_domain::{CheckId, DeviceId};

    #[test]
    fn host_message_is_single_json_line() {
        let msg = HostMessage::Check {
            id: "42".into(),
            request: CheckRequest {
                check_id: CheckId::from("system.cpu"),
                device_id: DeviceId::from("local"),
                params: Default::default(),
                timeout_ms: 5000,
            },
        };
        let line = serde_json::to_string(&msg).unwrap();
        assert!(!line.contains('\n'));
        assert!(line.contains("\"type\":\"check\""));
        let back: HostMessage = serde_json::from_str(&line).unwrap();
        assert_eq!(back, msg);
    }

    #[test]
    fn plugin_error_roundtrip() {
        let msg = PluginMessage::Error {
            id: Some("7".into()),
            error: PluginError {
                kind: PluginErrorKind::Unavailable,
                message: "ROS 2 not installed".into(),
            },
        };
        let line = serde_json::to_string(&msg).unwrap();
        assert!(line.contains("\"UNAVAILABLE\""));
        let back: PluginMessage = serde_json::from_str(&line).unwrap();
        assert_eq!(back, msg);
    }

    #[test]
    fn unknown_extra_fields_are_tolerated() {
        // Forward compatibility: a newer plugin may add fields.
        let line = r#"{"type":"pong","id":"1","extra":"future"}"#;
        let msg: PluginMessage = serde_json::from_str(line).unwrap();
        assert_eq!(msg.id(), Some("1"));
    }
}
