//! Typed ROS failure semantics, mapped onto the Robot Doctor status model.

use doctor_domain::CheckStatus;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RosErrorKind {
    RosNotInstalled,
    RosEnvironmentInvalid,
    RclpyUnavailable,
    CliUnavailable,
    DdsDiscoveryTimeout,
    TypeSupportMissing,
    TopicSampleTimeout,
    TfUnavailable,
    LifecycleUnsupported,
    PermissionDenied,
    ProviderError,
    Timeout,
}

impl RosErrorKind {
    pub fn code(self) -> &'static str {
        match self {
            RosErrorKind::RosNotInstalled => "ROS_NOT_INSTALLED",
            RosErrorKind::RosEnvironmentInvalid => "ROS_ENVIRONMENT_INVALID",
            RosErrorKind::RclpyUnavailable => "RCLPY_UNAVAILABLE",
            RosErrorKind::CliUnavailable => "CLI_UNAVAILABLE",
            RosErrorKind::DdsDiscoveryTimeout => "DDS_DISCOVERY_TIMEOUT",
            RosErrorKind::TypeSupportMissing => "TYPE_SUPPORT_MISSING",
            RosErrorKind::TopicSampleTimeout => "TOPIC_SAMPLE_TIMEOUT",
            RosErrorKind::TfUnavailable => "TF_UNAVAILABLE",
            RosErrorKind::LifecycleUnsupported => "LIFECYCLE_UNSUPPORTED",
            RosErrorKind::PermissionDenied => "PERMISSION_DENIED",
            RosErrorKind::ProviderError => "PROVIDER_ERROR",
            RosErrorKind::Timeout => "TIMEOUT",
        }
    }

    pub fn from_code(code: &str) -> RosErrorKind {
        match code {
            "ROS_NOT_INSTALLED" => RosErrorKind::RosNotInstalled,
            "ROS_ENVIRONMENT_INVALID" => RosErrorKind::RosEnvironmentInvalid,
            "RCLPY_UNAVAILABLE" => RosErrorKind::RclpyUnavailable,
            "CLI_UNAVAILABLE" => RosErrorKind::CliUnavailable,
            "DDS_DISCOVERY_TIMEOUT" => RosErrorKind::DdsDiscoveryTimeout,
            "TYPE_SUPPORT_MISSING" => RosErrorKind::TypeSupportMissing,
            "TOPIC_SAMPLE_TIMEOUT" => RosErrorKind::TopicSampleTimeout,
            "TF_UNAVAILABLE" => RosErrorKind::TfUnavailable,
            "LIFECYCLE_UNSUPPORTED" => RosErrorKind::LifecycleUnsupported,
            "PERMISSION_DENIED" => RosErrorKind::PermissionDenied,
            "TIMEOUT" => RosErrorKind::Timeout,
            _ => RosErrorKind::ProviderError,
        }
    }

    /// Map onto the Robot Doctor failure-semantics status model.
    pub fn check_status(self) -> CheckStatus {
        match self {
            RosErrorKind::RosNotInstalled
            | RosErrorKind::RclpyUnavailable
            | RosErrorKind::CliUnavailable
            | RosErrorKind::TfUnavailable => CheckStatus::Unavailable,
            RosErrorKind::RosEnvironmentInvalid => CheckStatus::Error,
            RosErrorKind::TypeSupportMissing => CheckStatus::DependencyMissing,
            RosErrorKind::DdsDiscoveryTimeout
            | RosErrorKind::TopicSampleTimeout
            | RosErrorKind::Timeout => CheckStatus::Timeout,
            RosErrorKind::LifecycleUnsupported => CheckStatus::Unsupported,
            RosErrorKind::PermissionDenied => CheckStatus::PermissionDenied,
            RosErrorKind::ProviderError => CheckStatus::Error,
        }
    }
}

#[derive(Debug, Clone)]
pub struct RosError {
    pub kind: RosErrorKind,
    pub message: String,
}

impl RosError {
    pub fn new(kind: RosErrorKind, message: impl Into<String>) -> Self {
        Self {
            kind,
            message: message.into(),
        }
    }

    /// `CODE: message` — the code stays greppable in evidence and errors.
    pub fn display(&self) -> String {
        format!("{}: {}", self.kind.code(), self.message)
    }
}

pub type RosResult<T> = Result<T, RosError>;
