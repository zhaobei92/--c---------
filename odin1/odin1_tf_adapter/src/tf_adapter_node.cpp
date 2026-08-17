// Copyright 2026 Odin1 integration contributors
// Licensed under the Apache License, Version 2.0

#include "odin1_tf_adapter/tf_adapter_node.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <vector>

#include <tf2/LinearMath/Matrix3x3.h>
#include <tf2/LinearMath/Quaternion.h>
#include <tf2/LinearMath/Vector3.h>

namespace odin1_tf_adapter
{

using namespace std::chrono_literals;

TfAdapterNode::TfAdapterNode(const rclcpp::NodeOptions & options)
: rclcpp::Node("odin1_tf_adapter", options)
{
  // --- frame naming --------------------------------------------------------
  prefix_ = declare_parameter<std::string>("frame_prefix", "");
  map_frame_ = prefix_ + declare_parameter<std::string>("map_frame", "map");
  odom_frame_ = prefix_ + declare_parameter<std::string>("odom_frame", "odom");
  base_frame_ = prefix_ + declare_parameter<std::string>("base_frame", "base_link");
  imu_frame_ = prefix_ + declare_parameter<std::string>("imu_frame", "imu");
  lidar_frame_ = prefix_ + declare_parameter<std::string>("lidar_frame", "lidar");
  camera_frame_ = prefix_ + declare_parameter<std::string>("camera_frame", "camera_0");

  // Frame names as they appear in the DRIVER's raw TF stream. These are
  // hard-coded in host_sdk_sample.h and are not affected by frame_prefix.
  driver_map_frame_ = declare_parameter<std::string>("driver_map_frame", "map");
  driver_odom_frame_ = declare_parameter<std::string>("driver_odom_frame", "odom");

  publish_base_link_ = declare_parameter<bool>("publish_base_link", true);
  publish_extrinsics_ = declare_parameter<bool>("publish_extrinsics", true);
  static_extrinsics_ = declare_parameter<bool>("static_extrinsics", false);
  map_odom_timeout_ = declare_parameter<double>("map_odom_timeout_sec", 1.0);
  map_odom_rate_ = declare_parameter<double>("map_odom_rate_hz", 50.0);
  extrinsics_rate_ = declare_parameter<double>("extrinsics_rate_hz", 10.0);
  odom_tf_rate_ = declare_parameter<double>("odom_tf_rate_hz", 0.0);
  query_device_state_ = declare_parameter<bool>("query_device_state", true);

  const auto fb = declare_parameter<std::string>("map_odom_fallback", "identity");
  if (fb == "identity") {
    fallback_ = MapOdomFallback::Identity;
  } else if (fb == "hold") {
    fallback_ = MapOdomFallback::Hold;
  } else if (fb == "none") {
    fallback_ = MapOdomFallback::None;
  } else {
    RCLCPP_WARN(
      get_logger(), "unknown map_odom_fallback '%s', using 'identity'", fb.c_str());
  }

  // base_link -> imu, i.e. where the module sits on the robot.
  // Identity by default, which makes base_link coincide with the Odin1 IMU -
  // correct only for a bench setup, so this is the first thing to set on a real
  // platform.
  const auto b2i = declare_parameter<std::vector<double>>(
    "base_to_imu", std::vector<double>{0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0});
  if (b2i.size() != 7) {
    RCLCPP_ERROR(
      get_logger(),
      "base_to_imu must have 7 entries [x y z qx qy qz qw], got %zu; using identity",
      b2i.size());
  } else {
    tf2::Quaternion q(b2i[3], b2i[4], b2i[5], b2i[6]);
    const double n = q.length();
    if (!std::isfinite(n) || n < 1e-9) {
      RCLCPP_ERROR(get_logger(), "base_to_imu quaternion is degenerate; using identity");
    } else {
      q.normalize();
      base_to_imu_.setRotation(q);
      base_to_imu_.setOrigin(tf2::Vector3(b2i[0], b2i[1], b2i[2]));
    }
  }

  // --- topics --------------------------------------------------------------
  const auto odom_topic =
    declare_parameter<std::string>("odom_topic", "/odin1/odometry_highfreq");
  const auto wiwc_topic = declare_parameter<std::string>("wiwc_topic", "/odin1/wiwc");
  const auto raw_tf_topic = declare_parameter<std::string>("raw_tf_topic", "/odin1/tf_raw");

  tf_broadcaster_ = std::make_unique<tf2_ros::TransformBroadcaster>(*this);
  tf_static_broadcaster_ = std::make_unique<tf2_ros::StaticTransformBroadcaster>(*this);

  // The driver publishes odometry with RELIABLE QoS and a deep queue for the
  // 400 Hz topic (host_sdk_sample.h:1905-1919); match RELIABLE so the
  // subscription actually connects, and keep the depth large enough that a
  // scheduling hiccup here does not silently drop samples the way the vendor
  // FAQ 5.13 describes for ros2 bag.
  const auto odom_qos = rclcpp::QoS(rclcpp::KeepLast(500)).reliable();
  const auto sensor_qos = rclcpp::QoS(rclcpp::KeepLast(10)).reliable();

  sub_odom_ = create_subscription<nav_msgs::msg::Odometry>(
    odom_topic, odom_qos,
    std::bind(&TfAdapterNode::onOdom, this, std::placeholders::_1));
  sub_wiwc_ = create_subscription<nav_msgs::msg::Odometry>(
    wiwc_topic, sensor_qos,
    std::bind(&TfAdapterNode::onWiwc, this, std::placeholders::_1));
  sub_raw_tf_ = create_subscription<tf2_msgs::msg::TFMessage>(
    raw_tf_topic, rclcpp::QoS(rclcpp::KeepLast(100)),
    std::bind(&TfAdapterNode::onRawTf, this, std::placeholders::_1));

  pub_status_ = create_publisher<odin1_interfaces::msg::LocalizationStatus>(
    "~/localization_status", rclcpp::QoS(1));

  if (query_device_state_) {
    cli_device_state_ =
      create_client<odin1_interfaces::srv::GetDeviceState>("/odin1/get_device_state");
  }

  status_timer_ = create_wall_timer(200ms, std::bind(&TfAdapterNode::onStatusTimer, this));

  if (publish_base_link_) {
    publishStaticBaseToImu();
  }

  const std::string chain = publish_base_link_
    ? (map_frame_ + " -> " + odom_frame_ + " -> " + base_frame_ + " -> " + imu_frame_)
    : (map_frame_ + " -> " + odom_frame_ + " -> " + imu_frame_);
  RCLCPP_INFO(
    get_logger(), "odin1_tf_adapter publishing %s. Sources: odom=%s wiwc=%s raw_tf=%s",
    chain.c_str(), odom_topic.c_str(), wiwc_topic.c_str(), raw_tf_topic.c_str());
  RCLCPP_INFO(
    get_logger(),
    "REMINDER: launch the driver with -r /tf:=%s so it does not also write /tf; "
    "two publishers of odom<->map produce a TF loop.", raw_tf_topic.c_str());
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

tf2::Transform TfAdapterNode::fromCovariance(const std::array<double, 36> & cov)
{
  // The driver packs a row-major 4x4 homogeneous matrix into cov[0..15] and
  // forces the bottom row to [0 0 0 1] (host_sdk_sample.h publishWiwc).
  tf2::Matrix3x3 rot(
    cov[0], cov[1], cov[2],
    cov[4], cov[5], cov[6],
    cov[8], cov[9], cov[10]);
  const tf2::Vector3 t(cov[3], cov[7], cov[11]);

  // Round-trip through a quaternion so a slightly non-orthonormal matrix from
  // the device cannot poison downstream tf2 lookups.
  tf2::Quaternion q;
  rot.getRotation(q);
  const double n = q.length();
  if (!std::isfinite(n) || n < 1e-9) {
    return tf2::Transform::getIdentity();
  }
  q.normalize();
  return tf2::Transform(q, t);
}

geometry_msgs::msg::TransformStamped TfAdapterNode::toMsg(
  const tf2::Transform & tf, const rclcpp::Time & stamp,
  const std::string & parent, const std::string & child)
{
  geometry_msgs::msg::TransformStamped m;
  m.header.stamp = stamp;
  m.header.frame_id = parent;
  m.child_frame_id = child;
  const tf2::Vector3 & o = tf.getOrigin();
  m.transform.translation.x = o.x();
  m.transform.translation.y = o.y();
  m.transform.translation.z = o.z();
  const tf2::Quaternion q = tf.getRotation();
  m.transform.rotation.x = q.x();
  m.transform.rotation.y = q.y();
  m.transform.rotation.z = q.z();
  m.transform.rotation.w = q.w();
  return m;
}

bool TfAdapterNode::rateAllows(Clock::time_point now, Clock::time_point & last, double rate_hz)
{
  if (rate_hz <= 0.0) {
    last = now;
    return true;
  }
  if (last == Clock::time_point{}) {
    last = now;
    return true;
  }
  const auto period = std::chrono::duration<double>(1.0 / rate_hz);
  if (now - last < std::chrono::duration_cast<Clock::duration>(period)) {
    return false;
  }
  last = now;
  return true;
}

void TfAdapterNode::publishStaticBaseToImu()
{
  // Stamped with the node's own clock: this is a mounting transform, not a
  // measurement, and /tf_static is latched so the stamp only affects logging.
  tf_static_broadcaster_->sendTransform(
    toMsg(base_to_imu_, now(), base_frame_, imu_frame_));
}

// ---------------------------------------------------------------------------
// odometry -> odom -> base_link (and the paced map -> odom)
// ---------------------------------------------------------------------------

void TfAdapterNode::onOdom(const nav_msgs::msg::Odometry::SharedPtr msg)
{
  const rclcpp::Time stamp(msg->header.stamp);
  const auto host_now = Clock::now();

  tf2::Quaternion q(
    msg->pose.pose.orientation.x, msg->pose.pose.orientation.y,
    msg->pose.pose.orientation.z, msg->pose.pose.orientation.w);
  const double n = q.length();
  if (!std::isfinite(n) || n < 1e-9) {
    RCLCPP_WARN_THROTTLE(
      get_logger(), *get_clock(), 5000,
      "dropping odometry sample with a degenerate orientation");
    return;
  }
  q.normalize();
  const tf2::Transform odom_to_imu(
    q, tf2::Vector3(
      msg->pose.pose.position.x, msg->pose.pose.position.y, msg->pose.pose.position.z));

  std::vector<geometry_msgs::msg::TransformStamped> out;

  {
    std::lock_guard<std::mutex> lock(mutex_);
    if (rateAllows(host_now, last_odom_tf_pub_, odom_tf_rate_)) {
      if (publish_base_link_) {
        // odom -> base_link = (odom -> imu) * (imu -> base) = odom_to_imu * base_to_imu^-1
        out.push_back(
          toMsg(odom_to_imu * base_to_imu_.inverse(), stamp, odom_frame_, base_frame_));
      } else {
        out.push_back(toMsg(odom_to_imu, stamp, odom_frame_, imu_frame_));
      }
    }

    // map -> odom is republished on the odometry clock so that tf2 always has a
    // map->odom bracketing any odom->base_link sample. ROS 2 Jazzy's tf2 does no
    // extrapolation (the same issue the vendor works around with
    // tf_extra_publish_rate), so a map->odom published only at relocalization
    // rate would make every map-frame lookup fail.
    const bool fresh = map_to_odom_seen_ &&
      std::chrono::duration<double>(host_now - map_to_odom_rx_).count() <= map_odom_timeout_;

    if (rateAllows(host_now, last_map_odom_pub_, map_odom_rate_)) {
      if (fresh) {
        out.push_back(toMsg(map_to_odom_, stamp, map_frame_, odom_frame_));
        identity_fallback_active_ = false;
      } else if (fallback_ == MapOdomFallback::Hold && map_to_odom_seen_) {
        // Keep the last known correction alive with fresh stamps. The device is
        // no longer confirming it, so LocalizationStatus reports STATE_STALE.
        out.push_back(toMsg(map_to_odom_, stamp, map_frame_, odom_frame_));
        identity_fallback_active_ = false;
      } else if (fallback_ != MapOdomFallback::None) {
        // Identity, or Hold with nothing to hold yet.
        out.push_back(toMsg(tf2::Transform::getIdentity(), stamp, map_frame_, odom_frame_));
        identity_fallback_active_ = true;
      } else {
        identity_fallback_active_ = false;
      }
    }
  }

  if (!out.empty()) {
    tf_broadcaster_->sendTransform(out);
  }
}

// ---------------------------------------------------------------------------
// wiwc -> imu -> lidar -> camera
// ---------------------------------------------------------------------------

void TfAdapterNode::onWiwc(const nav_msgs::msg::Odometry::SharedPtr msg)
{
  if (!publish_extrinsics_) {
    return;
  }

  // twist.covariance[0..15] = T_IL (pose of the lidar in the IMU frame)
  // pose.covariance [0..15] = T_CL (camera <- lidar)
  const tf2::Transform imu_to_lidar = fromCovariance(msg->twist.covariance);
  const tf2::Transform cam_from_lidar = fromCovariance(msg->pose.covariance);
  const tf2::Transform lidar_to_camera = cam_from_lidar.inverse();

  const rclcpp::Time stamp(msg->header.stamp);

  if (static_extrinsics_) {
    // These are calibration values: constant per device. Latching them once on
    // /tf_static removes them from the periodic TF traffic entirely.
    std::lock_guard<std::mutex> lock(mutex_);
    if (static_extrinsics_sent_) {
      return;
    }
    static_extrinsics_sent_ = true;
    tf_static_broadcaster_->sendTransform(
      {toMsg(imu_to_lidar, stamp, imu_frame_, lidar_frame_),
        toMsg(lidar_to_camera, stamp, lidar_frame_, camera_frame_)});
    RCLCPP_INFO(get_logger(), "latched %s->%s and %s->%s onto /tf_static",
      imu_frame_.c_str(), lidar_frame_.c_str(), lidar_frame_.c_str(), camera_frame_.c_str());
    return;
  }

  const auto host_now = Clock::now();
  {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!rateAllows(host_now, last_extrinsics_pub_, extrinsics_rate_)) {
      return;
    }
  }
  tf_broadcaster_->sendTransform(
    {toMsg(imu_to_lidar, stamp, imu_frame_, lidar_frame_),
      toMsg(lidar_to_camera, stamp, lidar_frame_, camera_frame_)});
}

// ---------------------------------------------------------------------------
// raw driver TF -> extract and invert odom -> map
// ---------------------------------------------------------------------------

void TfAdapterNode::onRawTf(const tf2_msgs::msg::TFMessage::SharedPtr msg)
{
  for (const auto & t : msg->transforms) {
    // The driver publishes this one INVERTED relative to REP-105: parent odom,
    // child map (host_sdk_sample.h:1538-1539).
    if (t.header.frame_id != driver_odom_frame_ || t.child_frame_id != driver_map_frame_) {
      continue;
    }

    tf2::Quaternion q(
      t.transform.rotation.x, t.transform.rotation.y,
      t.transform.rotation.z, t.transform.rotation.w);
    const double n = q.length();
    if (!std::isfinite(n) || n < 1e-9) {
      RCLCPP_WARN_THROTTLE(
        get_logger(), *get_clock(), 5000,
        "ignoring odom->map with a degenerate rotation");
      continue;
    }
    q.normalize();
    const tf2::Transform odom_to_map(
      q, tf2::Vector3(
        t.transform.translation.x, t.transform.translation.y, t.transform.translation.z));

    std::lock_guard<std::mutex> lock(mutex_);
    const bool first = !map_to_odom_seen_;
    map_to_odom_ = odom_to_map.inverse();
    map_to_odom_seen_ = true;
    map_to_odom_rx_ = Clock::now();
    if (first) {
      RCLCPP_INFO(
        get_logger(),
        "relocalization produced the first %s->%s; map frame is now meaningful",
        map_frame_.c_str(), odom_frame_.c_str());
    }
  }
}

// ---------------------------------------------------------------------------
// status
// ---------------------------------------------------------------------------

void TfAdapterNode::queryDeviceState()
{
  if (!cli_device_state_ || !cli_device_state_->service_is_ready()) {
    return;
  }
  // Never keep more than one request in flight: if odin1_control is wedged, an
  // un-throttled 5 Hz timer would otherwise pile up futures that never resolve.
  bool expected = false;
  if (!device_state_query_pending_.compare_exchange_strong(expected, true)) {
    return;
  }
  auto req = std::make_shared<odin1_interfaces::srv::GetDeviceState::Request>();
  cli_device_state_->async_send_request(
    req,
    [this](rclcpp::Client<odin1_interfaces::srv::GetDeviceState>::SharedFuture future) {
      const auto res = future.get();
      {
        std::lock_guard<std::mutex> lock(mutex_);
        device_map_mode_ = res->connected ? static_cast<int>(res->map_mode) : -1;
      }
      device_state_query_pending_ = false;
    });
}

void TfAdapterNode::onStatusTimer()
{
  // The status message is cheap and published at the timer rate; the service
  // query behind it is not, so only run it every fifth tick (~1 Hz).
  if (++status_tick_ % 5 == 0) {
    queryDeviceState();
  }

  odin1_interfaces::msg::LocalizationStatus st;
  st.header.stamp = now();
  st.header.frame_id = map_frame_;

  std::lock_guard<std::mutex> lock(mutex_);

  st.map_odom_valid = map_to_odom_seen_;
  st.map_odom_age_sec = map_to_odom_seen_
    ? static_cast<float>(
      std::chrono::duration<double>(Clock::now() - map_to_odom_rx_).count())
    : std::numeric_limits<float>::infinity();
  st.identity_fallback = identity_fallback_active_;

  const tf2::Vector3 & o = map_to_odom_.getOrigin();
  st.map_to_odom.translation.x = o.x();
  st.map_to_odom.translation.y = o.y();
  st.map_to_odom.translation.z = o.z();
  const tf2::Quaternion q = map_to_odom_.getRotation();
  st.map_to_odom.rotation.x = q.x();
  st.map_to_odom.rotation.y = q.y();
  st.map_to_odom.rotation.z = q.z();
  st.map_to_odom.rotation.w = q.w();

  const bool fresh = map_to_odom_seen_ && st.map_odom_age_sec <= map_odom_timeout_;

  if (device_map_mode_ == 0 || device_map_mode_ == 1) {
    // Odometry and mapping modes: map coincides with odom by design, there is
    // nothing to relocalize against.
    st.state = odin1_interfaces::msg::LocalizationStatus::STATE_NO_MAP;
    st.state_text = (device_map_mode_ == 0) ? "odometry mode (map == odom)"
      : "mapping mode (map == odom)";
  } else if (fresh) {
    st.state = odin1_interfaces::msg::LocalizationStatus::STATE_LOCALIZED;
    st.state_text = "localized";
  } else if (map_to_odom_seen_) {
    st.state = odin1_interfaces::msg::LocalizationStatus::STATE_STALE;
    st.state_text = "map->odom is stale; localization may have been lost";
  } else if (device_map_mode_ == 2) {
    st.state = odin1_interfaces::msg::LocalizationStatus::STATE_SEARCHING;
    st.state_text = "relocalizing: no map->odom yet. The driver silently falls back to "
      "SLAM while searching; map saving is disabled in that state";
  } else {
    st.state = odin1_interfaces::msg::LocalizationStatus::STATE_UNKNOWN;
    st.state_text = "no map->odom observed and device map_mode unknown";
  }

  pub_status_->publish(st);
}

}  // namespace odin1_tf_adapter

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<odin1_tf_adapter::TfAdapterNode>());
  rclcpp::shutdown();
  return 0;
}
