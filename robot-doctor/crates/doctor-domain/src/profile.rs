//! Profiles: explicit, versioned, plugin-independent expectations
//! (Phase C2).
//!
//! A Profile states what *should* be true. It is deliberately not a
//! programming language: expectations select comparison entities by
//! namespace/kind/key and apply one of a small closed set of operators.
//! No expression evaluation, no scripting, no shell — an imported profile
//! can never execute anything.
//!
//! Profiles are robot-agnostic *infrastructure*: naming `/scan` in a
//! profile is a user's description of their robot, never core knowledge.

use crate::comparison::AttributeValue;
use crate::ids::{DeviceId, ProfileId};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

/// Current profile schema version understood by this build.
pub const PROFILE_SCHEMA_VERSION: u32 = 1;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ProfileStatus {
    Draft,
    Active,
    Archived,
}

/// Whether an expectation participates in satisfaction evaluation when
/// its subject is absent.
///
/// Severity deliberately does not exist here — that belongs to findings
/// in Phase C3.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Requirement {
    Required,
    Optional,
}

/// How an expectation picks the entities it applies to.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct Selector {
    /// Exact entity key.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub key: Option<String>,
    /// Prefix match on the entity key (e.g. all `/camera/…` topics).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub key_prefix: Option<String>,
}

impl Selector {
    pub fn is_empty(&self) -> bool {
        self.key.is_none() && self.key_prefix.is_none()
    }

    pub fn matches(&self, key: &str) -> bool {
        match (&self.key, &self.key_prefix) {
            (Some(exact), _) => key == exact,
            (None, Some(prefix)) => key.starts_with(prefix.as_str()),
            (None, None) => true,
        }
    }
}

/// Whether a relationship expectation wants a direct edge or any path.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum RelationshipMode {
    /// The exact `from → to` edge must exist.
    DirectEdge,
    /// Any chain of edges connecting `from` to `to`.
    #[default]
    Path,
}

/// A constraint value for equality-style operators.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum ConstraintValue {
    Number(f64),
    Bool(bool),
    Text(String),
}

impl ConstraintValue {
    pub fn render(&self) -> String {
        match self {
            ConstraintValue::Number(n) => AttributeValue::Number(*n).render(),
            ConstraintValue::Bool(b) => b.to_string(),
            ConstraintValue::Text(s) => s.clone(),
        }
    }

    /// Compare against an observed attribute value, tolerating the
    /// number/text ambiguity that YAML naturally produces.
    pub fn equals(&self, actual: &AttributeValue) -> bool {
        match (self, actual) {
            (ConstraintValue::Number(a), AttributeValue::Number(b)) => a == b,
            (ConstraintValue::Bool(a), AttributeValue::Bool(b)) => a == b,
            (ConstraintValue::Text(a), AttributeValue::Text(b)) => a == b,
            (ConstraintValue::Text(a), AttributeValue::Bool(b)) => a == &b.to_string(),
            (ConstraintValue::Number(a), AttributeValue::Text(b)) => {
                b.parse::<f64>().is_ok_and(|b| &b == a)
            }
            (ConstraintValue::Text(a), AttributeValue::Number(b)) => {
                a.parse::<f64>().is_ok_and(|a| &a == b)
            }
            _ => false,
        }
    }
}

/// The closed operator set. Adding an operator is a deliberate act; there
/// is no escape hatch into arbitrary computation.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "operator", rename_all = "snake_case")]
pub enum Constraint {
    /// At least one selected entity exists.
    Exists,
    /// No selected entity exists.
    NotExists,
    Equals {
        field: String,
        value: ConstraintValue,
    },
    NotEquals {
        field: String,
        value: ConstraintValue,
    },
    /// Numeric field ≥ value.
    Min {
        field: String,
        value: f64,
    },
    /// Numeric field ≤ value.
    Max {
        field: String,
        value: f64,
    },
    Between {
        field: String,
        min: f64,
        max: f64,
    },
    /// At least `value` selected entities exist.
    CountMin {
        value: u32,
    },
    /// At most `value` selected entities exist.
    CountMax {
        value: u32,
    },
    /// Text field contains a substring.
    Contains {
        field: String,
        value: String,
    },
    /// Set field contains a member.
    SetContains {
        field: String,
        value: String,
    },
    /// A relationship exists between two nodes of an edge-shaped kind
    /// (entities carrying `from`/`to` attributes).
    RelationshipExists {
        from: String,
        to: String,
        #[serde(default)]
        mode: RelationshipMode,
    },
}

impl Constraint {
    pub fn operator_name(&self) -> &'static str {
        match self {
            Constraint::Exists => "exists",
            Constraint::NotExists => "not_exists",
            Constraint::Equals { .. } => "equals",
            Constraint::NotEquals { .. } => "not_equals",
            Constraint::Min { .. } => "min",
            Constraint::Max { .. } => "max",
            Constraint::Between { .. } => "between",
            Constraint::CountMin { .. } => "count_min",
            Constraint::CountMax { .. } => "count_max",
            Constraint::Contains { .. } => "contains",
            Constraint::SetContains { .. } => "set_contains",
            Constraint::RelationshipExists { .. } => "relationship_exists",
        }
    }

    /// The attribute this constraint reads, if any.
    pub fn field(&self) -> Option<&str> {
        match self {
            Constraint::Equals { field, .. }
            | Constraint::NotEquals { field, .. }
            | Constraint::Min { field, .. }
            | Constraint::Max { field, .. }
            | Constraint::Between { field, .. }
            | Constraint::Contains { field, .. }
            | Constraint::SetContains { field, .. } => Some(field),
            _ => None,
        }
    }

    /// Constraints that are meaningful without selecting a specific key.
    pub fn allows_empty_selector(&self) -> bool {
        matches!(
            self,
            Constraint::CountMin { .. }
                | Constraint::CountMax { .. }
                | Constraint::RelationshipExists { .. }
                | Constraint::NotExists
        )
    }

    /// Human rendering used in evaluation results.
    pub fn render(&self) -> String {
        match self {
            Constraint::Exists => "exists".into(),
            Constraint::NotExists => "does not exist".into(),
            Constraint::Equals { field, value } => format!("{field} == {}", value.render()),
            Constraint::NotEquals { field, value } => format!("{field} != {}", value.render()),
            Constraint::Min { field, value } => format!("{field} >= {value}"),
            Constraint::Max { field, value } => format!("{field} <= {value}"),
            Constraint::Between { field, min, max } => format!("{min} <= {field} <= {max}"),
            Constraint::CountMin { value } => format!("count >= {value}"),
            Constraint::CountMax { value } => format!("count <= {value}"),
            Constraint::Contains { field, value } => format!("{field} contains '{value}'"),
            Constraint::SetContains { field, value } => format!("{field} includes '{value}'"),
            Constraint::RelationshipExists { from, to, mode } => match mode {
                RelationshipMode::DirectEdge => format!("direct edge {from} -> {to}"),
                RelationshipMode::Path => format!("path {from} -> {to}"),
            },
        }
    }
}

/// One explicit statement about what should be true.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Expectation {
    /// Unique within the profile revision.
    pub id: String,
    #[serde(default)]
    pub description: String,
    pub namespace: String,
    /// Entity kind inside the namespace (`node`, `topic`, `gpu`, …).
    pub kind: String,
    #[serde(default)]
    pub selector: Selector,
    pub requirement: Requirement,
    pub constraint: Constraint,
    /// True when the value came from a baseline suggestion the user
    /// accepted, so the UI can show provenance.
    #[serde(default)]
    pub from_baseline: bool,
}

/// A profile revision: the immutable unit that evaluations reference.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Profile {
    pub schema_version: u32,
    pub id: ProfileId,
    pub name: String,
    #[serde(default)]
    pub description: String,
    pub revision: u32,
    pub status: ProfileStatus,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    #[serde(default)]
    pub tags: Vec<String>,
    pub expectations: Vec<Expectation>,
}

impl Profile {
    pub fn expectation(&self, id: &str) -> Option<&Expectation> {
        self.expectations.iter().find(|e| e.id == id)
    }

    /// Namespaces this profile makes statements about.
    pub fn namespaces(&self) -> Vec<String> {
        let mut names: Vec<String> = self
            .expectations
            .iter()
            .map(|e| e.namespace.clone())
            .collect();
        names.sort();
        names.dedup();
        names
    }
}

/// Which profile revision a device is currently evaluated against.
///
/// Separate from the profile itself so one profile can serve a fleet.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DeviceProfileAssignment {
    pub device_id: DeviceId,
    pub profile_id: ProfileId,
    pub profile_revision: u32,
    pub activated_at: DateTime<Utc>,
    /// Optional ROS runtime (or other runtime) selection to use when this
    /// profile is active on this device.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub runtime_id: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::comparison::AttributeValue;

    #[test]
    fn selector_matching() {
        let exact = Selector {
            key: Some("/scan".into()),
            key_prefix: None,
        };
        assert!(exact.matches("/scan"));
        assert!(!exact.matches("/scan_raw"));

        let prefix = Selector {
            key: None,
            key_prefix: Some("/camera/".into()),
        };
        assert!(prefix.matches("/camera/image"));
        assert!(!prefix.matches("/lidar"));

        assert!(Selector::default().matches("anything"));
        assert!(Selector::default().is_empty());
    }

    #[test]
    fn constraint_value_equality_tolerates_yaml_typing() {
        assert!(ConstraintValue::Number(8.0).equals(&AttributeValue::Number(8.0)));
        assert!(ConstraintValue::Text("8".into()).equals(&AttributeValue::Number(8.0)));
        assert!(ConstraintValue::Text("x86_64".into()).equals(&AttributeValue::text("x86_64")));
        assert!(!ConstraintValue::Text("arm64".into()).equals(&AttributeValue::text("x86_64")));
    }

    #[test]
    fn profile_has_no_severity_vocabulary() {
        // C2 must not leak finding severities into expectations.
        let json = serde_json::to_string(&Requirement::Required).unwrap();
        assert_eq!(json, "\"required\"");
        for forbidden in ["CRITICAL", "WARNING", "severity"] {
            assert!(!json.contains(forbidden));
        }
    }
}
