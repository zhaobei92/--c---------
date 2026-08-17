// Copyright 2026 Odin1 integration contributors
// Licensed under the Apache License, Version 2.0
//
// Standard ROS 2 control surface for the Odin1 module.
//
// Replaces the driver's ad-hoc control channel - a line written to
// /tmp/odin_command.txt and polled at 10 Hz by the main loop
// (host_sdk_sample.cpp:439-605, called from :2508 / :2542) - which only
// carries `set <name> <int>`, returns nothing, and cannot be sequenced from a
// launch file or a behaviour tree.
//
// Surface
//   /odin1/save_map        action  odin1_interfaces/action/SaveMap
//   /odin1/switch_mode     action  odin1_interfaces/action/SwitchMode
//   /odin1/load_map        service odin1_interfaces/srv/LoadMap
//   /odin1/set_init_pose   service odin1_interfaces/srv/SetInitPose
//   /odin1/reset_algo      service odin1_interfaces/srv/ResetAlgo
//   /odin1/get_device_state service odin1_interfaces/srv/GetDeviceState
//
// THREADING - the single most important design constraint here
// ------------------------------------------------------------
// The driver's ROS 2 main loop is `rclcpp::spin_some(node)` inside a 10 Hz
// rclcpp::Rate loop (host_sdk_sample.cpp:2485-2515). It is single threaded and
// it also drives the point-cloud render pairing and the command-file poll. A
// service callback placed in the node's default callback group would therefore
//   (a) be served at most every 100 ms, and
//   (b) block every publisher for its whole duration - and lidar_save_map()
//       alone blocks for up to 120 s.
//
// So this class owns its own executor:
//   * a Reentrant callback group created with
//     automatically_add_to_executor_with_node = false, so the driver's
//     spin_some() never picks it up;
//   * a MultiThreadedExecutor spun on a dedicated thread.
// The driver's data path and this control path never contend.
//
// Device operations are serialised among themselves by `device_op_mutex_`,
// because the vendor SDK's own control mutex protects individual calls but not
// multi-step sequences such as the mode switch.

#ifndef ODIN1_CONTROL__CONTROL_SERVER_HPP_
#define ODIN1_CONTROL__CONTROL_SERVER_HPP_

#include <atomic>
#include <chrono>
#include <memory>
#include <mutex>
#include <string>
#include <thread>

#include <rclcpp/rclcpp.hpp>
#include <rclcpp_action/rclcpp_action.hpp>

#include "odin1_control/device_context.hpp"

#include "odin1_interfaces/action/save_map.hpp"
#include "odin1_interfaces/action/switch_mode.hpp"
#include "odin1_interfaces/msg/device_status.hpp"
#include "odin1_interfaces/srv/get_device_state.hpp"
#include "odin1_interfaces/srv/load_map.hpp"
#include "odin1_interfaces/srv/reset_algo.hpp"
#include "odin1_interfaces/srv/set_init_pose.hpp"

namespace odin1_control
{

/// Returned by every device call wrapper. `rc` follows the vendor convention:
/// 0 = success, negative = SDK-side error, positive = device-side error.
/// -100 is added by this layer to mean "driver has not opened the device yet".
constexpr int RC_DEVICE_NOT_OPEN = -100;
/// Added by this layer: another control operation holds the device.
constexpr int RC_BUSY = -101;
/// Added by this layer: request rejected before touching the device.
constexpr int RC_INVALID_REQUEST = -102;

struct ControlServerOptions
{
  /// Service/action namespace. Topics become <ns>/save_map, <ns>/load_map, ...
  std::string ns = "/odin1";
  /// Threads in the control executor. 2 is enough (one long op + one query);
  /// 3 leaves headroom for a concurrent GetDeviceState during a mode switch.
  int executor_threads = 3;
  /// How long a service waits for the device lock before returning RC_BUSY.
  std::chrono::milliseconds lock_timeout{2000};
  /// SaveMap feedback tick period.
  std::chrono::milliseconds feedback_period{500};
  /// Publish /odin1/device_status at this period. 0 disables.
  std::chrono::milliseconds status_period{1000};
};

class ControlServer
{
public:
  using SaveMap = odin1_interfaces::action::SaveMap;
  using SwitchMode = odin1_interfaces::action::SwitchMode;
  using GoalHandleSaveMap = rclcpp_action::ServerGoalHandle<SaveMap>;
  using GoalHandleSwitchMode = rclcpp_action::ServerGoalHandle<SwitchMode>;

  /// Constructs and immediately starts serving. `node` must outlive this object.
  ControlServer(
    rclcpp::Node::SharedPtr node,
    DeviceContext ctx,
    ControlServerOptions options = ControlServerOptions{});

  ~ControlServer();

  ControlServer(const ControlServer &) = delete;
  ControlServer & operator=(const ControlServer &) = delete;

  /// Stops the control executor thread. Idempotent; also called by the dtor.
  void shutdown();

private:
  // --- service handlers ----------------------------------------------------
  void handleLoadMap(
    const std::shared_ptr<odin1_interfaces::srv::LoadMap::Request> req,
    std::shared_ptr<odin1_interfaces::srv::LoadMap::Response> res);

  void handleSetInitPose(
    const std::shared_ptr<odin1_interfaces::srv::SetInitPose::Request> req,
    std::shared_ptr<odin1_interfaces::srv::SetInitPose::Response> res);

  void handleResetAlgo(
    const std::shared_ptr<odin1_interfaces::srv::ResetAlgo::Request> req,
    std::shared_ptr<odin1_interfaces::srv::ResetAlgo::Response> res);

  void handleGetDeviceState(
    const std::shared_ptr<odin1_interfaces::srv::GetDeviceState::Request> req,
    std::shared_ptr<odin1_interfaces::srv::GetDeviceState::Response> res);

  // --- SaveMap action ------------------------------------------------------
  rclcpp_action::GoalResponse saveMapGoal(
    const rclcpp_action::GoalUUID & uuid, std::shared_ptr<const SaveMap::Goal> goal);
  rclcpp_action::CancelResponse saveMapCancel(std::shared_ptr<GoalHandleSaveMap> gh);
  void saveMapAccepted(std::shared_ptr<GoalHandleSaveMap> gh);
  void saveMapExecute(std::shared_ptr<GoalHandleSaveMap> gh);

  // --- SwitchMode action ---------------------------------------------------
  rclcpp_action::GoalResponse switchModeGoal(
    const rclcpp_action::GoalUUID & uuid, std::shared_ptr<const SwitchMode::Goal> goal);
  rclcpp_action::CancelResponse switchModeCancel(std::shared_ptr<GoalHandleSwitchMode> gh);
  void switchModeAccepted(std::shared_ptr<GoalHandleSwitchMode> gh);
  void switchModeExecute(std::shared_ptr<GoalHandleSwitchMode> gh);

  // --- helpers -------------------------------------------------------------
  /// nullptr + rc when the device is not usable.
  device_handle device(int & rc) const;
  /// Writes a single int custom parameter.
  int setIntParam(device_handle dev, const char * name, int value);
  /// Writes a float array custom parameter (init_pos etc.).
  int setFloatParam(device_handle dev, const char * name, const float * values, size_t count);
  /// Applies init_pos / search radius / max rotation. Returns first failing rc.
  int applyInitPose(
    device_handle dev, const double pos[3], const double quat_xyzw[4],
    float search_radius_m, float max_rot_deg, std::string & message);

  void publishStatus();
  static const char * mapModeText(int mode);
  static const char * deviceStateText(int state);

  rclcpp::Node::SharedPtr node_;
  DeviceContext ctx_;
  ControlServerOptions opt_;

  rclcpp::CallbackGroup::SharedPtr cb_group_;
  rclcpp::executors::MultiThreadedExecutor::SharedPtr executor_;
  std::thread executor_thread_;
  std::atomic<bool> running_{false};

  /// Serialises multi-step device sequences against each other.
  std::timed_mutex device_op_mutex_;
  std::atomic<bool> mode_switch_in_progress_{false};
  /// Guards against two SaveMap goals running at once, independently of the
  /// driver's own flag (which the legacy /tmp channel also sets).
  std::atomic<bool> save_map_running_{false};

  rclcpp::Service<odin1_interfaces::srv::LoadMap>::SharedPtr srv_load_map_;
  rclcpp::Service<odin1_interfaces::srv::SetInitPose>::SharedPtr srv_set_init_pose_;
  rclcpp::Service<odin1_interfaces::srv::ResetAlgo>::SharedPtr srv_reset_algo_;
  rclcpp::Service<odin1_interfaces::srv::GetDeviceState>::SharedPtr srv_get_device_state_;
  rclcpp_action::Server<SaveMap>::SharedPtr act_save_map_;
  rclcpp_action::Server<SwitchMode>::SharedPtr act_switch_mode_;

  rclcpp::Publisher<odin1_interfaces::msg::DeviceStatus>::SharedPtr pub_status_;
  rclcpp::TimerBase::SharedPtr status_timer_;
};

}  // namespace odin1_control

#endif  // ODIN1_CONTROL__CONTROL_SERVER_HPP_
