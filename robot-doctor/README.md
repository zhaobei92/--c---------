# Robot Doctor

Cross-platform (Windows/Linux) diagnostic, monitoring and fault-localization
platform for robots, AI workstations and edge devices.

Deterministic by design: no LLM in the diagnostic path, every conclusion
traces back to inspectable Evidence, and platform/robot specifics live in
process plugins and YAML profiles — never in the core.

## Layout

| Path | Contents |
|---|---|
| `crates/doctor-domain` | Stable domain model (Device, Check, Observation, Evidence, Finding, RootCause, Profile, Baseline…) |
| `crates/doctor-plugin-host` | Process-plugin system: JSONL-over-stdio protocol, `plugin.yaml` manifests, host runtime (timeouts, crash detection, restart), plugin-side SDK |
| `crates/doctor-core` | Diagnosis engine: plugin registry, concurrent check execution, progressive event streaming, run store |
| `plugins/system` | Cross-platform system plugin: identity/os/cpu/memory/swap/disk/process/uptime/time |
| `apps/desktop` | Tauri 2 + React + TypeScript desktop app |
| `profiles/examples` | Example device profiles (expected robot state, YAML) |
| `tests/integration` | Engine-level integration tests incl. deterministic stub plugin (crash/slow/unavailable) |

## Build & test

```bash
# Core workspace (no GUI libraries needed)
cargo test --workspace

# Headless smoke run of the real engine + system plugin
cargo build -p robot-doctor-plugin-system
cargo run -p doctor-core --example local_diagnosis plugins full

# Frontend
cd apps/desktop && npm install
npm run typecheck && npm test && npm run build

# Desktop app (Linux needs libwebkit2gtk-4.1-dev libgtk-3-dev)
cd apps/desktop && npm run tauri dev
```

Plugin binaries are discovered next to their `plugin.yaml`, next to the host
executable, via `$ROBOT_DOCTOR_PLUGIN_BIN_DIR`, or from the workspace
`target/` directory during development.

## Status

Phase A (vertical slice): domain model, plugin protocol, system plugin,
engine and desktop shell — local machine, QUICK/FULL diagnosis, progressive
results, evidence inspection. See CI (`robot-doctor-ci`) for the
Windows + Linux gate.
