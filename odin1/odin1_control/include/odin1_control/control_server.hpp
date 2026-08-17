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
#include <condition_variable>
#include <memory>
#include <mutex>
#include <string>
#include <thread>

#include <rclcpp/rclcpp.hpp>
#include <rclcpp_action/rclcpp_action.hpp>

#include "odin1_control/device_context.hpp"
// Emergency exit path (hardExit / kUndrainableExitCode). Its own
// ROS-free translation unit so the exit path can be built and tested
// without a ROS installation.
#include "odin1_control/hard_exit.hpp"

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
/// Added by this layer: the process is tearing down, no new device work accepted.
constexpr int RC_SHUTTING_DOWN = -103;
/// Added by this layer: the device went away (or was recycled by a reconnect)
/// part-way through a multi-step operation.
constexpr int RC_DEVICE_LOST = -104;
/// Added by this layer: an exception escaped an operation. The device may be in
/// an indeterminate state; re-run switch_mode to resynchronise.
constexpr int RC_INTERNAL_ERROR = -105;


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

  // -------------------------------------------------------------------------
  // Teardown / device-lifetime barriers.
  //
  // These exist because the vendor driver tears the SDK down out from under us.
  // Its SIGINT handler runs, in this order (host_sdk_sample.cpp:352-418):
  //     lidar_stop_stream(odinDevice) -> odinDevice = nullptr
  //         -> lidar_system_deinit() -> g_ros_object.reset()
  //         -> rclcpp::shutdown() -> exit(0)
  // and its device-attach path recycles the handle with a bare
  //     if (odinDevice) { odinDevice = nullptr; ... }   (:1298-1300)
  // on every reconnect.
  //
  // A SaveMap worker can sit inside lidar_save_map() for up to 120 s, so
  // without a barrier both of those paths can free SDK state while an SDK call
  // is in flight. beginTeardown() must therefore be called BEFORE
  // lidar_system_deinit(), and waitForDeviceIdle() before the handle is
  // recycled. The driver patch wires both.
  // -------------------------------------------------------------------------

  /// Stops accepting new device work and waits (bounded) for in-flight
  /// operations to finish. Safe to call repeatedly.
  ///
  /// Returns TRUE only if everything drained. A FALSE return is not advisory:
  /// the caller MUST NOT go on to touch the SDK (no lidar_stop_stream, no
  /// lidar_system_deinit) or destroy anything, because a worker is still inside
  /// an SDK call. The only correct response is emergencyExit().
  [[nodiscard]] bool beginTeardown(std::chrono::milliseconds grace);

  /// Waits (bounded) for in-flight device operations to finish WITHOUT
  /// refusing future work. For the reconnect path, where the driver is about to
  /// swap the device handle but the server keeps serving afterwards.
  ///
  /// Returns TRUE only if everything drained. On FALSE the caller MUST NOT
  /// recycle the device handle; skipping the reconnect is the correct response.
  [[nodiscard]] bool waitForDeviceIdle(std::chrono::milliseconds grace);

  /// Invalidates the current device generation so that any in-flight multi-step
  /// operation fails at its next step boundary instead of continuing against a
  /// handle the driver is about to abandon. Cheap, non-blocking; call it before
  /// waitForDeviceIdle() on the reconnect path to shorten the drain.
  void notifyDeviceInvalidated();

  /// beginTeardown() + stop the control executor thread. Idempotent.
  /// Returns TRUE only if the drain succeeded; see beginTeardown().
  [[nodiscard]] bool shutdown(std::chrono::milliseconds grace = std::chrono::milliseconds{130000});

  /// Logs why, then hardExit(). Call this when beginTeardown() returned false.
  [[noreturn]] void emergencyExit(const char * why) noexcept;

private:
  /// RAII ticket for any operation that will touch the SDK.
  ///
  /// Construction fails (ok() == false) once beginTeardown() has run, so a
  /// request arriving during shutdown is refused instead of racing the
  /// teardown. While alive it keeps in_flight_ non-zero, which is what
  /// beginTeardown()/waitForDeviceIdle() block on.
  ///
  /// handle() re-reads the device from the DeviceContext on every call and
  /// compares it with the handle seen at construction, so a disconnect or a
  /// reconnect part-way through a multi-step sequence is caught at the next
  /// step boundary rather than being papered over with a stale pointer.
  class DeviceSession
  {
public:
    explicit DeviceSession(ControlServer & owner);
    ~DeviceSession();
    DeviceSession(const DeviceSession &) = delete;
    DeviceSession & operator=(const DeviceSession &) = delete;

    bool ok() const {return admitted_ && rc_ == 0;}
    int rc() const {return rc_;}
    /// Current handle, or nullptr with rc() updated if the device went away or
    /// was swapped since this session started.
    device_handle handle();

private:
    ControlServer & owner_;
    bool admitted_ = false;
    device_handle initial_ = nullptr;
    uint64_t generation_ = 0;
    int rc_ = 0;
  };
  friend class DeviceSession;

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

  /// Firmware versions are immutable for a given connection, so query them once
  /// and reuse. Without this, every GetDeviceState call would put a USB control
  /// transfer on the same channel a running SaveMap is using.
  void fillVersions(
    device_handle dev, std::shared_ptr<odin1_interfaces::srv::GetDeviceState::Response> res);

  void publishStatus();
  static const char * mapModeText(int mode);
  static const char * deviceStateText(int state);
  static const char * rcText(int rc);

  rclcpp::Node::SharedPtr node_;
  DeviceContext ctx_;
  ControlServerOptions opt_;

  rclcpp::CallbackGroup::SharedPtr cb_group_;
  rclcpp::executors::MultiThreadedExecutor::SharedPtr executor_;
  std::thread executor_thread_;
  std::atomic<bool> running_{false};

  /// Serialises multi-step device sequences against each other.
  std::timed_mutex device_op_mutex_;

  // --- teardown / in-flight accounting -------------------------------------
  std::atomic<bool> accepting_{true};
  std::atomic<int> in_flight_{0};
  std::mutex idle_mutex_;
  std::condition_variable idle_cv_;
  /// Bumped by notifyDeviceInvalidated(). A DeviceSession captures it at
  /// construction and fails once it changes, which makes an in-flight
  /// switch_mode abandon at the next step rather than at the next handle swap.
  std::atomic<uint64_t> device_generation_{0};
  /// How long the destructor waits before giving up and hard-exiting. Long by
  /// default: the destructor runs on the normal exit path, where blocking is
  /// correct and nobody is watching a signal.
  std::chrono::milliseconds destructor_grace_{180000};

  // --- cached, immutable-per-connection firmware versions -------------------
  std::mutex version_mutex_;
  bool version_cached_ = false;
  device_handle version_cached_for_ = nullptr;
  std::string v_kernel_, v_mcu_, v_soc_, v_daemon_, v_slam_;

  /// The single admission slot for exclusive device operations.
  ///
  /// Deliberately ONE compare-exchanged slot rather than two independent flags.
  /// With a flag each, this interleaving accepts both goals:
  ///     save_map goal   : reads mode_switch flag  -> false, proceeds
  ///     switch_mode goal: reads save_map flag     -> false, proceeds
  ///     save_map goal   : sets its own flag
  ///     switch_mode goal: sets its own flag
  /// device_op_mutex_ would still keep them from touching the SDK at the same
  /// time, but only by making the loser fail with RC_BUSY after being told its
  /// goal was accepted. A single slot rejects the loser up front instead.
  enum ExclusiveOp : int
  {
    OP_NONE = 0,
    OP_SAVE_MAP = 1,
    OP_SWITCH_MODE = 2,
  };
  std::atomic<int> exclusive_op_{OP_NONE};

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
