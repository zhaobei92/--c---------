//! Network diagnostic checks, normalized to the Robot Doctor domain model.
//!
//! Cross-platform (Linux/Windows) via `netdev` for interface/route/DNS
//! discovery and std TCP sockets for reachability/latency/ports. Latency is
//! measured with bounded TCP connect sampling — explicit method, no raw
//! ICMP sockets (which need elevated privileges on both platforms).

use doctor_domain::{
    AttributeValue, CheckCost, CheckDeclaration, CheckId, CheckRequest, CheckResult, CheckStatus,
    DiagnosticMode, EvidenceKind, Severity,
};
use doctor_plugin_host::CheckContext;
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::net::{SocketAddr, TcpStream, ToSocketAddrs};
use std::time::{Duration, Instant};

pub const PLUGIN_ID: &str = "network";
pub const PLUGIN_VERSION: &str = env!("CARGO_PKG_VERSION");

/// A reachability/latency/port target. Never hardcoded — comes from check
/// params (later: Profiles). Default when unset: loopback.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Target {
    pub host: String,
    #[serde(default)]
    pub port: Option<u16>,
    #[serde(default)]
    pub timeout_ms: Option<u64>,
}

impl Target {
    fn timeout(&self) -> Duration {
        Duration::from_millis(self.timeout_ms.unwrap_or(1_000).clamp(50, 10_000))
    }

    fn label(&self) -> String {
        match self.port {
            Some(p) => format!("{}:{p}", self.host),
            None => self.host.clone(),
        }
    }
}

fn targets_from(request: &CheckRequest, default: &[Target]) -> Vec<Target> {
    request
        .params
        .get("targets")
        .and_then(|v| serde_json::from_value::<Vec<Target>>(v.clone()).ok())
        .filter(|t| !t.is_empty())
        .unwrap_or_else(|| default.to_vec())
}

fn loopback_target() -> Vec<Target> {
    vec![Target {
        host: "127.0.0.1".into(),
        port: None,
        timeout_ms: Some(1_000),
    }]
}

pub fn check_declarations() -> Vec<CheckDeclaration> {
    fn decl(
        id: &str,
        name: &str,
        description: &str,
        cost: CheckCost,
        timeout_ms: u64,
        modes: &[DiagnosticMode],
    ) -> CheckDeclaration {
        CheckDeclaration {
            id: CheckId::from(id),
            name: name.to_owned(),
            description: description.to_owned(),
            cost,
            timeout_ms,
            platforms: vec![],
            depends_on: vec![],
            modes: modes.to_vec(),
        }
    }
    let both = &[DiagnosticMode::Quick, DiagnosticMode::Full][..];
    let full = &[DiagnosticMode::Full][..];
    vec![
        decl(
            "network.interfaces",
            "Network interfaces",
            "Interface inventory: addresses, MAC, MTU, state",
            CheckCost::Fast,
            5_000,
            both,
        ),
        decl(
            "network.default_route",
            "Default route",
            "Default gateway and egress interface",
            CheckCost::Fast,
            5_000,
            both,
        ),
        decl(
            "network.dns",
            "DNS",
            "Configured resolvers and local name resolution",
            CheckCost::Fast,
            5_000,
            both,
        ),
        decl(
            "network.reachability",
            "Target reachability",
            "Resolve and reach configured targets",
            CheckCost::Medium,
            15_000,
            both,
        ),
        decl(
            "network.tcp_port",
            "TCP ports",
            "TCP connect tests against configured host:port targets",
            CheckCost::Medium,
            15_000,
            full,
        ),
        decl(
            "network.latency",
            "Latency",
            "Bounded latency sampling (TCP connect) against targets",
            CheckCost::Slow,
            30_000,
            full,
        ),
        decl(
            "network.time",
            "Clock sanity",
            "Wall clock vs monotonic clock sanity",
            CheckCost::Fast,
            5_000,
            both,
        ),
    ]
}

pub fn run_check(request: &CheckRequest) -> CheckResult {
    let mut ctx = CheckContext::new(PLUGIN_ID, request);
    let status = match request.check_id.as_str() {
        "network.interfaces" => check_interfaces(&mut ctx),
        "network.default_route" => check_default_route(&mut ctx),
        "network.dns" => check_dns(&mut ctx, request),
        "network.reachability" => check_reachability(&mut ctx, request),
        "network.tcp_port" => check_tcp_port(&mut ctx, request),
        "network.latency" => check_latency(&mut ctx, request),
        "network.time" => check_time(&mut ctx),
        _ => {
            return ctx.finish(
                PLUGIN_ID,
                CheckStatus::Unsupported,
                Some(format!(
                    "check '{}' is not provided by the network plugin",
                    request.check_id
                )),
            )
        }
    };
    ctx.finish(PLUGIN_ID, status, None)
}

/// Namespace this plugin projects into for baseline comparison.
pub const NAMESPACE: &str = "network";

/// Stable identity for a target entity: `host` or `host:port`.
///
/// The kind is declared even when no targets are configured, so "no
/// targets" reads as an observed empty set rather than an unobserved one.
fn target_entities(ctx: &mut CheckContext, kind: &str, results: Vec<(String, serde_json::Value)>) {
    let entities = results
        .into_iter()
        .map(|(label, data)| {
            let mut entity = ctx.entity(NAMESPACE, kind, label.clone(), label);
            if let Some(outcome) = data.get("outcome").and_then(|v| v.as_str()) {
                entity = entity.with("outcome", AttributeValue::text(outcome));
            }
            if let Some(reachable) = data.get("reachable").and_then(|v| v.as_bool()) {
                entity = entity.with("reachable", AttributeValue::Bool(reachable));
            }
            for numeric in [
                "latency_ms",
                "connect_latency_ms",
                "avg_ms",
                "min_ms",
                "max_ms",
            ] {
                if let Some(value) = data.get(numeric).and_then(|v| v.as_f64()) {
                    entity = entity.with(numeric, AttributeValue::Number(value));
                }
            }
            entity
        })
        .collect();
    ctx.observed_kinds(NAMESPACE, &[kind], entities);
}

// ── interfaces ─────────────────────────────────────────────────────────

fn check_interfaces(ctx: &mut CheckContext) -> CheckStatus {
    let interfaces = netdev::get_interfaces();
    if interfaces.is_empty() {
        // Could not enumerate: absence proves nothing downstream.
        ctx.not_observed_kinds(
            NAMESPACE,
            &["interface"],
            "interface enumeration returned nothing",
        );
        return CheckStatus::Error;
    }
    let table: Vec<serde_json::Value> = interfaces
        .iter()
        .map(|iface| {
            json!({
                "name": iface.name,
                "friendly_name": iface.friendly_name,
                "type": format!("{:?}", iface.if_type),
                "up": iface.is_up(),
                "loopback": iface.is_loopback(),
                "ipv4": iface.ipv4.iter().map(|n| n.to_string()).collect::<Vec<_>>(),
                "ipv6": iface.ipv6.iter().map(|n| n.to_string()).collect::<Vec<_>>(),
                "mac": iface.mac_addr.map(|m| m.to_string()),
                "mtu": iface.mtu,
                "default": iface.default,
            })
        })
        .collect();
    let up_count = interfaces
        .iter()
        .filter(|i| i.is_up() && !i.is_loopback())
        .count();
    let ev = ctx.evidence(
        EvidenceKind::Network,
        "netdev",
        "Interface inventory from OS",
        json!({"interfaces": table}),
    );
    ctx.json("network.interfaces.list", json!(table.clone()), &ev);
    ctx.number(
        "network.interfaces.count",
        interfaces.len() as f64,
        "count",
        &ev,
    );
    ctx.number(
        "network.interfaces.up_non_loopback",
        up_count as f64,
        "count",
        &ev,
    );

    // Projection: interface name is the stable identity; addresses and
    // MTU are structural. Byte counters and transient state are not
    // projected — they would produce meaningless diffs.
    let entities = table
        .iter()
        .filter_map(|iface| {
            let name = iface.get("name")?.as_str()?;
            let mut entity = ctx.entity(NAMESPACE, "interface", name, name);
            if let Some(kind) = iface.get("type").and_then(|v| v.as_str()) {
                entity = entity.with("type", AttributeValue::text(kind));
            }
            if let Some(up) = iface.get("up").and_then(|v| v.as_bool()) {
                entity = entity.with("up", AttributeValue::Bool(up));
            }
            if let Some(loopback) = iface.get("loopback").and_then(|v| v.as_bool()) {
                entity = entity.with("loopback", AttributeValue::Bool(loopback));
            }
            if let Some(mtu) = iface.get("mtu").and_then(|v| v.as_f64()) {
                entity = entity.with("mtu", AttributeValue::Number(mtu));
            }
            if let Some(addrs) = iface.get("ipv4").and_then(|v| v.as_array()) {
                entity = entity.with(
                    "ipv4",
                    AttributeValue::set(addrs.iter().filter_map(|a| a.as_str())),
                );
            }
            if let Some(mac) = iface.get("mac").and_then(|v| v.as_str()) {
                entity = entity.with("mac", AttributeValue::text(mac));
            }
            Some(entity.with_evidence(vec![ev.clone()]))
        })
        .collect();
    ctx.observed_kinds(NAMESPACE, &["interface"], entities);

    // Virtual/down interfaces are not errors — Profiles decide relevance.
    CheckStatus::Passed
}

// ── default route ──────────────────────────────────────────────────────

fn check_default_route(ctx: &mut CheckContext) -> CheckStatus {
    match netdev::get_default_interface() {
        Ok(iface) => {
            let gateway = iface.gateway.as_ref();
            let ev = ctx.evidence(
                EvidenceKind::Network,
                "netdev",
                "Default egress interface and gateway",
                json!({
                    "interface": iface.name,
                    "friendly_name": iface.friendly_name,
                    "gateway_ip": gateway.map(|g| g.ipv4.iter().map(|i| i.to_string()).collect::<Vec<_>>()),
                    "gateway_mac": gateway.map(|g| g.mac_addr.to_string()),
                    "ipv4": iface.ipv4.iter().map(|n| n.to_string()).collect::<Vec<_>>(),
                }),
            );
            ctx.text("network.default_route.present", "true", &ev);
            ctx.text("network.default_route.interface", iface.name.clone(), &ev);
            if let Some(gw) = gateway {
                if let Some(ip) = gw.ipv4.first() {
                    ctx.text("network.default_route.gateway", ip.to_string(), &ev);
                }
            }
            CheckStatus::Passed
        }
        Err(err) => {
            // No default route is a fact, not a plugin failure. Partial
            // observation; Profiles decide whether it matters.
            let ev = ctx.evidence(
                EvidenceKind::Network,
                "netdev",
                "No default route detected",
                json!({"error": err.to_string()}),
            );
            ctx.text("network.default_route.present", "false", &ev);
            CheckStatus::Passed
        }
    }
}

// ── dns ────────────────────────────────────────────────────────────────

fn check_dns(ctx: &mut CheckContext, request: &CheckRequest) -> CheckStatus {
    // Resolver inventory (best effort — partial data is fine).
    let resolvers: Vec<String> = netdev::get_default_interface()
        .map(|iface| iface.dns_servers.iter().map(|ip| ip.to_string()).collect())
        .unwrap_or_default();

    // Resolution test. Default hostname is loopback-safe for CI.
    let hostname = request
        .params
        .get("hostname")
        .and_then(|v| v.as_str())
        .unwrap_or("localhost")
        .to_owned();
    let started = Instant::now();
    let resolved: Result<Vec<SocketAddr>, std::io::Error> = format!("{hostname}:0")
        .to_socket_addrs()
        .map(|a| a.collect());
    let elapsed_ms = started.elapsed().as_millis() as f64;

    match resolved {
        Ok(addrs) => {
            let ev = ctx.evidence(
                EvidenceKind::Network,
                "std::net resolver",
                "DNS resolution succeeded",
                json!({
                    "hostname": hostname,
                    "resolved": addrs.iter().map(|a| a.ip().to_string()).collect::<Vec<_>>(),
                    "resolvers": resolvers,
                    "elapsed_ms": elapsed_ms,
                }),
            );
            ctx.text("network.dns.hostname", hostname, &ev);
            ctx.json(
                "network.dns.resolved",
                json!(addrs.iter().map(|a| a.ip().to_string()).collect::<Vec<_>>()),
                &ev,
            );
            ctx.json("network.dns.resolvers", json!(resolvers), &ev);
            ctx.number("network.dns.elapsed_ms", elapsed_ms, "milliseconds", &ev);
            CheckStatus::Passed
        }
        Err(err) => {
            let ev = ctx.evidence(
                EvidenceKind::Network,
                "std::net resolver",
                "DNS resolution failed",
                json!({
                    "hostname": hostname,
                    "error": err.to_string(),
                    "resolvers": resolvers,
                }),
            );
            ctx.finding(
                "DNS_FAILURE",
                Severity::Warning,
                format!("dns:{hostname}"),
                format!("Cannot resolve '{hostname}'"),
                format!("Name resolution failed: {err}"),
                vec![ev],
            );
            CheckStatus::Failed
        }
    }
}

// ── connect probing shared by reachability/ports/latency ───────────────

/// Typed outcome of one TCP connect attempt.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ConnectOutcome {
    /// Name resolved (resolution-only probe for targets without a port).
    Resolved,
    /// Connected successfully.
    Open,
    /// Host answered with RST — host reachable, port closed.
    Refused,
    /// No answer within the timeout.
    Timeout,
    /// Network/host unreachable (routing said no).
    Unreachable,
    /// Name did not resolve.
    DnsFailure,
    /// Other OS error.
    Error,
}

fn classify_io_error(err: &std::io::Error) -> ConnectOutcome {
    use std::io::ErrorKind;
    match err.kind() {
        ErrorKind::ConnectionRefused => ConnectOutcome::Refused,
        ErrorKind::TimedOut | ErrorKind::WouldBlock => ConnectOutcome::Timeout,
        ErrorKind::HostUnreachable | ErrorKind::NetworkUnreachable => ConnectOutcome::Unreachable,
        _ => ConnectOutcome::Error,
    }
}

/// One TCP connect attempt with timeout; returns outcome + elapsed ms.
pub fn tcp_probe(
    host: &str,
    port: u16,
    timeout: Duration,
) -> (ConnectOutcome, f64, Option<String>) {
    let addr = match (host, port).to_socket_addrs().map(|mut a| a.next()) {
        Ok(Some(addr)) => addr,
        Ok(None) | Err(_) => return (ConnectOutcome::DnsFailure, 0.0, None),
    };
    let started = Instant::now();
    match TcpStream::connect_timeout(&addr, timeout) {
        Ok(_) => (
            ConnectOutcome::Open,
            started.elapsed().as_secs_f64() * 1000.0,
            Some(addr.ip().to_string()),
        ),
        Err(err) => (
            classify_io_error(&err),
            started.elapsed().as_secs_f64() * 1000.0,
            Some(addr.ip().to_string()),
        ),
    }
}

// ── reachability ───────────────────────────────────────────────────────

fn check_reachability(ctx: &mut CheckContext, request: &CheckRequest) -> CheckStatus {
    let targets = targets_from(request, &loopback_target());
    let mut all_ok = true;
    let mut projected: Vec<(String, serde_json::Value)> = Vec::new();
    for target in &targets {
        let label = target.label();
        let (outcome, latency_ms, resolved, reachable) = match target.port {
            Some(port) => {
                let (outcome, ms, resolved) = tcp_probe(&target.host, port, target.timeout());
                // For reachability, a refused port still proves the HOST is
                // reachable — the port check is stricter.
                let reachable = matches!(outcome, ConnectOutcome::Open | ConnectOutcome::Refused);
                (outcome, ms, resolved, reachable)
            }
            None => {
                // Without a port, reachability means name resolution only.
                // Probing an arbitrary TCP port is firewall-dependent
                // (Windows runners drop closed-port SYNs on loopback) and
                // would misreport filtered hosts as down. Deeper checks
                // need an explicit port (tcp_port/latency) — or ICMP,
                // which requires privileges we do not assume.
                let started = Instant::now();
                let resolved = format!("{}:0", target.host)
                    .to_socket_addrs()
                    .ok()
                    .and_then(|mut a| a.next())
                    .map(|a| a.ip().to_string());
                let ms = started.elapsed().as_secs_f64() * 1000.0;
                match resolved {
                    Some(ip) => (ConnectOutcome::Resolved, ms, Some(ip), true),
                    None => (ConnectOutcome::DnsFailure, ms, None, false),
                }
            }
        };
        let ev = ctx.evidence(
            EvidenceKind::Network,
            "tcp_probe",
            &format!("Reachability probe {label}"),
            json!({
                "target": label,
                "resolved": resolved,
                "outcome": outcome,
                "latency_ms": latency_ms,
                "timeout_ms": target.timeout().as_millis() as u64,
                "method": "tcp_connect",
            }),
        );
        let summary = json!({"reachable": reachable, "outcome": outcome, "latency_ms": latency_ms});
        ctx.json(
            &format!("network.reachability.{label}"),
            summary.clone(),
            &ev,
        );
        projected.push((label.clone(), summary));
        if !reachable {
            all_ok = false;
            ctx.finding(
                "TARGET_UNREACHABLE",
                Severity::Warning,
                format!("target:{label}"),
                format!("Target {label} is unreachable ({outcome:?})"),
                match outcome {
                    ConnectOutcome::DnsFailure => {
                        format!("The name '{}' did not resolve.", target.host)
                    }
                    ConnectOutcome::Timeout => format!(
                        "No answer within {} ms — host down, filtered, or wrong network.",
                        target.timeout().as_millis()
                    ),
                    ConnectOutcome::Unreachable => {
                        "Routing reported the host/network as unreachable.".to_owned()
                    }
                    _ => format!("Probe ended with {outcome:?}."),
                },
                vec![ev],
            );
        }
    }
    target_entities(ctx, "target", projected);
    if all_ok {
        CheckStatus::Passed
    } else {
        CheckStatus::Failed
    }
}

// ── tcp port ───────────────────────────────────────────────────────────

fn check_tcp_port(ctx: &mut CheckContext, request: &CheckRequest) -> CheckStatus {
    let targets: Vec<Target> = targets_from(request, &[])
        .into_iter()
        .filter(|t| t.port.is_some())
        .collect();
    if targets.is_empty() {
        let ev = ctx.evidence(
            EvidenceKind::Network,
            "config",
            "No TCP port targets configured",
            json!({"configured_targets": 0}),
        );
        ctx.number("network.tcp_port.targets", 0.0, "count", &ev);
        // Observed, and there is genuinely nothing configured.
        ctx.observed_kinds(NAMESPACE, &["tcp_port"], vec![]);
        return CheckStatus::Passed;
    }
    let mut all_open = true;
    let mut projected: Vec<(String, serde_json::Value)> = Vec::new();
    for target in &targets {
        let port = target.port.expect("filtered above");
        let label = target.label();
        let (outcome, latency_ms, resolved) = tcp_probe(&target.host, port, target.timeout());
        let ev = ctx.evidence(
            EvidenceKind::Network,
            "tcp_probe",
            &format!("TCP connect {label}"),
            json!({
                "target": target.host,
                "port": port,
                "resolved": resolved,
                "outcome": outcome,
                "connect_latency_ms": latency_ms,
            }),
        );
        let summary = json!({"outcome": outcome, "connect_latency_ms": latency_ms});
        ctx.json(&format!("network.tcp_port.{label}"), summary.clone(), &ev);
        projected.push((label.clone(), summary));
        if outcome != ConnectOutcome::Open {
            all_open = false;
            ctx.finding(
                "PORT_UNREACHABLE",
                Severity::Warning,
                format!("port:{label}"),
                format!("TCP port {label} is not open ({outcome:?})"),
                format!("Connect attempt ended with {outcome:?} after {latency_ms:.1} ms."),
                vec![ev],
            );
        }
    }
    target_entities(ctx, "tcp_port", projected);
    if all_open {
        CheckStatus::Passed
    } else {
        CheckStatus::Failed
    }
}

// ── latency ────────────────────────────────────────────────────────────

fn check_latency(ctx: &mut CheckContext, request: &CheckRequest) -> CheckStatus {
    let targets = targets_from(request, &loopback_target());
    // Bounded sampling: FULL takes more samples than QUICK; never endless.
    let samples = match request.mode {
        Some(DiagnosticMode::Full) => 10,
        _ => 3,
    };
    let mut projected: Vec<(String, serde_json::Value)> = Vec::new();
    for target in &targets {
        let label = target.label();
        let port = target.port.unwrap_or(9);
        let mut ok = Vec::new();
        let mut failed = 0u32;
        for _ in 0..samples {
            let (outcome, ms, _) = tcp_probe(&target.host, port, target.timeout());
            // Open or refused both prove the round trip; timeout/unreachable
            // count as failed samples.
            if matches!(outcome, ConnectOutcome::Open | ConnectOutcome::Refused) {
                ok.push(ms);
            } else {
                failed += 1;
            }
        }
        let (min, max, avg) = if ok.is_empty() {
            (0.0, 0.0, 0.0)
        } else {
            let min = ok.iter().cloned().fold(f64::INFINITY, f64::min);
            let max = ok.iter().cloned().fold(0.0_f64, f64::max);
            let avg = ok.iter().sum::<f64>() / ok.len() as f64;
            (min, max, avg)
        };
        let ev = ctx.evidence(
            EvidenceKind::Network,
            "tcp_probe",
            &format!("Latency sampling {label} ({samples} samples)"),
            json!({
                "target": label,
                "method": "tcp_connect",
                "samples": samples,
                "successful": ok.len(),
                "failed": failed,
                "min_ms": min,
                "max_ms": max,
                "avg_ms": avg,
                "raw_ms": ok,
            }),
        );
        let summary = json!({
            "successful": ok.len(),
            "failed": failed,
            "min_ms": min,
            "max_ms": max,
            "avg_ms": avg,
        });
        ctx.json(&format!("network.latency.{label}"), summary.clone(), &ev);
        projected.push((label.clone(), summary));
        if ok.is_empty() {
            ctx.finding(
                "TARGET_UNREACHABLE",
                Severity::Warning,
                format!("target:{label}"),
                format!("No latency sample reached {label}"),
                format!("{samples} of {samples} samples failed (timeout/unreachable)."),
                vec![ev],
            );
        }
    }
    target_entities(ctx, "latency_target", projected);
    if ctx.has_findings() {
        CheckStatus::Failed
    } else {
        CheckStatus::Passed
    }
}

// ── time ───────────────────────────────────────────────────────────────

fn check_time(ctx: &mut CheckContext) -> CheckStatus {
    // Wall-vs-monotonic sanity: sleep a known interval and compare clocks.
    let wall_before = chrono::Utc::now();
    let mono_before = Instant::now();
    std::thread::sleep(Duration::from_millis(50));
    let wall_elapsed = (chrono::Utc::now() - wall_before).num_milliseconds().max(0) as f64;
    let mono_elapsed = mono_before.elapsed().as_secs_f64() * 1000.0;
    let drift = (wall_elapsed - mono_elapsed).abs();
    let ev = ctx.evidence(
        EvidenceKind::Network,
        "clock",
        "Wall vs monotonic clock over 50 ms",
        json!({
            "wall_elapsed_ms": wall_elapsed,
            "monotonic_elapsed_ms": mono_elapsed,
            "drift_ms": drift,
            "utc_now": chrono::Utc::now().to_rfc3339(),
        }),
    );
    ctx.number("network.time.drift_ms", drift, "milliseconds", &ev);
    ctx.text("network.time.utc", chrono::Utc::now().to_rfc3339(), &ev);
    // A wall clock jumping against monotonic during the sample window is
    // worth flagging (NTP step, manual clock set).
    if drift > 250.0 {
        ctx.finding(
            "CLOCK_SKEW",
            Severity::Warning,
            "clock".to_owned(),
            format!("Wall clock moved {drift:.0} ms against monotonic time"),
            "The system clock jumped during sampling — time sync may be unstable.".to_owned(),
            vec![ev],
        );
        return CheckStatus::Failed;
    }
    CheckStatus::Passed
}

#[cfg(test)]
mod tests {
    use super::*;
    use doctor_domain::DeviceId;
    use std::collections::BTreeMap;
    use std::net::TcpListener;

    fn request(check: &str, params: serde_json::Value) -> CheckRequest {
        let params: BTreeMap<String, serde_json::Value> = match params {
            serde_json::Value::Object(map) => map.into_iter().collect(),
            _ => BTreeMap::new(),
        };
        CheckRequest {
            check_id: CheckId::from(check),
            device_id: DeviceId::from("local"),
            mode: Some(DiagnosticMode::Full),
            params,
            timeout_ms: 15_000,
        }
    }

    #[test]
    fn interfaces_include_loopback_without_flagging_it() {
        let result = run_check(&request("network.interfaces", json!({})));
        assert_eq!(result.status, CheckStatus::Passed);
        assert!(result.findings.is_empty());
        let list = result
            .observations
            .iter()
            .find(|o| o.key == "network.interfaces.list")
            .unwrap();
        let json = serde_json::to_value(&list.value).unwrap();
        assert!(json.to_string().contains("loopback"));
    }

    #[test]
    fn default_route_absence_is_not_an_error() {
        let result = run_check(&request("network.default_route", json!({})));
        // Whatever the environment, this must evaluate without ERROR.
        assert!(result.status.evaluated());
        assert!(!result.evidence.is_empty());
    }

    #[test]
    fn dns_localhost_resolves() {
        let result = run_check(&request("network.dns", json!({})));
        assert_eq!(result.status, CheckStatus::Passed);
    }

    #[test]
    fn dns_failure_is_precise_not_generic() {
        let result = run_check(&request(
            "network.dns",
            json!({"hostname": "does-not-exist-fixture.invalid"}),
        ));
        assert_eq!(result.status, CheckStatus::Failed);
        assert_eq!(result.findings[0].code, "DNS_FAILURE");
        assert!(!result.findings[0].evidence_ids.is_empty());
    }

    #[test]
    fn tcp_port_open_and_refused_against_local_server() {
        // Temporary local server — no public Internet involved.
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();

        let open = run_check(&request(
            "network.tcp_port",
            json!({"targets": [{"host": "127.0.0.1", "port": port}]}),
        ));
        assert_eq!(open.status, CheckStatus::Passed);

        drop(listener);
        let closed = run_check(&request(
            "network.tcp_port",
            json!({"targets": [{"host": "127.0.0.1", "port": port}]}),
        ));
        assert_eq!(closed.status, CheckStatus::Failed);
        assert_eq!(closed.findings[0].code, "PORT_UNREACHABLE");
        let detail = serde_json::to_string(&closed.evidence).unwrap();
        assert!(
            detail.contains("REFUSED") || detail.contains("TIMEOUT"),
            "expected typed outcome in evidence: {detail}"
        );
    }

    #[test]
    fn reachability_loopback_ok_and_invalid_host_is_dns_failure() {
        let ok = run_check(&request(
            "network.reachability",
            json!({"targets": [{"host": "127.0.0.1"}]}),
        ));
        assert_eq!(ok.status, CheckStatus::Passed);

        let bad = run_check(&request(
            "network.reachability",
            json!({"targets": [{"host": "no-such-host-fixture.invalid"}]}),
        ));
        assert_eq!(bad.status, CheckStatus::Failed);
        assert_eq!(bad.findings[0].code, "TARGET_UNREACHABLE");
        let evidence = serde_json::to_string(&bad.evidence).unwrap();
        assert!(evidence.contains("DNS_FAILURE"), "evidence: {evidence}");
    }

    /// Deterministic TIMEOUT fixture without any Internet dependency:
    /// a loopback listener whose accept backlog is saturated drops further
    /// SYNs on Linux, so a connect attempt can only time out.
    #[cfg(target_os = "linux")]
    #[test]
    fn reachability_timeout_fixture_is_typed() {
        use socket2::{Domain, Socket, Type};
        let socket = Socket::new(Domain::IPV4, Type::STREAM, None).unwrap();
        socket
            .bind(
                &"127.0.0.1:0"
                    .parse::<std::net::SocketAddr>()
                    .unwrap()
                    .into(),
            )
            .unwrap();
        socket.listen(0).unwrap(); // minimal backlog, never accepted
        let addr = socket.local_addr().unwrap().as_socket().unwrap();
        // Saturate the accept queue so further SYNs are dropped.
        let _fillers: Vec<_> = (0..4)
            .filter_map(|_| TcpStream::connect_timeout(&addr, Duration::from_millis(200)).ok())
            .collect();

        let result = run_check(&request(
            "network.reachability",
            json!({"targets": [{"host": "127.0.0.1", "port": addr.port(), "timeout_ms": 300}]}),
        ));
        assert_eq!(result.status, CheckStatus::Failed);
        let evidence = serde_json::to_string(&result.evidence).unwrap();
        assert!(evidence.contains("TIMEOUT"), "evidence: {evidence}");
    }

    /// On Windows the backlog trick answers with RST instead, so the
    /// timeout fixture uses a non-routable TEST-NET address (no proxy on
    /// CI runners). TIMEOUT and UNREACHABLE are both typed outcomes.
    #[cfg(windows)]
    #[test]
    fn reachability_timeout_fixture_is_typed() {
        let result = run_check(&request(
            "network.reachability",
            json!({"targets": [{"host": "192.0.2.1", "port": 81, "timeout_ms": 300}]}),
        ));
        assert_eq!(result.status, CheckStatus::Failed);
        let evidence = serde_json::to_string(&result.evidence).unwrap();
        assert!(
            evidence.contains("TIMEOUT") || evidence.contains("UNREACHABLE"),
            "evidence: {evidence}"
        );
    }

    #[test]
    fn latency_sampling_is_bounded_and_reports_stats() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let result = run_check(&request(
            "network.latency",
            json!({"targets": [{"host": "127.0.0.1", "port": port}]}),
        ));
        assert_eq!(result.status, CheckStatus::Passed);
        let obs = result
            .observations
            .iter()
            .find(|o| o.key.starts_with("network.latency."))
            .unwrap();
        let stats = serde_json::to_value(&obs.value).unwrap();
        let stats = &stats["value"];
        assert_eq!(stats["successful"].as_u64().unwrap(), 10); // FULL = 10 samples
        assert!(stats["avg_ms"].as_f64().unwrap() >= 0.0);
    }

    #[test]
    fn time_check_passes_on_stable_clock() {
        let result = run_check(&request("network.time", json!({})));
        assert!(result.status.evaluated());
        assert!(result
            .observations
            .iter()
            .any(|o| o.key == "network.time.drift_ms"));
    }

    #[test]
    fn manifest_is_bootstrap_only() {
        let manifest_path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("plugin.yaml");
        let manifest: doctor_domain::PluginManifest =
            serde_yaml::from_str(&std::fs::read_to_string(manifest_path).unwrap()).unwrap();
        assert!(manifest.checks.is_empty());
    }
}
