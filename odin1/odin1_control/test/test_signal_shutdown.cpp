// Copyright 2026 Odin1 integration contributors
// Licensed under the Apache License, Version 2.0
//
// Tests for the async-signal-safe SIGINT/SIGTERM front end.
//
// Mostly death tests, because the contract IS "the process ends, and which way
// it ends depends on whether the drain succeeded". The exit codes carry the
// assertion: 42 means the deferred teardown ran, kUndrainableExitCode means it
// was deliberately skipped. They cannot both happen, so the code alone proves
// which branch was taken - no gmock matchers needed.
//
// gtest's death-test style is set to "threadsafe" in main() (test_control_server.cpp)
// because this binary runs worker threads and forking a threaded process is not
// safe; threadsafe style re-execs instead.

#include <chrono>
#include <csignal>
#include <mutex>
#include <thread>

#include <unistd.h>

#include <gtest/gtest.h>

#include "odin1_control/signal_shutdown.hpp"
#include "odin1_control/hard_exit.hpp"

using namespace std::chrono_literals;
using odin1_control::SignalShutdownGuard;

namespace
{
constexpr int kDeferredRanExitCode = 42;
}  // namespace

TEST(SignalShutdown, SuccessfulDrainRunsTheDeferredTeardownOnANormalThread)
{
  EXPECT_EXIT(
    {
      SignalShutdownGuard guard(
        [](std::chrono::milliseconds) {return true;},
        [](int) {
          // Taking a lock here is the point: if this body were still running in
          // a signal context, doing so would be undefined and could deadlock.
          // It is legal only because the guard defers it to a normal thread.
          static std::mutex m;
          std::lock_guard<std::mutex> lock(m);
          ::write(STDERR_FILENO, "DEFERRED_RAN", 12);
          ::_exit(kDeferredRanExitCode);
        },
        100ms);
      ::raise(SIGTERM);
      std::this_thread::sleep_for(10s);   // the guard should end the process first
    },
    ::testing::ExitedWithCode(kDeferredRanExitCode), "DEFERRED_RAN");
}

TEST(SignalShutdown, DrainFailureSkipsTheDeferredTeardownAndHardExits)
{
  // The Ctrl+C-during-save case. The vendor teardown would call
  // lidar_stop_stream() and lidar_system_deinit() underneath a worker that is
  // still inside the SDK, so it must not run at all.
  //
  // Exiting with kUndrainableExitCode rather than kDeferredRanExitCode is the
  // assertion that it did not.
  EXPECT_EXIT(
    {
      SignalShutdownGuard guard(
        [](std::chrono::milliseconds) {return false;},
        [](int) {::_exit(kDeferredRanExitCode);},
        50ms);
      ::raise(SIGINT);
      std::this_thread::sleep_for(10s);
    },
    ::testing::ExitedWithCode(odin1_control::kUndrainableExitCode),
    "Skipping ALL teardown");
}

TEST(SignalShutdown, ASecondSignalExitsStraightFromTheHandler)
{
  // An operator who has waited out the grace period and wants out now. _exit(2)
  // is async-signal-safe, so the handler may do this itself - and it must,
  // because the shutdown thread is stuck waiting on a drain that will not
  // finish.
  EXPECT_EXIT(
    {
      SignalShutdownGuard guard(
        [](std::chrono::milliseconds) {
          std::this_thread::sleep_for(60s);   // a drain that never completes
          return true;
        },
        [](int) {::_exit(kDeferredRanExitCode);},
        60s);
      ::raise(SIGINT);
      std::this_thread::sleep_for(300ms);     // let the first one be forwarded
      ::raise(SIGINT);
      std::this_thread::sleep_for(10s);
    },
    ::testing::ExitedWithCode(odin1_control::kUndrainableExitCode), "second signal");
}

TEST(SignalShutdown, InstallsAndTearsDownCleanlyWithoutASignal)
{
  // Also a deadlock test for the destructor: it has to wake a thread that is
  // blocked in read(2) by closing the write end, then join it.
  bool deferred_ran = false;
  bool drained = false;
  {
    SignalShutdownGuard guard(
      [&drained](std::chrono::milliseconds) {drained = true; return true;},
      [&deferred_ran](int) {deferred_ran = true;},
      10ms);
    EXPECT_TRUE(guard.installed());
  }   // must not hang here
  EXPECT_FALSE(drained) << "no signal was raised";
  EXPECT_FALSE(deferred_ran) << "no signal was raised";
}

TEST(SignalShutdown, ASecondGuardRefusesToInstall)
{
  SignalShutdownGuard first(
    [](std::chrono::milliseconds) {return true;}, [](int) {}, 10ms);
  ASSERT_TRUE(first.installed());

  SignalShutdownGuard second(
    [](std::chrono::milliseconds) {return true;}, [](int) {}, 10ms);
  EXPECT_FALSE(second.installed())
    << "two guards would fight over the file-static handler state";
}
