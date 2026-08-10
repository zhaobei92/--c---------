//! # doctor-core
//!
//! The diagnosis engine shared by the desktop app (embedded, local device)
//! and the remote agent: plugin discovery, concurrent check execution with
//! progressive result streaming, and run bookkeeping.
//!
//! Platform-neutral by construction — all platform specifics live behind
//! process plugins.

pub mod engine;
pub mod registry;
pub mod store;

pub use engine::{DiagnosisEvent, Engine};
pub use registry::{PluginRegistry, PluginSummary, RegisteredPlugin};
pub use store::RunStore;
