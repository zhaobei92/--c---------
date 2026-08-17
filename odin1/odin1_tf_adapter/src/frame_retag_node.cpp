// Copyright 2026 Odin1 integration contributors
// Licensed under the Apache License, Version 2.0
//
// Rewrites header.frame_id (and child_frame_id for Odometry) on the driver's
// data topics.
//
// Only needed when odin1_tf_adapter runs with a non-empty frame_prefix. The
// driver hard-codes its frame names in the message headers - `lidar` for
// cloud_raw / cloud_render, `odom` for cloud_slam and odometry, `imu` for the
// IMU (host_sdk_sample.h:325, 581, 650, 913, 941) - so prefixing the TF frames
// alone would leave those messages pointing at frames that no longer exist.
//
// Leave frame_prefix empty (the default) and you do not need this node at all:
// the adapter then publishes TF using the same names the driver already stamps
// into its messages, and nothing has to be copied.
//
// Republishes on <input_topic><output_suffix> so the original stream is left
// untouched for anything already consuming it.

#include <algorithm>
#include <map>
#include <memory>
#include <string>
#include <vector>

#include <rclcpp/rclcpp.hpp>
#include <nav_msgs/msg/odometry.hpp>
#include <sensor_msgs/msg/imu.hpp>
#include <sensor_msgs/msg/point_cloud2.hpp>

namespace odin1_tf_adapter
{

class FrameRetagNode : public rclcpp::Node
{
public:
  FrameRetagNode()
  : rclcpp::Node("odin1_frame_retag")
  {
    prefix_ = declare_parameter<std::string>("frame_prefix", "odin1_");
    suffix_ = declare_parameter<std::string>("output_suffix", "_retagged");
    const auto depth = declare_parameter<int>("queue_depth", 10);
    const auto imu_depth = declare_parameter<int>("imu_queue_depth", 500);

    const auto clouds = declare_parameter<std::vector<std::string>>(
      "pointcloud_topics",
      {"/odin1/cloud_raw", "/odin1/cloud_render", "/odin1/cloud_slam"});
    const auto imus = declare_parameter<std::vector<std::string>>(
      "imu_topics", {"/odin1/imu"});
    const auto odoms = declare_parameter<std::vector<std::string>>(
      "odometry_topics", {"/odin1/odometry", "/odin1/odometry_highfreq"});

    if (prefix_.empty()) {
      RCLCPP_WARN(
        get_logger(),
        "frame_prefix is empty: this node would only copy messages without changing "
        "anything. Either set frame_prefix or do not run odin1_frame_retag.");
    }

    for (const auto & t : clouds) {
      addRelay<sensor_msgs::msg::PointCloud2>(t, depth);
    }
    for (const auto & t : imus) {
      addRelay<sensor_msgs::msg::Imu>(t, imu_depth);
    }
    for (const auto & t : odoms) {
      addRelay<nav_msgs::msg::Odometry>(t, imu_depth);
    }
  }

private:
  /// Odometry carries a second frame name that also has to be prefixed.
  static void retagChild(nav_msgs::msg::Odometry & m, const std::string & prefix)
  {
    if (!m.child_frame_id.empty()) {
      m.child_frame_id = prefix + m.child_frame_id;
    }
  }
  static void retagChild(sensor_msgs::msg::PointCloud2 &, const std::string &) {}
  static void retagChild(sensor_msgs::msg::Imu &, const std::string &) {}

  template<typename MsgT>
  void addRelay(const std::string & topic, int depth)
  {
    const auto qos = rclcpp::QoS(rclcpp::KeepLast(static_cast<size_t>(std::max(1, depth))))
      .reliable();
    auto pub = create_publisher<MsgT>(topic + suffix_, qos);
    auto sub = create_subscription<MsgT>(
      topic, qos,
      [this, pub](std::unique_ptr<MsgT> msg) {
        // Taking the message by unique_ptr lets rclcpp hand over ownership and
        // skip a copy when publisher and subscriber share a process.
        if (!msg->header.frame_id.empty()) {
          msg->header.frame_id = prefix_ + msg->header.frame_id;
        }
        retagChild(*msg, prefix_);
        pub->publish(std::move(msg));
      });
    subs_.push_back(sub);
    pubs_.push_back(pub);
    RCLCPP_INFO(get_logger(), "retagging %s -> %s%s", topic.c_str(), topic.c_str(),
      suffix_.c_str());
  }

  std::string prefix_;
  std::string suffix_;
  std::vector<rclcpp::SubscriptionBase::SharedPtr> subs_;
  std::vector<rclcpp::PublisherBase::SharedPtr> pubs_;
};

}  // namespace odin1_tf_adapter

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<odin1_tf_adapter::FrameRetagNode>());
  rclcpp::shutdown();
  return 0;
}
