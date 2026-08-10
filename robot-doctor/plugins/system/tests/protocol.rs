//! End-to-end plugin protocol test: spawns the real system plugin binary
//! and drives it through the JSONL protocol via the host runtime.

use doctor_domain::{CheckId, CheckRequest, CheckStatus, DeviceId, Platform, PluginManifest};
use doctor_plugin_host::{LoadedManifest, PluginHandle};
use std::collections::BTreeMap;
use std::path::PathBuf;

fn loaded_manifest() -> LoadedManifest {
    let plugin_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let manifest: PluginManifest =
        serde_yaml::from_str(&std::fs::read_to_string(plugin_dir.join("plugin.yaml")).unwrap())
            .unwrap();
    assert!(manifest.supports_platform(Platform::current()));
    LoadedManifest {
        manifest,
        plugin_dir,
        executable: PathBuf::from(env!("CARGO_BIN_EXE_robot-doctor-plugin-system")),
    }
}

fn request(check: &str) -> CheckRequest {
    CheckRequest {
        check_id: CheckId::from(check),
        device_id: DeviceId::from("local"),
        mode: None,
        params: BTreeMap::new(),
        timeout_ms: 10_000,
    }
}

#[tokio::test]
async fn handshake_capabilities_check_and_shutdown() {
    let handle = PluginHandle::spawn(&loaded_manifest()).unwrap();
    handle.handshake("test-host").await.unwrap();

    let caps = handle.capabilities().await.unwrap();
    assert!(caps.checks.iter().any(|c| c.id.as_str() == "system.cpu"));
    assert!(caps.capabilities.iter().any(|c| c.0 == "system"));

    let result = handle.run_check(request("system.os")).await.unwrap();
    assert_eq!(result.status, CheckStatus::Passed);
    assert!(!result.observations.is_empty());
    assert!(!result.evidence.is_empty());
    assert_eq!(result.plugin_id.as_str(), "system");

    handle.ping().await.unwrap();
    handle.shutdown().await;
    assert!(!handle.is_alive());
}

#[tokio::test]
async fn concurrent_checks_are_answered_by_request_id() {
    let handle = PluginHandle::spawn(&loaded_manifest()).unwrap();
    handle.handshake("test-host").await.unwrap();

    // Fire several checks without awaiting in between; correlation must
    // route every response to the right caller even though the plugin
    // answers sequentially.
    let (a, b, c) = tokio::join!(
        handle.run_check(request("system.memory")),
        handle.run_check(request("system.uptime")),
        handle.run_check(request("system.time")),
    );
    assert_eq!(a.unwrap().check_id.as_str(), "system.memory");
    assert_eq!(b.unwrap().check_id.as_str(), "system.uptime");
    assert_eq!(c.unwrap().check_id.as_str(), "system.time");
    handle.shutdown().await;
}

#[tokio::test]
async fn unknown_check_reports_unsupported_not_crash() {
    let handle = PluginHandle::spawn(&loaded_manifest()).unwrap();
    handle.handshake("test-host").await.unwrap();

    let result = handle.run_check(request("system.nope")).await.unwrap();
    assert_eq!(result.status, CheckStatus::Unsupported);
    assert!(result.error.is_some());

    // The plugin is still healthy afterwards.
    let ok = handle.run_check(request("system.os")).await.unwrap();
    assert_eq!(ok.status, CheckStatus::Passed);
    handle.shutdown().await;
}
