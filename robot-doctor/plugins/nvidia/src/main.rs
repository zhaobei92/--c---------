//! NVIDIA plugin binary: speaks the Robot Doctor plugin protocol.

use doctor_domain::{CheckRequest, CheckResult, PluginCapability};
use doctor_plugin_host::{run_plugin_stdio, PluginService, ServiceCapabilities};

struct NvidiaPlugin;

impl PluginService for NvidiaPlugin {
    fn plugin_id(&self) -> String {
        plugin_nvidia::PLUGIN_ID.to_owned()
    }

    fn plugin_version(&self) -> String {
        plugin_nvidia::PLUGIN_VERSION.to_owned()
    }

    fn max_concurrency(&self) -> u32 {
        // NVML is thread-safe; queries are short.
        2
    }

    fn capabilities(&self) -> ServiceCapabilities {
        ServiceCapabilities {
            capabilities: vec![PluginCapability("gpu.nvidia".to_owned())],
            checks: plugin_nvidia::check_declarations(),
            actions: vec![],
            baseline_projection: true,
        }
    }

    fn run_check(&self, request: &CheckRequest) -> CheckResult {
        plugin_nvidia::run_check(request)
    }
}

fn main() -> std::io::Result<()> {
    run_plugin_stdio(NvidiaPlugin)
}
