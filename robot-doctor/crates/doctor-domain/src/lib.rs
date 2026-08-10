//! # doctor-domain
//!
//! The stable domain model shared by every Robot Doctor component:
//! desktop app, embedded core, remote agent, plugin host and rules.
//!
//! Design rules:
//! - Types here are serialization-stable (serde) and platform-neutral.
//! - No I/O, no platform code, no plugin-protocol details.
//! - Every conclusion type (Finding, RootCause) references Evidence IDs so
//!   results remain traceable and inspectable.

pub mod action;
pub mod baseline;
pub mod check;
pub mod comparison;
pub mod device;
pub mod evaluation;
pub mod evidence;
pub mod finding;
pub mod health;
pub mod ids;
pub mod plugin;
pub mod profile;
pub mod ros;

pub use action::{ActionDefinition, ActionResult, ActionStatus};
pub use baseline::{
    Baseline, BaselineAttributeDiff, BaselineCompatibility, BaselineDiff, BaselineDiffEntity,
    BaselineEntity, BaselineSource, CompatibilityNote, EntityDiffState, EntityStability,
    NumericSummary, RunProjection,
};
pub use check::{
    CheckCost, CheckDefinition, CheckError, CheckRequest, CheckResult, CheckRun, DiagnosticMode,
    Platform,
};
pub use comparison::{
    AttributeValue, ComparisonEntity, EntityKey, NamespaceAvailability, ProjectionReport,
};
pub use device::{Capability, ConnectionState, Device, DeviceEndpoint};
pub use evaluation::{EvaluationRun, ExpectationResult, ExpectationStatus};
pub use evidence::{Evidence, EvidenceKind, Metric, Observation, ObservationValue};
pub use finding::{Finding, Incident, IncidentState, Remediation, RootCause};
pub use health::{CheckStatus, HealthState, Severity};
pub use ids::{
    ActionId, BaselineId, CheckId, DeviceId, EvidenceId, FindingId, IncidentId, ObservationId,
    PluginId, ProfileId, RootCauseId, RuleId, RunId,
};
pub use plugin::{
    ActionDeclaration, Architecture, CheckDeclaration, PluginCapability, PluginManifest,
};
pub use profile::{
    Constraint, ConstraintValue, DeviceProfileAssignment, Expectation, Profile, ProfileStatus,
    RelationshipMode, Requirement, Selector, PROFILE_SCHEMA_VERSION,
};
pub use ros::{
    QosCompatibility, QosDurability, QosDuration, QosHistory, QosLiveliness, QosReliability,
    RosActionInfo, RosClockObservation, RosDiagnosticStatus, RosEndpointInfo, RosEndpointType,
    RosEnvironmentInfo, RosGraphSnapshot, RosLifecycleState, RosNodeInfo, RosQosProfile,
    RosRuntimeConfig, RosRuntimeMode, RosServiceInfo, RosTfEdge, RosTfQueryResult, RosTfSnapshot,
    RosTopicInfo, RosTopicSample,
};
