// Copyright 2026 Odin1 integration contributors
// Licensed under the Apache License, Version 2.0
//
// Link-time mock of the Odin1 host SDK.
//
// HOW THIS WORKS
// --------------
// odin1_control is a STATIC library that calls lidar_*() but never links
// liblydHostApi. Those symbols stay undefined until something links the archive.
// The driver resolves them with the vendor .a; a test binary resolves them with
// this file instead. No seams, no #ifdef TESTING in production code, and the
// exact same object files are exercised that ship to the device.
//
// Everything is scripted through MockSdk::instance(). Tests set return codes and
// delays before the call and assert against the recorded call log afterwards -
// which is how the 7-step switch_mode sequence is verified in order rather than
// just by outcome.

#ifndef ODIN1_CONTROL__TEST__MOCK__ODIN_SDK_MOCK_HPP_
#define ODIN1_CONTROL__TEST__MOCK__ODIN_SDK_MOCK_HPP_

#include <atomic>
#include <chrono>
#include <functional>
#include <map>
#include <mutex>
#include <string>
#include <vector>

#include "lidar_api.h"
#include "lidar_api_type.h"

namespace odin1_control_test
{

/// One recorded SDK entry point invocation.
struct SdkCall
{
  std::string fn;      ///< e.g. "lidar_set_custom_parameter"
  std::string name;    ///< custom-parameter name or file path, when applicable
  long ivalue = 0;     ///< int payload / mode / stream type, when applicable
};

class MockSdk
{
public:
  static MockSdk & instance();

  /// Restores every field to its default. Call from SetUp().
  void reset();

  // --- scripted return codes ------------------------------------------------
  int rc_set_custom_parameter = 0;
  /// Per-parameter override, keyed by name ("map_mode", "save_map", ...).
  /// Takes precedence over rc_set_custom_parameter.
  std::map<std::string, int> rc_param_override;
  int rc_set_relocalization_map = 0;
  int rc_save_map = 0;
  int rc_set_mode = 0;
  int rc_start_stream = 0;
  int rc_stop_stream = 0;
  int rc_activate_stream = 0;
  int rc_get_version = 0;
  int rc_get_device_state = 0;

  lidar_device_initial_state_e device_state = LIDAR_DEVICE_STREAMING;

  /// How long lidar_save_map() blocks. Lets a test hold the device long enough
  /// to fire a second goal, or to call shutdown() mid-save.
  std::chrono::milliseconds save_map_delay{0};
  /// Same for the mode-switch primitives, to exercise cancel-between-steps.
  std::chrono::milliseconds set_mode_delay{0};

  /// Invoked (once) from inside lidar_save_map() after the delay starts, so a
  /// test can act while the device is held.
  std::function<void()> on_save_map_entered;
  /// Invoked from inside lidar_set_mode(), with the mode argument. Lets a test
  /// perturb state at an exact step of the switch_mode sequence instead of
  /// racing it with a sleep.
  std::function<void(int)> on_set_mode;

  /// Set by lidar_save_map() for the duration of the call.
  std::atomic<bool> save_map_running{false};
  /// Highest number of concurrent lidar_save_map() calls observed. Must stay 1.
  std::atomic<int> max_concurrent_save_map{0};

  // --- observation ----------------------------------------------------------
  std::vector<SdkCall> calls;
  int get_version_calls = 0;

  void record(const SdkCall & c);
  /// Names of the recorded calls, in order.
  std::vector<std::string> callNames() const;
  /// Recorded custom-parameter writes, in order, as (name, value).
  std::vector<std::pair<std::string, long>> paramWrites() const;
  int countCalls(const std::string & fn) const;
  bool sawCall(const std::string & fn) const {return countCalls(fn) > 0;}

  mutable std::mutex mutex;

  /// Live count of lidar_save_map() bodies executing. Public so the shim can
  /// bump it without a friend declaration against an extern "C" symbol.
  std::atomic<int> concurrent_save_map{0};

private:
  MockSdk() = default;
};

/// A non-null placeholder handle for tests. Any distinct value works; the
/// control layer only ever compares handles for identity.
extern device_handle kFakeDevice;
extern device_handle kOtherFakeDevice;

}  // namespace odin1_control_test

#endif  // ODIN1_CONTROL__TEST__MOCK__ODIN_SDK_MOCK_HPP_
