//! Network plugin binary: speaks the Robot Doctor plugin protocol.

use doctor_domain::{CheckRequest, CheckResult, PluginCapability};
use doctor_plugin_host::{run_plugin_stdio, PluginService, ServiceCapabilities};

struct NetworkPlugin;

impl PluginService for NetworkPlugin {
    fn plugin_id(&self) -> String {
        plugin_network::PLUGIN_ID.to_owned()
    }

    fn plugin_version(&self) -> String {
        plugin_network::PLUGIN_VERSION.to_owned()
    }

    fn max_concurrency(&self) -> u32 {
        // Checks are independent socket/enumeration operations.
        4
    }

    fn capabilities(&self) -> ServiceCapabilities {
        ServiceCapabilities {
            capabilities: vec![PluginCapability("network".to_owned())],
            checks: plugin_network::check_declarations(),
            actions: vec![],
        }
    }

    fn run_check(&self, request: &CheckRequest) -> CheckResult {
        plugin_network::run_check(request)
    }
}

fn main() -> std::io::Result<()> {
    run_plugin_stdio(NetworkPlugin)
}
