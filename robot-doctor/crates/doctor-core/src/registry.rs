//! Plugin registry: discovers plugin manifests and manages plugin processes.

use doctor_domain::{CheckDefinition, Platform, PluginId};
use doctor_plugin_host::{scan_plugins_dir, ManagedPlugin, NegotiatedCapabilities};
use serde::Serialize;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use tokio::sync::Mutex;

pub const HOST_VERSION: &str = env!("CARGO_PKG_VERSION");

/// One discovered plugin: managed process + capabilities negotiated on
/// first contact.
pub struct RegisteredPlugin {
    pub managed: ManagedPlugin,
    /// Populated after the first successful CAPABILITIES exchange.
    negotiated: Mutex<Option<NegotiatedCapabilities>>,
}

impl RegisteredPlugin {
    pub fn id(&self) -> &PluginId {
        &self.managed.manifest().id
    }

    /// Check definitions for scheduling. Prefers the running plugin's
    /// negotiated answer, falls back to the manifest before first contact.
    pub async fn check_definitions(&self) -> Vec<CheckDefinition> {
        let negotiated = self.negotiated.lock().await;
        match negotiated.as_ref() {
            Some(caps) => caps
                .checks
                .iter()
                .cloned()
                .map(|c| c.into_definition(self.id()))
                .collect(),
            None => self.managed.manifest().check_definitions(),
        }
    }

    /// Ensure the plugin process runs and capabilities are negotiated.
    pub async fn connect(
        &self,
    ) -> Result<doctor_plugin_host::PluginHandle, doctor_plugin_host::HostError> {
        let handle = self.managed.ensure_running().await?;
        let mut negotiated = self.negotiated.lock().await;
        if negotiated.is_none() {
            let caps = handle.capabilities().await?;
            *negotiated = Some(caps);
        }
        Ok(handle)
    }
}

/// Serializable status of a plugin for the UI.
#[derive(Debug, Clone, Serialize)]
pub struct PluginSummary {
    pub id: String,
    pub name: String,
    pub version: String,
    pub api_version: u32,
    pub check_count: usize,
    pub restarts: u64,
    /// Load/parse error when the plugin could not be registered at all.
    pub error: Option<String>,
}

/// All plugins known to this core instance.
pub struct PluginRegistry {
    plugins: Vec<Arc<RegisteredPlugin>>,
    /// Plugins whose manifest failed to load — reported, never fatal.
    failed: Vec<(PathBuf, String)>,
}

impl PluginRegistry {
    /// Scan `plugins_dir` for plugin directories. Broken plugins are
    /// recorded as failures; Robot Doctor stays healthy.
    pub fn discover(plugins_dir: &Path) -> Self {
        let (loaded, failed) = scan_plugins_dir(plugins_dir, Platform::current());
        let plugins = loaded
            .into_iter()
            .map(|manifest| {
                Arc::new(RegisteredPlugin {
                    managed: ManagedPlugin::new(manifest, HOST_VERSION),
                    negotiated: Mutex::new(None),
                })
            })
            .collect();
        let failed = failed
            .into_iter()
            .map(|(path, err)| (path, err.to_string()))
            .collect();
        Self { plugins, failed }
    }

    pub fn plugins(&self) -> &[Arc<RegisteredPlugin>] {
        &self.plugins
    }

    pub fn summaries(&self) -> Vec<PluginSummary> {
        let mut out: Vec<PluginSummary> = self
            .plugins
            .iter()
            .map(|p| {
                let m = p.managed.manifest();
                PluginSummary {
                    id: m.id.to_string(),
                    name: m.name.clone(),
                    version: m.version.clone(),
                    api_version: m.api_version,
                    check_count: m.checks.len(),
                    restarts: p.managed.restart_count(),
                    error: None,
                }
            })
            .collect();
        for (path, err) in &self.failed {
            out.push(PluginSummary {
                id: path
                    .file_name()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .to_string(),
                name: path.display().to_string(),
                version: String::new(),
                api_version: 0,
                check_count: 0,
                restarts: 0,
                error: Some(err.clone()),
            });
        }
        out
    }

    pub async fn shutdown(&self) {
        for plugin in &self.plugins {
            plugin.managed.shutdown().await;
        }
    }
}
