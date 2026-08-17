// Copyright 2026 Odin1 integration contributors
// Licensed under the Apache License, Version 2.0
//
// Bridge between the vendor driver's file-static globals and odin1_control.
//
// WHY THIS EXISTS
// ---------------
// Only one process may hold the Odin1 USB device (libusb returns
// LIBUSB_ERROR_BUSY otherwise, see the vendor README FAQ 5.12). The device
// handle therefore lives inside host_sdk_sample, and any control surface must
// run in that same process.
//
// host_sdk_sample.cpp keeps its state in file-static globals
// (odinDevice, deviceConnected, g_custom_map_mode, ...) that are not reachable
// from another translation unit. Rather than making them extern - which would
// grow the patch and couple us to their exact names forever - the driver patch
// fills in this struct with small lambdas. That keeps the required patch to a
// single #include plus one construction site, and lets odin1_control be
// reviewed and (mostly) tested on its own.
//
// Every std::function may be left empty. Accessors that are not provided
// degrade gracefully: the corresponding field is reported as unknown/invalid
// rather than crashing.

#ifndef ODIN1_CONTROL__DEVICE_CONTEXT_HPP_
#define ODIN1_CONTROL__DEVICE_CONTEXT_HPP_

#include <cstdint>
#include <functional>
#include <string>

// Vendor SDK public header. Provided by odin_ros_driver/include.
// CMake locates it via ODIN1_SDK_INCLUDE_DIR; the build fails loudly if absent,
// deliberately, so that prototypes can never silently drift out of sync.
#include "lidar_api.h"
#include "lidar_api_type.h"

namespace odin1_control
{

/// Which data streams the driver has enabled, so a mode switch can re-apply
/// lidar_activate_stream_type() exactly as the driver's connect path does
/// (host_sdk_sample.cpp:1891-1915).
struct StreamFlags
{
  bool rgb = true;
  bool imu = true;
  bool odom = true;
  bool dtof = true;
  bool cloud_slam = true;
};

struct DeviceContext
{
  // --- device handle and connectivity -------------------------------------
  /// Returns the open device handle, or nullptr when the driver has not
  /// completed lidar_open_device() yet. Required.
  std::function<device_handle()> get_device;

  /// Mirrors the driver's `deviceConnected` atomic. Required.
  std::function<bool()> is_connected;

  /// Operating mode the driver passes to lidar_set_mode()/lidar_start_stream().
  /// The driver hard-codes LIDAR_MODE_SLAM (host_sdk_sample.cpp:1271); a mode
  /// switch must restart the stream with the same value.
  std::function<int()> get_stream_mode;

  // --- algorithm mode ------------------------------------------------------
  /// Reads/writes the driver's `g_custom_map_mode` so that the driver and the
  /// control layer never disagree about which mode is live.
  std::function<int()> get_map_mode;
  std::function<void(int)> set_map_mode;

  // --- relocalization map --------------------------------------------------
  std::function<std::string()> get_reloc_map_path;              // g_relocalization_map_abs_path
  std::function<void(const std::string &)> set_reloc_map_path;

  // --- map save destination defaults ---------------------------------------
  /// g_mapping_result_dest_dir ("" when unset).
  std::function<std::string()> get_configured_map_dir;
  /// g_mapping_result_file_name ("" when unset).
  std::function<std::string()> get_configured_map_name;
  /// map_root_dir_ - the {ws}/src/odin_ros_driver/map/{start_time} fallback.
  std::function<std::string()> get_default_map_dir;

  // --- stream enable flags --------------------------------------------------
  std::function<StreamFlags()> get_stream_flags;

  // --- transfer interlock ---------------------------------------------------
  // Shared with the driver's own `g_map_transfer_in_progress`, so a save
  // triggered through the legacy /tmp/odin_command.txt channel and one
  // triggered through the SaveMap action cannot overlap.
  std::function<bool()> is_map_transfer_in_progress;
  std::function<void(bool)> set_map_transfer_in_progress;

  // --- optional: cached device status ---------------------------------------
  /// Fills `out` with the most recent LIDAR_DT_DEV_STATUS sample and
  /// `out_stamp_ns` with the host time it was cached. Returns false if no
  /// sample has arrived. Leaving this empty is fine - GetDeviceState then
  /// reports status.valid = false.
  std::function<bool(lidar_device_status_t & out, uint64_t & out_stamp_ns)> get_device_status;

  /// Driver version string, for GetDeviceState. Optional.
  std::string driver_version;

  /// True when every required accessor is populated.
  bool valid() const
  {
    return static_cast<bool>(get_device) && static_cast<bool>(is_connected);
  }
};

}  // namespace odin1_control

#endif  // ODIN1_CONTROL__DEVICE_CONTEXT_HPP_
