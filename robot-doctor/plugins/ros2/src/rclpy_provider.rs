//! RclpyProvider: preferred provider. Manages a persistent Python helper
//! (py/rclpy_provider.py) launched with the bootstrapped ROS environment,
//! speaking one JSON object per line. Every request has a hard deadline;
//! a hung helper is killed and respawned on the next request — one
//! ordinary timeout never kills the plugin.

use crate::bootstrap::EnvMap;
use crate::error::{RosError, RosErrorKind, RosResult};
use crate::provider::{ProviderOp, RosProvider};
use doctor_domain::ros::{
    RosClockObservation, RosDiagnosticStatus, RosGraphSnapshot, RosLifecycleState,
    RosTfQueryResult, RosTfSnapshot, RosTopicSample,
};
use serde_json::json;
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc;
use std::sync::Mutex;
use std::time::Duration;

struct Helper {
    child: Child,
    stdin: ChildStdin,
    rx: mpsc::Receiver<serde_json::Value>,
    next_id: u64,
}

pub struct RclpyProvider {
    python: String,
    script: std::path::PathBuf,
    env: EnvMap,
    helper: Mutex<Option<Helper>>,
}

impl RclpyProvider {
    /// Start the helper and verify rclpy is importable. Fails with
    /// RCLPY_UNAVAILABLE when the environment cannot support it.
    pub fn start(python: String, script: std::path::PathBuf, env: EnvMap) -> RosResult<Self> {
        let provider = Self {
            python,
            script,
            env,
            helper: Mutex::new(None),
        };
        let hello = provider.request("hello", json!({}), Duration::from_secs(20))?;
        if !hello
            .get("rclpy")
            .and_then(|v| v.as_bool())
            .unwrap_or(false)
        {
            let reason = hello
                .get("rclpy_error")
                .and_then(|v| v.as_str())
                .unwrap_or("rclpy import failed");
            return Err(RosError::new(RosErrorKind::RclpyUnavailable, reason));
        }
        Ok(provider)
    }

    fn spawn(&self) -> RosResult<Helper> {
        let mut cmd = Command::new(&self.python);
        cmd.arg(&self.script)
            .env_clear()
            .envs(&self.env)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        let mut child = cmd.spawn().map_err(|e| {
            RosError::new(
                RosErrorKind::RclpyUnavailable,
                format!("failed to start python helper '{}': {e}", self.python),
            )
        })?;
        let stdin = child.stdin.take().expect("piped");
        let stdout = child.stdout.take().expect("piped");
        let (tx, rx) = mpsc::channel();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                let Ok(line) = line else { break };
                if let Ok(value) = serde_json::from_str::<serde_json::Value>(&line) {
                    if tx.send(value).is_err() {
                        break;
                    }
                }
            }
        });
        Ok(Helper {
            child,
            stdin,
            rx,
            next_id: 1,
        })
    }

    /// One request/response with a hard deadline. Requests are serialized:
    /// the helper's node context is single-threaded by design.
    fn request(
        &self,
        op: &str,
        args: serde_json::Value,
        timeout: Duration,
    ) -> RosResult<serde_json::Value> {
        let mut slot = self.helper.lock().expect("helper lock");
        if slot.is_none() {
            *slot = Some(self.spawn()?);
        }
        let helper = slot.as_mut().expect("just ensured");
        let id = helper.next_id.to_string();
        helper.next_id += 1;
        let line = serde_json::to_string(&json!({"id": id, "op": op, "args": args}))
            .expect("request serializes");
        if writeln!(helper.stdin, "{line}").is_err() {
            // Helper is gone; drop it so the next request respawns.
            let _ = helper.child.kill();
            *slot = None;
            return Err(RosError::new(
                RosErrorKind::ProviderError,
                "python helper pipe closed",
            ));
        }
        let deadline = std::time::Instant::now() + timeout;
        loop {
            let remaining = deadline.saturating_duration_since(std::time::Instant::now());
            if remaining.is_zero() {
                // Hung helper: kill it so the provider stays recoverable.
                let _ = helper.child.kill();
                let _ = helper.child.wait();
                *slot = None;
                return Err(RosError::new(
                    RosErrorKind::Timeout,
                    format!("provider op '{op}' exceeded {} ms", timeout.as_millis()),
                ));
            }
            match helper.rx.recv_timeout(remaining) {
                Ok(msg) => {
                    if msg.get("id").and_then(|v| v.as_str()) != Some(id.as_str()) {
                        continue; // stale response from a killed request
                    }
                    if msg.get("ok").and_then(|v| v.as_bool()) == Some(true) {
                        return Ok(msg.get("data").cloned().unwrap_or_default());
                    }
                    let kind = msg
                        .get("kind")
                        .and_then(|v| v.as_str())
                        .unwrap_or("PROVIDER_ERROR");
                    let message = msg
                        .get("message")
                        .and_then(|v| v.as_str())
                        .unwrap_or("provider error")
                        .to_owned();
                    return Err(RosError::new(RosErrorKind::from_code(kind), message));
                }
                Err(mpsc::RecvTimeoutError::Timeout) => continue,
                Err(mpsc::RecvTimeoutError::Disconnected) => {
                    let _ = helper.child.kill();
                    *slot = None;
                    return Err(RosError::new(
                        RosErrorKind::ProviderError,
                        "python helper exited unexpectedly",
                    ));
                }
            }
        }
    }

    fn parse<T: serde::de::DeserializeOwned>(value: serde_json::Value) -> RosResult<T> {
        serde_json::from_value(value).map_err(|e| {
            RosError::new(
                RosErrorKind::ProviderError,
                format!("provider returned malformed data: {e}"),
            )
        })
    }

    pub fn shutdown(&self) {
        let mut slot = self.helper.lock().expect("helper lock");
        if let Some(helper) = slot.as_mut() {
            let _ = writeln!(helper.stdin, r#"{{"id":"bye","op":"shutdown"}}"#);
            let _ = helper.child.wait();
        }
        *slot = None;
    }
}

impl Drop for RclpyProvider {
    fn drop(&mut self) {
        self.shutdown();
    }
}

impl RosProvider for RclpyProvider {
    fn name(&self) -> &'static str {
        "rclpy"
    }

    fn supports(&self, _op: ProviderOp) -> bool {
        true
    }

    fn graph_snapshot(&self, discovery_s: f64) -> RosResult<RosGraphSnapshot> {
        let data = self.request(
            "graph",
            json!({"discovery_s": discovery_s}),
            Duration::from_secs_f64(discovery_s + 15.0),
        )?;
        let mut snapshot: RosGraphSnapshot = Self::parse(data)?;
        snapshot.provider = "rclpy".into();
        snapshot.captured_at = chrono::Utc::now();
        snapshot.normalize();
        Ok(snapshot)
    }

    fn topic_sample(&self, topic: &str, duration_s: f64) -> RosResult<RosTopicSample> {
        let data = self.request(
            "sample",
            json!({"topic": topic, "duration_s": duration_s}),
            Duration::from_secs_f64(duration_s + 10.0),
        )?;
        Self::parse(data)
    }

    fn tf_snapshot(&self, listen_s: f64) -> RosResult<RosTfSnapshot> {
        let data = self.request(
            "tf",
            json!({"listen_s": listen_s}),
            Duration::from_secs_f64(listen_s + 10.0),
        )?;
        Self::parse(data)
    }

    fn tf_query(&self, source: &str, target: &str, timeout_s: f64) -> RosResult<RosTfQueryResult> {
        let data = self.request(
            "tf_query",
            json!({"source": source, "target": target, "timeout_s": timeout_s}),
            Duration::from_secs_f64(timeout_s + 10.0),
        )?;
        Self::parse(data)
    }

    fn diagnostics_snapshot(&self, window_s: f64) -> RosResult<Vec<RosDiagnosticStatus>> {
        let data = self.request(
            "diagnostics",
            json!({"window_s": window_s}),
            Duration::from_secs_f64(window_s + 10.0),
        )?;
        let statuses = data.get("statuses").cloned().unwrap_or(json!([]));
        Self::parse(statuses)
    }

    fn lifecycle_snapshot(&self, timeout_s: f64) -> RosResult<Vec<RosLifecycleState>> {
        let data = self.request(
            "lifecycle",
            json!({"timeout_s": timeout_s}),
            Duration::from_secs_f64(timeout_s * 8.0 + 15.0),
        )?;
        let states = data.get("states").cloned().unwrap_or(json!([]));
        Self::parse(states)
    }

    fn clock_snapshot(&self, window_s: f64) -> RosResult<RosClockObservation> {
        let data = self.request(
            "clock",
            json!({"window_s": window_s}),
            Duration::from_secs_f64(window_s + 10.0),
        )?;
        Self::parse(data)
    }

    fn qos_compat(&self, topic: &str) -> RosResult<serde_json::Value> {
        self.request(
            "qos_compat",
            json!({"topic": topic}),
            Duration::from_secs(15),
        )
    }
}
