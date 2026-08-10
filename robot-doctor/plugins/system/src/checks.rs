//! System diagnostic checks, normalized to the Robot Doctor domain model.
//!
//! All platform differences are absorbed here (via `sysinfo`): Linux and
//! Windows produce identical observation keys and evidence shapes.

use chrono::{Local, Offset, Utc};
use doctor_domain::{
    AttributeValue, CheckCost, CheckDeclaration, CheckId, CheckRequest, CheckResult, CheckStatus,
    ComparisonEntity, DeviceId, DiagnosticMode, EntityKey, Evidence, EvidenceKind, Finding,
    FindingId, Observation, PluginId, ProjectionReport, RuleId, Severity,
};
use serde_json::json;
use sysinfo::{Disks, ProcessesToUpdate, System, MINIMUM_CPU_UPDATE_INTERVAL};

pub const PLUGIN_ID: &str = "system";
pub const PLUGIN_VERSION: &str = env!("CARGO_PKG_VERSION");

/// Thresholds for the plugin's built-in generic findings.
///
/// These are system-generic (never robot-specific) heuristics. When the
/// central rule engine lands (Phase D) they move into `doctor-rules`;
/// the observation keys they read are already the stable contract.
const MEMORY_WARN_PERCENT: f64 = 90.0;
const MEMORY_CRIT_PERCENT: f64 = 97.0;
const DISK_WARN_FREE_PERCENT: f64 = 10.0;
const DISK_CRIT_FREE_PERCENT: f64 = 5.0;
/// Ignore tiny volumes (squashfs snaps, boot partitions) for DISK_LOW.
const DISK_MIN_TOTAL_BYTES: u64 = 8 * 1024 * 1024 * 1024;

/// The checks this plugin implements. Mirrored in `plugin.yaml` for
/// pre-start discovery; the running plugin's answer is authoritative.
pub fn check_declarations() -> Vec<CheckDeclaration> {
    fn decl(
        id: &str,
        name: &str,
        description: &str,
        cost: CheckCost,
        timeout_ms: u64,
    ) -> CheckDeclaration {
        CheckDeclaration {
            id: CheckId::from(id),
            name: name.to_owned(),
            description: description.to_owned(),
            cost,
            timeout_ms,
            platforms: vec![],
            depends_on: vec![],
            modes: vec![DiagnosticMode::Quick, DiagnosticMode::Full],
        }
    }
    vec![
        decl(
            "system.identity",
            "Machine identity",
            "Hostname, architecture and OS identity",
            CheckCost::Fast,
            5_000,
        ),
        decl(
            "system.os",
            "Operating system",
            "OS name, version and kernel",
            CheckCost::Fast,
            5_000,
        ),
        decl(
            "system.cpu",
            "CPU",
            "Core count, per-core utilization and load",
            CheckCost::Medium,
            8_000,
        ),
        decl(
            "system.memory",
            "Memory",
            "RAM capacity and pressure",
            CheckCost::Fast,
            5_000,
        ),
        decl(
            "system.swap",
            "Swap",
            "Swap capacity and usage",
            CheckCost::Fast,
            5_000,
        ),
        decl(
            "system.disk",
            "Disks",
            "Mounted filesystems, capacity and free space",
            CheckCost::Fast,
            8_000,
        ),
        decl(
            "system.process",
            "Processes",
            "Process inventory and top consumers",
            CheckCost::Medium,
            10_000,
        ),
        decl(
            "system.uptime",
            "Uptime",
            "System uptime and boot time",
            CheckCost::Fast,
            5_000,
        ),
        decl(
            "system.time",
            "System time",
            "Wall clock and timezone offset",
            CheckCost::Fast,
            5_000,
        ),
    ]
}

/// Execute one check by id. Unknown ids produce an `Unsupported` result
/// (never a crash).
pub fn run_check(request: &CheckRequest) -> CheckResult {
    let started = std::time::Instant::now();
    let started_at = Utc::now();
    let mut ctx = Ctx::new(&request.check_id, &request.device_id);

    let status = match request.check_id.as_str() {
        "system.identity" => check_identity(&mut ctx),
        "system.os" => check_os(&mut ctx),
        "system.cpu" => check_cpu(&mut ctx),
        "system.memory" => check_memory(&mut ctx),
        "system.swap" => check_swap(&mut ctx),
        "system.disk" => check_disk(&mut ctx),
        "system.process" => check_process(&mut ctx),
        "system.uptime" => check_uptime(&mut ctx),
        "system.time" => check_time(&mut ctx),
        _ => CheckStatus::Unsupported,
    };

    let error = match status {
        CheckStatus::Passed | CheckStatus::Failed => None,
        other => Some(doctor_domain::CheckError {
            status: other,
            message: match other {
                CheckStatus::Unsupported => {
                    format!(
                        "check '{}' is not provided by the system plugin",
                        request.check_id
                    )
                }
                _ => "check could not evaluate its subject".to_owned(),
            },
        }),
    };

    // Baseline projection (C2): the system namespace is observable
    // whenever a check evaluated; nothing here judges the values.
    let projection = if status.evaluated() {
        Some(ProjectionReport::observed_kinds(
            NAMESPACE,
            projected_kinds(&request.check_id).iter().copied(),
            project(&request.check_id, &ctx.observations),
        ))
    } else if status == CheckStatus::Unsupported {
        None
    } else {
        Some(ProjectionReport::not_observed_kinds(
            NAMESPACE,
            projected_kinds(&request.check_id).iter().copied(),
            format!("check '{}' could not observe the system", request.check_id),
        ))
    };

    CheckResult {
        check_id: request.check_id.clone(),
        plugin_id: PluginId::from(PLUGIN_ID),
        device_id: request.device_id.clone(),
        status,
        started_at,
        duration_ms: started.elapsed().as_millis() as u64,
        observations: ctx.observations,
        evidence: ctx.evidence,
        findings: ctx.findings,
        error,
        projection,
    }
}

/// Namespace this plugin projects into for baseline comparison.
pub const NAMESPACE: &str = "system";

/// Entity kinds a given check speaks for, declared independently of what
/// it happened to find: a machine with no mounted disk still counts as
/// having been looked at.
fn projected_kinds(check_id: &CheckId) -> &'static [&'static str] {
    match check_id.as_str() {
        "system.identity" => &["host"],
        "system.os" => &["os"],
        "system.cpu" => &["cpu"],
        "system.memory" => &["memory"],
        "system.swap" => &["swap"],
        "system.disk" => &["disk"],
        // Volatile checks project nothing at all.
        _ => &[],
    }
}

/// Translate this plugin's observations into stable comparison entities.
///
/// Structural facts only: identity, capacities and mount structure are
/// compared; instantaneous load, free space and clock values are left as
/// observations because diffing them would be meaningless noise.
fn project(check_id: &CheckId, observations: &[Observation]) -> Vec<ComparisonEntity> {
    let plugin_id = PluginId::from(PLUGIN_ID);
    let entity = |kind: &str, key: &str, display: &str| {
        ComparisonEntity::new(
            EntityKey::new(NAMESPACE, kind, key),
            display,
            &plugin_id,
            check_id,
        )
    };
    let text = |key: &str| {
        observations
            .iter()
            .find(|o| o.key == key)
            .and_then(|o| o.as_text().map(str::to_owned))
    };
    let number = |key: &str| {
        observations
            .iter()
            .find(|o| o.key == key)
            .and_then(|o| o.as_number())
    };
    let evidence: Vec<_> = observations
        .iter()
        .flat_map(|o| o.evidence_ids.iter().cloned())
        .take(4)
        .collect();

    let mut entities = Vec::new();
    match check_id.as_str() {
        "system.identity" => {
            let mut host = entity("host", "primary", "This machine").with_evidence(evidence);
            if let Some(name) = text("system.identity.hostname") {
                host = host.with("hostname", AttributeValue::text(name));
            }
            if let Some(arch) = text("system.identity.arch") {
                host = host.with("architecture", AttributeValue::text(arch));
            }
            if let Some(distribution) = text("system.identity.distribution") {
                host = host.with("distribution", AttributeValue::text(distribution));
            }
            entities.push(host);
        }
        "system.os" => {
            let mut os = entity("os", "primary", "Operating system").with_evidence(evidence);
            for (field, key) in [
                ("name", "system.os.name"),
                ("version", "system.os.version"),
                ("kernel", "system.os.kernel"),
            ] {
                if let Some(value) = text(key) {
                    os = os.with(field, AttributeValue::text(value));
                }
            }
            entities.push(os);
        }
        "system.cpu" => {
            let mut cpu = entity("cpu", "primary", "CPU").with_evidence(evidence);
            if let Some(brand) = text("system.cpu.brand") {
                cpu = cpu.with("brand", AttributeValue::text(brand));
            }
            if let Some(cores) = number("system.cpu.logical_cores") {
                cpu = cpu.with("logical_cores", AttributeValue::Number(cores));
            }
            entities.push(cpu);
        }
        "system.memory" => {
            let mut memory = entity("memory", "primary", "Memory").with_evidence(evidence);
            if let Some(total) = number("system.memory.total_bytes") {
                memory = memory.with("total_bytes", AttributeValue::Number(total));
            }
            entities.push(memory);
        }
        "system.swap" => {
            let mut swap = entity("swap", "primary", "Swap").with_evidence(evidence);
            if let Some(total) = number("system.swap.total_bytes") {
                swap = swap.with("total_bytes", AttributeValue::Number(total));
            }
            entities.push(swap);
        }
        "system.disk" => {
            let mounts = observations
                .iter()
                .find(|o| o.key == "system.disk.mounts")
                .and_then(|o| match &o.value {
                    doctor_domain::ObservationValue::Json(v) => v.as_array().cloned(),
                    _ => None,
                })
                .unwrap_or_default();
            for mount in mounts {
                let Some(path) = mount.get("mount").and_then(|v| v.as_str()) else {
                    continue;
                };
                let mut disk = entity("disk", path, path).with_evidence(evidence.clone());
                if let Some(total) = mount.get("total_bytes").and_then(|v| v.as_f64()) {
                    disk = disk.with("total_bytes", AttributeValue::Number(total));
                }
                if let Some(fs) = mount.get("file_system").and_then(|v| v.as_str()) {
                    disk = disk.with("file_system", AttributeValue::text(fs));
                }
                if let Some(free) = mount.get("free_percent").and_then(|v| v.as_f64()) {
                    disk = disk.with("free_percent", AttributeValue::Number(free));
                }
                entities.push(disk);
            }
        }
        // Uptime, process inventory and wall clock are inherently
        // volatile: observed, never compared.
        _ => {}
    }
    entities
}

/// Collection context for one check execution.
struct Ctx {
    check_id: CheckId,
    device_id: DeviceId,
    observations: Vec<Observation>,
    evidence: Vec<Evidence>,
    findings: Vec<Finding>,
}

impl Ctx {
    fn new(check_id: &CheckId, device_id: &DeviceId) -> Self {
        Self {
            check_id: check_id.clone(),
            device_id: device_id.clone(),
            observations: Vec::new(),
            evidence: Vec::new(),
            findings: Vec::new(),
        }
    }

    fn add_evidence(
        &mut self,
        summary: &str,
        data: serde_json::Value,
    ) -> doctor_domain::EvidenceId {
        let ev = Evidence::new(EvidenceKind::Api, "sysinfo", summary, data);
        let id = ev.id.clone();
        self.evidence.push(ev);
        id
    }

    fn number(&mut self, key: &str, value: f64, unit: &str, evidence: &doctor_domain::EvidenceId) {
        self.observations.push(Observation::number(
            &self.check_id,
            key,
            value,
            unit,
            vec![evidence.clone()],
        ));
    }

    fn text(&mut self, key: &str, value: impl Into<String>, evidence: &doctor_domain::EvidenceId) {
        self.observations.push(Observation::text(
            &self.check_id,
            key,
            value,
            vec![evidence.clone()],
        ));
    }

    fn json(&mut self, key: &str, value: serde_json::Value, evidence: &doctor_domain::EvidenceId) {
        self.observations.push(Observation::json(
            &self.check_id,
            key,
            value,
            vec![evidence.clone()],
        ));
    }

    fn finding(
        &mut self,
        code: &str,
        severity: Severity,
        subject: String,
        title: String,
        detail: String,
        evidence: Vec<doctor_domain::EvidenceId>,
    ) {
        self.findings.push(Finding {
            id: FindingId::generate(),
            device_id: self.device_id.clone(),
            check_id: self.check_id.clone(),
            rule_id: Some(RuleId::from(code)),
            severity,
            code: code.to_owned(),
            title,
            detail,
            subject,
            evidence_ids: evidence,
            detected_at: Utc::now(),
        });
    }
}

fn check_identity(ctx: &mut Ctx) -> CheckStatus {
    let hostname = System::host_name().unwrap_or_else(|| "unknown".to_owned());
    let arch = System::cpu_arch();
    let os = System::long_os_version().unwrap_or_else(|| "unknown".to_owned());
    let distribution = System::distribution_id();
    let ev = ctx.add_evidence(
        "Machine identity from OS APIs",
        json!({
            "hostname": hostname,
            "arch": arch,
            "os": os,
            "distribution_id": distribution,
        }),
    );
    ctx.text("system.identity.hostname", hostname, &ev);
    ctx.text("system.identity.arch", arch, &ev);
    ctx.text("system.identity.os", os, &ev);
    ctx.text("system.identity.distribution", distribution, &ev);
    CheckStatus::Passed
}

fn check_os(ctx: &mut Ctx) -> CheckStatus {
    let name = System::name().unwrap_or_else(|| "unknown".to_owned());
    let version = System::os_version().unwrap_or_else(|| "unknown".to_owned());
    let long = System::long_os_version().unwrap_or_else(|| "unknown".to_owned());
    let kernel = System::kernel_version().unwrap_or_else(|| "unknown".to_owned());
    let ev = ctx.add_evidence(
        "OS identification",
        json!({
            "name": name,
            "version": version,
            "long_version": long,
            "kernel": kernel,
        }),
    );
    ctx.text("system.os.name", name, &ev);
    ctx.text("system.os.version", version, &ev);
    ctx.text("system.os.long_version", long, &ev);
    ctx.text("system.os.kernel", kernel, &ev);
    CheckStatus::Passed
}

fn check_cpu(ctx: &mut Ctx) -> CheckStatus {
    let mut sys = System::new();
    sys.refresh_cpu_all();
    std::thread::sleep(MINIMUM_CPU_UPDATE_INTERVAL);
    sys.refresh_cpu_all();

    let cpus = sys.cpus();
    if cpus.is_empty() {
        return CheckStatus::Error;
    }
    let per_core: Vec<serde_json::Value> = cpus
        .iter()
        .enumerate()
        .map(|(i, c)| json!({"core": i, "usage_percent": c.cpu_usage(), "frequency_mhz": c.frequency()}))
        .collect();
    let load = System::load_average();
    let brand = cpus[0].brand().trim().to_owned();
    let global = sys.global_cpu_usage() as f64;

    let ev = ctx.add_evidence(
        "CPU sampled over two refresh cycles",
        json!({
            "brand": brand,
            "logical_cores": cpus.len(),
            "global_usage_percent": global,
            "per_core": per_core,
            "load_average": {"one": load.one, "five": load.five, "fifteen": load.fifteen},
        }),
    );
    ctx.text("system.cpu.brand", brand, &ev);
    ctx.number("system.cpu.logical_cores", cpus.len() as f64, "count", &ev);
    ctx.number("system.cpu.usage_percent", global, "percent", &ev);
    ctx.number("system.cpu.load_1m", load.one, "load", &ev);
    ctx.number("system.cpu.load_5m", load.five, "load", &ev);
    ctx.number("system.cpu.load_15m", load.fifteen, "load", &ev);
    ctx.json("system.cpu.per_core", json!(per_core), &ev);
    CheckStatus::Passed
}

fn check_memory(ctx: &mut Ctx) -> CheckStatus {
    let mut sys = System::new();
    sys.refresh_memory();
    let total = sys.total_memory();
    if total == 0 {
        return CheckStatus::Error;
    }
    let available = sys.available_memory();
    let used = total.saturating_sub(available);
    let used_percent = used as f64 / total as f64 * 100.0;

    let ev = ctx.add_evidence(
        "Memory usage from OS",
        json!({
            "total_bytes": total,
            "available_bytes": available,
            "used_bytes": used,
            "used_percent": used_percent,
        }),
    );
    ctx.number("system.memory.total_bytes", total as f64, "bytes", &ev);
    ctx.number(
        "system.memory.available_bytes",
        available as f64,
        "bytes",
        &ev,
    );
    ctx.number("system.memory.used_percent", used_percent, "percent", &ev);

    if used_percent >= MEMORY_CRIT_PERCENT || used_percent >= MEMORY_WARN_PERCENT {
        let severity = if used_percent >= MEMORY_CRIT_PERCENT {
            Severity::Critical
        } else {
            Severity::Warning
        };
        ctx.finding(
            "MEMORY_PRESSURE",
            severity,
            "memory".to_owned(),
            format!("Memory usage at {used_percent:.0}%"),
            format!(
                "{:.1} GiB of {:.1} GiB in use; free memory is running out, processes may be OOM-killed or start swapping.",
                used as f64 / 1073741824.0,
                total as f64 / 1073741824.0
            ),
            vec![ev],
        );
        return CheckStatus::Failed;
    }
    CheckStatus::Passed
}

fn check_swap(ctx: &mut Ctx) -> CheckStatus {
    let mut sys = System::new();
    sys.refresh_memory();
    let total = sys.total_swap();
    let used = sys.used_swap();
    let used_percent = if total > 0 {
        used as f64 / total as f64 * 100.0
    } else {
        0.0
    };
    let ev = ctx.add_evidence(
        "Swap usage from OS",
        json!({"total_bytes": total, "used_bytes": used, "used_percent": used_percent}),
    );
    ctx.number("system.swap.total_bytes", total as f64, "bytes", &ev);
    ctx.number("system.swap.used_bytes", used as f64, "bytes", &ev);
    ctx.number("system.swap.used_percent", used_percent, "percent", &ev);
    CheckStatus::Passed
}

fn check_disk(ctx: &mut Ctx) -> CheckStatus {
    let disks = Disks::new_with_refreshed_list();
    if disks.list().is_empty() {
        return CheckStatus::Error;
    }
    let mut any_low = false;
    let mut table = Vec::new();
    for disk in disks.list() {
        let mount = disk.mount_point().to_string_lossy().to_string();
        let total = disk.total_space();
        let available = disk.available_space();
        let free_percent = if total > 0 {
            available as f64 / total as f64 * 100.0
        } else {
            0.0
        };
        table.push(json!({
            "mount": mount,
            "file_system": disk.file_system().to_string_lossy(),
            "total_bytes": total,
            "available_bytes": available,
            "free_percent": free_percent,
            "removable": disk.is_removable(),
        }));
    }
    let ev = ctx.add_evidence("Mounted filesystems", json!({"disks": table.clone()}));
    ctx.json("system.disk.mounts", json!(table), &ev);

    for disk in disks.list() {
        let total = disk.total_space();
        if disk.is_removable() || total < DISK_MIN_TOTAL_BYTES {
            continue;
        }
        let available = disk.available_space();
        let free_percent = if total > 0 {
            available as f64 / total as f64 * 100.0
        } else {
            0.0
        };
        let mount = disk.mount_point().to_string_lossy().to_string();
        ctx.number(
            &format!("system.disk.{mount}.free_percent"),
            free_percent,
            "percent",
            &ev,
        );
        if free_percent < DISK_WARN_FREE_PERCENT {
            any_low = true;
            let severity = if free_percent < DISK_CRIT_FREE_PERCENT {
                Severity::Critical
            } else {
                Severity::Warning
            };
            ctx.finding(
                "DISK_LOW",
                severity,
                format!("disk:{mount}"),
                format!("Disk {mount} has only {free_percent:.1}% free"),
                format!(
                    "{:.1} GiB free of {:.1} GiB. Logging, bag recording and updates may start failing.",
                    available as f64 / 1073741824.0,
                    total as f64 / 1073741824.0
                ),
                vec![ev.clone()],
            );
        }
    }
    if any_low {
        CheckStatus::Failed
    } else {
        CheckStatus::Passed
    }
}

fn check_process(ctx: &mut Ctx) -> CheckStatus {
    let mut sys = System::new();
    sys.refresh_processes(ProcessesToUpdate::All, true);
    std::thread::sleep(MINIMUM_CPU_UPDATE_INTERVAL);
    sys.refresh_processes(ProcessesToUpdate::All, true);

    let processes = sys.processes();
    if processes.is_empty() {
        return CheckStatus::Error;
    }
    let mut list: Vec<(_, String, f32, u64)> = processes
        .iter()
        .map(|(pid, p)| {
            (
                pid.as_u32(),
                p.name().to_string_lossy().to_string(),
                p.cpu_usage(),
                p.memory(),
            )
        })
        .collect();

    list.sort_by(|a, b| b.2.total_cmp(&a.2));
    let top_cpu: Vec<serde_json::Value> = list
        .iter()
        .take(10)
        .map(|(pid, name, cpu, mem)| {
            json!({"pid": pid, "name": name, "cpu_percent": cpu, "memory_bytes": mem})
        })
        .collect();

    list.sort_by_key(|item| std::cmp::Reverse(item.3));
    let top_memory: Vec<serde_json::Value> = list
        .iter()
        .take(10)
        .map(|(pid, name, cpu, mem)| {
            json!({"pid": pid, "name": name, "cpu_percent": cpu, "memory_bytes": mem})
        })
        .collect();

    let ev = ctx.add_evidence(
        "Process inventory (top consumers captured)",
        json!({
            "total": processes.len(),
            "top_cpu": top_cpu.clone(),
            "top_memory": top_memory.clone(),
        }),
    );
    ctx.number("system.process.count", processes.len() as f64, "count", &ev);
    ctx.json("system.process.top_cpu", json!(top_cpu), &ev);
    ctx.json("system.process.top_memory", json!(top_memory), &ev);
    CheckStatus::Passed
}

fn check_uptime(ctx: &mut Ctx) -> CheckStatus {
    let uptime = System::uptime();
    let boot = System::boot_time();
    let ev = ctx.add_evidence(
        "Uptime from OS",
        json!({"uptime_seconds": uptime, "boot_time_unix": boot}),
    );
    ctx.number("system.uptime.seconds", uptime as f64, "seconds", &ev);
    ctx.number(
        "system.uptime.boot_time_unix",
        boot as f64,
        "unix_seconds",
        &ev,
    );
    CheckStatus::Passed
}

fn check_time(ctx: &mut Ctx) -> CheckStatus {
    let now_utc = Utc::now();
    let local = Local::now();
    let offset_seconds = local.offset().fix().local_minus_utc();
    let ev = ctx.add_evidence(
        "System wall clock",
        json!({
            "utc": now_utc.to_rfc3339(),
            "local": local.to_rfc3339(),
            "utc_offset_seconds": offset_seconds,
        }),
    );
    ctx.text("system.time.utc", now_utc.to_rfc3339(), &ev);
    ctx.number(
        "system.time.utc_offset_seconds",
        offset_seconds as f64,
        "seconds",
        &ev,
    );
    ctx.number(
        "system.time.unix_ms",
        now_utc.timestamp_millis() as f64,
        "milliseconds",
        &ev,
    );
    CheckStatus::Passed
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    fn request(check: &str) -> CheckRequest {
        CheckRequest {
            check_id: CheckId::from(check),
            device_id: DeviceId::from("local"),
            mode: None,
            params: BTreeMap::new(),
            timeout_ms: 10_000,
        }
    }

    #[test]
    fn every_declared_check_runs_and_produces_evidence() {
        for decl in check_declarations() {
            let result = run_check(&request(decl.id.as_str()));
            assert!(
                result.status.evaluated(),
                "check {} returned {:?}",
                decl.id,
                result.status
            );
            assert!(
                !result.evidence.is_empty(),
                "check {} produced no evidence",
                decl.id
            );
            assert!(
                !result.observations.is_empty(),
                "check {} produced no observations",
                decl.id
            );
            // Every observation must reference captured evidence.
            let evidence_ids: Vec<_> = result.evidence.iter().map(|e| e.id.clone()).collect();
            for obs in &result.observations {
                assert!(obs.evidence_ids.iter().all(|id| evidence_ids.contains(id)));
            }
            // Every finding must reference captured evidence.
            for finding in &result.findings {
                assert!(!finding.evidence_ids.is_empty());
                assert!(finding
                    .evidence_ids
                    .iter()
                    .all(|id| evidence_ids.contains(id)));
            }
        }
    }

    #[test]
    fn unknown_check_is_unsupported_not_a_crash() {
        let result = run_check(&request("system.does_not_exist"));
        assert_eq!(result.status, CheckStatus::Unsupported);
        assert!(result.error.is_some());
    }

    #[test]
    fn cpu_check_reports_positive_core_count() {
        let result = run_check(&request("system.cpu"));
        let cores = result
            .observations
            .iter()
            .find(|o| o.key == "system.cpu.logical_cores")
            .and_then(|o| o.as_number())
            .unwrap();
        assert!(cores >= 1.0);
    }

    #[test]
    fn manifest_is_bootstrap_only() {
        // plugin.yaml must not declare checks: the runtime CAPABILITIES
        // answer is the single authoritative source (no duplication).
        let manifest_path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("plugin.yaml");
        let manifest: doctor_domain::PluginManifest =
            serde_yaml::from_str(&std::fs::read_to_string(manifest_path).unwrap()).unwrap();
        assert!(manifest.checks.is_empty());
        assert!(manifest.capabilities.is_empty());
        assert!(!check_declarations().is_empty());
    }
}
