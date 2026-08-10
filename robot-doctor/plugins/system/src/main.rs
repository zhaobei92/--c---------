//! System plugin binary: speaks the Robot Doctor plugin protocol
//! (one JSON object per line over stdin/stdout).

use doctor_domain::{CheckRequest, CheckResult, PluginCapability};
use doctor_plugin_host::{run_plugin_stdio, PluginService, ServiceCapabilities};

struct SystemPlugin;

impl PluginService for SystemPlugin {
    fn plugin_id(&self) -> String {
        plugin_system::PLUGIN_ID.to_owned()
    }

    fn plugin_version(&self) -> String {
        plugin_system::PLUGIN_VERSION.to_owned()
    }

    fn max_concurrency(&self) -> u32 {
        // Each check builds its own sysinfo snapshot; they are independent.
        4
    }

    fn capabilities(&self) -> ServiceCapabilities {
        ServiceCapabilities {
            capabilities: vec![PluginCapability("system".to_owned())],
            checks: plugin_system::check_declarations(),
            actions: vec![],
            baseline_projection: true,
        }
    }

    fn run_check(&self, request: &CheckRequest) -> CheckResult {
        plugin_system::run_check(request)
    }
}

fn main() -> std::io::Result<()> {
    run_plugin_stdio(SystemPlugin)
}
