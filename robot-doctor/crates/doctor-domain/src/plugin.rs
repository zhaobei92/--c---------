//! Plugin manifests: the static contract a process plugin publishes.

use crate::check::{CheckCost, CheckDefinition, DiagnosticMode, Platform};
use crate::ids::{ActionId, CheckId, PluginId};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// CPU architectures a plugin ships binaries for.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Architecture {
    X86_64,
    Aarch64,
}

/// A named capability a plugin provides, e.g. `system`, `ros`, `gpu.nvidia`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(transparent)]
pub struct PluginCapability(pub String);

/// A check as declared in `plugin.yaml` (plugin id is implied by the file).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CheckDeclaration {
    pub id: CheckId,
    pub name: String,
    #[serde(default)]
    pub description: String,
    pub cost: CheckCost,
    pub timeout_ms: u64,
    #[serde(default)]
    pub platforms: Vec<Platform>,
    #[serde(default)]
    pub depends_on: Vec<CheckId>,
    pub modes: Vec<DiagnosticMode>,
}

impl CheckDeclaration {
    pub fn into_definition(self, plugin_id: &PluginId) -> CheckDefinition {
        CheckDefinition {
            id: self.id,
            plugin_id: plugin_id.clone(),
            name: self.name,
            description: self.description,
            cost: self.cost,
            timeout_ms: self.timeout_ms,
            platforms: self.platforms,
            depends_on: self.depends_on,
            modes: self.modes,
        }
    }
}

/// An action as declared in `plugin.yaml`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ActionDeclaration {
    pub id: ActionId,
    pub name: String,
    #[serde(default)]
    pub description: String,
    /// Destructive actions require explicit user confirmation and are
    /// never auto-run.
    #[serde(default)]
    pub destructive: bool,
    pub timeout_ms: u64,
    #[serde(default)]
    pub platforms: Vec<Platform>,
}

/// The parsed contents of a plugin's `plugin.yaml`.
///
/// The manifest is **static bootstrap metadata only**: identity, version,
/// compatibility and how to start the process. The authoritative source
/// for capabilities, checks and actions is the running plugin's
/// CAPABILITIES response — never this file.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PluginManifest {
    pub id: PluginId,
    pub name: String,
    pub version: String,
    /// Plugin protocol API version this plugin implements.
    pub api_version: u32,
    #[serde(default)]
    pub description: String,
    pub platforms: Vec<Platform>,
    #[serde(default)]
    pub architectures: Vec<Architecture>,
    /// Executable per platform, relative to the plugin directory
    /// (or absolute, mainly for tests/development).
    pub executable: BTreeMap<Platform, String>,
    #[serde(default)]
    pub args: Vec<String>,
    /// Legacy (pre-runtime-negotiation) fields. Still parsed so old
    /// manifests load, but never used as runtime state.
    #[serde(default)]
    pub capabilities: Vec<PluginCapability>,
    #[serde(default)]
    pub checks: Vec<CheckDeclaration>,
    #[serde(default)]
    pub actions: Vec<ActionDeclaration>,
}

impl PluginManifest {
    pub fn supports_platform(&self, platform: Platform) -> bool {
        self.platforms.contains(&platform)
    }

    pub fn executable_for(&self, platform: Platform) -> Option<&str> {
        self.executable.get(&platform).map(String::as_str)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bootstrap_manifest_parses() {
        let yaml = r#"
id: system
name: System Diagnostics
version: 0.1.0
api_version: 1
description: Cross-platform system diagnostics
platforms: [linux, windows]
architectures: [x86_64, aarch64]
executable:
  linux: robot-doctor-plugin-system
  windows: robot-doctor-plugin-system.exe
"#;
        let manifest: PluginManifest = serde_yaml::from_str(yaml).unwrap();
        assert_eq!(manifest.id, PluginId::from("system"));
        assert!(manifest.supports_platform(Platform::Linux));
        assert!(manifest.supports_platform(Platform::Windows));
        assert_eq!(
            manifest.executable_for(Platform::Windows),
            Some("robot-doctor-plugin-system.exe")
        );
        // Checks are not declared here: the runtime CAPABILITIES answer is
        // the single authoritative source.
        assert!(manifest.checks.is_empty());
    }

    #[test]
    fn legacy_manifest_with_checks_still_loads() {
        let yaml = r#"
id: legacy
name: Legacy
version: 0.1.0
api_version: 1
platforms: [linux]
executable:
  linux: legacy-plugin
checks:
  - id: legacy.check
    name: Old-style declaration
    cost: FAST
    timeout_ms: 5000
    modes: [QUICK]
"#;
        // Old manifests parse, but their check lists are legacy data only.
        let manifest: PluginManifest = serde_yaml::from_str(yaml).unwrap();
        assert_eq!(manifest.checks.len(), 1);
    }
}
