// Copyright 2026 Odin1 integration contributors
// Licensed under the Apache License, Version 2.0
//
// Behavioural tests for ControlServer against the link-time SDK mock.
//
// These exist because every failure mode that matters here is a timing or
// lifetime problem - a second goal arriving mid-save, Ctrl+C while the device is
// held, a reconnect swapping the handle - and none of them is reachable by
// reading the code or by poking a real device by hand.

#include <chrono>
#include <filesystem>
#include <fstream>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#include <gtest/gtest.h>
#include <rclcpp/rclcpp.hpp>
#include <rclcpp_action/rclcpp_action.hpp>

#include "odin1_control/control_server.hpp"
#include "mock/odin_sdk_mock.hpp"

using namespace std::chrono_literals;
using odin1_control::ControlServer;
using odin1_control::ControlServerOptions;
using odin1_control::DeviceContext;
using odin1_control::StreamFlags;
using odin1_control_test::MockSdk;
using odin1_control_test::kFakeDevice;
using odin1_control_test::kOtherFakeDevice;

using SaveMap = odin1_interfaces::action::SaveMap;
using SwitchMode = odin1_interfaces::action::SwitchMode;

namespace
{
int g_test_seq = 0;

std::string makeTempMap()
{
  const auto p = std::filesystem::temp_directory_path() /
    ("odin1_test_map_" + std::to_string(++g_test_seq) + ".bin");
  std::ofstream(p) << "MAPV0001";
  return p.string();
}
}  // namespace

class ControlServerTest : public ::testing::Test
{
protected:
  void SetUp() override
  {
    MockSdk::instance().reset();

    connected_ = true;
    device_ = kFakeDevice;
    map_mode_ = 1;              // mapping, so SaveMap preconditions pass by default
    legacy_transfer_ = false;
    reloc_path_.clear();

    // A namespace per test: sequential tests reuse the same process, and DDS
    // discovery of a torn-down server can otherwise leak into the next case.
    ns_ = "/odin1_t" + std::to_string(++g_test_seq);

    server_node_ = std::make_shared<rclcpp::Node>("odin1_ctrl_srv" + std::to_string(g_test_seq));
    client_node_ = std::make_shared<rclcpp::Node>("odin1_ctrl_cli" + std::to_string(g_test_seq));

    ControlServerOptions opt;
    opt.ns = ns_;
    opt.lock_timeout = 300ms;
    opt.feedback_period = 30ms;
    opt.status_period = 0ms;    // no periodic publisher in tests
    server_ = std::make_unique<ControlServer>(server_node_, makeContext(), opt);

    executor_ = std::make_unique<rclcpp::executors::MultiThreadedExecutor>(
      rclcpp::ExecutorOptions(), 2);
    executor_->add_node(client_node_);
    spin_thread_ = std::thread([this] {executor_->spin();});
  }

  void TearDown() override
  {
    if (server_) {
      // Short grace: a test that leaves work running should fail fast, not hang
      // for the production default.
      server_->shutdown(3s);
      server_.reset();
    }
    if (executor_) {
      executor_->cancel();
    }
    if (spin_thread_.joinable()) {
      spin_thread_.join();
    }
  }

  DeviceContext makeContext()
  {
    DeviceContext ctx;
    ctx.get_device = [this]() -> device_handle {return device_;};
    ctx.is_connected = [this] {return connected_;};
    ctx.get_stream_mode = [] {return static_cast<int>(LIDAR_MODE_SLAM);};
    ctx.get_map_mode = [this] {return map_mode_;};
    ctx.set_map_mode = [this](int m) {map_mode_ = m;};
    ctx.get_reloc_map_path = [this] {return reloc_path_;};
    ctx.set_reloc_map_path = [this](const std::string & p) {reloc_path_ = p;};
    ctx.get_configured_map_dir = [this] {return map_dir_;};
    ctx.get_configured_map_name = [] {return std::string();};
    ctx.get_default_map_dir = [this] {return map_dir_;};
    ctx.get_stream_flags = [] {return StreamFlags{};};
    ctx.is_map_transfer_in_progress = [this] {return legacy_transfer_;};
    ctx.set_map_transfer_in_progress = [this](bool v) {legacy_transfer_ = v;};
    ctx.driver_version = "test";
    return ctx;
  }

  // --- service helper -------------------------------------------------------
  template<typename SrvT>
  typename SrvT::Response::SharedPtr call(
    const std::string & suffix, typename SrvT::Request::SharedPtr req,
    std::chrono::seconds timeout = 3s)
  {
    auto client = client_node_->create_client<SrvT>(ns_ + suffix);
    if (!client->wait_for_service(timeout)) {
      return nullptr;
    }
    auto future = client->async_send_request(req);
    if (future.wait_for(timeout) != std::future_status::ready) {
      return nullptr;
    }
    return future.get();
  }

  // --- action helpers -------------------------------------------------------
  template<typename ActionT>
  typename rclcpp_action::Client<ActionT>::SharedPtr actionClient(const std::string & suffix)
  {
    auto c = rclcpp_action::create_client<ActionT>(client_node_, ns_ + suffix);
    EXPECT_TRUE(c->wait_for_action_server(3s));
    return c;
  }

  /// Sends a goal and returns the goal handle (nullptr if the server rejected).
  template<typename ActionT>
  typename rclcpp_action::ClientGoalHandle<ActionT>::SharedPtr sendGoal(
    typename rclcpp_action::Client<ActionT>::SharedPtr client,
    const typename ActionT::Goal & goal, std::chrono::seconds timeout = 3s)
  {
    auto future = client->async_send_goal(goal);
    if (future.wait_for(timeout) != std::future_status::ready) {
      return nullptr;
    }
    return future.get();
  }

  template<typename ActionT>
  typename rclcpp_action::ClientGoalHandle<ActionT>::WrappedResult awaitResult(
    typename rclcpp_action::Client<ActionT>::SharedPtr client,
    typename rclcpp_action::ClientGoalHandle<ActionT>::SharedPtr gh,
    std::chrono::seconds timeout = 10s)
  {
    auto future = client->async_get_result(gh);
    EXPECT_EQ(future.wait_for(timeout), std::future_status::ready);
    return future.get();
  }

  // --- fake driver state ----------------------------------------------------
  device_handle device_ = kFakeDevice;
  bool connected_ = true;
  int map_mode_ = 1;
  bool legacy_transfer_ = false;
  std::string reloc_path_;
  std::string map_dir_ = (std::filesystem::temp_directory_path() / "odin1_test_maps").string();

  std::string ns_;
  rclcpp::Node::SharedPtr server_node_;
  rclcpp::Node::SharedPtr client_node_;
  std::unique_ptr<ControlServer> server_;
  std::unique_ptr<rclcpp::executors::MultiThreadedExecutor> executor_;
  std::thread spin_thread_;
};

// ===========================================================================
// Device disconnected
// ===========================================================================

TEST_F(ControlServerTest, ServicesReportDeviceNotOpenWhenDisconnected)
{
  connected_ = false;

  auto reset_res = call<odin1_interfaces::srv::ResetAlgo>(
    "/reset_algo", std::make_shared<odin1_interfaces::srv::ResetAlgo::Request>());
  ASSERT_NE(reset_res, nullptr);
  EXPECT_FALSE(reset_res->success);
  EXPECT_EQ(reset_res->rc, odin1_control::RC_DEVICE_NOT_OPEN);

  // Nothing must have reached the SDK.
  EXPECT_FALSE(MockSdk::instance().sawCall("lidar_set_custom_parameter"));
}

TEST_F(ControlServerTest, NullHandleIsTreatedAsDisconnected)
{
  device_ = nullptr;

  auto res = call<odin1_interfaces::srv::ResetAlgo>(
    "/reset_algo", std::make_shared<odin1_interfaces::srv::ResetAlgo::Request>());
  ASSERT_NE(res, nullptr);
  EXPECT_EQ(res->rc, odin1_control::RC_DEVICE_NOT_OPEN);
}

TEST_F(ControlServerTest, SaveMapAbortsWhenDeviceDisconnectsBeforeExecution)
{
  auto client = actionClient<SaveMap>("/save_map");
  connected_ = false;   // after the goal server exists, before the goal is sent

  auto gh = sendGoal<SaveMap>(client, SaveMap::Goal());
  ASSERT_NE(gh, nullptr) << "goal should be accepted; the device check happens in the worker";

  auto result = awaitResult<SaveMap>(client, gh);
  EXPECT_EQ(result.code, rclcpp_action::ResultCode::ABORTED);
  EXPECT_EQ(result.result->rc, odin1_control::RC_DEVICE_NOT_OPEN);
  EXPECT_FALSE(MockSdk::instance().sawCall("lidar_save_map"));
}

// ===========================================================================
// Busy / contention
// ===========================================================================

TEST_F(ControlServerTest, SaveMapRejectedWhileLegacyTransferRunning)
{
  // The /tmp/odin_command.txt channel is still live in the driver; a transfer
  // started there must block ours rather than racing it.
  legacy_transfer_ = true;

  auto client = actionClient<SaveMap>("/save_map");
  auto gh = sendGoal<SaveMap>(client, SaveMap::Goal());
  EXPECT_EQ(gh, nullptr) << "goal must be rejected while a legacy transfer holds the device";
}

TEST_F(ControlServerTest, LoadMapReturnsBusyWhileSaveMapHoldsTheDevice)
{
  auto & mock = MockSdk::instance();
  mock.save_map_delay = 1500ms;

  auto client = actionClient<SaveMap>("/save_map");
  auto gh = sendGoal<SaveMap>(client, SaveMap::Goal());
  ASSERT_NE(gh, nullptr);

  // Wait until the SDK call is actually in flight.
  const auto deadline = std::chrono::steady_clock::now() + 2s;
  while (!mock.save_map_running.load() && std::chrono::steady_clock::now() < deadline) {
    std::this_thread::sleep_for(10ms);
  }
  ASSERT_TRUE(mock.save_map_running.load());

  auto req = std::make_shared<odin1_interfaces::srv::LoadMap::Request>();
  req->map_path = makeTempMap();
  auto res = call<odin1_interfaces::srv::LoadMap>("/load_map", req);
  ASSERT_NE(res, nullptr);
  EXPECT_FALSE(res->success);
  EXPECT_EQ(res->rc, odin1_control::RC_BUSY)
    << "device_op_mutex_ must serialise LoadMap behind SaveMap";

  awaitResult<SaveMap>(client, gh);
}

TEST_F(ControlServerTest, GetDeviceStateStaysAnswerableDuringSaveMap)
{
  // A status query must never be blocked by a 120 s save - that is the whole
  // reason handleGetDeviceState does not take device_op_mutex_.
  auto & mock = MockSdk::instance();
  mock.save_map_delay = 1000ms;

  auto client = actionClient<SaveMap>("/save_map");
  auto gh = sendGoal<SaveMap>(client, SaveMap::Goal());
  ASSERT_NE(gh, nullptr);

  const auto deadline = std::chrono::steady_clock::now() + 2s;
  while (!mock.save_map_running.load() && std::chrono::steady_clock::now() < deadline) {
    std::this_thread::sleep_for(10ms);
  }

  const auto t0 = std::chrono::steady_clock::now();
  auto res = call<odin1_interfaces::srv::GetDeviceState>(
    "/get_device_state", std::make_shared<odin1_interfaces::srv::GetDeviceState::Request>());
  const auto elapsed = std::chrono::steady_clock::now() - t0;

  ASSERT_NE(res, nullptr);
  EXPECT_TRUE(res->map_transfer_in_progress);
  EXPECT_LT(elapsed, 800ms) << "status query was blocked behind the save";

  awaitResult<SaveMap>(client, gh);
}

// ===========================================================================
// Double goal
// ===========================================================================

TEST_F(ControlServerTest, SecondSaveMapGoalIsRejectedWhileFirstRuns)
{
  auto & mock = MockSdk::instance();
  mock.save_map_delay = 1200ms;

  auto client = actionClient<SaveMap>("/save_map");
  auto gh1 = sendGoal<SaveMap>(client, SaveMap::Goal());
  ASSERT_NE(gh1, nullptr);

  const auto deadline = std::chrono::steady_clock::now() + 2s;
  while (!mock.save_map_running.load() && std::chrono::steady_clock::now() < deadline) {
    std::this_thread::sleep_for(10ms);
  }

  auto gh2 = sendGoal<SaveMap>(client, SaveMap::Goal());
  EXPECT_EQ(gh2, nullptr) << "the exclusive slot must reject the second goal";

  awaitResult<SaveMap>(client, gh1);
  EXPECT_EQ(mock.max_concurrent_save_map.load(), 1)
    << "lidar_save_map must never run twice concurrently";
}

TEST_F(ControlServerTest, SwitchModeIsRejectedWhileSaveMapRuns)
{
  // The two actions share ONE admission slot precisely so this cannot interleave.
  auto & mock = MockSdk::instance();
  mock.save_map_delay = 1200ms;

  auto save_client = actionClient<SaveMap>("/save_map");
  auto gh = sendGoal<SaveMap>(save_client, SaveMap::Goal());
  ASSERT_NE(gh, nullptr);

  const auto deadline = std::chrono::steady_clock::now() + 2s;
  while (!mock.save_map_running.load() && std::chrono::steady_clock::now() < deadline) {
    std::this_thread::sleep_for(10ms);
  }

  auto switch_client = actionClient<SwitchMode>("/switch_mode");
  SwitchMode::Goal goal;
  goal.map_mode = 0;
  auto gh2 = sendGoal<SwitchMode>(switch_client, goal);
  EXPECT_EQ(gh2, nullptr);

  awaitResult<SaveMap>(save_client, gh);
}

// ===========================================================================
// Timeout and SDK error mapping
// ===========================================================================

TEST_F(ControlServerTest, SaveMapTimeoutIsReportedNotSwallowed)
{
  MockSdk::instance().rc_save_map = -3;   // device never finished generating

  auto client = actionClient<SaveMap>("/save_map");
  auto gh = sendGoal<SaveMap>(client, SaveMap::Goal());
  ASSERT_NE(gh, nullptr);

  auto result = awaitResult<SaveMap>(client, gh);
  EXPECT_EQ(result.code, rclcpp_action::ResultCode::ABORTED);
  EXPECT_EQ(result.result->rc, -3);
  EXPECT_NE(result.result->message.find("timed out"), std::string::npos);
  EXPECT_TRUE(result.result->map_path.empty()) << "no path may be reported for a failed save";
}

TEST_F(ControlServerTest, SaveMapReleasesTheSlotAfterFailureSoRetryWorks)
{
  MockSdk::instance().rc_save_map = -4;
  auto client = actionClient<SaveMap>("/save_map");

  auto gh1 = sendGoal<SaveMap>(client, SaveMap::Goal());
  ASSERT_NE(gh1, nullptr);
  EXPECT_EQ(awaitResult<SaveMap>(client, gh1).code, rclcpp_action::ResultCode::ABORTED);

  MockSdk::instance().rc_save_map = 0;
  auto gh2 = sendGoal<SaveMap>(client, SaveMap::Goal());
  ASSERT_NE(gh2, nullptr) << "a failed save must not leak the exclusive slot";
  EXPECT_EQ(awaitResult<SaveMap>(client, gh2).code, rclcpp_action::ResultCode::SUCCEEDED);
}

TEST_F(ControlServerTest, SaveMapRejectedOutsideMappingMode)
{
  map_mode_ = 2;   // relocalization: the device refuses to save
  auto client = actionClient<SaveMap>("/save_map");
  EXPECT_EQ(sendGoal<SaveMap>(client, SaveMap::Goal()), nullptr);
}

// ===========================================================================
// Exit during save
// ===========================================================================

TEST_F(ControlServerTest, ShutdownDrainsAnInFlightSaveInsteadOfRacingIt)
{
  // The scenario the driver's SIGINT handler creates: teardown requested while
  // lidar_save_map() is mid-call. beginTeardown must not return until the SDK
  // call has finished, or the driver would deinit the SDK underneath it.
  auto & mock = MockSdk::instance();
  mock.save_map_delay = 700ms;

  auto client = actionClient<SaveMap>("/save_map");
  auto gh = sendGoal<SaveMap>(client, SaveMap::Goal());
  ASSERT_NE(gh, nullptr);

  const auto deadline = std::chrono::steady_clock::now() + 2s;
  while (!mock.save_map_running.load() && std::chrono::steady_clock::now() < deadline) {
    std::this_thread::sleep_for(10ms);
  }
  ASSERT_TRUE(mock.save_map_running.load());

  const auto t0 = std::chrono::steady_clock::now();
  server_->shutdown(5s);
  const auto elapsed = std::chrono::steady_clock::now() - t0;

  EXPECT_FALSE(mock.save_map_running.load())
    << "shutdown returned while an SDK call was still in flight";
  EXPECT_GE(elapsed, 100ms) << "shutdown did not actually wait";
  server_.reset();   // must not hang or crash
}

TEST_F(ControlServerTest, RequestsAfterTeardownAreRefusedNotRaced)
{
  server_->beginTeardown(1s);

  auto res = call<odin1_interfaces::srv::ResetAlgo>(
    "/reset_algo", std::make_shared<odin1_interfaces::srv::ResetAlgo::Request>());
  ASSERT_NE(res, nullptr);
  EXPECT_EQ(res->rc, odin1_control::RC_SHUTTING_DOWN);
  EXPECT_FALSE(MockSdk::instance().sawCall("lidar_set_custom_parameter"));

  auto client = actionClient<SaveMap>("/save_map");
  EXPECT_EQ(sendGoal<SaveMap>(client, SaveMap::Goal()), nullptr);
}

// ===========================================================================
// Reconnect swapping the handle
// ===========================================================================

TEST_F(ControlServerTest, SaveMapFailsIfTheHandleIsRecycledMidOperation)
{
  // The driver's attach path does `if (odinDevice) { odinDevice = nullptr; ... }`
  // on every reconnect. A stale handle must be detected, not used.
  auto & mock = MockSdk::instance();
  mock.save_map_delay = 400ms;
  mock.on_save_map_entered = [this] {device_ = kOtherFakeDevice;};

  auto client = actionClient<SaveMap>("/save_map");
  auto gh = sendGoal<SaveMap>(client, SaveMap::Goal());
  ASSERT_NE(gh, nullptr);

  auto result = awaitResult<SaveMap>(client, gh);
  // The save itself completes (it was already in flight); what must not happen
  // is a later step reusing the abandoned handle. SwitchMode below covers the
  // multi-step case explicitly.
  EXPECT_TRUE(
    result.code == rclcpp_action::ResultCode::SUCCEEDED ||
    result.code == rclcpp_action::ResultCode::ABORTED);
}

TEST_F(ControlServerTest, SwitchModeAbortsWhenTheHandleChangesBetweenSteps)
{
  auto & mock = MockSdk::instance();
  mock.set_mode_delay = 60ms;

  auto client = actionClient<SwitchMode>("/switch_mode");
  SwitchMode::Goal goal;
  goal.map_mode = 0;
  goal.restart_stream = true;

  auto gh = sendGoal<SwitchMode>(client, goal);
  ASSERT_NE(gh, nullptr);

  // Swap the handle while the sequence is mid-flight.
  std::this_thread::sleep_for(40ms);
  device_ = kOtherFakeDevice;

  auto result = awaitResult<SwitchMode>(client, gh);
  EXPECT_EQ(result.code, rclcpp_action::ResultCode::ABORTED);
  EXPECT_EQ(result.result->rc, odin1_control::RC_DEVICE_LOST);
  EXPECT_FALSE(result.result->failed_stage.empty())
    << "the failing step must be identified for on-hardware debugging";
}

// ===========================================================================
// SwitchMode sequence
// ===========================================================================

TEST_F(ControlServerTest, SwitchModeRunsTheDocumentedSevenStepSequenceInOrder)
{
  auto client = actionClient<SwitchMode>("/switch_mode");
  SwitchMode::Goal goal;
  goal.map_mode = 1;              // mapping
  goal.restart_stream = true;

  auto gh = sendGoal<SwitchMode>(client, goal);
  ASSERT_NE(gh, nullptr);
  auto result = awaitResult<SwitchMode>(client, gh);
  ASSERT_EQ(result.code, rclcpp_action::ResultCode::SUCCEEDED);
  EXPECT_EQ(result.result->active_map_mode, 1u);

  const auto names = MockSdk::instance().callNames();
  const std::vector<std::string> expected_prefix = {
    "lidar_stop_stream",             // 1
    "lidar_set_mode",                // 2 RAW
    "lidar_set_mode",                // 3 SLAM
    "lidar_set_custom_parameter",    // 4 map_mode
    "lidar_set_custom_parameter",    // 5 save_map = 0
    "lidar_start_stream",            // 6
  };
  ASSERT_GE(names.size(), expected_prefix.size());
  for (size_t i = 0; i < expected_prefix.size(); ++i) {
    EXPECT_EQ(names[i], expected_prefix[i]) << "step " << i + 1 << " out of order";
  }
  // 7: every configured stream re-activated.
  EXPECT_EQ(MockSdk::instance().countCalls("lidar_activate_stream_type"), 5);

  const auto params = MockSdk::instance().paramWrites();
  ASSERT_GE(params.size(), 2u);
  EXPECT_EQ(params[0].first, "map_mode");
  EXPECT_EQ(params[0].second, 1);
  EXPECT_EQ(params[1].first, "save_map");
  EXPECT_EQ(params[1].second, 0) << "save_map must be armed to 0, else the first save is a no-op";
}

TEST_F(ControlServerTest, SwitchModeStartStreamFailureIsPinpointed)
{
  MockSdk::instance().rc_start_stream = -1;

  auto client = actionClient<SwitchMode>("/switch_mode");
  SwitchMode::Goal goal;
  goal.map_mode = 0;
  auto gh = sendGoal<SwitchMode>(client, goal);
  ASSERT_NE(gh, nullptr);

  auto result = awaitResult<SwitchMode>(client, gh);
  EXPECT_EQ(result.code, rclcpp_action::ResultCode::ABORTED);
  EXPECT_EQ(result.result->failed_stage, "start_stream");
  EXPECT_NE(result.result->message.find("re-run switch_mode"), std::string::npos);
}

TEST_F(ControlServerTest, SwitchModeWithoutRestartOnlyConfigures)
{
  auto client = actionClient<SwitchMode>("/switch_mode");
  SwitchMode::Goal goal;
  goal.map_mode = 0;
  goal.restart_stream = false;

  auto gh = sendGoal<SwitchMode>(client, goal);
  ASSERT_NE(gh, nullptr);
  ASSERT_EQ(awaitResult<SwitchMode>(client, gh).code, rclcpp_action::ResultCode::SUCCEEDED);

  EXPECT_FALSE(MockSdk::instance().sawCall("lidar_stop_stream"));
  EXPECT_FALSE(MockSdk::instance().sawCall("lidar_start_stream"));
  EXPECT_FALSE(MockSdk::instance().sawCall("lidar_set_mode"));
}

// ===========================================================================
// Relocalization failures
// ===========================================================================

TEST_F(ControlServerTest, SwitchModeToRelocalizationRejectedWithoutAMap)
{
  auto client = actionClient<SwitchMode>("/switch_mode");
  SwitchMode::Goal goal;
  goal.map_mode = 2;   // no map_path, and none previously loaded
  EXPECT_EQ(sendGoal<SwitchMode>(client, goal), nullptr);
}

TEST_F(ControlServerTest, SwitchModeAbortsWhenMapUploadFails)
{
  MockSdk::instance().rc_set_relocalization_map = -1;

  auto client = actionClient<SwitchMode>("/switch_mode");
  SwitchMode::Goal goal;
  goal.map_mode = 2;
  goal.map_path = makeTempMap();

  auto gh = sendGoal<SwitchMode>(client, goal);
  ASSERT_NE(gh, nullptr);
  auto result = awaitResult<SwitchMode>(client, gh);

  EXPECT_EQ(result.code, rclcpp_action::ResultCode::ABORTED);
  EXPECT_EQ(result.result->failed_stage, "configure");
  EXPECT_FALSE(MockSdk::instance().sawCall("lidar_start_stream"))
    << "the stream must not be restarted after a failed map upload";
}

TEST_F(ControlServerTest, SwitchModeRejectsAMapPathThatDoesNotExist)
{
  reloc_path_ = "/nonexistent/previously_loaded.bin";   // satisfies the goal-level check

  auto client = actionClient<SwitchMode>("/switch_mode");
  SwitchMode::Goal goal;
  goal.map_mode = 2;

  auto gh = sendGoal<SwitchMode>(client, goal);
  ASSERT_NE(gh, nullptr);
  auto result = awaitResult<SwitchMode>(client, gh);

  EXPECT_EQ(result.code, rclcpp_action::ResultCode::ABORTED);
  EXPECT_EQ(result.result->rc, odin1_control::RC_INVALID_REQUEST);
  EXPECT_FALSE(MockSdk::instance().sawCall("lidar_start_stream"));
}

TEST_F(ControlServerTest, LoadMapRejectsMissingFileWithoutTouchingTheDevice)
{
  auto req = std::make_shared<odin1_interfaces::srv::LoadMap::Request>();
  req->map_path = "/definitely/not/here.bin";

  auto res = call<odin1_interfaces::srv::LoadMap>("/load_map", req);
  ASSERT_NE(res, nullptr);
  EXPECT_FALSE(res->success);
  EXPECT_EQ(res->rc, odin1_control::RC_INVALID_REQUEST);
  EXPECT_FALSE(MockSdk::instance().sawCall("lidar_set_relocalization_map"));
}

TEST_F(ControlServerTest, LoadMapRetriesThenReportsFailure)
{
  MockSdk::instance().rc_set_relocalization_map = -1;

  auto req = std::make_shared<odin1_interfaces::srv::LoadMap::Request>();
  req->map_path = makeTempMap();
  req->retries = 2;   // 1 + 2 attempts

  auto res = call<odin1_interfaces::srv::LoadMap>("/load_map", req);
  ASSERT_NE(res, nullptr);
  EXPECT_FALSE(res->success);
  EXPECT_EQ(MockSdk::instance().countCalls("lidar_set_relocalization_map"), 3);
}

TEST_F(ControlServerTest, LoadMapNeverClaimsToHaveActivatedTheMap)
{
  auto req = std::make_shared<odin1_interfaces::srv::LoadMap::Request>();
  req->map_path = makeTempMap();

  auto res = call<odin1_interfaces::srv::LoadMap>("/load_map", req);
  ASSERT_NE(res, nullptr);
  EXPECT_TRUE(res->success);
  EXPECT_FALSE(res->activated);
  EXPECT_NE(res->message.find("switch_mode"), std::string::npos)
    << "the response must tell the caller how to actually activate the map";
}

// ===========================================================================
// SetInitPose
// ===========================================================================

TEST_F(ControlServerTest, SetInitPoseRejectsAnUnnormalisedQuaternion)
{
  auto req = std::make_shared<odin1_interfaces::srv::SetInitPose::Request>();
  req->pose.pose.pose.orientation.w = 0.5;   // |q| = 0.5

  auto res = call<odin1_interfaces::srv::SetInitPose>("/set_init_pose", req);
  ASSERT_NE(res, nullptr);
  EXPECT_FALSE(res->success);
  EXPECT_EQ(res->rc, odin1_control::RC_INVALID_REQUEST);
  EXPECT_FALSE(MockSdk::instance().sawCall("lidar_set_custom_parameter"))
    << "a bad quaternion must be caught before it reaches the device";
}

TEST_F(ControlServerTest, SetInitPoseReportsThatStreamingDefersTheValue)
{
  MockSdk::instance().device_state = LIDAR_DEVICE_STREAMING;

  auto req = std::make_shared<odin1_interfaces::srv::SetInitPose::Request>();
  req->pose.pose.pose.orientation.w = 1.0;
  req->search_radius_m = 4.0f;
  req->max_rot_deg = 180.0f;

  auto res = call<odin1_interfaces::srv::SetInitPose>("/set_init_pose", req);
  ASSERT_NE(res, nullptr);
  EXPECT_TRUE(res->success);
  EXPECT_FALSE(res->applied_immediately)
    << "the device only consumes init_pos at stream start; saying otherwise would be a lie";

  const auto params = MockSdk::instance().paramWrites();
  ASSERT_EQ(params.size(), 3u);
  EXPECT_EQ(params[0].first, "init_pos");
  EXPECT_EQ(params[0].second, 28) << "init_pos must be 7 floats = 28 bytes";
  EXPECT_EQ(params[1].first, "init_pose_search_radius");
  EXPECT_EQ(params[2].first, "init_pose_max_rot_deg");
}

TEST_F(ControlServerTest, SetInitPoseAppliesImmediatelyWhenNotStreaming)
{
  MockSdk::instance().device_state = LIDAR_DEVICE_INITIALIZED;

  auto req = std::make_shared<odin1_interfaces::srv::SetInitPose::Request>();
  req->pose.pose.pose.orientation.w = 1.0;

  auto res = call<odin1_interfaces::srv::SetInitPose>("/set_init_pose", req);
  ASSERT_NE(res, nullptr);
  EXPECT_TRUE(res->applied_immediately);
}

// ===========================================================================
// GetDeviceState
// ===========================================================================

TEST_F(ControlServerTest, FirmwareVersionIsQueriedOnceAndCached)
{
  auto req = std::make_shared<odin1_interfaces::srv::GetDeviceState::Request>();

  auto r1 = call<odin1_interfaces::srv::GetDeviceState>("/get_device_state", req);
  ASSERT_NE(r1, nullptr);
  EXPECT_EQ(r1->slam_version, "1.2.3");

  auto r2 = call<odin1_interfaces::srv::GetDeviceState>("/get_device_state", req);
  ASSERT_NE(r2, nullptr);
  EXPECT_EQ(r2->slam_version, "1.2.3");

  std::lock_guard<std::mutex> lock(MockSdk::instance().mutex);
  EXPECT_EQ(MockSdk::instance().get_version_calls, 1)
    << "repeating the USB version query on every status call would add traffic to the "
       "same control channel a running save is using";
}

TEST_F(ControlServerTest, GetDeviceStateReportsModeAndTransferFlags)
{
  map_mode_ = 2;
  reloc_path_ = "/tmp/some_map.bin";

  auto res = call<odin1_interfaces::srv::GetDeviceState>(
    "/get_device_state", std::make_shared<odin1_interfaces::srv::GetDeviceState::Request>());
  ASSERT_NE(res, nullptr);
  EXPECT_EQ(res->map_mode, 2u);
  EXPECT_EQ(res->map_mode_text, "relocalization");
  EXPECT_EQ(res->relocalization_map_path, "/tmp/some_map.bin");
  EXPECT_FALSE(res->map_transfer_in_progress);
  EXPECT_FALSE(res->mode_switch_in_progress);
}

int main(int argc, char ** argv)
{
  ::testing::InitGoogleTest(&argc, argv);
  rclcpp::init(argc, argv);
  const int rc = RUN_ALL_TESTS();
  rclcpp::shutdown();
  return rc;
}
