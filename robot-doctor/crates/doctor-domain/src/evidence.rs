//! Evidence and observations: the raw, inspectable facts every conclusion
//! must trace back to.

use crate::ids::{CheckId, EvidenceId, ObservationId};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

/// Where a piece of evidence came from.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum EvidenceKind {
    /// A programmatic API (sysinfo, NVML, rclpy…).
    Api,
    /// Operating system facilities (/proc, registry, WMI…).
    Os,
    /// ROS graph / ROS tooling.
    Ros,
    /// An executed command (captured invocation + output).
    Command,
    /// A running (or missing) process.
    Process,
    /// Log file or log excerpt.
    Log,
    /// Network probe (socket connect, ping, DNS…).
    Network,
    /// A sampled metric series or single sample.
    Metric,
}

/// A captured, inspectable fact. Immutable once recorded.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Evidence {
    pub id: EvidenceId,
    pub kind: EvidenceKind,
    /// Machine-readable origin, e.g. `sysinfo`, `cmd:ros2 node list`,
    /// `file:/var/log/syslog`, `nvml`.
    pub source: String,
    /// One-line human summary shown in evidence lists.
    pub summary: String,
    /// Structured payload. Shape depends on `kind`/`source`; always JSON so
    /// the UI can render it and tests can assert on it.
    pub data: serde_json::Value,
    pub captured_at: DateTime<Utc>,
}

impl Evidence {
    pub fn new(
        kind: EvidenceKind,
        source: impl Into<String>,
        summary: impl Into<String>,
        data: serde_json::Value,
    ) -> Self {
        Self {
            id: EvidenceId::generate(),
            kind,
            source: source.into(),
            summary: summary.into(),
            data,
            captured_at: Utc::now(),
        }
    }
}

/// A single normalized measured value.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Metric {
    /// Namespaced metric name, e.g. `cpu.usage_percent`, `disk./.free_bytes`.
    pub name: String,
    pub value: f64,
    /// Unit string, e.g. `percent`, `bytes`, `hz`, `celsius`, `seconds`.
    pub unit: String,
    pub timestamp: DateTime<Utc>,
}

/// The value carried by an observation.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", content = "value", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ObservationValue {
    Number(f64),
    Text(String),
    Bool(bool),
    /// Structured value for complex facts (interface tables, topic lists…).
    Json(serde_json::Value),
}

/// A normalized fact derived from evidence, consumed by rules.
///
/// Observations are the boundary between platform-specific collection and
/// deterministic evaluation: rules only ever see observations.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Observation {
    pub id: ObservationId,
    /// The check that produced this observation.
    pub check_id: CheckId,
    /// Namespaced key, e.g. `system.memory.used_percent`, `ros.topic./scan.hz`.
    pub key: String,
    pub value: ObservationValue,
    /// Optional unit for numeric values.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub unit: Option<String>,
    /// Evidence backing this observation.
    pub evidence_ids: Vec<EvidenceId>,
    pub observed_at: DateTime<Utc>,
}

impl Observation {
    pub fn number(
        check_id: &CheckId,
        key: impl Into<String>,
        value: f64,
        unit: impl Into<String>,
        evidence_ids: Vec<EvidenceId>,
    ) -> Self {
        Self {
            id: ObservationId::generate(),
            check_id: check_id.clone(),
            key: key.into(),
            value: ObservationValue::Number(value),
            unit: Some(unit.into()),
            evidence_ids,
            observed_at: Utc::now(),
        }
    }

    pub fn text(
        check_id: &CheckId,
        key: impl Into<String>,
        value: impl Into<String>,
        evidence_ids: Vec<EvidenceId>,
    ) -> Self {
        Self {
            id: ObservationId::generate(),
            check_id: check_id.clone(),
            key: key.into(),
            value: ObservationValue::Text(value.into()),
            unit: None,
            evidence_ids,
            observed_at: Utc::now(),
        }
    }

    pub fn json(
        check_id: &CheckId,
        key: impl Into<String>,
        value: serde_json::Value,
        evidence_ids: Vec<EvidenceId>,
    ) -> Self {
        Self {
            id: ObservationId::generate(),
            check_id: check_id.clone(),
            key: key.into(),
            value: ObservationValue::Json(value),
            unit: None,
            evidence_ids,
            observed_at: Utc::now(),
        }
    }

    pub fn as_number(&self) -> Option<f64> {
        match &self.value {
            ObservationValue::Number(n) => Some(*n),
            _ => None,
        }
    }

    pub fn as_text(&self) -> Option<&str> {
        match &self.value {
            ObservationValue::Text(s) => Some(s),
            _ => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn observation_roundtrip() {
        let check = CheckId::from("system.cpu");
        let ev = Evidence::new(
            EvidenceKind::Api,
            "sysinfo",
            "CPU sampled",
            serde_json::json!({"cores": 8}),
        );
        let obs = Observation::number(
            &check,
            "system.cpu.usage_percent",
            42.5,
            "percent",
            vec![ev.id.clone()],
        );
        let json = serde_json::to_string(&obs).unwrap();
        let back: Observation = serde_json::from_str(&json).unwrap();
        assert_eq!(back, obs);
        assert_eq!(back.as_number(), Some(42.5));
        assert_eq!(back.evidence_ids, vec![ev.id]);
    }
}
