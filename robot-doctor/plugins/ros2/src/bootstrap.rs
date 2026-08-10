//! ROS runtime bootstrap: obtain a ROS environment without requiring
//! Robot Doctor itself to be launched from a sourced shell.
//!
//! Linux: a controlled `bash --noprofile --norc` sources the configured
//! setup chain (base ROS → overlays, in order) and dumps `env -0`.
//! Windows: `cmd /d /c "call a.bat && call b.bat && set"`.
//! User shell startup files are never sourced implicitly.

use crate::error::{RosError, RosErrorKind, RosResult};
use doctor_domain::ros::{RosRuntimeConfig, RosRuntimeMode};
use std::collections::BTreeMap;
use std::path::Path;
use std::process::Command;
use std::time::Duration;

pub type EnvMap = BTreeMap<String, String>;

/// Run a command to completion with a hard deadline.
pub fn run_bounded(
    mut cmd: Command,
    timeout: Duration,
) -> RosResult<(i32, String, String, Duration)> {
    use std::io::Read;
    use std::process::Stdio;
    let started = std::time::Instant::now();
    cmd.stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = cmd
        .spawn()
        .map_err(|e| RosError::new(RosErrorKind::ProviderError, format!("spawn failed: {e}")))?;
    let mut stdout = child.stdout.take().expect("piped");
    let mut stderr = child.stderr.take().expect("piped");
    let out_thread = std::thread::spawn(move || {
        let mut buf = Vec::new();
        let _ = stdout.read_to_end(&mut buf);
        buf
    });
    let err_thread = std::thread::spawn(move || {
        let mut buf = Vec::new();
        let _ = stderr.read_to_end(&mut buf);
        buf
    });
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                let out = out_thread.join().unwrap_or_default();
                let err = err_thread.join().unwrap_or_default();
                return Ok((
                    status.code().unwrap_or(-1),
                    String::from_utf8_lossy(&out).into_owned(),
                    String::from_utf8_lossy(&err).into_owned(),
                    started.elapsed(),
                ));
            }
            Ok(None) => {
                if started.elapsed() > timeout {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(RosError::new(
                        RosErrorKind::Timeout,
                        format!("command exceeded {} ms", timeout.as_millis()),
                    ));
                }
                std::thread::sleep(Duration::from_millis(25));
            }
            Err(e) => {
                return Err(RosError::new(
                    RosErrorKind::ProviderError,
                    format!("wait failed: {e}"),
                ))
            }
        }
    }
}

/// Expand `${VAR}` references in extra_env values against the captured env.
fn expand(value: &str, env: &EnvMap) -> String {
    let mut out = value.to_owned();
    for (key, val) in env {
        let pattern = format!("${{{key}}}");
        if out.contains(&pattern) {
            out = out.replace(&pattern, val);
        }
    }
    out
}

#[cfg(unix)]
fn capture_setup_env(scripts: &[String]) -> RosResult<EnvMap> {
    for script in scripts {
        if !Path::new(script).exists() {
            return Err(RosError::new(
                RosErrorKind::RosEnvironmentInvalid,
                format!("setup script not found: {script}"),
            ));
        }
    }
    let mut chain = String::from("set -e; ");
    for script in scripts {
        chain.push_str(&format!("source '{}'; ", script.replace('\'', "'\\''")));
    }
    chain.push_str("env -0");
    let mut cmd = Command::new("bash");
    cmd.arg("--noprofile").arg("--norc").arg("-c").arg(chain);
    let (code, stdout, stderr, _) = run_bounded(cmd, Duration::from_secs(20))?;
    if code != 0 {
        return Err(RosError::new(
            RosErrorKind::RosEnvironmentInvalid,
            format!("setup chain exited with {code}: {}", stderr.trim()),
        ));
    }
    let mut env = EnvMap::new();
    for entry in stdout.split('\0') {
        if let Some((key, value)) = entry.split_once('=') {
            env.insert(key.to_owned(), value.to_owned());
        }
    }
    Ok(env)
}

#[cfg(windows)]
fn capture_setup_env(scripts: &[String]) -> RosResult<EnvMap> {
    for script in scripts {
        if !Path::new(script).exists() {
            return Err(RosError::new(
                RosErrorKind::RosEnvironmentInvalid,
                format!("setup script not found: {script}"),
            ));
        }
    }
    let mut chain = String::new();
    for script in scripts {
        chain.push_str(&format!("call \"{script}\" && "));
    }
    chain.push_str("set");
    let mut cmd = Command::new("cmd");
    cmd.arg("/d").arg("/c").arg(chain);
    let (code, stdout, stderr, _) = run_bounded(cmd, Duration::from_secs(30))?;
    if code != 0 {
        return Err(RosError::new(
            RosErrorKind::RosEnvironmentInvalid,
            format!("setup chain exited with {code}: {}", stderr.trim()),
        ));
    }
    let mut env = EnvMap::new();
    for line in stdout.lines() {
        if let Some((key, value)) = line.split_once('=') {
            env.insert(key.to_owned(), value.to_owned());
        }
    }
    Ok(env)
}

/// Bootstrap the environment for a runtime configuration.
pub fn bootstrap_env(config: &RosRuntimeConfig) -> RosResult<EnvMap> {
    let mut env: EnvMap = match config.mode {
        RosRuntimeMode::Inherited => std::env::vars().collect(),
        RosRuntimeMode::Configured | RosRuntimeMode::Auto => {
            if config.setup_scripts.is_empty() {
                // AUTO with no scripts: fall back to the inherited env if it
                // already carries ROS, otherwise there is nothing to run.
                let inherited: EnvMap = std::env::vars().collect();
                if inherited.contains_key("ROS_DISTRO") {
                    inherited
                } else {
                    return Err(RosError::new(
                        RosErrorKind::RosNotInstalled,
                        "no ROS environment inherited and no setup scripts configured",
                    ));
                }
            } else {
                capture_setup_env(&config.setup_scripts)?
            }
        }
        RosRuntimeMode::Fixture => EnvMap::new(),
    };

    if let Some(domain) = config.domain_id {
        env.insert("ROS_DOMAIN_ID".into(), domain.to_string());
    }
    if let Some(rmw) = &config.rmw {
        env.insert("RMW_IMPLEMENTATION".into(), rmw.clone());
    }
    let extras: Vec<(String, String)> = config
        .extra_env
        .iter()
        .map(|(k, v)| (k.clone(), expand(v, &env)))
        .collect();
    for (key, value) in extras {
        env.insert(key, value);
    }
    Ok(env)
}

/// Resolve an executable name inside a captured environment's PATH.
pub fn which_in(env: &EnvMap, name: &str) -> Option<String> {
    let path = env.get("PATH")?;
    let sep = if cfg!(windows) { ';' } else { ':' };
    let candidates: Vec<String> = if cfg!(windows) {
        vec![
            format!("{name}.exe"),
            format!("{name}.bat"),
            name.to_owned(),
        ]
    } else {
        vec![name.to_owned()]
    };
    for dir in path.split(sep) {
        for candidate in &candidates {
            let full = Path::new(dir).join(candidate);
            if full.is_file() {
                return Some(full.display().to_string());
            }
        }
    }
    None
}

/// Conservative ROS runtime discovery. Never scans whole disks; only
/// well-known install locations and the inherited environment.
pub fn discover_runtimes() -> Vec<RosRuntimeConfig> {
    let mut found = Vec::new();

    // 1) Inherited environment already carries ROS.
    if let Ok(distro) = std::env::var("ROS_DISTRO") {
        found.push(RosRuntimeConfig {
            id: format!("inherited:{distro}"),
            name: format!("Inherited environment ({distro})"),
            mode: RosRuntimeMode::Inherited,
            setup_scripts: vec![],
            domain_id: None,
            rmw: None,
            extra_env: BTreeMap::new(),
            fixture: None,
            preferred_provider: None,
        });
    }

    // 2) Standard installation locations.
    #[cfg(unix)]
    {
        if let Ok(entries) = std::fs::read_dir("/opt/ros") {
            let mut distros: Vec<_> = entries
                .flatten()
                .filter(|e| e.path().join("setup.bash").exists())
                .collect();
            distros.sort_by_key(|e| e.file_name());
            for entry in distros {
                let distro = entry.file_name().to_string_lossy().to_string();
                let setup = entry.path().join("setup.bash").display().to_string();
                found.push(RosRuntimeConfig {
                    id: format!("auto:{distro}"),
                    name: format!("ROS 2 {distro} (/opt/ros/{distro})"),
                    mode: RosRuntimeMode::Configured,
                    setup_scripts: vec![setup],
                    domain_id: None,
                    rmw: None,
                    extra_env: BTreeMap::new(),
                    fixture: None,
                    preferred_provider: None,
                });
            }
        }
    }
    #[cfg(windows)]
    {
        for base in ["C:\\dev", "C:\\opt\\ros", "C:\\ros2"] {
            if let Ok(entries) = std::fs::read_dir(base) {
                for entry in entries.flatten() {
                    let setup = entry.path().join("local_setup.bat");
                    if setup.exists() {
                        let name = entry.file_name().to_string_lossy().to_string();
                        found.push(RosRuntimeConfig {
                            id: format!("auto:{name}"),
                            name: format!("ROS 2 {name} ({})", entry.path().display()),
                            mode: RosRuntimeMode::Configured,
                            setup_scripts: vec![setup.display().to_string()],
                            domain_id: None,
                            rmw: None,
                            extra_env: BTreeMap::new(),
                            fixture: None,
                            preferred_provider: None,
                        });
                    }
                }
            }
        }
    }

    found
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extra_env_expansion_references_captured_values() {
        let mut env = EnvMap::new();
        env.insert("PATH".into(), "/usr/bin".into());
        assert_eq!(expand("/opt/shim:${PATH}", &env), "/opt/shim:/usr/bin");
        assert_eq!(expand("plain", &env), "plain");
    }

    #[cfg(unix)]
    #[test]
    fn missing_setup_script_is_environment_invalid() {
        let config = RosRuntimeConfig {
            id: "t".into(),
            name: "t".into(),
            mode: RosRuntimeMode::Configured,
            setup_scripts: vec!["/does/not/exist/setup.bash".into()],
            domain_id: None,
            rmw: None,
            extra_env: BTreeMap::new(),
            fixture: None,
            preferred_provider: None,
        };
        let err = bootstrap_env(&config).unwrap_err();
        assert_eq!(err.kind, RosErrorKind::RosEnvironmentInvalid);
    }

    #[cfg(unix)]
    #[test]
    fn setup_chain_is_sourced_in_order_without_user_rc_files() {
        let dir = std::env::temp_dir().join(format!("rd-ros-boot-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let a = dir.join("a.bash");
        let b = dir.join("b.bash");
        std::fs::write(&a, "export CHAIN=a\nexport BASE=1\n").unwrap();
        // b overrides CHAIN — later overlays win, like ROS overlays.
        std::fs::write(&b, "export CHAIN=$CHAIN-b\n").unwrap();
        let config = RosRuntimeConfig {
            id: "t".into(),
            name: "t".into(),
            mode: RosRuntimeMode::Configured,
            setup_scripts: vec![a.display().to_string(), b.display().to_string()],
            domain_id: Some(42),
            rmw: Some("rmw_test".into()),
            extra_env: BTreeMap::from([("EXTRA".to_string(), "x-${CHAIN}".to_string())]),
            fixture: None,
            preferred_provider: None,
        };
        let env = bootstrap_env(&config).unwrap();
        assert_eq!(env.get("CHAIN").map(String::as_str), Some("a-b"));
        assert_eq!(env.get("BASE").map(String::as_str), Some("1"));
        assert_eq!(env.get("ROS_DOMAIN_ID").map(String::as_str), Some("42"));
        assert_eq!(
            env.get("RMW_IMPLEMENTATION").map(String::as_str),
            Some("rmw_test")
        );
        assert_eq!(env.get("EXTRA").map(String::as_str), Some("x-a-b"));
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn auto_without_ros_reports_not_installed() {
        // Only meaningful when the test environment itself has no ROS.
        if std::env::var("ROS_DISTRO").is_ok() {
            return;
        }
        let config = RosRuntimeConfig {
            id: "auto".into(),
            name: "auto".into(),
            mode: RosRuntimeMode::Auto,
            setup_scripts: vec![],
            domain_id: None,
            rmw: None,
            extra_env: BTreeMap::new(),
            fixture: None,
            preferred_provider: None,
        };
        let err = bootstrap_env(&config).unwrap_err();
        assert_eq!(err.kind, RosErrorKind::RosNotInstalled);
    }
}
