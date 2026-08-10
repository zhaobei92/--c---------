//! Shared helpers for integration tests (see `tests/`).
//! The `stub-plugin` binary in this package simulates well-behaved,
//! unavailable, slow and crashing plugins deterministically.

/// Check ids implemented by the stub plugin in `good` mode.
pub const STUB_GOOD_CHECKS: [&str; 3] = ["stub.ok", "stub.unavailable", "stub.slow"];

/// Check id implemented by the stub plugin in `crashy` mode.
pub const STUB_CRASH_CHECK: &str = "stub.crash";
