// Copyright 2026 Odin1 integration contributors
// Licensed under the Apache License, Version 2.0
//
// The emergency exit path, deliberately free of ROS and of the vendor SDK.
//
// Kept in its own translation unit so that the most safety-critical code in this
// package - the part that runs when a worker is stuck inside an uninterruptible
// SDK call - can be compiled and exercised without a ROS installation.

#ifndef ODIN1_CONTROL__HARD_EXIT_HPP_
#define ODIN1_CONTROL__HARD_EXIT_HPP_

namespace odin1_control
{

/// Process exit status used when the control layer refuses to let teardown
/// proceed. Distinct so an operator or a supervisor can tell it apart from a
/// clean stop and from a crash.
constexpr int kUndrainableExitCode = 75;

/// Terminate the process NOW, without touching the SDK and without running any
/// destructor.
///
/// This is the safe fast path when an uninterruptible SDK call
/// (lidar_save_map() blocks up to its gen_timeout, 120 s by default) is still
/// running and something wants to tear the world down. Three things are racing
/// at that moment:
///
///   1. the detached worker thread, inside the SDK;
///   2. the driver's SIGINT handler, about to call lidar_system_deinit();
///   3. static destructors, which exit() would then run under both of the above.
///
/// _exit() removes legs 2 and 3 by construction: no atexit handlers, no static
/// destruction, no SDK calls. Leg 1 is simply frozen mid-call, which is safe
/// precisely because nothing it points at is being freed. The kernel reclaims
/// the USB file descriptors, and the device re-enumerates on the next connect.
/// Leaving the module streaming for one power cycle is a far better outcome
/// than deinitialising the SDK underneath a live transfer.
///
/// Async-signal-safe: writes with write(2) only, never the ROS logger (a worker
/// may well be holding a logging mutex, which would deadlock the handler).
[[noreturn]] void hardExit(const char * why) noexcept;

}  // namespace odin1_control

#endif  // ODIN1_CONTROL__HARD_EXIT_HPP_
