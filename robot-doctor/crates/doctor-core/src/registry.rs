//! Plugin registry: discovers plugin manifests (bootstrap metadata) and
//! manages plugin processes. Checks/capabilities always come from the
//! running plugin's CAPABILITIES answer — the manifest never defines them.

use doctor_domain::{CheckDefinition, Platform, PluginId};
use doctor_plugin_host::{
    scan_plugins_dir, HostError, ManagedPlugin, NegotiatedCapabilities, PluginHandle,
};
use serde::Serialize;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use tokio::sync::Mutex;

pub const HOST_VERSION: &str = env!("CARGO_PKG_VERSION");

/// One discovered plugin: managed process + capabilities negotiated on
/// first contact (authoritative runtime source).
pub struct RegisteredPlugin {
    pub managed: ManagedPlugin,
    negotiated: Mutex<Option<NegotiatedCapabilities>>,
    /// Last connect/negotiation error, surfaced in summaries.
    last_error: std::sync::Mutex<Option<String>>,
}

impl RegisteredPlugin {
    pub fn id(&self) -> &PluginId {
        &self.managed.manifest().id
    }

    /// Ensure the plugin process runs and capabilities are negotiated.
    /// Returns the live handle plus the negotiated capabilities.
    pub async fn connect(&self) -> Result<(PluginHandle, NegotiatedCapabilities), HostError> {
        let result = self.connect_inner().await;
        match &result {
            Ok(_) => *self.last_error.lock().expect("last_error lock") = None,
            Err(err) => {
                *self.last_error.lock().expect("last_error lock") = Some(err.to_string());
            }
        }
        result
    }

    async fn connect_inner(&self) -> Result<(PluginHandle, NegotiatedCapabilities), HostError> {
        let handle = self.managed.ensure_running().await?;
        let mut negotiated = self.negotiated.lock().await;
        if negotiated.is_none() {
            *negotiated = Some(handle.capabilities().await?);
        }
        Ok((handle, negotiated.clone().expect("just negotiated")))
    }

    /// Check definitions from the runtime negotiation (spawning the plugin
    /// if needed). This is the only path the scheduler uses.
    pub async fn check_definitions(&self) -> Result<Vec<CheckDefinition>, HostError> {
        let (_, caps) = self.connect().await?;
        Ok(caps
            .checks
            .iter()
            .cloned()
            .map(|c| c.into_definition(self.id()))
            .collect())
    }

    /// Cached negotiated capabilities, if the plugin was contacted already.
    pub async fn cached_capabilities(&self) -> Option<NegotiatedCapabilities> {
        self.negotiated.lock().await.clone()
    }

    pub fn last_error(&self) -> Option<String> {
        self.last_error.lock().expect("last_error lock").clone()
    }
}

/// Serializable status of a plugin for the UI.
#[derive(Debug, Clone, Serialize)]
pub struct PluginSummary {
    pub id: String,
    pub name: String,
    pub version: String,
    pub api_version: u32,
    pub description: String,
    /// Runtime-discovered (empty until first contact).
    pub capabilities: Vec<String>,
    /// Runtime-discovered check ids (empty until first contact).
    pub checks: Vec<String>,
    pub max_concurrency: u32,
    pub restarts: u64,
    /// True once CAPABILITIES has been negotiated with the live process.
    pub contacted: bool,
    /// Manifest load error or last connect error.
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
                    last_error: std::sync::Mutex::new(None),
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

    pub async fn summaries(&self) -> Vec<PluginSummary> {
        let mut out = Vec::new();
        for p in &self.plugins {
            let m = p.managed.manifest();
            let caps = p.cached_capabilities().await;
            out.push(PluginSummary {
                id: m.id.to_string(),
                name: m.name.clone(),
                version: m.version.clone(),
                api_version: m.api_version,
                description: m.description.clone(),
                capabilities: caps
                    .as_ref()
                    .map(|c| c.capabilities.iter().map(|x| x.0.clone()).collect())
                    .unwrap_or_default(),
                checks: caps
                    .as_ref()
                    .map(|c| c.checks.iter().map(|x| x.id.to_string()).collect())
                    .unwrap_or_default(),
                max_concurrency: caps.as_ref().map(|c| c.max_concurrency).unwrap_or(1),
                restarts: p.managed.restart_count(),
                contacted: caps.is_some(),
                error: p.last_error(),
            });
        }
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
                description: String::new(),
                capabilities: vec![],
                checks: vec![],
                max_concurrency: 0,
                restarts: 0,
                contacted: false,
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
