//! Devices and their capabilities.

use crate::health::HealthState;
use crate::ids::{DeviceId, ProfileId};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

/// How Robot Doctor reaches a device.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum DeviceEndpoint {
    /// The machine Robot Doctor itself runs on (embedded core).
    Local,
    /// A remote robotdoctor-agent reached over gRPC.
    Remote { host: String, port: u16 },
}

/// Connection state towards a device.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ConnectionState {
    Connected,
    Degraded,
    Disconnected,
    /// Agent reachable but protocol versions do not overlap.
    Incompatible,
}

/// A capability a device offers, discovered from its plugins,
/// e.g. `system.cpu`, `ros.topics`, `gpu.nvidia`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Capability {
    pub name: String,
    /// Whether the capability's dependencies are currently present
    /// (e.g. `ros.*` exists as a plugin but ROS is not installed → false).
    pub available: bool,
}

/// A managed device: local machine, remote robot, workstation, edge box.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Device {
    pub id: DeviceId,
    /// User-visible name, e.g. "This machine", "warehouse-amr-03".
    pub name: String,
    pub endpoint: DeviceEndpoint,
    /// Profile describing the expected state of this device, when assigned.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub profile_id: Option<ProfileId>,
    pub connection: ConnectionState,
    /// Last computed health (from the most recent diagnostic run).
    pub health: HealthState,
    pub added_at: DateTime<Utc>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_seen: Option<DateTime<Utc>>,
    /// Discovered capabilities (populated after first contact).
    #[serde(default)]
    pub capabilities: Vec<Capability>,
}

impl Device {
    /// The built-in local device present in every installation.
    pub fn local() -> Self {
        Self {
            id: DeviceId::from("local"),
            name: "This machine".into(),
            endpoint: DeviceEndpoint::Local,
            profile_id: None,
            connection: ConnectionState::Connected,
            health: HealthState::Unknown,
            added_at: Utc::now(),
            last_seen: Some(Utc::now()),
            capabilities: Vec::new(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn local_device_is_connected() {
        let d = Device::local();
        assert_eq!(d.endpoint, DeviceEndpoint::Local);
        assert_eq!(d.connection, ConnectionState::Connected);
        let back: Device = serde_json::from_str(&serde_json::to_string(&d).unwrap()).unwrap();
        assert_eq!(back, d);
    }
}
