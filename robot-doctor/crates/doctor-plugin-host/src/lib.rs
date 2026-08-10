//! # doctor-plugin-host
//!
//! The language-independent process-plugin system:
//! - [`protocol`]: JSONL wire messages (one JSON object per line over stdio).
//! - [`manifest`]: `plugin.yaml` loading and validation.
//! - [`host`]: async host runtime — spawn, handshake, request/response with
//!   timeouts, crash detection and restart.
//! - [`sdk`]: blocking plugin-side run loop for plugins written in Rust.
//!
//! Plugins are separate OS processes, never dynamic libraries: a crashing
//! or misbehaving plugin can never take Robot Doctor down.

pub mod host;
pub mod manifest;
pub mod protocol;
pub mod sdk;

pub use host::{HostError, ManagedPlugin, NegotiatedCapabilities, PluginHandle};
pub use manifest::{load_manifest, scan_plugins_dir, LoadedManifest, ManifestError};
pub use protocol::{HostMessage, PluginError, PluginErrorKind, PluginMessage, PROTOCOL_VERSION};
pub use sdk::{run_plugin_stdio, CheckContext, PluginService, ServiceCapabilities};
