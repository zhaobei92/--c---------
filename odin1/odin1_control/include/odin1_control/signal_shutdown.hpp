// Copyright 2026 Odin1 integration contributors
// Licensed under the Apache License, Version 2.0
//
// Async-signal-safe SIGINT/SIGTERM front end.
//
// WHY
// ---
// A signal handler may only call functions on POSIX's async-signal-safe list.
// mutexes, condition variables, malloc, iostreams, the ROS logger and the Odin
// SDK are all off that list, and the reason is not pedantry: the handler runs on
// whichever thread happened to be interrupted, so if that thread already holds
// the lock the handler wants, the handler deadlocks and the process hangs
// forever with no output.
//
// That is not hypothetical here. The vendor's handler
// (host_sdk_sample.cpp:319) logs with RCLCPP_*, calls fclose(), dereferences C++
// objects, calls into the SDK and ends with exit() - which then runs static
// destructors on top of everything. Earlier revisions of this package made it
// worse by calling ControlServer::beginTeardown() from inside it: that takes
// idle_mutex_ and waits on idle_cv_, and ~DeviceSession takes the same mutex on
// the worker thread. A Ctrl+C landing in that window self-deadlocks - precisely
// in the "save is running" case the call was added to protect.
//
// HOW
// ---
// The installed handler does exactly two async-signal-safe things:
//
//     g_signal_number = sig;                  // volatile sig_atomic_t store
//     write(self_pipe_write_fd, &byte, 1);    // write(2)
//
// (plus errno save/restore, and _exit(2) on a repeated signal). Everything else
// - draining in-flight device work, the vendor's SDK teardown, the timeout hard
// exit - runs on a NORMAL thread that sits blocked in read(2) on the other end
// of the pipe, where mutexes and logging are perfectly legal.
//
// A pleasant side effect: the vendor's handler body becomes safe too, because it
// is no longer executed in a signal context - this guard calls it as an ordinary
// function from the shutdown thread.

#ifndef ODIN1_CONTROL__SIGNAL_SHUTDOWN_HPP_
#define ODIN1_CONTROL__SIGNAL_SHUTDOWN_HPP_

#include <chrono>
#include <functional>
#include <thread>

namespace odin1_control
{

class SignalShutdownGuard
{
public:
  /// Drains in-flight device work. Returns false if it could not, in which case
  /// the guard hard-exits WITHOUT running `deferred`. Runs on the shutdown
  /// thread, so it may block, lock and log.
  using DrainFn = std::function<bool (std::chrono::milliseconds)>;

  /// The real teardown - in production, the vendor's own signal_handler().
  /// Runs on the shutdown thread. Expected not to return (the vendor's calls
  /// exit()); if it does return, the guard exits the process anyway.
  using DeferredFn = std::function<void (int signal_number)>;

  /// Installs handlers for SIGINT and SIGTERM, replacing whatever was there.
  /// Only one guard may exist at a time; a second one refuses to install and
  /// says so on stderr.
  ///
  /// Order inside the constructor matters and is deliberate: pipe first, then
  /// the thread, and only then the handlers - so a signal can never arrive
  /// before there is something to receive it.
  SignalShutdownGuard(
    DrainFn drain, DeferredFn deferred,
    std::chrono::milliseconds grace = std::chrono::seconds(3));

  ~SignalShutdownGuard();

  SignalShutdownGuard(const SignalShutdownGuard &) = delete;
  SignalShutdownGuard & operator=(const SignalShutdownGuard &) = delete;

  /// True when the handlers were actually installed.
  bool installed() const {return installed_;}

private:
  void run();
  void uninstall();

  DrainFn drain_;
  DeferredFn deferred_;
  std::chrono::milliseconds grace_;
  std::thread thread_;
  bool installed_ = false;
};

}  // namespace odin1_control

#endif  // ODIN1_CONTROL__SIGNAL_SHUTDOWN_HPP_
