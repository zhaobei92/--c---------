//! NVIDIA GPU diagnostics via NVML — the programmatic management interface
//! (never parsing human-formatted `nvidia-smi` output).
//!
//! NVML is loaded dynamically at runtime. Absence of driver/library/GPU is
//! a normal UNAVAILABLE state, never a Robot Doctor error, and never
//! degrades overall machine health. Multi-GPU from the start: every check
//! iterates all devices and keys observations by GPU index with a stable
//! UUID identity. Units are normalized (°C, bytes, W, %, MHz); the UI
//! decides display formatting.

use doctor_domain::{
    AttributeValue, CheckCost, CheckDeclaration, CheckId, CheckRequest, CheckResult, CheckStatus,
    DiagnosticMode, EvidenceKind,
};
use doctor_plugin_host::CheckContext;
use nvml_wrapper::enum_wrappers::device::{Clock, TemperatureSensor};
use nvml_wrapper::error::NvmlError;
use nvml_wrapper::Nvml;
use serde_json::json;
use std::sync::OnceLock;

pub const PLUGIN_ID: &str = "nvidia";
pub const PLUGIN_VERSION: &str = env!("CARGO_PKG_VERSION");

/// NVML handle initialized once per plugin process.
static NVML: OnceLock<Result<Nvml, String>> = OnceLock::new();

fn nvml() -> Result<&'static Nvml, (CheckStatus, String)> {
    let slot = NVML.get_or_init(|| Nvml::init().map_err(|e| init_error_text(&e)));
    match slot {
        Ok(nvml) => Ok(nvml),
        Err(msg) => Err((CheckStatus::Unavailable, msg.clone())),
    }
}

/// Whether NVML's failure means "this machine genuinely has no NVIDIA
/// support" (a successful observation of absence) rather than "we could
/// not determine anything" (see the C2 UNKNOWN/UNSATISFIED distinction).
fn nvidia_definitively_absent() -> bool {
    matches!(
        NVML.get(),
        Some(Err(msg)) if msg.contains("NVML library not found")
    )
}

fn init_error_text(err: &NvmlError) -> String {
    match err {
        NvmlError::LibloadingError(_) => {
            "NVML library not found — no NVIDIA driver installed on this system".to_owned()
        }
        NvmlError::DriverNotLoaded => "NVIDIA driver is installed but not loaded".to_owned(),
        NvmlError::LibRmVersionMismatch => {
            "NVIDIA driver/library version mismatch — reinstall or reboot".to_owned()
        }
        NvmlError::NoPermission => "no permission to talk to the NVIDIA driver".to_owned(),
        other => format!("NVML initialization failed: {other}"),
    }
}

/// Map a per-call NVML error to failure semantics. `None` means the metric
/// is unsupported on this GPU (skip it, don't fail the check).
fn classify(err: &NvmlError) -> Option<(CheckStatus, String)> {
    match err {
        NvmlError::NotSupported => None,
        NvmlError::NoPermission => Some((
            CheckStatus::PermissionDenied,
            "no permission for this NVML query".to_owned(),
        )),
        NvmlError::GpuLost | NvmlError::NotFound => Some((
            CheckStatus::Error,
            format!("GPU disappeared during query: {err}"),
        )),
        other => Some((CheckStatus::Error, format!("NVML query failed: {other}"))),
    }
}

pub fn check_declarations() -> Vec<CheckDeclaration> {
    fn decl(
        id: &str,
        name: &str,
        description: &str,
        cost: CheckCost,
        modes: &[DiagnosticMode],
    ) -> CheckDeclaration {
        CheckDeclaration {
            id: CheckId::from(id),
            name: name.to_owned(),
            description: description.to_owned(),
            cost,
            timeout_ms: 8_000,
            platforms: vec![],
            depends_on: vec![],
            modes: modes.to_vec(),
        }
    }
    let both = &[DiagnosticMode::Quick, DiagnosticMode::Full][..];
    let full = &[DiagnosticMode::Full][..];
    vec![
        decl(
            "nvidia.driver",
            "NVIDIA driver",
            "Driver, NVML and CUDA versions",
            CheckCost::Fast,
            both,
        ),
        decl(
            "nvidia.devices",
            "GPU inventory",
            "GPU identity and memory per device",
            CheckCost::Fast,
            both,
        ),
        decl(
            "nvidia.utilization",
            "GPU utilization",
            "GPU and memory-controller load",
            CheckCost::Fast,
            full,
        ),
        decl(
            "nvidia.memory",
            "GPU memory",
            "VRAM usage per device",
            CheckCost::Fast,
            full,
        ),
        decl(
            "nvidia.temperature",
            "GPU temperature",
            "Core temperature per device",
            CheckCost::Fast,
            full,
        ),
        decl(
            "nvidia.power",
            "GPU power",
            "Power draw and limits",
            CheckCost::Fast,
            full,
        ),
        decl(
            "nvidia.clocks",
            "GPU clocks",
            "Graphics/SM/memory clocks",
            CheckCost::Fast,
            full,
        ),
        decl(
            "nvidia.processes",
            "GPU processes",
            "Processes using each GPU",
            CheckCost::Fast,
            full,
        ),
    ]
}

/// Namespace this plugin projects into for baseline comparison.
pub const NAMESPACE: &str = "nvidia";

/// Kinds this plugin is authoritative for. Declared explicitly so that a
/// machine with zero GPUs still counts as *observed* (§43) instead of
/// looking like a namespace nobody inspected.
pub const KINDS: &[&str] = &["gpu", "driver"];

pub fn run_check(request: &CheckRequest) -> CheckResult {
    let mut ctx = CheckContext::new(PLUGIN_ID, request);
    let nvml = match nvml() {
        Ok(nvml) => nvml,
        Err((status, msg)) => {
            // Critical C2 distinction: a machine with no NVIDIA driver has
            // been *successfully observed* to have no GPU, so a REQUIRED
            // GPU expectation is legitimately UNSATISFIED. A driver/library
            // mismatch or permission error means we could not look at all,
            // so expectations must stay UNKNOWN.
            if nvidia_definitively_absent() {
                ctx.observed_kinds(NAMESPACE, KINDS, vec![]);
            } else {
                ctx.not_observed(NAMESPACE, msg.clone());
            }
            return ctx.finish(PLUGIN_ID, status, Some(msg));
        }
    };
    let outcome = match request.check_id.as_str() {
        "nvidia.driver" => check_driver(&mut ctx, nvml),
        "nvidia.devices" => per_gpu(&mut ctx, nvml, "devices", device_inventory),
        "nvidia.utilization" => per_gpu(&mut ctx, nvml, "utilization", device_utilization),
        "nvidia.memory" => per_gpu(&mut ctx, nvml, "memory", device_memory),
        "nvidia.temperature" => per_gpu(&mut ctx, nvml, "temperature", device_temperature),
        "nvidia.power" => per_gpu(&mut ctx, nvml, "power", device_power),
        "nvidia.clocks" => per_gpu(&mut ctx, nvml, "clocks", device_clocks),
        "nvidia.processes" => per_gpu(&mut ctx, nvml, "processes", device_processes),
        _ => Err((
            CheckStatus::Unsupported,
            format!(
                "check '{}' is not provided by the nvidia plugin",
                request.check_id
            ),
        )),
    };
    match outcome {
        Ok(status) => {
            project_gpus(&mut ctx);
            ctx.finish(PLUGIN_ID, status, None)
        }
        Err((status, msg)) => {
            if status == CheckStatus::Unsupported {
                // Unknown check id says nothing about the namespace.
            } else if status == CheckStatus::Unavailable {
                ctx.observed_kinds(NAMESPACE, KINDS, vec![]);
            } else {
                ctx.not_observed(NAMESPACE, msg.clone());
            }
            ctx.finish(PLUGIN_ID, status, Some(msg))
        }
    }
}

/// Project per-GPU entities keyed by **UUID** — the only stable identity
/// across executions (index alone can change when devices are added,
/// removed or re-enumerated). Units stay normalized; no formatting.
fn project_gpus(ctx: &mut CheckContext) {
    use std::collections::BTreeMap;

    let evidence = ctx.evidence_ids();
    // gpu index → (attribute name, value)
    let mut per_index: BTreeMap<u32, BTreeMap<String, AttributeValue>> = BTreeMap::new();
    for obs in ctx.observations() {
        let Some(rest) = obs.key.strip_prefix("nvidia.gpu") else {
            continue;
        };
        let Some((index, field)) = rest.split_once('.') else {
            continue;
        };
        let Ok(index) = index.parse::<u32>() else {
            continue;
        };
        let entry = per_index.entry(index).or_default();
        match &obs.value {
            doctor_domain::ObservationValue::Number(n) => {
                entry.insert(field.to_owned(), AttributeValue::Number(*n));
            }
            doctor_domain::ObservationValue::Text(t) => {
                entry.insert(field.to_owned(), AttributeValue::text(t.clone()));
            }
            _ => {}
        }
    }

    let mut entities = Vec::new();
    for (index, attributes) in per_index {
        // Without a UUID the entity has no stable identity; fall back to
        // the index and record that the identity is weaker.
        let uuid = attributes
            .get("uuid")
            .and_then(|v| v.as_text().map(str::to_owned));
        let key = uuid.clone().unwrap_or_else(|| format!("index:{index}"));
        let display = attributes
            .get("model")
            .and_then(|v| v.as_text())
            .unwrap_or("NVIDIA GPU")
            .to_owned();
        let mut entity = ctx
            .entity(NAMESPACE, "gpu", key, display)
            .with("index", AttributeValue::Number(index as f64))
            .with_evidence(evidence.clone());
        for (name, value) in attributes {
            entity = entity.with(&name, value);
        }
        entities.push(entity);
    }

    // Driver facts are a separate entity so a profile can require a
    // driver version without naming a GPU.
    if let Some(version) = ctx
        .observations()
        .iter()
        .find(|o| o.key == "nvidia.driver.version")
        .and_then(|o| o.as_text())
    {
        entities.push(
            ctx.entity(NAMESPACE, "driver", "primary", "NVIDIA driver")
                .with("version", AttributeValue::text(version))
                .with_evidence(evidence.clone()),
        );
    }

    ctx.observed_kinds(NAMESPACE, KINDS, entities);
}

fn check_driver(ctx: &mut CheckContext, nvml: &Nvml) -> Result<CheckStatus, (CheckStatus, String)> {
    let driver = nvml.sys_driver_version().map_err(map_fatal)?;
    let nvml_version = nvml.sys_nvml_version().map_err(map_fatal)?;
    let cuda = nvml.sys_cuda_driver_version().ok();
    let count = nvml.device_count().map_err(map_fatal)?;
    let ev = ctx.evidence(
        EvidenceKind::Api,
        "nvml",
        "Driver and library versions",
        json!({
            "driver_version": driver,
            "nvml_version": nvml_version,
            "cuda_driver_version": cuda,
            "device_count": count,
        }),
    );
    ctx.text("nvidia.driver.version", driver, &ev);
    ctx.text("nvidia.driver.nvml_version", nvml_version, &ev);
    if let Some(cuda) = cuda {
        ctx.number("nvidia.driver.cuda_version", cuda as f64, "version", &ev);
    }
    ctx.number("nvidia.driver.device_count", count as f64, "count", &ev);
    Ok(CheckStatus::Passed)
}

fn map_fatal(err: NvmlError) -> (CheckStatus, String) {
    classify(&err).unwrap_or((
        CheckStatus::Unavailable,
        "metric unsupported on this system".to_owned(),
    ))
}

/// Iterate every GPU; one GPU's unsupported metric never fails the others.
fn per_gpu(
    ctx: &mut CheckContext,
    nvml: &Nvml,
    kind: &str,
    collect: fn(&mut CheckContext, &nvml_wrapper::Device<'_>, u32) -> Result<(), NvmlError>,
) -> Result<CheckStatus, (CheckStatus, String)> {
    let count = nvml.device_count().map_err(map_fatal)?;
    if count == 0 {
        return Err((
            CheckStatus::Unavailable,
            "NVIDIA driver present but no GPU devices found".to_owned(),
        ));
    }
    let mut hard_error: Option<(CheckStatus, String)> = None;
    for index in 0..count {
        let device = match nvml.device_by_index(index) {
            Ok(d) => d,
            Err(err) => {
                if let Some(e) = classify(&err) {
                    hard_error.get_or_insert(e);
                }
                continue;
            }
        };
        if let Err(err) = collect(ctx, &device, index) {
            match classify(&err) {
                // Unsupported on this GPU: record the fact, keep going.
                None => {
                    let ev = ctx.evidence(
                        EvidenceKind::Api,
                        "nvml",
                        &format!("GPU {index}: {kind} not supported"),
                        json!({"gpu": index, "unsupported": kind}),
                    );
                    ctx.text(&format!("nvidia.{kind}.gpu{index}.supported"), "false", &ev);
                }
                Some(e) => {
                    hard_error.get_or_insert(e);
                }
            }
        }
    }
    match hard_error {
        // At least record what we could; a hard error on every path
        // surfaces as the typed error status.
        Some((status, msg)) if ctx.has_findings() || !status.evaluated() => Err((status, msg)),
        _ => Ok(CheckStatus::Passed),
    }
}

fn gpu_identity(device: &nvml_wrapper::Device<'_>, index: u32) -> serde_json::Value {
    json!({
        "index": index,
        "uuid": device.uuid().ok(),
        "model": device.name().ok(),
    })
}

fn device_inventory(
    ctx: &mut CheckContext,
    device: &nvml_wrapper::Device<'_>,
    index: u32,
) -> Result<(), NvmlError> {
    let name = device.name()?;
    let uuid = device.uuid()?;
    let memory = device.memory_info()?;
    let ev = ctx.evidence(
        EvidenceKind::Api,
        "nvml",
        &format!("GPU {index} identity"),
        json!({
            "index": index,
            "model": name,
            "uuid": uuid,
            "memory_total_bytes": memory.total,
            "memory_used_bytes": memory.used,
        }),
    );
    ctx.text(&format!("nvidia.gpu{index}.model"), name, &ev);
    ctx.text(&format!("nvidia.gpu{index}.uuid"), uuid, &ev);
    ctx.number(
        &format!("nvidia.gpu{index}.memory_total_bytes"),
        memory.total as f64,
        "bytes",
        &ev,
    );
    ctx.number(
        &format!("nvidia.gpu{index}.memory_used_bytes"),
        memory.used as f64,
        "bytes",
        &ev,
    );
    Ok(())
}

fn device_utilization(
    ctx: &mut CheckContext,
    device: &nvml_wrapper::Device<'_>,
    index: u32,
) -> Result<(), NvmlError> {
    let util = device.utilization_rates()?;
    let ev = ctx.evidence(
        EvidenceKind::Api,
        "nvml",
        &format!("GPU {index} utilization"),
        json!({"identity": gpu_identity(device, index), "gpu_percent": util.gpu, "memory_percent": util.memory}),
    );
    ctx.number(
        &format!("nvidia.gpu{index}.utilization_percent"),
        util.gpu as f64,
        "percent",
        &ev,
    );
    ctx.number(
        &format!("nvidia.gpu{index}.memory_activity_percent"),
        util.memory as f64,
        "percent",
        &ev,
    );
    Ok(())
}

fn device_memory(
    ctx: &mut CheckContext,
    device: &nvml_wrapper::Device<'_>,
    index: u32,
) -> Result<(), NvmlError> {
    let memory = device.memory_info()?;
    let used_percent = if memory.total > 0 {
        memory.used as f64 / memory.total as f64 * 100.0
    } else {
        0.0
    };
    let ev = ctx.evidence(
        EvidenceKind::Api,
        "nvml",
        &format!("GPU {index} memory"),
        json!({
            "identity": gpu_identity(device, index),
            "total_bytes": memory.total,
            "used_bytes": memory.used,
            "free_bytes": memory.free,
        }),
    );
    ctx.number(
        &format!("nvidia.gpu{index}.memory_total_bytes"),
        memory.total as f64,
        "bytes",
        &ev,
    );
    ctx.number(
        &format!("nvidia.gpu{index}.memory_used_bytes"),
        memory.used as f64,
        "bytes",
        &ev,
    );
    ctx.number(
        &format!("nvidia.gpu{index}.memory_used_percent"),
        used_percent,
        "percent",
        &ev,
    );
    Ok(())
}

fn device_temperature(
    ctx: &mut CheckContext,
    device: &nvml_wrapper::Device<'_>,
    index: u32,
) -> Result<(), NvmlError> {
    let temp = device.temperature(TemperatureSensor::Gpu)?;
    let ev = ctx.evidence(
        EvidenceKind::Api,
        "nvml",
        &format!("GPU {index} temperature"),
        json!({"identity": gpu_identity(device, index), "celsius": temp}),
    );
    ctx.number(
        &format!("nvidia.gpu{index}.temperature_celsius"),
        temp as f64,
        "celsius",
        &ev,
    );
    Ok(())
}

fn device_power(
    ctx: &mut CheckContext,
    device: &nvml_wrapper::Device<'_>,
    index: u32,
) -> Result<(), NvmlError> {
    // NVML reports milliwatts; normalize to watts.
    let usage_w = device.power_usage()? as f64 / 1000.0;
    let limit_w = device
        .enforced_power_limit()
        .ok()
        .map(|l| l as f64 / 1000.0);
    let ev = ctx.evidence(
        EvidenceKind::Api,
        "nvml",
        &format!("GPU {index} power"),
        json!({"identity": gpu_identity(device, index), "usage_watts": usage_w, "limit_watts": limit_w}),
    );
    ctx.number(
        &format!("nvidia.gpu{index}.power_watts"),
        usage_w,
        "watts",
        &ev,
    );
    if let Some(limit) = limit_w {
        ctx.number(
            &format!("nvidia.gpu{index}.power_limit_watts"),
            limit,
            "watts",
            &ev,
        );
    }
    Ok(())
}

fn device_clocks(
    ctx: &mut CheckContext,
    device: &nvml_wrapper::Device<'_>,
    index: u32,
) -> Result<(), NvmlError> {
    let graphics = device.clock_info(Clock::Graphics)?;
    let sm = device.clock_info(Clock::SM).ok();
    let memory = device.clock_info(Clock::Memory).ok();
    let pstate = device.performance_state().ok();
    let ev = ctx.evidence(
        EvidenceKind::Api,
        "nvml",
        &format!("GPU {index} clocks"),
        json!({
            "identity": gpu_identity(device, index),
            "graphics_mhz": graphics,
            "sm_mhz": sm,
            "memory_mhz": memory,
            "performance_state": pstate.as_ref().map(|p| format!("{p:?}")),
        }),
    );
    ctx.number(
        &format!("nvidia.gpu{index}.clock_graphics_mhz"),
        graphics as f64,
        "mhz",
        &ev,
    );
    if let Some(sm) = sm {
        ctx.number(
            &format!("nvidia.gpu{index}.clock_sm_mhz"),
            sm as f64,
            "mhz",
            &ev,
        );
    }
    if let Some(memory) = memory {
        ctx.number(
            &format!("nvidia.gpu{index}.clock_memory_mhz"),
            memory as f64,
            "mhz",
            &ev,
        );
    }
    if let Some(pstate) = pstate {
        ctx.text(
            &format!("nvidia.gpu{index}.performance_state"),
            format!("{pstate:?}"),
            &ev,
        );
    }
    Ok(())
}

fn device_processes(
    ctx: &mut CheckContext,
    device: &nvml_wrapper::Device<'_>,
    index: u32,
) -> Result<(), NvmlError> {
    let compute = device.running_compute_processes()?;
    let graphics = device.running_graphics_processes().unwrap_or_default();
    let as_json = |procs: &[nvml_wrapper::struct_wrappers::device::ProcessInfo]| {
        procs
            .iter()
            .map(|p| {
                json!({
                    "pid": p.pid,
                    "used_gpu_memory": format!("{:?}", p.used_gpu_memory),
                })
            })
            .collect::<Vec<_>>()
    };
    let ev = ctx.evidence(
        EvidenceKind::Api,
        "nvml",
        &format!("GPU {index} active processes"),
        json!({
            "identity": gpu_identity(device, index),
            "compute": as_json(&compute),
            "graphics": as_json(&graphics),
        }),
    );
    ctx.number(
        &format!("nvidia.gpu{index}.process_count"),
        (compute.len() + graphics.len()) as f64,
        "count",
        &ev,
    );
    ctx.json(
        &format!("nvidia.gpu{index}.processes"),
        json!({"compute": as_json(&compute), "graphics": as_json(&graphics)}),
        &ev,
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use doctor_domain::DeviceId;
    use std::collections::BTreeMap;

    fn request(check: &str) -> CheckRequest {
        CheckRequest {
            check_id: CheckId::from(check),
            device_id: DeviceId::from("local"),
            mode: Some(DiagnosticMode::Full),
            params: BTreeMap::new(),
            timeout_ms: 8_000,
        }
    }

    /// On machines without NVIDIA hardware every check must be a clean
    /// UNAVAILABLE (never ERROR, never a crash). On machines with NVIDIA
    /// the checks must evaluate. Both paths are legitimate outcomes.
    #[test]
    fn absence_is_unavailable_not_error() {
        for decl in check_declarations() {
            let result = run_check(&request(decl.id.as_str()));
            match result.status {
                CheckStatus::Unavailable => {
                    let err = result.error.expect("unavailable carries a message");
                    assert!(!err.message.is_empty());
                }
                CheckStatus::Passed | CheckStatus::Failed | CheckStatus::PermissionDenied => {}
                other => panic!("check {} returned {:?}", decl.id, other),
            }
        }
    }

    #[test]
    fn unknown_check_is_unsupported() {
        let result = run_check(&request("nvidia.nope"));
        assert!(matches!(
            result.status,
            CheckStatus::Unsupported | CheckStatus::Unavailable
        ));
    }

    #[test]
    fn manifest_is_bootstrap_only() {
        let manifest_path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("plugin.yaml");
        let manifest: doctor_domain::PluginManifest =
            serde_yaml::from_str(&std::fs::read_to_string(manifest_path).unwrap()).unwrap();
        assert!(manifest.checks.is_empty());
    }
}
