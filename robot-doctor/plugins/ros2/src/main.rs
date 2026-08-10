//! ROS 2 plugin binary: speaks the Robot Doctor plugin protocol.
//! Capabilities are advertised based on what the current system can
//! actually do (conservative discovery at negotiation time).

use doctor_domain::{CheckRequest, CheckResult, PluginCapability};
use doctor_plugin_host::{run_plugin_stdio, PluginService, ServiceCapabilities};
use plugin_ros2::{bootstrap::discover_runtimes, check_declarations, run_check, RosPlugin};

struct Ros2PluginService {
    plugin: RosPlugin,
}

impl PluginService for Ros2PluginService {
    fn plugin_id(&self) -> String {
        plugin_ros2::PLUGIN_ID.to_owned()
    }

    fn plugin_version(&self) -> String {
        plugin_ros2::PLUGIN_VERSION.to_owned()
    }

    fn max_concurrency(&self) -> u32 {
        // Provider operations serialize on the runtime mutex; allowing 2
        // lets ros.environment answer while a longer op runs elsewhere.
        2
    }

    fn capabilities(&self) -> ServiceCapabilities {
        // Only advertise what the current system plausibly supports:
        // the base capability always (checks report typed UNAVAILABLE),
        // provider capabilities only when a runtime was actually found.
        let mut capabilities = vec![PluginCapability("ros".to_owned())];
        if !discover_runtimes().is_empty() {
            capabilities.push(PluginCapability("ros.graph".to_owned()));
            capabilities.push(PluginCapability("ros.observation".to_owned()));
        }
        ServiceCapabilities {
            capabilities,
            checks: check_declarations(),
            actions: vec![],
        }
    }

    fn run_check(&self, request: &CheckRequest) -> CheckResult {
        run_check(&self.plugin, request)
    }
}

fn main() -> std::io::Result<()> {
    let plugin_dir = std::env::current_dir().unwrap_or_else(|_| ".".into());
    run_plugin_stdio(Ros2PluginService {
        plugin: RosPlugin::new(plugin_dir),
    })
}
