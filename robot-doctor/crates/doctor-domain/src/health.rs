//! Health, severity and failure semantics.

use serde::{Deserialize, Serialize};

/// Overall health of a device, subsystem or capability.
///
/// Deliberately *not* a numeric score: states are derived from findings and
/// stay explainable.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum HealthState {
    Healthy,
    Degraded,
    Critical,
    Offline,
    Unknown,
}

impl HealthState {
    /// Combine two health states, keeping the worse one.
    /// Ordering (best → worst): Healthy < Unknown < Degraded < Critical < Offline is
    /// not linear in practice; we treat Offline as worst, then Critical, then
    /// Degraded, then Unknown, then Healthy.
    pub fn worst(self, other: HealthState) -> HealthState {
        fn rank(h: HealthState) -> u8 {
            match h {
                HealthState::Healthy => 0,
                HealthState::Unknown => 1,
                HealthState::Degraded => 2,
                HealthState::Critical => 3,
                HealthState::Offline => 4,
            }
        }
        if rank(other) > rank(self) {
            other
        } else {
            self
        }
    }
}

/// Severity of a finding or root cause.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum Severity {
    Info,
    Warning,
    Error,
    Critical,
}

impl Severity {
    /// The health state a finding of this severity implies for its subject.
    pub fn implied_health(self) -> HealthState {
        match self {
            Severity::Info => HealthState::Healthy,
            Severity::Warning => HealthState::Degraded,
            Severity::Error => HealthState::Degraded,
            Severity::Critical => HealthState::Critical,
        }
    }
}

/// Outcome status of a single check execution.
///
/// Distinguishes "the check ran and the subject is broken" (`Failed`) from
/// the many ways a check can be *inapplicable or unable to run* — those must
/// never be presented as Robot Doctor errors.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum CheckStatus {
    /// Check ran, subject is within expectations.
    Passed,
    /// Check ran, subject violates expectations (findings explain why).
    Failed,
    /// The capability the check needs does not exist on this system
    /// (e.g. ROS not installed, no NVIDIA GPU). Not an error.
    Unavailable,
    /// The check does not apply to this platform/architecture.
    Unsupported,
    /// The check did not finish within its timeout.
    Timeout,
    /// The check could not run due to missing privileges.
    PermissionDenied,
    /// A prerequisite check failed or a required dependency is absent,
    /// so running this check would only produce noise.
    DependencyMissing,
    /// The target device is unreachable; downstream errors are suppressed.
    Offline,
    /// The run (or this check) was cancelled before completion.
    Cancelled,
    /// The check itself malfunctioned (plugin crash, protocol error).
    Error,
}

impl CheckStatus {
    /// Whether this status means the check actually evaluated its subject.
    pub fn evaluated(self) -> bool {
        matches!(self, CheckStatus::Passed | CheckStatus::Failed)
    }

    /// Health contribution of this check status for run summaries.
    pub fn implied_health(self) -> HealthState {
        match self {
            CheckStatus::Passed => HealthState::Healthy,
            CheckStatus::Failed => HealthState::Degraded, // findings refine this
            CheckStatus::Unavailable | CheckStatus::Unsupported => HealthState::Healthy,
            CheckStatus::DependencyMissing | CheckStatus::Cancelled => HealthState::Unknown,
            CheckStatus::Timeout | CheckStatus::PermissionDenied | CheckStatus::Error => {
                HealthState::Unknown
            }
            CheckStatus::Offline => HealthState::Offline,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn worst_health_wins() {
        assert_eq!(
            HealthState::Healthy.worst(HealthState::Critical),
            HealthState::Critical
        );
        assert_eq!(
            HealthState::Degraded.worst(HealthState::Healthy),
            HealthState::Degraded
        );
        assert_eq!(
            HealthState::Critical.worst(HealthState::Offline),
            HealthState::Offline
        );
    }

    #[test]
    fn unavailable_does_not_degrade_health() {
        assert_eq!(
            CheckStatus::Unavailable.implied_health(),
            HealthState::Healthy
        );
        assert_eq!(
            CheckStatus::Unsupported.implied_health(),
            HealthState::Healthy
        );
    }

    #[test]
    fn enum_wire_format_is_screaming_snake() {
        assert_eq!(
            serde_json::to_string(&CheckStatus::PermissionDenied).unwrap(),
            "\"PERMISSION_DENIED\""
        );
        assert_eq!(
            serde_json::to_string(&HealthState::Degraded).unwrap(),
            "\"DEGRADED\""
        );
    }
}
