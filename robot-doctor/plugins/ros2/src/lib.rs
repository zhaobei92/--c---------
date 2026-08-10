//! ROS 2 observation plugin (Phase C1).
//!
//! Observational only: this plugin reports what a ROS system looks like —
//! it never publishes commands, calls business services, sends actions,
//! changes parameters or transitions lifecycle states, and it renders no
//! judgement about whether the observed state is good (that belongs to
//! Profiles and the rule engine). The single exception per spec: it may
//! report its *own* operational failure (a configured runtime that cannot
//! initialize) as a finding.

pub mod bootstrap;
pub mod cli_provider;
pub mod error;
pub mod projection;
pub mod provider;
pub mod rclpy_provider;

use bootstrap::{bootstrap_env, discover_runtimes, run_bounded, which_in, EnvMap};
use cli_provider::CliFallbackProvider;
use doctor_domain::ros::{RosEnvironmentInfo, RosRuntimeConfig, RosRuntimeMode};
use doctor_domain::{
    CheckCost, CheckDeclaration, CheckId, CheckRequest, CheckResult, CheckStatus, DiagnosticMode,
    EvidenceKind, Severity,
};
use doctor_plugin_host::CheckContext;
use error::{RosError, RosErrorKind, RosResult};
use provider::{FixtureProvider, RosProvider};
use rclpy_provider::RclpyProvider;
use serde_json::json;
use std::path::PathBuf;
use std::process::Command;
use std::sync::Mutex;
use std::time::Duration;

pub const PLUGIN_ID: &str = "ros2";
pub const PLUGIN_VERSION: &str = env!("CARGO_PKG_VERSION");

struct ActiveRuntime {
    key: String,
    config: RosRuntimeConfig,
    env: EnvMap,
    info: RosEnvironmentInfo,
    provider: Option<Box<dyn RosProvider>>,
    /// Why the preferred/any provider could not start, for evidence.
    provider_errors: Vec<String>,
    /// Bootstrap failure (configured runtime that cannot initialize).
    bootstrap_error: Option<RosError>,
}

/// Plugin state: one active runtime at a time, cached by config identity.
pub struct RosPlugin {
    plugin_dir: PathBuf,
    runtime: Mutex<Option<ActiveRuntime>>,
}

impl RosPlugin {
    pub fn new(plugin_dir: PathBuf) -> Self {
        Self {
            plugin_dir,
            runtime: Mutex::new(None),
        }
    }

    fn fixtures_dir(&self) -> PathBuf {
        std::env::var("ROBOT_DOCTOR_ROS_FIXTURES")
            .map(PathBuf::from)
            .unwrap_or_else(|_| self.plugin_dir.join("fixtures"))
    }

    /// Resolve the runtime config for a request: explicit `runtime` param,
    /// or the best AUTO-discovered candidate.
    fn requested_config(&self, request: &CheckRequest) -> Option<RosRuntimeConfig> {
        if let Some(value) = request.params.get("runtime") {
            if let Ok(config) = serde_json::from_value::<RosRuntimeConfig>(value.clone()) {
                return Some(config);
            }
        }
        // AUTO: prefer the inherited environment, else the newest /opt/ros.
        let mut discovered = discover_runtimes();
        if discovered.is_empty() {
            None
        } else {
            Some(discovered.remove(0))
        }
    }

    /// Ensure the runtime for this request is bootstrapped (cached by
    /// config identity). Returns a guard-free snapshot of the state.
    fn activate(
        &self,
        config: RosRuntimeConfig,
    ) -> std::sync::MutexGuard<'_, Option<ActiveRuntime>> {
        let key = serde_json::to_string(&config).expect("config serializes");
        let mut slot = self.runtime.lock().expect("runtime lock");
        if slot.as_ref().map(|r| r.key.as_str()) == Some(key.as_str()) {
            return slot;
        }
        // Different runtime selected: tear down any previous provider.
        *slot = None;
        *slot = Some(self.build_runtime(key, config));
        slot
    }

    fn build_runtime(&self, key: String, config: RosRuntimeConfig) -> ActiveRuntime {
        if config.mode == RosRuntimeMode::Fixture {
            let name = config.fixture.clone().unwrap_or_default();
            let (provider, provider_errors): (Option<Box<dyn RosProvider>>, Vec<String>) =
                match FixtureProvider::load(&self.fixtures_dir(), &name) {
                    Ok(p) => (Some(Box::new(p)), vec![]),
                    Err(e) => (None, vec![e.display()]),
                };
            return ActiveRuntime {
                key,
                config,
                env: EnvMap::new(),
                info: RosEnvironmentInfo {
                    distro: Some(format!("fixture:{name}")),
                    provider: provider.as_ref().map(|p| p.name().to_owned()),
                    ..Default::default()
                },
                provider,
                provider_errors,
                bootstrap_error: None,
            };
        }

        let env = match bootstrap_env(&config) {
            Ok(env) => env,
            Err(err) => {
                return ActiveRuntime {
                    key,
                    config,
                    env: EnvMap::new(),
                    info: RosEnvironmentInfo::default(),
                    provider: None,
                    provider_errors: vec![],
                    bootstrap_error: Some(err),
                }
            }
        };

        let mut info = environment_info(&env);
        let mut provider_errors = Vec::new();
        let mut provider: Option<Box<dyn RosProvider>> = None;

        // Preferred: rclpy. Partial fallback: CLI. A partial provider is
        // valid — never declare the plugin dead while something works.
        let force_cli = config.preferred_provider.as_deref() == Some("cli");
        if force_cli {
            provider_errors.push("provider preference: cli (rclpy skipped)".to_owned());
        }
        if !force_cli {
            if let Some(python) = python_in(&env) {
                info.python_executable = Some(python.clone());
                info.python_version = python_version(&env, &python);
                let script = self.plugin_dir.join("py").join("rclpy_provider.py");
                match RclpyProvider::start(python, script, env.clone()) {
                    Ok(p) => {
                        info.rclpy_available = true;
                        provider = Some(Box::new(p));
                    }
                    Err(err) => {
                        info.rclpy_available = false;
                        provider_errors.push(err.display());
                    }
                }
            } else {
                provider_errors
                    .push("RCLPY_UNAVAILABLE: no python interpreter in runtime PATH".to_owned());
            }
        }
        info.cli_available = which_in(&env, "ros2").is_some();
        if provider.is_none() && info.cli_available {
            match CliFallbackProvider::new(env.clone()) {
                Ok(p) => provider = Some(Box::new(p)),
                Err(err) => provider_errors.push(err.display()),
            }
        }
        info.provider = provider.as_ref().map(|p| p.name().to_owned());

        ActiveRuntime {
            key,
            config,
            env,
            info,
            provider,
            provider_errors,
            bootstrap_error: None,
        }
    }
}

fn python_in(env: &EnvMap) -> Option<String> {
    if let Some(explicit) = env.get("ROBOT_DOCTOR_PYTHON") {
        return Some(explicit.clone());
    }
    which_in(env, "python3").or_else(|| which_in(env, "python"))
}

fn python_version(env: &EnvMap, python: &str) -> Option<String> {
    let mut cmd = Command::new(python);
    cmd.arg("--version").env_clear().envs(env);
    run_bounded(cmd, Duration::from_secs(10))
        .ok()
        .map(|(_, out, err, _)| {
            let text = if out.trim().is_empty() { err } else { out };
            text.trim().trim_start_matches("Python ").to_owned()
        })
}

fn environment_info(env: &EnvMap) -> RosEnvironmentInfo {
    let base_prefix = env.get("ROS_DISTRO").map(|d| format!("/opt/ros/{d}"));
    let overlays: Vec<String> = env
        .get("AMENT_PREFIX_PATH")
        .map(|p| {
            let sep = if cfg!(windows) { ';' } else { ':' };
            p.split(sep)
                .filter(|entry| !entry.is_empty() && Some(*entry) != base_prefix.as_deref())
                .map(str::to_owned)
                .collect()
        })
        .unwrap_or_default();
    RosEnvironmentInfo {
        distro: env.get("ROS_DISTRO").cloned(),
        ros_version: env.get("ROS_VERSION").cloned(),
        domain_id: env.get("ROS_DOMAIN_ID").and_then(|v| v.parse().ok()),
        rmw_implementation: env.get("RMW_IMPLEMENTATION").cloned(),
        localhost_only: env
            .get("ROS_LOCALHOST_ONLY")
            .map(|v| v == "1" || v.eq_ignore_ascii_case("true")),
        install_prefix: base_prefix,
        python_executable: None,
        python_version: None,
        rclpy_available: false,
        cli_available: false,
        overlays,
        provider: None,
    }
}

// ── check declarations ─────────────────────────────────────────────────

pub fn check_declarations() -> Vec<CheckDeclaration> {
    fn decl(
        id: &str,
        name: &str,
        description: &str,
        cost: CheckCost,
        timeout_ms: u64,
        modes: &[DiagnosticMode],
        depends_on: &[&str],
    ) -> CheckDeclaration {
        CheckDeclaration {
            id: CheckId::from(id),
            name: name.to_owned(),
            description: description.to_owned(),
            cost,
            timeout_ms,
            platforms: vec![],
            depends_on: depends_on.iter().map(|d| CheckId::from(*d)).collect(),
            modes: modes.to_vec(),
        }
    }
    let both = &[DiagnosticMode::Quick, DiagnosticMode::Full][..];
    let full = &[DiagnosticMode::Full][..];
    vec![
        decl(
            "ros.environment",
            "ROS environment",
            "Runtime discovery, bootstrap and environment facts",
            CheckCost::Fast,
            30_000,
            both,
            &[],
        ),
        decl(
            "ros.graph",
            "ROS graph",
            "Nodes, topics, services, actions, endpoints and QoS",
            CheckCost::Medium,
            40_000,
            both,
            &["ros.environment"],
        ),
        decl(
            "ros.clock",
            "ROS clock",
            "System/ROS time, /clock presence and progression",
            CheckCost::Fast,
            20_000,
            both,
            &["ros.environment"],
        ),
        decl(
            "ros.diagnostics",
            "ROS diagnostics",
            "/diagnostics component states (source levels preserved)",
            CheckCost::Medium,
            25_000,
            both,
            &["ros.environment"],
        ),
        decl(
            "ros.doctor",
            "ros2 doctor",
            "Supplemental ros2 doctor report as evidence",
            CheckCost::Medium,
            30_000,
            full,
            &["ros.environment"],
        ),
        decl(
            "ros.qos",
            "QoS analysis",
            "Endpoint QoS and deterministic pub/sub compatibility",
            CheckCost::Medium,
            30_000,
            full,
            &["ros.graph"],
        ),
        decl(
            "ros.topic_rate",
            "Topic sampling",
            "Bounded rate/inter-arrival measurement of selected topics",
            CheckCost::Slow,
            60_000,
            full,
            &["ros.graph"],
        ),
        decl(
            "ros.topic_age",
            "Topic age",
            "Message-stamp age vs receive age for selected topics",
            CheckCost::Slow,
            45_000,
            full,
            &["ros.graph"],
        ),
        decl(
            "ros.tf",
            "TF observation",
            "TF frames, edges, components and explicit path queries",
            CheckCost::Medium,
            40_000,
            full,
            &["ros.environment"],
        ),
        decl(
            "ros.lifecycle",
            "Lifecycle nodes",
            "Managed lifecycle node states (read-only)",
            CheckCost::Medium,
            40_000,
            full,
            &["ros.environment"],
        ),
    ]
}

// ── check execution ────────────────────────────────────────────────────

pub fn run_check(plugin: &RosPlugin, request: &CheckRequest) -> CheckResult {
    let mut ctx = CheckContext::new(PLUGIN_ID, request);
    let check = request.check_id.as_str();

    // Runtime resolution. Without any runtime, only ros.environment can
    // still do useful work (report discovery = nothing found).
    let Some(config) = plugin.requested_config(request) else {
        return no_runtime_result(plugin, ctx, request);
    };

    let mut slot = plugin.activate(config);
    let runtime = slot.as_mut().expect("activated");

    if let Some(err) = &runtime.bootstrap_error {
        // The plugin's own operational failure. For a *configured* runtime
        // this is the one legitimate C1 finding.
        return bootstrap_failure_result(ctx, runtime, err.clone());
    }

    let status = match check {
        "ros.environment" => check_environment(&mut ctx, runtime),
        "ros.doctor" => check_doctor(&mut ctx, runtime),
        "ros.graph" => check_graph(&mut ctx, runtime, request),
        "ros.clock" => check_clock(&mut ctx, runtime, request),
        "ros.diagnostics" => check_diagnostics(&mut ctx, runtime, request),
        "ros.qos" => check_qos(&mut ctx, runtime, request),
        "ros.topic_rate" => check_topic_rate(&mut ctx, runtime, request, false),
        "ros.topic_age" => check_topic_rate(&mut ctx, runtime, request, true),
        "ros.tf" => check_tf(&mut ctx, runtime, request),
        "ros.lifecycle" => check_lifecycle(&mut ctx, runtime, request),
        _ => {
            return ctx.finish(
                PLUGIN_ID,
                CheckStatus::Unsupported,
                Some(format!(
                    "check '{check}' is not provided by the ros2 plugin"
                )),
            )
        }
    };
    match status {
        Ok(status) => ctx.finish(PLUGIN_ID, status, None),
        Err(err) => {
            // Anything that prevented observation keeps the namespace
            // NOT_OBSERVED, so downstream expectations stay UNKNOWN.
            if err.kind != RosErrorKind::TypeSupportMissing {
                ctx.not_observed_kinds(
                    projection::NAMESPACE,
                    projection::kinds_for(check),
                    err.display(),
                );
            }
            ctx.finish(PLUGIN_ID, err.kind.check_status(), Some(err.display()))
        }
    }
}

fn no_runtime_result(
    plugin: &RosPlugin,
    mut ctx: CheckContext,
    request: &CheckRequest,
) -> CheckResult {
    if request.check_id.as_str() == "ros.environment" {
        let discovered = discover_runtimes();
        let ev = ctx.evidence(
            EvidenceKind::Os,
            "ros-discovery",
            "Conservative ROS runtime discovery",
            json!({
                "discovered": discovered,
                "fixtures_dir": plugin.fixtures_dir().display().to_string(),
                "searched": ["inherited environment", "/opt/ros/*", "well-known Windows paths"],
            }),
        );
        ctx.json("ros.runtimes.discovered", json!(discovered), &ev);
        ctx.number("ros.runtimes.count", discovered.len() as f64, "count", &ev);
    }
    ctx.finish(
        PLUGIN_ID,
        CheckStatus::Unavailable,
        Some(
            RosError::new(
                RosErrorKind::RosNotInstalled,
                "no ROS runtime found on this system",
            )
            .display(),
        ),
    )
}

fn bootstrap_failure_result(
    mut ctx: CheckContext,
    runtime: &ActiveRuntime,
    err: RosError,
) -> CheckResult {
    ctx.not_observed(projection::NAMESPACE, err.display());
    if err.kind == RosErrorKind::RosNotInstalled {
        return ctx.finish(PLUGIN_ID, CheckStatus::Unavailable, Some(err.display()));
    }
    let ev = ctx.evidence(
        EvidenceKind::Command,
        "ros-bootstrap",
        "Configured ROS runtime failed to initialize",
        json!({
            "runtime": runtime.config,
            "error": err.display(),
        }),
    );
    ctx.finding(
        "ROS_RUNTIME_INIT_FAILED",
        Severity::Error,
        format!("ros-runtime:{}", runtime.config.id),
        format!("ROS runtime '{}' cannot initialize", runtime.config.name),
        err.display(),
        vec![ev],
    );
    ctx.finish(PLUGIN_ID, CheckStatus::Failed, None)
}

fn provider(runtime: &ActiveRuntime) -> RosResult<&dyn RosProvider> {
    runtime.provider.as_deref().ok_or_else(|| {
        RosError::new(
            RosErrorKind::RosNotInstalled,
            format!(
                "no usable ROS provider ({})",
                if runtime.provider_errors.is_empty() {
                    "no rclpy, no ros2 CLI".to_owned()
                } else {
                    runtime.provider_errors.join("; ")
                }
            ),
        )
    })
}

fn param_f64(request: &CheckRequest, key: &str, default: f64) -> f64 {
    request
        .params
        .get(key)
        .and_then(|v| v.as_f64())
        .unwrap_or(default)
}

fn param_topics(request: &CheckRequest) -> Vec<String> {
    request
        .params
        .get("topics")
        .and_then(|v| serde_json::from_value::<Vec<String>>(v.clone()).ok())
        .unwrap_or_default()
}

fn is_quick(request: &CheckRequest) -> bool {
    request.mode == Some(DiagnosticMode::Quick)
}

// ── individual checks ──────────────────────────────────────────────────

fn check_environment(ctx: &mut CheckContext, runtime: &ActiveRuntime) -> RosResult<CheckStatus> {
    let info = &runtime.info;
    let discovered = discover_runtimes();
    let ev = ctx.evidence(
        EvidenceKind::Os,
        "ros-bootstrap",
        "Bootstrapped ROS environment",
        json!({
            "runtime": runtime.config,
            "environment": info,
            "discovered_runtimes": discovered,
            "provider_errors": runtime.provider_errors,
            "env_keys_of_interest": {
                "ROS_DISTRO": runtime.env.get("ROS_DISTRO"),
                "ROS_VERSION": runtime.env.get("ROS_VERSION"),
                "ROS_DOMAIN_ID": runtime.env.get("ROS_DOMAIN_ID"),
                "RMW_IMPLEMENTATION": runtime.env.get("RMW_IMPLEMENTATION"),
                "ROS_LOCALHOST_ONLY": runtime.env.get("ROS_LOCALHOST_ONLY"),
                "AMENT_PREFIX_PATH": runtime.env.get("AMENT_PREFIX_PATH"),
            },
        }),
    );
    ctx.json(
        "ros.env.info",
        serde_json::to_value(info).expect("serializes"),
        &ev,
    );
    ctx.json("ros.runtimes.discovered", json!(discovered), &ev);
    if let Some(distro) = &info.distro {
        ctx.text("ros.env.distro", distro.clone(), &ev);
    }
    if let Some(domain) = info.domain_id {
        ctx.number("ros.env.domain_id", domain as f64, "id", &ev);
    }
    if let Some(rmw) = &info.rmw_implementation {
        ctx.text("ros.env.rmw", rmw.clone(), &ev);
    }
    ctx.text(
        "ros.env.rclpy_available",
        info.rclpy_available.to_string(),
        &ev,
    );
    ctx.text("ros.env.cli_available", info.cli_available.to_string(), &ev);
    if let Some(provider) = &info.provider {
        ctx.text("ros.env.provider", provider.clone(), &ev);
    }
    // Unusual values are not classified as errors — observations only.
    if runtime.provider.is_some() {
        let entity = projection::runtime_entity(ctx, info);
        ctx.observed_kinds(
            projection::NAMESPACE,
            projection::kinds_for("ros.environment"),
            vec![entity],
        );
        Ok(CheckStatus::Passed)
    } else {
        Err(RosError::new(
            RosErrorKind::RosNotInstalled,
            format!(
                "environment bootstrapped but no provider is usable: {}",
                runtime.provider_errors.join("; ")
            ),
        ))
    }
}

fn check_doctor(ctx: &mut CheckContext, runtime: &ActiveRuntime) -> RosResult<CheckStatus> {
    // Supplemental evidence only — never the canonical graph source, and
    // no product logic parses the human-readable report text.
    let Some(ros2) = which_in(&runtime.env, "ros2") else {
        return Err(RosError::new(
            RosErrorKind::CliUnavailable,
            "ros2 CLI not in runtime PATH",
        ));
    };
    let mut cmd = Command::new(&ros2);
    cmd.args(["doctor", "--report"])
        .env_clear()
        .envs(&runtime.env);
    let (code, stdout, stderr, elapsed) = run_bounded(cmd, Duration::from_secs(25))?;
    let ev = ctx.evidence(
        EvidenceKind::Command,
        "cmd:ros2 doctor --report",
        &format!("ros2 doctor exited {code} in {} ms", elapsed.as_millis()),
        json!({
            "exit_status": code,
            "stdout": stdout,
            "stderr": stderr,
            "duration_ms": elapsed.as_millis() as u64,
        }),
    );
    ctx.number("ros.doctor.exit_status", code as f64, "status", &ev);
    ctx.number(
        "ros.doctor.duration_ms",
        elapsed.as_millis() as f64,
        "milliseconds",
        &ev,
    );
    Ok(CheckStatus::Passed)
}

fn check_graph(
    ctx: &mut CheckContext,
    runtime: &ActiveRuntime,
    request: &CheckRequest,
) -> RosResult<CheckStatus> {
    let provider = provider(runtime)?;
    let discovery_s = param_f64(
        request,
        "discovery_s",
        if is_quick(request) { 2.0 } else { 3.0 },
    );
    let mut snapshot = provider.graph_snapshot(discovery_s)?;
    snapshot.runtime_id = runtime.config.id.clone();
    let ev = ctx.evidence(
        EvidenceKind::Ros,
        &format!("provider:{}", provider.name()),
        &format!(
            "ROS graph snapshot: {} nodes, {} topics ({} ms discovery)",
            snapshot.nodes.len(),
            snapshot.topics.len(),
            snapshot.discovery_ms
        ),
        serde_json::to_value(&snapshot).expect("snapshot serializes"),
    );
    ctx.number(
        "ros.graph.node_count",
        snapshot.nodes.len() as f64,
        "count",
        &ev,
    );
    ctx.number(
        "ros.graph.topic_count",
        snapshot.topics.len() as f64,
        "count",
        &ev,
    );
    ctx.number(
        "ros.graph.service_count",
        snapshot.services.len() as f64,
        "count",
        &ev,
    );
    ctx.number(
        "ros.graph.action_count",
        snapshot.actions.len() as f64,
        "count",
        &ev,
    );
    ctx.number(
        "ros.graph.discovery_ms",
        snapshot.discovery_ms as f64,
        "milliseconds",
        &ev,
    );
    ctx.json(
        "ros.graph.snapshot",
        serde_json::to_value(&snapshot).expect("snapshot serializes"),
        &ev,
    );
    let entities = projection::graph_entities(ctx, &snapshot);
    ctx.observed_kinds(
        projection::NAMESPACE,
        projection::kinds_for("ros.graph"),
        entities,
    );
    Ok(CheckStatus::Passed)
}

fn check_clock(
    ctx: &mut CheckContext,
    runtime: &ActiveRuntime,
    request: &CheckRequest,
) -> RosResult<CheckStatus> {
    let provider = provider(runtime)?;
    let window = param_f64(
        request,
        "window_s",
        if is_quick(request) { 0.5 } else { 1.5 },
    );
    let clock = provider.clock_snapshot(window)?;
    let ev = ctx.evidence(
        EvidenceKind::Ros,
        &format!("provider:{}", provider.name()),
        "ROS clock observation",
        serde_json::to_value(&clock).expect("serializes"),
    );
    ctx.json(
        "ros.clock",
        serde_json::to_value(&clock).expect("serializes"),
        &ev,
    );
    ctx.text(
        "ros.clock.topic_present",
        clock.clock_topic_present.to_string(),
        &ev,
    );
    if let Some(advance) = clock.clock_advance {
        ctx.number("ros.clock.advance_s", advance, "seconds", &ev);
    }
    // /clock absence on a wall-clock system is NOT a failure.
    Ok(CheckStatus::Passed)
}

fn check_diagnostics(
    ctx: &mut CheckContext,
    runtime: &ActiveRuntime,
    request: &CheckRequest,
) -> RosResult<CheckStatus> {
    let provider = provider(runtime)?;
    let window = param_f64(
        request,
        "window_s",
        if is_quick(request) { 1.0 } else { 3.0 },
    );
    let statuses = provider.diagnostics_snapshot(window)?;
    let ev = ctx.evidence(
        EvidenceKind::Ros,
        &format!("provider:{}", provider.name()),
        &format!(
            "{} diagnostic component states over {window}s",
            statuses.len()
        ),
        serde_json::to_value(&statuses).expect("serializes"),
    );
    ctx.number(
        "ros.diagnostics.component_count",
        statuses.len() as f64,
        "count",
        &ev,
    );
    for level in 0u8..=3 {
        let count = statuses.iter().filter(|s| s.level == level).count();
        let label = match level {
            0 => "ok",
            1 => "warn",
            2 => "error",
            _ => "stale",
        };
        ctx.number(
            &format!("ros.diagnostics.{label}_count"),
            count as f64,
            "count",
            &ev,
        );
    }
    // Source levels are preserved verbatim; no reinterpretation here.
    ctx.json(
        "ros.diagnostics.statuses",
        serde_json::to_value(&statuses).expect("serializes"),
        &ev,
    );
    let entities = projection::diagnostics_entities(ctx, &statuses);
    ctx.observed_kinds(
        projection::NAMESPACE,
        projection::kinds_for("ros.diagnostics"),
        entities,
    );
    Ok(CheckStatus::Passed)
}

fn check_qos(
    ctx: &mut CheckContext,
    runtime: &ActiveRuntime,
    request: &CheckRequest,
) -> RosResult<CheckStatus> {
    let provider = provider(runtime)?;
    let snapshot = provider.graph_snapshot(param_f64(request, "discovery_s", 1.5))?;
    let mut evaluated = 0;
    for topic in snapshot
        .topics
        .iter()
        .filter(|t| t.publisher_count > 0 && t.subscriber_count > 0)
        .take(20)
    {
        match provider.qos_compat(&topic.name) {
            Ok(result) => {
                evaluated += 1;
                let ev = ctx.evidence(
                    EvidenceKind::Ros,
                    &format!("provider:{}", provider.name()),
                    &format!("QoS compatibility for {}", topic.name),
                    result.clone(),
                );
                ctx.json(&format!("ros.qos.{}", topic.name), result, &ev);
            }
            Err(err) if err.kind == RosErrorKind::CliUnavailable => return Err(err),
            Err(err) => {
                let ev = ctx.evidence(
                    EvidenceKind::Ros,
                    "qos",
                    &format!("QoS evaluation failed for {}", topic.name),
                    json!({"error": err.display()}),
                );
                ctx.json(
                    &format!("ros.qos.{}", topic.name),
                    json!({"error": err.display()}),
                    &ev,
                );
            }
        }
    }
    let ev = ctx.evidence(
        EvidenceKind::Ros,
        "qos",
        &format!("QoS evaluated on {evaluated} active topics"),
        json!({"evaluated": evaluated}),
    );
    ctx.number("ros.qos.evaluated_topics", evaluated as f64, "count", &ev);
    Ok(CheckStatus::Passed)
}

fn check_topic_rate(
    ctx: &mut CheckContext,
    runtime: &ActiveRuntime,
    request: &CheckRequest,
    age_focus: bool,
) -> RosResult<CheckStatus> {
    let provider = provider(runtime)?;
    let topics = param_topics(request);
    if topics.is_empty() {
        // Sampling is explicitly scheduled — never automatic scanning.
        let ev = ctx.evidence(
            EvidenceKind::Ros,
            "config",
            "No topics selected for sampling",
            json!({"configured_topics": 0}),
        );
        ctx.number("ros.sampling.topics", 0.0, "count", &ev);
        // Nothing was requested: this check makes no claim about the
        // namespace, so it emits no projection at all.
        return Ok(CheckStatus::Passed);
    }
    let duration = param_f64(request, "duration_s", if age_focus { 2.0 } else { 4.0 }).min(20.0);
    let mut worst: Option<RosError> = None;
    let mut successes = 0u32;
    let mut sampled: Vec<doctor_domain::ros::RosTopicSample> = Vec::new();
    for topic in topics.iter().take(10) {
        match provider.topic_sample(topic, duration) {
            Ok(sample) => {
                successes += 1;
                sampled.push(sample.clone());
                let ev = ctx.evidence(
                    EvidenceKind::Metric,
                    &format!("provider:{}", provider.name()),
                    &format!(
                        "Sampled {topic}: {} msgs in {:.1}s",
                        sample.sample_count, sample.sampling_duration_s
                    ),
                    serde_json::to_value(&sample).expect("serializes"),
                );
                let prefix = if age_focus {
                    "ros.topic_age"
                } else {
                    "ros.topic_rate"
                };
                ctx.json(
                    &format!("{prefix}.{topic}"),
                    serde_json::to_value(&sample).expect("serializes"),
                    &ev,
                );
                if let Some(hz) = sample.observed_hz {
                    ctx.number(&format!("{prefix}.{topic}.hz"), hz, "hz", &ev);
                }
                // The two ages are deliberately distinct observations.
                if let Some(age) = sample.message_stamp_age_s {
                    ctx.number(
                        &format!("{prefix}.{topic}.stamp_age_s"),
                        age,
                        "seconds",
                        &ev,
                    );
                }
                if let Some(age) = sample.receive_age_s {
                    ctx.number(
                        &format!("{prefix}.{topic}.receive_age_s"),
                        age,
                        "seconds",
                        &ev,
                    );
                }
            }
            Err(err) => {
                let ev = ctx.evidence(
                    EvidenceKind::Ros,
                    "sampling",
                    &format!("Sampling failed for {topic}"),
                    json!({"topic": topic, "error": err.display(), "kind": err.kind.code()}),
                );
                let prefix = if age_focus {
                    "ros.topic_age"
                } else {
                    "ros.topic_rate"
                };
                ctx.json(
                    &format!("{prefix}.{topic}"),
                    json!({"error": err.display()}),
                    &ev,
                );
                // TYPE_SUPPORT_MISSING etc. does not mark the topic broken;
                // it becomes the overall typed status only if nothing worked.
                worst.get_or_insert(err);
            }
        }
    }
    if successes > 0 {
        let check_id = if age_focus {
            "ros.topic_age"
        } else {
            "ros.topic_rate"
        };
        let kind = projection::kinds_for(check_id)[0];
        let entities = sampled
            .iter()
            .map(|sample| projection::sample_entity(ctx, kind, sample))
            .collect();
        ctx.observed_kinds(
            projection::NAMESPACE,
            projection::kinds_for(check_id),
            entities,
        );
    }
    // A failing topic (e.g. TYPE_SUPPORT_MISSING) never marks the topic —
    // or the check — broken while other samples succeeded. Only when
    // nothing could be sampled does the typed error become the status.
    match worst {
        Some(err) if successes == 0 => Err(err),
        _ => Ok(CheckStatus::Passed),
    }
}

fn check_tf(
    ctx: &mut CheckContext,
    runtime: &ActiveRuntime,
    request: &CheckRequest,
) -> RosResult<CheckStatus> {
    let provider = provider(runtime)?;
    let listen = param_f64(request, "listen_s", 2.0).min(10.0);
    let snapshot = provider.tf_snapshot(listen)?;
    let ev = ctx.evidence(
        EvidenceKind::Ros,
        &format!("provider:{}", provider.name()),
        &format!(
            "TF snapshot: {} frames, {} edges, {} components",
            snapshot.frames.len(),
            snapshot.edges.len(),
            snapshot.connected_components.len()
        ),
        serde_json::to_value(&snapshot).expect("serializes"),
    );
    ctx.number(
        "ros.tf.frame_count",
        snapshot.frames.len() as f64,
        "count",
        &ev,
    );
    ctx.number(
        "ros.tf.edge_count",
        snapshot.edges.len() as f64,
        "count",
        &ev,
    );
    ctx.number(
        "ros.tf.component_count",
        snapshot.connected_components.len() as f64,
        "count",
        &ev,
    );
    ctx.json(
        "ros.tf.snapshot",
        serde_json::to_value(&snapshot).expect("serializes"),
        &ev,
    );
    let entities = projection::tf_entities(ctx, &snapshot);
    ctx.observed_kinds(
        projection::NAMESPACE,
        projection::kinds_for("ros.tf"),
        entities,
    );

    // Explicit CAN TRANSFORM queries (source/target pairs from params).
    #[derive(serde::Deserialize)]
    struct QuerySpec {
        source: String,
        target: String,
        #[serde(default)]
        timeout_s: Option<f64>,
    }
    let queries: Vec<QuerySpec> = request
        .params
        .get("tf_queries")
        .and_then(|v| serde_json::from_value(v.clone()).ok())
        .unwrap_or_default();
    for query in queries.iter().take(10) {
        let timeout = query.timeout_s.unwrap_or(2.0).min(10.0);
        match provider.tf_query(&query.source, &query.target, timeout) {
            Ok(result) => {
                let ev = ctx.evidence(
                    EvidenceKind::Ros,
                    &format!("provider:{}", provider.name()),
                    &format!(
                        "TF query {} -> {}: {}",
                        result.source, result.target, result.status
                    ),
                    serde_json::to_value(&result).expect("serializes"),
                );
                ctx.json(
                    &format!("ros.tf.query.{}->{}", query.source, query.target),
                    serde_json::to_value(&result).expect("serializes"),
                    &ev,
                );
            }
            Err(err) => {
                let ev = ctx.evidence(
                    EvidenceKind::Ros,
                    "tf",
                    &format!("TF query {} -> {} failed", query.source, query.target),
                    json!({"error": err.display()}),
                );
                ctx.json(
                    &format!("ros.tf.query.{}->{}", query.source, query.target),
                    json!({"status": "UNAVAILABLE", "error": err.display()}),
                    &ev,
                );
            }
        }
    }
    // Disconnected components are observations, not defects (C1).
    Ok(CheckStatus::Passed)
}

fn check_lifecycle(
    ctx: &mut CheckContext,
    runtime: &ActiveRuntime,
    request: &CheckRequest,
) -> RosResult<CheckStatus> {
    let provider = provider(runtime)?;
    let states = provider.lifecycle_snapshot(param_f64(request, "timeout_s", 2.0))?;
    let ev = ctx.evidence(
        EvidenceKind::Ros,
        &format!("provider:{}", provider.name()),
        &format!("{} lifecycle-managed nodes observed", states.len()),
        serde_json::to_value(&states).expect("serializes"),
    );
    ctx.number(
        "ros.lifecycle.node_count",
        states.len() as f64,
        "count",
        &ev,
    );
    ctx.json(
        "ros.lifecycle.states",
        serde_json::to_value(&states).expect("serializes"),
        &ev,
    );
    let entities = projection::lifecycle_entities(ctx, &states);
    ctx.observed_kinds(
        projection::NAMESPACE,
        projection::kinds_for("ros.lifecycle"),
        entities,
    );
    // Nodes without managed lifecycle are not errors.
    Ok(CheckStatus::Passed)
}
