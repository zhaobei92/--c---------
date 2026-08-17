// Copyright 2026 Odin1 integration contributors
// Licensed under the Apache License, Version 2.0

#include "odin1_control/control_server.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstring>
#include <ctime>
#include <exception>
#include <filesystem>
#include <iterator>
#include <utility>
#include <vector>

namespace odin1_control
{

using namespace std::chrono_literals;

namespace
{

/// Device custom-parameter names. These are the driver's YAML keys with the
/// "custom_" prefix stripped - the rule is implemented in
/// odin_ros_driver/src/yaml_parser.cpp:69-70 and is not stated in any document.
constexpr const char * kParamMapMode = "map_mode";
constexpr const char * kParamSaveMap = "save_map";
constexpr const char * kParamAlgoReset = "algo_reset";
constexpr const char * kParamInitPos = "init_pos";
constexpr const char * kParamInitSearchRadius = "init_pose_search_radius";
constexpr const char * kParamInitMaxRotDeg = "init_pose_max_rot_deg";

std::string timestampNow()
{
  const auto now = std::chrono::system_clock::now();
  const std::time_t t = std::chrono::system_clock::to_time_t(now);
  std::tm tm{};
  localtime_r(&t, &tm);
  char buf[32];
  std::strftime(buf, sizeof(buf), "%Y%m%d_%H%M%S", &tm);
  return std::string(buf);
}

double elapsedSince(const std::chrono::steady_clock::time_point & t0)
{
  return std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
}

// ---------------------------------------------------------------------------
// Goal-handle calls that must never take the process down.
//
// rclcpp_action throws (rclcpp::exceptions::RCLError) when a goal handle is
// already in a terminal state - which happens on legitimate races, e.g. a
// cancel landing while the worker is finishing. These workers run on DETACHED
// threads, so an escaping exception is std::terminate for the whole driver
// process, taking the point-cloud and odometry publishers with it.
// ---------------------------------------------------------------------------

template<typename GoalHandleT, typename ResultT>
void safeSucceed(const rclcpp::Logger & log, const GoalHandleT & gh, ResultT result)
{
  try {
    gh->succeed(std::move(result));
  } catch (const std::exception & e) {
    RCLCPP_WARN(log, "[odin1_control] could not mark goal succeeded: %s", e.what());
  }
}

template<typename GoalHandleT, typename ResultT>
void safeAbort(const rclcpp::Logger & log, const GoalHandleT & gh, ResultT result)
{
  try {
    gh->abort(std::move(result));
  } catch (const std::exception & e) {
    RCLCPP_WARN(log, "[odin1_control] could not mark goal aborted: %s", e.what());
  }
}

template<typename GoalHandleT, typename ResultT>
void safeCanceled(const rclcpp::Logger & log, const GoalHandleT & gh, ResultT result)
{
  try {
    gh->canceled(std::move(result));
  } catch (const std::exception & e) {
    RCLCPP_WARN(log, "[odin1_control] could not mark goal canceled: %s", e.what());
  }
}

template<typename GoalHandleT, typename FeedbackT>
void safeFeedback(const rclcpp::Logger & log, const GoalHandleT & gh, FeedbackT fb)
{
  try {
    if (gh->is_active()) {
      gh->publish_feedback(std::move(fb));
    }
  } catch (const std::exception & e) {
    RCLCPP_DEBUG(log, "[odin1_control] feedback dropped: %s", e.what());
  }
}

}  // namespace

// ---------------------------------------------------------------------------
// DeviceSession
// ---------------------------------------------------------------------------

ControlServer::DeviceSession::DeviceSession(ControlServer & owner)
: owner_(owner)
{
  if (!owner_.accepting_.load()) {
    rc_ = RC_SHUTTING_DOWN;
    return;
  }
  owner_.in_flight_.fetch_add(1);
  admitted_ = true;

  // Re-check after registering: beginTeardown() may have flipped the flag
  // between our test and the increment. Registering first and then re-testing
  // means we either see the flag (and back out) or beginTeardown sees our
  // increment (and waits). There is no interleaving where both miss.
  if (!owner_.accepting_.load()) {
    rc_ = RC_SHUTTING_DOWN;
    return;
  }

  int rc = 0;
  initial_ = owner_.device(rc);
  rc_ = rc;
}

ControlServer::DeviceSession::~DeviceSession()
{
  if (!admitted_) {
    return;
  }
  if (owner_.in_flight_.fetch_sub(1) == 1) {
    std::lock_guard<std::mutex> lock(owner_.idle_mutex_);
    owner_.idle_cv_.notify_all();
  }
}

device_handle ControlServer::DeviceSession::handle()
{
  if (!admitted_) {
    return nullptr;
  }
  int rc = 0;
  device_handle current = owner_.device(rc);
  if (!current) {
    rc_ = rc;
    return nullptr;
  }
  if (initial_ && current != initial_) {
    // The driver recycled the handle mid-operation, which it does on every
    // reconnect (host_sdk_sample.cpp:1298-1300). Continuing with either handle
    // would be undefined; fail the operation instead.
    rc_ = RC_DEVICE_LOST;
    return nullptr;
  }
  initial_ = current;
  rc_ = 0;
  return current;
}

// ---------------------------------------------------------------------------
// construction / teardown
// ---------------------------------------------------------------------------

ControlServer::ControlServer(
  rclcpp::Node::SharedPtr node, DeviceContext ctx, ControlServerOptions options)
: node_(std::move(node)), ctx_(std::move(ctx)), opt_(std::move(options))
{
  if (!ctx_.valid()) {
    RCLCPP_ERROR(
      node_->get_logger(),
      "[odin1_control] DeviceContext is missing get_device/is_connected; "
      "control surface will report rc=%d for every request.", RC_DEVICE_NOT_OPEN);
  }

  // Reentrant, and explicitly NOT auto-added to whatever executor spins the
  // node. The driver calls rclcpp::spin_some(node) at 10 Hz on its main thread;
  // if this group were picked up there, lidar_save_map() (up to 120 s) would
  // stall every publisher in the process.
  cb_group_ = node_->create_callback_group(
    rclcpp::CallbackGroupType::Reentrant,
    /*automatically_add_to_executor_with_node=*/false);

  const std::string ns = opt_.ns;

  srv_load_map_ = node_->create_service<odin1_interfaces::srv::LoadMap>(
    ns + "/load_map",
    std::bind(&ControlServer::handleLoadMap, this, std::placeholders::_1, std::placeholders::_2),
    rmw_qos_profile_services_default, cb_group_);

  srv_set_init_pose_ = node_->create_service<odin1_interfaces::srv::SetInitPose>(
    ns + "/set_init_pose",
    std::bind(&ControlServer::handleSetInitPose, this, std::placeholders::_1, std::placeholders::_2),
    rmw_qos_profile_services_default, cb_group_);

  srv_reset_algo_ = node_->create_service<odin1_interfaces::srv::ResetAlgo>(
    ns + "/reset_algo",
    std::bind(&ControlServer::handleResetAlgo, this, std::placeholders::_1, std::placeholders::_2),
    rmw_qos_profile_services_default, cb_group_);

  srv_get_device_state_ = node_->create_service<odin1_interfaces::srv::GetDeviceState>(
    ns + "/get_device_state",
    std::bind(
      &ControlServer::handleGetDeviceState, this, std::placeholders::_1, std::placeholders::_2),
    rmw_qos_profile_services_default, cb_group_);

  act_save_map_ = rclcpp_action::create_server<SaveMap>(
    node_, ns + "/save_map",
    std::bind(&ControlServer::saveMapGoal, this, std::placeholders::_1, std::placeholders::_2),
    std::bind(&ControlServer::saveMapCancel, this, std::placeholders::_1),
    std::bind(&ControlServer::saveMapAccepted, this, std::placeholders::_1),
    rcl_action_server_get_default_options(), cb_group_);

  act_switch_mode_ = rclcpp_action::create_server<SwitchMode>(
    node_, ns + "/switch_mode",
    std::bind(&ControlServer::switchModeGoal, this, std::placeholders::_1, std::placeholders::_2),
    std::bind(&ControlServer::switchModeCancel, this, std::placeholders::_1),
    std::bind(&ControlServer::switchModeAccepted, this, std::placeholders::_1),
    rcl_action_server_get_default_options(), cb_group_);

  if (opt_.status_period.count() > 0) {
    pub_status_ = node_->create_publisher<odin1_interfaces::msg::DeviceStatus>(
      ns + "/device_status", rclcpp::QoS(1));
    status_timer_ = node_->create_wall_timer(
      opt_.status_period, std::bind(&ControlServer::publishStatus, this), cb_group_);
  }

  executor_ = std::make_shared<rclcpp::executors::MultiThreadedExecutor>(
    rclcpp::ExecutorOptions(), static_cast<size_t>(std::max(2, opt_.executor_threads)));
  executor_->add_callback_group(cb_group_, node_->get_node_base_interface());

  running_ = true;
  executor_thread_ = std::thread(
    [this]() {
      try {
        executor_->spin();
      } catch (const std::exception & e) {
        RCLCPP_ERROR(
          node_->get_logger(), "[odin1_control] control executor stopped: %s", e.what());
      }
    });

  RCLCPP_INFO(
    node_->get_logger(),
    "[odin1_control] ready on a dedicated %d-thread executor: "
    "%s/{save_map,switch_mode} (action) %s/{load_map,set_init_pose,reset_algo,get_device_state} "
    "(service)",
    std::max(2, opt_.executor_threads), ns.c_str(), ns.c_str());
}

ControlServer::~ControlServer()
{
  shutdown();
}

bool ControlServer::waitForDeviceIdle(std::chrono::milliseconds grace)
{
  std::unique_lock<std::mutex> lock(idle_mutex_);
  const bool drained = idle_cv_.wait_for(
    lock, grace, [this] {return in_flight_.load() == 0;});
  if (!drained) {
    RCLCPP_ERROR(
      node_->get_logger(),
      "[odin1_control] %d device operation(s) still in flight after %ld ms. "
      "lidar_save_map() is not interruptible, so this can happen on Ctrl+C during a "
      "save; the SDK may be torn down underneath it.",
      in_flight_.load(), static_cast<long>(grace.count()));
  }
  return drained;
}

bool ControlServer::beginTeardown(std::chrono::milliseconds grace)
{
  const bool was_accepting = accepting_.exchange(false);
  if (was_accepting && in_flight_.load() > 0) {
    RCLCPP_INFO(
      node_->get_logger(),
      "[odin1_control] teardown requested, waiting up to %ld ms for %d in-flight "
      "operation(s)", static_cast<long>(grace.count()), in_flight_.load());
  }
  return waitForDeviceIdle(grace);
}

void ControlServer::shutdown(std::chrono::milliseconds grace)
{
  // beginTeardown() first and unconditionally: even if shutdown() has already
  // run once, a later caller wants the "no new work + drained" guarantee.
  beginTeardown(grace);

  if (!running_.exchange(false)) {
    return;
  }
  if (executor_) {
    executor_->cancel();
  }
  if (executor_thread_.joinable()) {
    executor_thread_.join();
  }
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

device_handle ControlServer::device(int & rc) const
{
  if (!ctx_.get_device || !ctx_.is_connected) {
    rc = RC_DEVICE_NOT_OPEN;
    return nullptr;
  }
  if (!ctx_.is_connected()) {
    rc = RC_DEVICE_NOT_OPEN;
    return nullptr;
  }
  device_handle dev = ctx_.get_device();
  rc = dev ? 0 : RC_DEVICE_NOT_OPEN;
  return dev;
}

int ControlServer::setIntParam(device_handle dev, const char * name, int value)
{
  return lidar_set_custom_parameter(dev, name, &value, sizeof(int));
}

int ControlServer::setFloatParam(
  device_handle dev, const char * name, const float * values, size_t count)
{
  return lidar_set_custom_parameter(dev, name, values, count * sizeof(float));
}

int ControlServer::applyInitPose(
  device_handle dev, const double pos[3], const double quat_xyzw[4],
  float search_radius_m, float max_rot_deg, std::string & message)
{
  if (!std::isfinite(pos[0]) || !std::isfinite(pos[1]) || !std::isfinite(pos[2])) {
    message = "init pose position contains a non-finite value";
    return RC_INVALID_REQUEST;
  }
  const double n = std::sqrt(
    quat_xyzw[0] * quat_xyzw[0] + quat_xyzw[1] * quat_xyzw[1] +
    quat_xyzw[2] * quat_xyzw[2] + quat_xyzw[3] * quat_xyzw[3]);
  if (!std::isfinite(n) || std::fabs(n - 1.0) > 1e-3) {
    message = "orientation quaternion is not normalised (|q| = " + std::to_string(n) +
      "); the device requires |q| == 1";
    return RC_INVALID_REQUEST;
  }

  // init_pos = [x, y, z, qx, qy, qz, qw], 7 floats = 28 bytes.
  const float init_pos[7] = {
    static_cast<float>(pos[0]), static_cast<float>(pos[1]), static_cast<float>(pos[2]),
    static_cast<float>(quat_xyzw[0]), static_cast<float>(quat_xyzw[1]),
    static_cast<float>(quat_xyzw[2]), static_cast<float>(quat_xyzw[3])};

  int rc = setFloatParam(dev, kParamInitPos, init_pos, 7);
  if (rc != 0) {
    message = "failed to set init_pos";
    return rc;
  }

  if (search_radius_m > 0.0f) {
    rc = setFloatParam(dev, kParamInitSearchRadius, &search_radius_m, 1);
    if (rc != 0) {
      message = "init_pos set, but init_pose_search_radius failed";
      return rc;
    }
  }
  if (max_rot_deg > 0.0f) {
    rc = setFloatParam(dev, kParamInitMaxRotDeg, &max_rot_deg, 1);
    if (rc != 0) {
      message = "init_pos set, but init_pose_max_rot_deg failed";
      return rc;
    }
  }
  return 0;
}

const char * ControlServer::mapModeText(int mode)
{
  switch (mode) {
    case 0: return "odometry";
    case 1: return "mapping";
    case 2: return "relocalization";
    default: return "unknown";
  }
}

const char * ControlServer::deviceStateText(int state)
{
  switch (state) {
    case LIDAR_DEVICE_NONE: return "none";
    case LIDAR_DEVICE_NOT_INITIALIZED: return "not_initialized";
    case LIDAR_DEVICE_INITIALIZED: return "initialized";
    case LIDAR_DEVICE_STREAMING: return "streaming";
    case LIDAR_DEVICE_STREAM_STOPPED: return "stream_stopped";
    default: return "unknown";
  }
}

const char * ControlServer::rcText(int rc)
{
  switch (rc) {
    case 0: return "ok";
    case RC_DEVICE_NOT_OPEN: return "driver has not opened the device yet";
    case RC_BUSY: return "another control operation is in progress";
    case RC_INVALID_REQUEST: return "invalid request";
    case RC_SHUTTING_DOWN: return "driver is shutting down";
    case RC_DEVICE_LOST: return "the device disconnected or was reconnected mid-operation";
    case RC_INTERNAL_ERROR: return "internal error";
    case -2: return "device is busy with a file transfer";
    default: return "device/SDK error";
  }
}

void ControlServer::fillVersions(
  device_handle dev, std::shared_ptr<odin1_interfaces::srv::GetDeviceState::Response> res)
{
  std::lock_guard<std::mutex> lock(version_mutex_);
  if (!version_cached_ || version_cached_for_ != dev) {
    lidar_fireware_version_t ver{};
    if (lidar_get_version(dev, &ver) != 0) {
      return;   // leave the version strings empty; not worth failing the call
    }
    auto fmt = [](const lidar_version_t & v) {
        return std::to_string(v.major) + "." + std::to_string(v.minor) + "." +
               std::to_string(v.patch);
      };
    v_kernel_ = fmt(ver.kernel_version);
    v_mcu_ = fmt(ver.mcu_version);
    v_soc_ = fmt(ver.soc_version);
    v_daemon_ = fmt(ver.Daemon_proc_version);
    v_slam_ = fmt(ver.slam_version);
    version_cached_ = true;
    version_cached_for_ = dev;
  }
  res->kernel_version = v_kernel_;
  res->mcu_version = v_mcu_;
  res->soc_version = v_soc_;
  res->daemon_version = v_daemon_;
  res->slam_version = v_slam_;
}

// ---------------------------------------------------------------------------
// LoadMap
// ---------------------------------------------------------------------------

void ControlServer::handleLoadMap(
  const std::shared_ptr<odin1_interfaces::srv::LoadMap::Request> req,
  std::shared_ptr<odin1_interfaces::srv::LoadMap::Response> res)
{
  res->activated = false;
  res->success = false;

  try {
    DeviceSession session(*this);
    if (!session.ok()) {
      res->rc = session.rc();
      res->message = rcText(session.rc());
      return;
    }

    std::error_code ec;
    if (req->map_path.empty() || !std::filesystem::exists(req->map_path, ec)) {
      res->rc = RC_INVALID_REQUEST;
      res->message = "map_path does not exist on the driver host: " + req->map_path;
      return;
    }

    std::unique_lock<std::timed_mutex> lock(device_op_mutex_, std::defer_lock);
    if (!lock.try_lock_for(opt_.lock_timeout)) {
      res->rc = RC_BUSY;
      res->message = rcText(RC_BUSY);
      return;
    }

    // The vendor driver retries this three times on connect
    // (host_sdk_sample.cpp:1737-1749) because the chunked transfer can fail on a
    // busy bus; mirror that behaviour, caller-configurable.
    const int attempts = 1 + static_cast<int>(req->retries);
    int rc = RC_DEVICE_LOST;
    for (int i = 0; i < attempts; ++i) {
      device_handle dev = session.handle();
      if (!dev) {
        rc = session.rc();
        break;
      }
      rc = lidar_set_relocalization_map(dev, req->map_path.c_str());
      if (rc == 0) {
        break;
      }
      RCLCPP_WARN(
        node_->get_logger(), "[odin1_control] load_map attempt %d/%d failed rc=%d",
        i + 1, attempts, rc);
    }

    res->rc = rc;
    res->success = (rc == 0);
    if (rc == 0) {
      if (ctx_.set_reloc_map_path) {
        ctx_.set_reloc_map_path(req->map_path);
      }
      res->message =
        "map uploaded. It is NOT active yet: the on-device algorithm consumes a map "
        "at stream start. Call the switch_mode action with map_mode=2 to activate it.";
    } else {
      res->message = std::string("lidar_set_relocalization_map failed: ") + rcText(rc);
    }
  } catch (const std::exception & e) {
    res->success = false;
    res->rc = RC_INTERNAL_ERROR;
    res->message = std::string("internal error: ") + e.what();
    RCLCPP_ERROR(node_->get_logger(), "[odin1_control] load_map threw: %s", e.what());
  }
}

// ---------------------------------------------------------------------------
// SetInitPose
// ---------------------------------------------------------------------------

void ControlServer::handleSetInitPose(
  const std::shared_ptr<odin1_interfaces::srv::SetInitPose::Request> req,
  std::shared_ptr<odin1_interfaces::srv::SetInitPose::Response> res)
{
  res->applied_immediately = false;
  res->success = false;

  try {
    DeviceSession session(*this);
    if (!session.ok()) {
      res->rc = session.rc();
      res->message = rcText(session.rc());
      return;
    }

    std::unique_lock<std::timed_mutex> lock(device_op_mutex_, std::defer_lock);
    if (!lock.try_lock_for(opt_.lock_timeout)) {
      res->rc = RC_BUSY;
      res->message = rcText(RC_BUSY);
      return;
    }

    device_handle dev = session.handle();
    if (!dev) {
      res->rc = session.rc();
      res->message = rcText(session.rc());
      return;
    }

    const auto & p = req->pose.pose.pose.position;
    const auto & q = req->pose.pose.pose.orientation;
    const double pos[3] = {p.x, p.y, p.z};
    const double quat[4] = {q.x, q.y, q.z, q.w};

    std::string message;
    const int rc = applyInitPose(
      dev, pos, quat, req->search_radius_m, req->max_rot_deg, message);
    res->rc = rc;
    res->success = (rc == 0);
    if (rc != 0) {
      res->message = message.empty() ? rcText(rc) : message;
      return;
    }

    // The device only consumes init_pos when the algorithm starts. Report which
    // situation the caller is in rather than pretending the pose took effect.
    lidar_device_initial_state_e state = LIDAR_DEVICE_NONE;
    const bool streaming =
      (lidar_get_device_state(&state) == 0 && state == LIDAR_DEVICE_STREAMING);
    res->applied_immediately = !streaming;
    res->message = streaming
      ? "init pose staged on the device. The algorithm is already streaming, so it will "
        "NOT be used until the stream restarts - call the switch_mode action to apply it."
      : "init pose staged; it will be consumed on the next stream start.";
  } catch (const std::exception & e) {
    res->success = false;
    res->rc = RC_INTERNAL_ERROR;
    res->message = std::string("internal error: ") + e.what();
    RCLCPP_ERROR(node_->get_logger(), "[odin1_control] set_init_pose threw: %s", e.what());
  }
}

// ---------------------------------------------------------------------------
// ResetAlgo
// ---------------------------------------------------------------------------

void ControlServer::handleResetAlgo(
  const std::shared_ptr<odin1_interfaces::srv::ResetAlgo::Request> /*req*/,
  std::shared_ptr<odin1_interfaces::srv::ResetAlgo::Response> res)
{
  res->success = false;

  try {
    DeviceSession session(*this);
    if (!session.ok()) {
      res->rc = session.rc();
      res->message = rcText(session.rc());
      return;
    }

    std::unique_lock<std::timed_mutex> lock(device_op_mutex_, std::defer_lock);
    if (!lock.try_lock_for(opt_.lock_timeout)) {
      res->rc = RC_BUSY;
      res->message = rcText(RC_BUSY);
      return;
    }

    device_handle dev = session.handle();
    if (!dev) {
      res->rc = session.rc();
      res->message = rcText(session.rc());
      return;
    }

    const int rc = setIntParam(dev, kParamAlgoReset, 1);
    res->rc = rc;
    res->success = (rc == 0);
    res->message = (rc == 0)
      ? "algo_reset sent; SLAM will re-initialise"
      : std::string("algo_reset failed: ") + rcText(rc);
  } catch (const std::exception & e) {
    res->success = false;
    res->rc = RC_INTERNAL_ERROR;
    res->message = std::string("internal error: ") + e.what();
    RCLCPP_ERROR(node_->get_logger(), "[odin1_control] reset_algo threw: %s", e.what());
  }
}

// ---------------------------------------------------------------------------
// GetDeviceState
// ---------------------------------------------------------------------------

void ControlServer::handleGetDeviceState(
  const std::shared_ptr<odin1_interfaces::srv::GetDeviceState::Request> /*req*/,
  std::shared_ptr<odin1_interfaces::srv::GetDeviceState::Response> res)
{
  res->success = false;
  res->driver_version = ctx_.driver_version;
  res->status.valid = false;
  res->status.cpu_use_rate.assign(8, 0);

  try {
    // Deliberately does NOT take device_op_mutex_: a status query must stay
    // answerable while a 120 s SaveMap holds the device. The only SDK calls
    // below are lidar_get_device_state() (handle-free, reads cached heartbeat
    // state) and a version query that is cached after the first success.
    DeviceSession session(*this);

    res->connected = ctx_.is_connected ? ctx_.is_connected() : false;
    const int op = exclusive_op_.load();
    res->map_transfer_in_progress =
      (ctx_.is_map_transfer_in_progress ? ctx_.is_map_transfer_in_progress() : false) ||
      op == OP_SAVE_MAP;
    res->mode_switch_in_progress = (op == OP_SWITCH_MODE);
    res->relocalization_map_path = ctx_.get_reloc_map_path ? ctx_.get_reloc_map_path() : "";

    const int map_mode = ctx_.get_map_mode ? ctx_.get_map_mode() : -1;
    res->map_mode = static_cast<uint8_t>(map_mode < 0 ? 0 : map_mode);
    res->map_mode_text = mapModeText(map_mode);

    if (!session.ok()) {
      res->rc = session.rc();
      return;
    }

    // lidar_get_device_state() deliberately takes no handle: it reports the
    // globally tracked heartbeat state and stays valid across reconnects.
    lidar_device_initial_state_e state = LIDAR_DEVICE_NONE;
    const int state_rc = lidar_get_device_state(&state);
    res->initial_state = static_cast<uint8_t>(state);
    res->state_text = deviceStateText(static_cast<int>(state));

    if (device_handle dev = session.handle()) {
      fillVersions(dev, res);
    }

    if (ctx_.get_device_status) {
      lidar_device_status_t s{};
      uint64_t stamp_ns = 0;
      if (ctx_.get_device_status(s, stamp_ns)) {
        auto & st = res->status;
        st.valid = true;
        st.stamp.sec = static_cast<int32_t>(stamp_ns / 1000000000ULL);
        st.stamp.nanosec = static_cast<uint32_t>(stamp_ns % 1000000000ULL);
        st.uptime_seconds = s.uptime_seconds;
        st.package_temp = s.soc_thermal.package_temp;
        st.cpu_temp = s.soc_thermal.cpu_temp;
        st.center_temp = s.soc_thermal.center_temp;
        st.gpu_temp = s.soc_thermal.gpu_temp;
        st.npu_temp = s.soc_thermal.npu_temp;
        st.cpu_use_rate.assign(std::begin(s.cpu_use_rate), std::end(s.cpu_use_rate));
        st.ram_use_rate = s.ram_use_rate;
        st.rgb_configured_odr = s.rgb_sensor.configured_odr;
        st.rgb_tx_odr = s.rgb_sensor.tx_odr;
        st.dtof_configured_odr = s.dtof_sensor.configured_odr;
        st.dtof_tx_odr = s.dtof_sensor.tx_odr;
        st.dtof_subframe_odr = s.dtof_sensor.subframe_odr;
        st.dtof_tx_temp = s.dtof_sensor.tx_temp;
        st.dtof_rx_temp = s.dtof_sensor.rx_temp;
        st.imu_configured_odr = s.imu_sensor.configured_odr;
        st.imu_tx_odr = s.imu_sensor.tx_odr;
        st.slam_cloud_tx_odr = s.slam_cloud_tx_odr;
        st.slam_odom_tx_odr = s.slam_odom_tx_odr;
        st.slam_odom_highfreq_tx_odr = s.slam_odom_highfreq_tx_odr;
      }
    }

    res->rc = state_rc;
    res->success = (state_rc == 0);
  } catch (const std::exception & e) {
    res->success = false;
    res->rc = RC_INTERNAL_ERROR;
    RCLCPP_ERROR(node_->get_logger(), "[odin1_control] get_device_state threw: %s", e.what());
  }
}

void ControlServer::publishStatus()
{
  // Deliberately does NOT go through handleGetDeviceState: that call can query
  // firmware versions over USB, and doing so once per second would add pointless
  // traffic on the same control channel the data path shares.
  try {
    if (!pub_status_ || !ctx_.get_device_status) {
      return;
    }
    lidar_device_status_t s{};
    uint64_t stamp_ns = 0;
    if (!ctx_.get_device_status(s, stamp_ns)) {
      return;
    }

    odin1_interfaces::msg::DeviceStatus st;
    st.valid = true;
    st.stamp.sec = static_cast<int32_t>(stamp_ns / 1000000000ULL);
    st.stamp.nanosec = static_cast<uint32_t>(stamp_ns % 1000000000ULL);
    st.uptime_seconds = s.uptime_seconds;
    st.package_temp = s.soc_thermal.package_temp;
    st.cpu_temp = s.soc_thermal.cpu_temp;
    st.center_temp = s.soc_thermal.center_temp;
    st.gpu_temp = s.soc_thermal.gpu_temp;
    st.npu_temp = s.soc_thermal.npu_temp;
    st.cpu_use_rate.assign(std::begin(s.cpu_use_rate), std::end(s.cpu_use_rate));
    st.ram_use_rate = s.ram_use_rate;
    st.rgb_configured_odr = s.rgb_sensor.configured_odr;
    st.rgb_tx_odr = s.rgb_sensor.tx_odr;
    st.dtof_configured_odr = s.dtof_sensor.configured_odr;
    st.dtof_tx_odr = s.dtof_sensor.tx_odr;
    st.dtof_subframe_odr = s.dtof_sensor.subframe_odr;
    st.dtof_tx_temp = s.dtof_sensor.tx_temp;
    st.dtof_rx_temp = s.dtof_sensor.rx_temp;
    st.imu_configured_odr = s.imu_sensor.configured_odr;
    st.imu_tx_odr = s.imu_sensor.tx_odr;
    st.slam_cloud_tx_odr = s.slam_cloud_tx_odr;
    st.slam_odom_tx_odr = s.slam_odom_tx_odr;
    st.slam_odom_highfreq_tx_odr = s.slam_odom_highfreq_tx_odr;
    pub_status_->publish(st);
  } catch (const std::exception & e) {
    RCLCPP_WARN_THROTTLE(
      node_->get_logger(), *node_->get_clock(), 10000,
      "[odin1_control] device_status publish failed: %s", e.what());
  }
}

// ---------------------------------------------------------------------------
// SaveMap action
// ---------------------------------------------------------------------------

rclcpp_action::GoalResponse ControlServer::saveMapGoal(
  const rclcpp_action::GoalUUID & /*uuid*/, std::shared_ptr<const SaveMap::Goal> /*goal*/)
{
  if (!accepting_.load()) {
    RCLCPP_WARN(node_->get_logger(), "[odin1_control] save_map rejected: shutting down");
    return rclcpp_action::GoalResponse::REJECT;
  }
  if (ctx_.is_map_transfer_in_progress && ctx_.is_map_transfer_in_progress()) {
    RCLCPP_WARN(
      node_->get_logger(),
      "[odin1_control] save_map rejected: a map transfer started elsewhere "
      "(legacy /tmp/odin_command.txt channel?) is still running");
    return rclcpp_action::GoalResponse::REJECT;
  }
  const int map_mode = ctx_.get_map_mode ? ctx_.get_map_mode() : -1;
  if (map_mode != 1) {
    RCLCPP_WARN(
      node_->get_logger(),
      "[odin1_control] save_map rejected: map_mode is %s, the device only saves in "
      "mapping mode (1)", mapModeText(map_mode));
    return rclcpp_action::GoalResponse::REJECT;
  }

  // Claim the exclusive slot HERE, not in the execute callback: checking in the
  // goal callback and setting in the worker leaves a window where two goals both
  // pass and both get accepted. saveMapExecute()'s guard always releases it.
  int expected = OP_NONE;
  if (!exclusive_op_.compare_exchange_strong(expected, OP_SAVE_MAP)) {
    RCLCPP_WARN(
      node_->get_logger(), "[odin1_control] save_map rejected: %s already running",
      expected == OP_SAVE_MAP ? "a save_map" : "a switch_mode");
    return rclcpp_action::GoalResponse::REJECT;
  }
  return rclcpp_action::GoalResponse::ACCEPT_AND_EXECUTE;
}

rclcpp_action::CancelResponse ControlServer::saveMapCancel(
  std::shared_ptr<GoalHandleSaveMap> /*gh*/)
{
  // lidar_save_map() is a single synchronous SDK call that drives the whole
  // trigger -> poll -> transfer state machine internally and exposes no
  // interruption point. Pretending to cancel would leave the device generating
  // a map with nobody collecting it, so refuse honestly.
  RCLCPP_WARN(
    node_->get_logger(),
    "[odin1_control] save_map cancel rejected: lidar_save_map() is not interruptible");
  return rclcpp_action::CancelResponse::REJECT;
}

void ControlServer::saveMapAccepted(std::shared_ptr<GoalHandleSaveMap> gh)
{
  std::thread{std::bind(&ControlServer::saveMapExecute, this, std::placeholders::_1), gh}.detach();
}

void ControlServer::saveMapExecute(std::shared_ptr<GoalHandleSaveMap> gh)
{
  auto result = std::make_shared<SaveMap::Result>();
  const auto t0 = std::chrono::steady_clock::now();

  // The exclusive slot was claimed by saveMapGoal(); this guard is the single
  // owner of releasing it, on every path including exceptions.
  struct Guard
  {
    std::atomic<int> & slot;
    DeviceContext & ctx;
    bool armed = false;
    ~Guard()
    {
      if (armed && ctx.set_map_transfer_in_progress) {
        ctx.set_map_transfer_in_progress(false);
      }
      slot.store(OP_NONE);
    }
  } guard{exclusive_op_, ctx_, false};

  try {
    // This session is what keeps beginTeardown()/waitForDeviceIdle() blocked for
    // the whole save, so the driver cannot call lidar_system_deinit() or recycle
    // the handle while lidar_save_map() is running.
    DeviceSession session(*this);
    if (!session.ok()) {
      result->rc = session.rc();
      result->success = false;
      result->message = rcText(session.rc());
      safeAbort(node_->get_logger(), gh, result);
      return;
    }

    std::unique_lock<std::timed_mutex> lock(device_op_mutex_, std::defer_lock);
    if (!lock.try_lock_for(opt_.lock_timeout)) {
      result->rc = RC_BUSY;
      result->success = false;
      result->message = rcText(RC_BUSY);
      safeAbort(node_->get_logger(), gh, result);
      return;
    }

    const auto goal = gh->get_goal();

    // Destination resolution mirrors the driver's own defaults
    // (host_sdk_sample.cpp:519-520) so both entry points agree on where maps land.
    std::string dir = goal->dest_dir;
    if (dir.empty() && ctx_.get_configured_map_dir) {
      dir = ctx_.get_configured_map_dir();
    }
    if (dir.empty() && ctx_.get_default_map_dir) {
      dir = ctx_.get_default_map_dir();
    }
    if (dir.empty()) {
      result->rc = RC_INVALID_REQUEST;
      result->success = false;
      result->message = "no destination directory: pass dest_dir, or set "
        "mapping_result_dest_dir in control_command.yaml";
      safeAbort(node_->get_logger(), gh, result);
      return;
    }

    std::string name = goal->file_name;
    if (name.empty() && ctx_.get_configured_map_name) {
      name = ctx_.get_configured_map_name();
    }
    if (name.empty()) {
      name = "map_" + timestampNow() + ".bin";
    }

    // lidar_save_map() does not create the directory.
    std::error_code ec;
    std::filesystem::create_directories(dir, ec);
    if (ec) {
      result->rc = RC_INVALID_REQUEST;
      result->success = false;
      result->message = "cannot create destination directory " + dir + ": " + ec.message();
      safeAbort(node_->get_logger(), gh, result);
      return;
    }

    device_handle dev = session.handle();
    if (!dev) {
      result->rc = session.rc();
      result->success = false;
      result->message = rcText(session.rc());
      safeAbort(node_->get_logger(), gh, result);
      return;
    }

    if (ctx_.set_map_transfer_in_progress) {
      ctx_.set_map_transfer_in_progress(true);
      guard.armed = true;
    }

    {
      auto fb = std::make_shared<SaveMap::Feedback>();
      fb->stage = SaveMap::Feedback::STAGE_TRIGGER;
      fb->elapsed_sec = 0.0f;
      safeFeedback(node_->get_logger(), gh, fb);
    }

    // lidar_save_map() blocks for the whole trigger -> poll -> transfer cycle
    // (default cap 120 s). Run it here and tick feedback from a helper thread so
    // the client sees liveness.
    std::atomic<bool> done{false};
    std::thread ticker(
      [this, gh, t0, &done]() {
        try {
          while (!done.load()) {
            std::this_thread::sleep_for(opt_.feedback_period);
            if (done.load()) {
              break;
            }
            auto fb = std::make_shared<SaveMap::Feedback>();
            fb->stage = SaveMap::Feedback::STAGE_SAVING;
            fb->elapsed_sec = static_cast<float>(elapsedSince(t0));
            safeFeedback(node_->get_logger(), gh, fb);
          }
        } catch (const std::exception & e) {
          RCLCPP_WARN(
            node_->get_logger(), "[odin1_control] save_map feedback ticker stopped: %s",
            e.what());
        }
      });

    int rc;
    try {
      rc = lidar_save_map(dev, dir.c_str(), name.c_str(), goal->gen_timeout_ms);
    } catch (...) {
      done = true;
      if (ticker.joinable()) {
        ticker.join();
      }
      throw;
    }

    done = true;
    if (ticker.joinable()) {
      ticker.join();
    }

    result->rc = rc;
    result->success = (rc == 0);
    result->elapsed_sec = static_cast<float>(elapsedSince(t0));
    result->map_path = (rc == 0) ? (std::filesystem::path(dir) / name).string() : "";

    switch (rc) {
      case 0: result->message = "map saved"; break;
      case -1: result->message = "invalid arguments or SDK not initialised"; break;
      case -2: result->message = "device is busy with another file transfer"; break;
      case -3: result->message = "timed out waiting for the device to finish generating the map";
        break;
      case -4: result->message = "file transfer stalled or failed"; break;
      default: result->message = std::string("lidar_save_map failed: ") + rcText(rc); break;
    }

    {
      auto fb = std::make_shared<SaveMap::Feedback>();
      fb->stage = SaveMap::Feedback::STAGE_DONE;
      fb->elapsed_sec = result->elapsed_sec;
      safeFeedback(node_->get_logger(), gh, fb);
    }

    if (rc == 0) {
      RCLCPP_INFO(
        node_->get_logger(), "[odin1_control] map saved to %s (%.1f s)",
        result->map_path.c_str(), result->elapsed_sec);
      safeSucceed(node_->get_logger(), gh, result);
    } else {
      RCLCPP_ERROR(
        node_->get_logger(), "[odin1_control] save_map failed rc=%d (%s)",
        rc, result->message.c_str());
      safeAbort(node_->get_logger(), gh, result);
    }
  } catch (const std::exception & e) {
    // Detached thread: an escaping exception would std::terminate the whole
    // driver process, killing every sensor publisher with it.
    RCLCPP_ERROR(node_->get_logger(), "[odin1_control] save_map threw: %s", e.what());
    result->rc = RC_INTERNAL_ERROR;
    result->success = false;
    result->elapsed_sec = static_cast<float>(elapsedSince(t0));
    result->message = std::string("internal error: ") + e.what();
    safeAbort(node_->get_logger(), gh, result);
  } catch (...) {
    RCLCPP_ERROR(node_->get_logger(), "[odin1_control] save_map threw a non-std exception");
    result->rc = RC_INTERNAL_ERROR;
    result->success = false;
    result->message = "internal error";
    safeAbort(node_->get_logger(), gh, result);
  }
}

// ---------------------------------------------------------------------------
// SwitchMode action
// ---------------------------------------------------------------------------

rclcpp_action::GoalResponse ControlServer::switchModeGoal(
  const rclcpp_action::GoalUUID & /*uuid*/, std::shared_ptr<const SwitchMode::Goal> goal)
{
  if (!accepting_.load()) {
    RCLCPP_WARN(node_->get_logger(), "[odin1_control] switch_mode rejected: shutting down");
    return rclcpp_action::GoalResponse::REJECT;
  }
  if (goal->map_mode > 2) {
    RCLCPP_WARN(
      node_->get_logger(), "[odin1_control] switch_mode rejected: map_mode=%u out of range",
      static_cast<unsigned>(goal->map_mode));
    return rclcpp_action::GoalResponse::REJECT;
  }
  if (ctx_.is_map_transfer_in_progress && ctx_.is_map_transfer_in_progress()) {
    RCLCPP_WARN(
      node_->get_logger(),
      "[odin1_control] switch_mode rejected: a map transfer is in progress");
    return rclcpp_action::GoalResponse::REJECT;
  }
  if (goal->map_mode == 2) {
    const std::string existing = ctx_.get_reloc_map_path ? ctx_.get_reloc_map_path() : "";
    if (goal->map_path.empty() && existing.empty()) {
      RCLCPP_WARN(
        node_->get_logger(),
        "[odin1_control] switch_mode rejected: relocalization needs map_path "
        "(no map has been loaded yet)");
      return rclcpp_action::GoalResponse::REJECT;
    }
  }

  // Claim the exclusive slot as the last step before ACCEPT - see saveMapGoal().
  int expected = OP_NONE;
  if (!exclusive_op_.compare_exchange_strong(expected, OP_SWITCH_MODE)) {
    RCLCPP_WARN(
      node_->get_logger(), "[odin1_control] switch_mode rejected: %s already running",
      expected == OP_SAVE_MAP ? "a save_map" : "a switch_mode");
    return rclcpp_action::GoalResponse::REJECT;
  }
  return rclcpp_action::GoalResponse::ACCEPT_AND_EXECUTE;
}

rclcpp_action::CancelResponse ControlServer::switchModeCancel(
  std::shared_ptr<GoalHandleSwitchMode> /*gh*/)
{
  // Cancellation is honoured between steps. It cannot interrupt an SDK call
  // that is already in flight, and it does not roll back completed steps - the
  // device may be left with the stream stopped, which a follow-up switch_mode
  // will recover.
  return rclcpp_action::CancelResponse::ACCEPT;
}

void ControlServer::switchModeAccepted(std::shared_ptr<GoalHandleSwitchMode> gh)
{
  std::thread{std::bind(&ControlServer::switchModeExecute, this, std::placeholders::_1), gh}
  .detach();
}

void ControlServer::switchModeExecute(std::shared_ptr<GoalHandleSwitchMode> gh)
{
  auto result = std::make_shared<SwitchMode::Result>();

  // The exclusive slot was claimed by switchModeGoal().
  struct Guard
  {
    std::atomic<int> & slot;
    ~Guard() {slot.store(OP_NONE);}
  } guard{exclusive_op_};

  try {
    const auto goal = gh->get_goal();
    const int previous_mode = ctx_.get_map_mode ? ctx_.get_map_mode() : -1;
    result->active_map_mode = static_cast<uint8_t>(previous_mode < 0 ? 0 : previous_mode);

    DeviceSession session(*this);
    if (!session.ok()) {
      result->rc = session.rc();
      result->success = false;
      result->failed_stage = "precondition";
      result->message = rcText(session.rc());
      safeAbort(node_->get_logger(), gh, result);
      return;
    }

    std::unique_lock<std::timed_mutex> lock(device_op_mutex_, std::defer_lock);
    if (!lock.try_lock_for(opt_.lock_timeout)) {
      result->rc = RC_BUSY;
      result->success = false;
      result->failed_stage = "precondition";
      result->message = rcText(RC_BUSY);
      safeAbort(node_->get_logger(), gh, result);
      return;
    }

    const int stream_mode = ctx_.get_stream_mode ? ctx_.get_stream_mode() : LIDAR_MODE_SLAM;
    const bool restart = goal->restart_stream;
    const uint8_t total_steps = restart ? 7 : 2;
    uint8_t step = 0;

    auto feedback = [&](const char * stage) {
        auto fb = std::make_shared<SwitchMode::Feedback>();
        fb->stage = stage;
        fb->step = ++step;
        fb->total_steps = total_steps;
        safeFeedback(node_->get_logger(), gh, fb);
        RCLCPP_INFO(
          node_->get_logger(), "[odin1_control] switch_mode %u/%u %s",
          static_cast<unsigned>(fb->step), static_cast<unsigned>(total_steps), stage);
      };

    auto fail = [&](const char * stage, int code, const std::string & msg) {
        result->rc = code;
        result->success = false;
        result->failed_stage = stage;
        result->message = msg;
        result->active_map_mode =
          static_cast<uint8_t>(ctx_.get_map_mode ? std::max(0, ctx_.get_map_mode()) : 0);
        RCLCPP_ERROR(
          node_->get_logger(), "[odin1_control] switch_mode failed at %s rc=%d: %s",
          stage, code, msg.c_str());
        safeAbort(node_->get_logger(), gh, result);
      };

    auto canceled = [&](const char * stage) {
        if (!gh->is_canceling()) {
          return false;
        }
        result->rc = 0;
        result->success = false;
        result->failed_stage = stage;
        result->message = "canceled between steps; the device may be left with the stream "
          "stopped - re-run switch_mode to recover";
        result->active_map_mode =
          static_cast<uint8_t>(ctx_.get_map_mode ? std::max(0, ctx_.get_map_mode()) : 0);
        safeCanceled(node_->get_logger(), gh, result);
        return true;
      };

    // Re-fetch the handle before every SDK call. The driver nulls and recreates
    // odinDevice on each reconnect, so a handle captured at the top of a
    // multi-second sequence can be stale by the time a later step runs.
    device_handle dev = nullptr;
    auto refresh = [&](const char * stage) {
        dev = session.handle();
        if (!dev) {
          fail(stage, session.rc(), rcText(session.rc()));
          return false;
        }
        return true;
      };

    // The device does not support changing map_mode on a live stream (vendor wiki
    // 6.6). The supported sequence is stop -> RAW -> SLAM -> configure -> start,
    // matching the driver's own connect path (host_sdk_sample.cpp:1687-1915).
    if (restart) {
      feedback("stop_stream");
      if (!refresh("stop_stream")) {return;}
      int rc = lidar_stop_stream(dev, stream_mode);
      if (rc != 0) {
        // Non-fatal: the stream may already be stopped (first switch before any
        // start, or after a previous canceled switch).
        RCLCPP_WARN(
          node_->get_logger(),
          "[odin1_control] lidar_stop_stream rc=%d (continuing; stream was probably "
          "already stopped)", rc);
      }
      if (canceled("stop_stream")) {return;}

      feedback("algorithm_off");
      if (!refresh("algorithm_off")) {return;}
      rc = lidar_set_mode(dev, LIDAR_MODE_RAW);
      if (rc != 0) {
        fail("algorithm_off", rc, "lidar_set_mode(LIDAR_MODE_RAW) failed");
        return;
      }
      if (canceled("algorithm_off")) {return;}

      feedback("algorithm_on");
      if (!refresh("algorithm_on")) {return;}
      rc = lidar_set_mode(dev, stream_mode);
      if (rc != 0) {
        fail("algorithm_on", rc, "lidar_set_mode(LIDAR_MODE_SLAM) failed");
        return;
      }
      if (canceled("algorithm_on")) {return;}
    }

    feedback("set_map_mode");
    if (!refresh("set_map_mode")) {return;}
    int rc = setIntParam(dev, kParamMapMode, static_cast<int>(goal->map_mode));
    if (rc != 0) {
      fail("set_map_mode", rc, "failed to set custom parameter map_mode");
      return;
    }
    if (ctx_.set_map_mode) {
      ctx_.set_map_mode(static_cast<int>(goal->map_mode));
    }
    result->active_map_mode = goal->map_mode;
    if (canceled("set_map_mode")) {return;}

    feedback("configure");
    if (!refresh("configure")) {return;}
    if (goal->map_mode == 1) {
      // Mapping: arm the save flag exactly as the driver does on connect
      // (host_sdk_sample.cpp:1714-1732), otherwise the first save_map=1 is a no-op.
      rc = setIntParam(dev, kParamSaveMap, 0);
      if (rc != 0) {
        fail("configure", rc, "failed to initialise save_map = 0");
        return;
      }
    } else if (goal->map_mode == 2) {
      std::string map_path = goal->map_path;
      if (map_path.empty() && ctx_.get_reloc_map_path) {
        map_path = ctx_.get_reloc_map_path();
      }
      std::error_code ec;
      if (map_path.empty() || !std::filesystem::exists(map_path, ec)) {
        fail("configure", RC_INVALID_REQUEST, "relocalization map not found: " + map_path);
        return;
      }
      if (!goal->map_path.empty()) {
        rc = lidar_set_relocalization_map(dev, map_path.c_str());
        if (rc != 0) {
          fail("configure", rc, "lidar_set_relocalization_map failed for " + map_path);
          return;
        }
        if (ctx_.set_reloc_map_path) {
          ctx_.set_reloc_map_path(map_path);
        }
      }
      if (goal->use_init_pose) {
        const auto & p = goal->init_pose.position;
        const auto & q = goal->init_pose.orientation;
        const double pos[3] = {p.x, p.y, p.z};
        const double quat[4] = {q.x, q.y, q.z, q.w};
        std::string msg;
        rc = applyInitPose(dev, pos, quat, goal->search_radius_m, goal->max_rot_deg, msg);
        if (rc != 0) {
          fail("configure", rc, msg.empty() ? "failed to apply init pose" : msg);
          return;
        }
      }
    }
    if (canceled("configure")) {return;}

    if (restart) {
      feedback("start_stream");
      if (!refresh("start_stream")) {return;}
      uint32_t dtof_subframe_odr = 0;
      rc = lidar_start_stream(dev, stream_mode, dtof_subframe_odr);
      if (rc != 0) {
        fail("start_stream", rc, "lidar_start_stream failed - the device is left with the "
          "stream stopped; re-run switch_mode");
        return;
      }

      feedback("activate_streams");
      if (!refresh("activate_streams")) {return;}
      const StreamFlags flags = ctx_.get_stream_flags ? ctx_.get_stream_flags() : StreamFlags{};
      auto apply = [&](bool on, int type) {
          if (on) {
            lidar_activate_stream_type(dev, type);
          } else {
            lidar_deactivate_stream_type(dev, type);
          }
        };
      apply(flags.rgb, LIDAR_DT_RAW_RGB);
      apply(flags.imu, LIDAR_DT_RAW_IMU);
      apply(flags.odom, LIDAR_DT_SLAM_ODOMETRY);
      apply(flags.dtof, LIDAR_DT_RAW_DTOF);
      apply(flags.cloud_slam, LIDAR_DT_SLAM_CLOUD);
    }

    result->rc = 0;
    result->success = true;
    result->failed_stage = "";
    result->message = std::string("switched to ") + mapModeText(goal->map_mode) +
      (restart
      ? " and restarted the stream; odom has been reset, treat this as a hard pose "
        "discontinuity downstream"
      : " (stream not restarted; the device applies the new mode at the next start)");
    RCLCPP_INFO(node_->get_logger(), "[odin1_control] %s", result->message.c_str());
    safeSucceed(node_->get_logger(), gh, result);
  } catch (const std::exception & e) {
    RCLCPP_ERROR(node_->get_logger(), "[odin1_control] switch_mode threw: %s", e.what());
    result->rc = RC_INTERNAL_ERROR;
    result->success = false;
    result->failed_stage = "internal";
    result->message = std::string("internal error: ") + e.what() +
      " - the device may be mid-sequence; re-run switch_mode to resynchronise";
    safeAbort(node_->get_logger(), gh, result);
  } catch (...) {
    RCLCPP_ERROR(node_->get_logger(), "[odin1_control] switch_mode threw a non-std exception");
    result->rc = RC_INTERNAL_ERROR;
    result->success = false;
    result->failed_stage = "internal";
    result->message = "internal error";
    safeAbort(node_->get_logger(), gh, result);
  }
}

}  // namespace odin1_control
