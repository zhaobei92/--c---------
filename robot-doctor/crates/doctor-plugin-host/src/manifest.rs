//! Loading and validating `plugin.yaml` manifests from disk.

use doctor_domain::{Platform, PluginManifest};
use std::path::{Path, PathBuf};
use thiserror::Error;

use crate::protocol::PROTOCOL_VERSION;

#[derive(Debug, Error)]
pub enum ManifestError {
    #[error("failed to read {path}: {source}")]
    Io {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("invalid plugin.yaml at {path}: {source}")]
    Parse {
        path: PathBuf,
        #[source]
        source: serde_yaml::Error,
    },
    #[error("plugin '{plugin}' requires API version {required}, host supports {supported}")]
    IncompatibleApiVersion {
        plugin: String,
        required: u32,
        supported: u32,
    },
    #[error("plugin '{plugin}' does not support platform {platform:?}")]
    UnsupportedPlatform { plugin: String, platform: Platform },
    #[error("plugin '{plugin}' declares no executable for platform {platform:?}")]
    NoExecutable { plugin: String, platform: Platform },
    #[error("plugin '{plugin}' executable not found at {path}")]
    ExecutableMissing { plugin: String, path: PathBuf },
}

/// A manifest successfully loaded from a plugin directory, with the
/// executable resolved for the current platform.
#[derive(Debug, Clone)]
pub struct LoadedManifest {
    pub manifest: PluginManifest,
    pub plugin_dir: PathBuf,
    pub executable: PathBuf,
}

/// Load `plugin.yaml` from `plugin_dir` and resolve the executable for
/// `platform`. Does not spawn anything.
pub fn load_manifest(
    plugin_dir: &Path,
    platform: Platform,
) -> Result<LoadedManifest, ManifestError> {
    let path = plugin_dir.join("plugin.yaml");
    let text = std::fs::read_to_string(&path).map_err(|source| ManifestError::Io {
        path: path.clone(),
        source,
    })?;
    let manifest: PluginManifest =
        serde_yaml::from_str(&text).map_err(|source| ManifestError::Parse {
            path: path.clone(),
            source,
        })?;

    if manifest.api_version > PROTOCOL_VERSION {
        return Err(ManifestError::IncompatibleApiVersion {
            plugin: manifest.id.to_string(),
            required: manifest.api_version,
            supported: PROTOCOL_VERSION,
        });
    }
    if !manifest.supports_platform(platform) {
        return Err(ManifestError::UnsupportedPlatform {
            plugin: manifest.id.to_string(),
            platform,
        });
    }
    let exe_decl =
        manifest
            .executable_for(platform)
            .ok_or_else(|| ManifestError::NoExecutable {
                plugin: manifest.id.to_string(),
                platform,
            })?;

    let exe_path = resolve_executable(plugin_dir, exe_decl).ok_or_else(|| {
        ManifestError::ExecutableMissing {
            plugin: manifest.id.to_string(),
            path: plugin_dir.join(exe_decl),
        }
    })?;

    Ok(LoadedManifest {
        manifest,
        plugin_dir: plugin_dir.to_path_buf(),
        executable: exe_path,
    })
}

/// Resolve a manifest's executable declaration to an existing file.
///
/// Resolution order:
/// 1. Absolute path as-is (mainly tests/development overrides).
/// 2. Relative to the plugin directory (packaged layout: binary ships
///    next to its `plugin.yaml`).
/// 3. `$ROBOT_DOCTOR_PLUGIN_BIN_DIR/<file name>` when that env var is set.
/// 4. The bare file name next to the host executable (installed layout
///    where all binaries live in one bin directory; also matches
///    `target/debug` during development).
/// 5. `<plugins dir>/../target/{debug,release}/<file name>` — the cargo
///    workspace layout, so `tauri dev` finds freshly built plugins.
fn resolve_executable(plugin_dir: &Path, exe_decl: &str) -> Option<PathBuf> {
    // The plugin process is spawned with `current_dir = plugin_dir`, so the
    // resolved path must be absolute — a relative path would be re-resolved
    // against the plugin dir at spawn time and miss.
    fn absolute(path: PathBuf) -> Option<PathBuf> {
        path.canonicalize().ok()
    }

    let p = Path::new(exe_decl);
    if p.is_absolute() {
        return p.exists().then(|| p.to_path_buf());
    }
    let in_plugin_dir = plugin_dir.join(p);
    if in_plugin_dir.exists() {
        return absolute(in_plugin_dir);
    }
    let file_name = p.file_name()?;
    if let Ok(bin_dir) = std::env::var("ROBOT_DOCTOR_PLUGIN_BIN_DIR") {
        let candidate = Path::new(&bin_dir).join(file_name);
        if candidate.exists() {
            return absolute(candidate);
        }
    }
    if let Ok(host_exe) = std::env::current_exe() {
        if let Some(host_dir) = host_exe.parent() {
            let beside_host = host_dir.join(file_name);
            if beside_host.exists() {
                return absolute(beside_host);
            }
        }
    }
    if let Some(workspace_root) = plugin_dir.parent().and_then(Path::parent) {
        for profile in ["debug", "release"] {
            let candidate = workspace_root.join("target").join(profile).join(file_name);
            if candidate.exists() {
                return absolute(candidate);
            }
        }
    }
    None
}

/// Scan a directory of plugin directories. Unloadable plugins are returned
/// as errors alongside the loadable ones — one broken plugin must never
/// prevent the rest from loading.
pub fn scan_plugins_dir(
    plugins_dir: &Path,
    platform: Platform,
) -> (Vec<LoadedManifest>, Vec<(PathBuf, ManifestError)>) {
    let mut loaded = Vec::new();
    let mut failed = Vec::new();
    let entries = match std::fs::read_dir(plugins_dir) {
        Ok(e) => e,
        Err(source) => {
            failed.push((
                plugins_dir.to_path_buf(),
                ManifestError::Io {
                    path: plugins_dir.to_path_buf(),
                    source,
                },
            ));
            return (loaded, failed);
        }
    };
    for entry in entries.flatten() {
        let dir = entry.path();
        if !dir.is_dir() || !dir.join("plugin.yaml").exists() {
            continue;
        }
        match load_manifest(&dir, platform) {
            Ok(m) => loaded.push(m),
            Err(e) => failed.push((dir, e)),
        }
    }
    loaded.sort_by(|a, b| a.manifest.id.cmp(&b.manifest.id));
    (loaded, failed)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write_manifest(dir: &Path, executable_lines: &str) {
        std::fs::create_dir_all(dir).unwrap();
        let yaml = format!(
            r#"
id: demo
name: Demo
version: 0.1.0
api_version: 1
platforms: [linux, windows]
executable:
{executable_lines}
checks: []
"#
        );
        std::fs::write(dir.join("plugin.yaml"), yaml).unwrap();
    }

    #[test]
    fn missing_executable_is_a_clear_error() {
        let dir = std::env::temp_dir().join(format!("rd-manifest-{}", uuid::Uuid::new_v4()));
        write_manifest(
            &dir,
            "  linux: does-not-exist\n  windows: does-not-exist.exe",
        );
        let err = load_manifest(&dir, Platform::current()).unwrap_err();
        assert!(matches!(err, ManifestError::ExecutableMissing { .. }));
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn manifest_resolves_existing_executable() {
        let dir = std::env::temp_dir().join(format!("rd-manifest-{}", uuid::Uuid::new_v4()));
        write_manifest(&dir, "  linux: plug\n  windows: plug");
        std::fs::write(dir.join("plug"), b"#!/bin/sh\n").unwrap();
        let loaded = load_manifest(&dir, Platform::current()).unwrap();
        assert_eq!(loaded.manifest.id.as_str(), "demo");
        assert!(loaded.executable.ends_with("plug"));
        // Must be absolute: the process is spawned with cwd = plugin dir.
        assert!(loaded.executable.is_absolute());
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn newer_api_version_is_rejected() {
        let dir = std::env::temp_dir().join(format!("rd-manifest-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            dir.join("plugin.yaml"),
            r#"
id: demo
name: Demo
version: 0.1.0
api_version: 999
platforms: [linux, windows]
executable:
  linux: plug
  windows: plug
"#,
        )
        .unwrap();
        let err = load_manifest(&dir, Platform::current()).unwrap_err();
        assert!(matches!(err, ManifestError::IncompatibleApiVersion { .. }));
        std::fs::remove_dir_all(&dir).ok();
    }
}
