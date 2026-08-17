// Copyright 2026 Odin1 integration contributors
// Licensed under the Apache License, Version 2.0
//
// REP-105 adapter for the Odin1 driver's TF output.
//
// THE PROBLEM
// -----------
// The vendor driver publishes a tree that is not REP-105 compliant and cannot be
// consumed by Nav2 as-is:
//
//   * odom -> map is published with odom as the PARENT and map as the CHILD
//     (odin_ros_driver/include/host_sdk_sample.h:1538-1539 for ROS 2,
//     :1688-1689 for ROS 1). REP-105 requires map -> odom -> base_link. The
//     vendor README confirms the intent: "The following topics are published in
//     the odom frame ... To obtain these in the map frame, apply the TF from
//     odom frame to map frame."
//   * There is no base_link at all: odometry is published straight onto the
//     `imu` frame.
//   * Frame names carry no prefix (`imu`, `lidar`, `odom`, `map`, `camera_0`),
//     which collides immediately in any multi-sensor robot.
//   * The odom -> map branch is NOT gated by send_odom_baselink_tf, so it cannot
//     be silenced from control_command.yaml.
//
// THE APPROACH
// ------------
// Because odom -> map cannot be turned off, the driver's /tf must be remapped
// away and this node must own /tf exclusively. Launch the driver with
//     --ros-args -r /tf:=/odin1/tf_raw -r /tf_static:=/odin1/tf_static_raw
// (odin1_tf_adapter.launch.py does this for you). Publishing the corrected tree
// while the driver still writes to /tf would give `map` two parents and produce
// a TF loop.
//
// Sources are chosen for quality, not convenience:
//   * odom -> base_link comes from /odin1/odometry_highfreq (~400 Hz) rather
//     than the driver's ~10 Hz TF, which the vendor FAQ Q4.4 calls out as the
//     fix for "TF does not track the vehicle in fast motion".
//   * imu -> lidar and lidar -> camera come from /odin1/wiwc, which carries the
//     raw 4x4 extrinsics (T_IL in twist.covariance[0..15], T_CL in
//     pose.covariance[0..15]) instead of re-deriving them from TF.
//   * map -> odom is the one thing only available on TF, so the raw stream is
//     read purely to extract and invert it.
//
// OUTPUT TREE
//   map -> odom -> base_link -> <p>imu -> <p>lidar -> <p>camera
// with base_link -> <p>imu static (the robot mounting transform).

#ifndef ODIN1_TF_ADAPTER__TF_ADAPTER_NODE_HPP_
#define ODIN1_TF_ADAPTER__TF_ADAPTER_NODE_HPP_

#include <array>
#include <atomic>
#include <chrono>
#include <memory>
#include <mutex>
#include <string>

#include <rclcpp/rclcpp.hpp>
#include <geometry_msgs/msg/transform_stamped.hpp>
#include <nav_msgs/msg/odometry.hpp>
#include <tf2_msgs/msg/tf_message.hpp>
#include <tf2_ros/static_transform_broadcaster.h>
#include <tf2_ros/transform_broadcaster.h>
#include <tf2/LinearMath/Transform.h>

#include "odin1_interfaces/msg/localization_status.hpp"
#include "odin1_interfaces/srv/get_device_state.hpp"

#include "odin1_tf_adapter/map_odom_policy.hpp"

namespace odin1_tf_adapter
{

class TfAdapterNode : public rclcpp::Node
{
public:
  explicit TfAdapterNode(const rclcpp::NodeOptions & options = rclcpp::NodeOptions());

private:
  void onOdom(const nav_msgs::msg::Odometry::SharedPtr msg);
  void onWiwc(const nav_msgs::msg::Odometry::SharedPtr msg);
  void onRawTf(const tf2_msgs::msg::TFMessage::SharedPtr msg);
  void onStatusTimer();
  void publishStaticBaseToImu();
  void queryDeviceState();

  /// Reads a row-major 4x4 matrix out of the first 16 covariance entries.
  static tf2::Transform fromCovariance(const std::array<double, 36> & cov);
  static geometry_msgs::msg::TransformStamped toMsg(
    const tf2::Transform & tf, const rclcpp::Time & stamp,
    const std::string & parent, const std::string & child);

  /// Host-side pacing clock.
  ///
  /// Deliberately NOT the message stamps: with the driver's default
  /// use_host_ros_time=0 those carry the device's boot time, so they are not
  /// comparable with the host clock and their epoch resets when the module is
  /// power-cycled. Message stamps are still used verbatim when STAMPING an
  /// outgoing transform - only the "has enough time passed" decisions and the
  /// staleness timeout run on this clock.
  using Clock = std::chrono::steady_clock;
  /// True when enough time has passed since `last` for the configured rate.
  /// rate_hz <= 0 means "no limit". A default-constructed `last` means "never".
  static bool rateAllows(Clock::time_point now, Clock::time_point & last, double rate_hz);

  // --- parameters ---------------------------------------------------------
  std::string prefix_;
  std::string map_frame_, odom_frame_, base_frame_;
  std::string imu_frame_, lidar_frame_, camera_frame_;
  std::string driver_map_frame_, driver_odom_frame_;
  bool publish_base_link_{true};
  bool publish_extrinsics_{true};
  bool static_extrinsics_{false};
  double map_odom_timeout_{1.0};
  double map_odom_rate_{50.0};
  double extrinsics_rate_{10.0};
  double odom_tf_rate_{0.0};          // 0 = every message
  MapOdomFallback fallback_{MapOdomFallback::Auto};
  bool query_device_state_{true};

  tf2::Transform base_to_imu_{tf2::Transform::getIdentity()};

  // --- state --------------------------------------------------------------
  std::mutex mutex_;
  tf2::Transform map_to_odom_{tf2::Transform::getIdentity()};
  bool map_to_odom_seen_{false};
  Clock::time_point map_to_odom_rx_{};          // default = never received
  Clock::time_point last_map_odom_pub_{};
  Clock::time_point last_extrinsics_pub_{};
  Clock::time_point last_odom_tf_pub_{};
  bool static_extrinsics_sent_{false};
  bool identity_fallback_active_{false};
  const char * map_odom_reason_ = "starting up";
  int device_map_mode_{-1};           // -1 = unknown; filled by GetDeviceState
  /// A switch_mode restarts the stream and resets odom to the origin, which
  /// invalidates any stored map -> odom. Tracked so the correction is dropped
  /// exactly once per switch rather than being applied to a fresh odom epoch.
  bool mode_switch_in_progress_{false};
  bool saw_mode_switch_{false};
  std::atomic<bool> device_state_query_pending_{false};
  uint64_t status_tick_{0};

  /// Drops any stored map -> odom. Called when the device changes mode or
  /// finishes a switch_mode, because both reset the odom frame.
  void invalidateMapOdom(const char * why);

  // --- ROS ----------------------------------------------------------------
  std::unique_ptr<tf2_ros::TransformBroadcaster> tf_broadcaster_;
  std::unique_ptr<tf2_ros::StaticTransformBroadcaster> tf_static_broadcaster_;
  rclcpp::Subscription<nav_msgs::msg::Odometry>::SharedPtr sub_odom_;
  rclcpp::Subscription<nav_msgs::msg::Odometry>::SharedPtr sub_wiwc_;
  rclcpp::Subscription<tf2_msgs::msg::TFMessage>::SharedPtr sub_raw_tf_;
  rclcpp::Publisher<odin1_interfaces::msg::LocalizationStatus>::SharedPtr pub_status_;
  rclcpp::Client<odin1_interfaces::srv::GetDeviceState>::SharedPtr cli_device_state_;
  rclcpp::TimerBase::SharedPtr status_timer_;
  /// The status timer and the GetDeviceState client live in their own group so
  /// the 400 Hz odometry subscription (default group, mutually exclusive) cannot
  /// starve them. That matters because the device-state response is what feeds
  /// the map->odom safety policy.
  rclcpp::CallbackGroup::SharedPtr cb_group_aux_;
};

}  // namespace odin1_tf_adapter

#endif  // ODIN1_TF_ADAPTER__TF_ADAPTER_NODE_HPP_
