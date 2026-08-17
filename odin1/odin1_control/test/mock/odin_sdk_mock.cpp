// Copyright 2026 Odin1 integration contributors
// Licensed under the Apache License, Version 2.0
//
// Definitions of every lidar_*() symbol odin1_control references.
//
// Including lidar_api.h first means the compiler checks these against the real
// vendor prototypes: if the SDK signature ever changes, this file fails to build
// rather than silently mocking the wrong contract.

#include "odin_sdk_mock.hpp"

#include <algorithm>
#include <cstring>
#include <thread>

namespace odin1_control_test
{

// Deliberately not real pointers - never dereferenced, only compared.
device_handle kFakeDevice = reinterpret_cast<device_handle>(0xD1);
device_handle kOtherFakeDevice = reinterpret_cast<device_handle>(0xD2);

MockSdk & MockSdk::instance()
{
  static MockSdk inst;
  return inst;
}

void MockSdk::reset()
{
  std::lock_guard<std::mutex> lock(mutex);
  rc_set_custom_parameter = 0;
  rc_param_override.clear();
  rc_set_relocalization_map = 0;
  rc_save_map = 0;
  rc_set_mode = 0;
  rc_start_stream = 0;
  rc_stop_stream = 0;
  rc_activate_stream = 0;
  rc_get_version = 0;
  rc_get_device_state = 0;
  device_state = LIDAR_DEVICE_STREAMING;
  save_map_delay = std::chrono::milliseconds{0};
  set_mode_delay = std::chrono::milliseconds{0};
  on_save_map_entered = nullptr;
  save_map_running = false;
  max_concurrent_save_map = 0;
  concurrent_save_map = 0;
  calls.clear();
  get_version_calls = 0;
}

void MockSdk::record(const SdkCall & c)
{
  std::lock_guard<std::mutex> lock(mutex);
  calls.push_back(c);
}

std::vector<std::string> MockSdk::callNames() const
{
  std::lock_guard<std::mutex> lock(mutex);
  std::vector<std::string> out;
  out.reserve(calls.size());
  for (const auto & c : calls) {
    out.push_back(c.fn);
  }
  return out;
}

std::vector<std::pair<std::string, long>> MockSdk::paramWrites() const
{
  std::lock_guard<std::mutex> lock(mutex);
  std::vector<std::pair<std::string, long>> out;
  for (const auto & c : calls) {
    if (c.fn == "lidar_set_custom_parameter") {
      out.emplace_back(c.name, c.ivalue);
    }
  }
  return out;
}

int MockSdk::countCalls(const std::string & fn) const
{
  std::lock_guard<std::mutex> lock(mutex);
  return static_cast<int>(std::count_if(
             calls.begin(), calls.end(), [&](const SdkCall & c) {return c.fn == fn;}));
}

}  // namespace odin1_control_test

using odin1_control_test::MockSdk;
using odin1_control_test::SdkCall;

// ---------------------------------------------------------------------------
// SDK surface
// ---------------------------------------------------------------------------

int lidar_set_custom_parameter(
  device_handle device, const char * param_name, const void * value_data, size_t value_length)
{
  auto & m = MockSdk::instance();
  const std::string name = param_name ? param_name : "";

  // Record the int payload for scalar parameters and the float count for
  // init_pos, so tests can assert both the name and the value that went out.
  long value = 0;
  if (value_data && value_length == sizeof(int)) {
    int v = 0;
    std::memcpy(&v, value_data, sizeof(int));
    value = v;
  } else {
    value = static_cast<long>(value_length);
  }
  m.record(SdkCall{"lidar_set_custom_parameter", name, value});

  if (!device) {
    return -1;
  }
  const auto it = m.rc_param_override.find(name);
  if (it != m.rc_param_override.end()) {
    return it->second;
  }
  return m.rc_set_custom_parameter;
}

int lidar_get_custom_parameter(device_handle device, const char * param_name, int * value)
{
  auto & m = MockSdk::instance();
  m.record(SdkCall{"lidar_get_custom_parameter", param_name ? param_name : "", 0});
  if (!device || !value) {
    return -1;
  }
  *value = 0;
  return 0;
}

int lidar_set_relocalization_map(device_handle device, const char * abs_path)
{
  auto & m = MockSdk::instance();
  m.record(SdkCall{"lidar_set_relocalization_map", abs_path ? abs_path : "", 0});
  if (!device) {
    return -1;
  }
  return m.rc_set_relocalization_map;
}

int lidar_get_mapping_result(device_handle device, const char * dest_dir, const char * file_name)
{
  auto & m = MockSdk::instance();
  m.record(SdkCall{"lidar_get_mapping_result", std::string(dest_dir ? dest_dir : "") + "/" +
      (file_name ? file_name : ""), 0});
  return device ? 0 : -1;
}

int lidar_save_map(
  device_handle device, const char * dest_dir, const char * file_name, uint32_t gen_timeout_ms)
{
  auto & m = MockSdk::instance();
  m.record(SdkCall{"lidar_save_map", std::string(dest_dir ? dest_dir : "") + "/" +
      (file_name ? file_name : ""), static_cast<long>(gen_timeout_ms)});

  if (!device) {
    return -1;
  }

  const int live = m.concurrent_save_map.fetch_add(1) + 1;
  int prev = m.max_concurrent_save_map.load();
  while (live > prev && !m.max_concurrent_save_map.compare_exchange_weak(prev, live)) {
  }
  m.save_map_running = true;

  if (m.on_save_map_entered) {
    // Copied out so a test hook cannot be swapped mid-call.
    auto hook = m.on_save_map_entered;
    hook();
  }
  if (m.save_map_delay.count() > 0) {
    std::this_thread::sleep_for(m.save_map_delay);
  }

  m.save_map_running = false;
  m.concurrent_save_map.fetch_sub(1);
  return m.rc_save_map;
}

int lidar_set_mode(device_handle device, int mode)
{
  auto & m = MockSdk::instance();
  m.record(SdkCall{"lidar_set_mode", "", mode});
  if (m.set_mode_delay.count() > 0) {
    std::this_thread::sleep_for(m.set_mode_delay);
  }
  return device ? m.rc_set_mode : -1;
}

int lidar_start_stream(device_handle device, int type, uint32_t & dtof_subframe_odr)
{
  auto & m = MockSdk::instance();
  m.record(SdkCall{"lidar_start_stream", "", type});
  dtof_subframe_odr = 100;
  return device ? m.rc_start_stream : -1;
}

int lidar_stop_stream(device_handle device, int type)
{
  auto & m = MockSdk::instance();
  m.record(SdkCall{"lidar_stop_stream", "", type});
  return device ? m.rc_stop_stream : -1;
}

int lidar_activate_stream_type(device_handle device, int type)
{
  auto & m = MockSdk::instance();
  m.record(SdkCall{"lidar_activate_stream_type", "", type});
  return device ? m.rc_activate_stream : -1;
}

int lidar_deactivate_stream_type(device_handle device, int type)
{
  auto & m = MockSdk::instance();
  m.record(SdkCall{"lidar_deactivate_stream_type", "", type});
  return device ? m.rc_activate_stream : -1;
}

int lidar_get_device_state(lidar_device_initial_state_e * state)
{
  auto & m = MockSdk::instance();
  m.record(SdkCall{"lidar_get_device_state", "", 0});
  if (!state) {
    return -1;
  }
  *state = m.device_state;
  return m.rc_get_device_state;
}

int lidar_get_version(device_handle device, lidar_fireware_version_t * version)
{
  auto & m = MockSdk::instance();
  {
    std::lock_guard<std::mutex> lock(m.mutex);
    ++m.get_version_calls;
  }
  m.record(SdkCall{"lidar_get_version", "", 0});
  if (!device || !version) {
    return -1;
  }
  *version = lidar_fireware_version_t{};
  version->soc_version = lidar_version_t{0, 13, 0};
  version->slam_version = lidar_version_t{1, 2, 3};
  version->kernel_version = lidar_version_t{4, 5, 6};
  version->mcu_version = lidar_version_t{7, 8, 9};
  version->Daemon_proc_version = lidar_version_t{1, 0, 0};
  return m.rc_get_version;
}
