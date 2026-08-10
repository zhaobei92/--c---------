//! Stable, strongly-typed identifiers.
//!
//! Every entity that is referenced across component boundaries (plugins,
//! storage, UI, remote agent) uses a newtype ID so references cannot be
//! mixed up and remain stable across serialization.

use serde::{Deserialize, Serialize};
use std::fmt;

macro_rules! string_id {
    ($(#[$doc:meta])* $name:ident) => {
        $(#[$doc])*
        #[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
        #[serde(transparent)]
        pub struct $name(pub String);

        impl $name {
            /// Create a fresh random ID.
            pub fn generate() -> Self {
                Self(uuid::Uuid::new_v4().to_string())
            }

            pub fn as_str(&self) -> &str {
                &self.0
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str(&self.0)
            }
        }

        impl From<&str> for $name {
            fn from(s: &str) -> Self {
                Self(s.to_owned())
            }
        }

        impl From<String> for $name {
            fn from(s: String) -> Self {
                Self(s)
            }
        }
    };
}

string_id!(
    /// Identifies a managed device (local machine, remote robot, workstation).
    DeviceId
);
string_id!(
    /// Identifies a plugin, e.g. `system`, `network`, `ros2`.
    PluginId
);
string_id!(
    /// Identifies a check definition, e.g. `system.cpu`, `ros.topics`.
    /// Namespaced with the plugin's capability prefix.
    CheckId
);
string_id!(
    /// Identifies one execution of a whole diagnostic run (many checks).
    RunId
);
string_id!(
    /// Identifies a single piece of captured evidence.
    EvidenceId
);
string_id!(
    /// Identifies a finding produced by a check or a rule.
    FindingId
);
string_id!(
    /// Identifies an observation (normalized measured fact).
    ObservationId
);
string_id!(
    /// Identifies a deterministic rule, e.g. `TOPIC_RATE_LOW`.
    RuleId
);
string_id!(
    /// Identifies a root cause conclusion.
    RootCauseId
);
string_id!(
    /// Identifies an action definition, e.g. `system.restart_service`.
    ActionId
);
string_id!(
    /// Identifies a device profile (expected robot state).
    ProfileId
);
string_id!(
    /// Identifies a captured baseline snapshot.
    BaselineId
);
string_id!(
    /// Identifies an incident (grouped, persisted problem episode).
    IncidentId
);

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ids_serialize_transparently() {
        let id = CheckId::from("system.cpu");
        let json = serde_json::to_string(&id).unwrap();
        assert_eq!(json, "\"system.cpu\"");
        let back: CheckId = serde_json::from_str(&json).unwrap();
        assert_eq!(back, id);
    }

    #[test]
    fn generated_ids_are_unique() {
        assert_ne!(RunId::generate(), RunId::generate());
    }
}
