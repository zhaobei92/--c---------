// Copyright 2026 Odin1 integration contributors
// Licensed under the Apache License, Version 2.0

#include "odin1_control/signal_shutdown.hpp"

#include <atomic>
#include <cerrno>
#include <csignal>
#include <utility>

#include <fcntl.h>
#include <unistd.h>

#include "odin1_control/hard_exit.hpp"

namespace odin1_control
{
namespace
{

// ---------------------------------------------------------------------------
// State reachable from the signal handler.
//
// Only these three objects may be touched there, and only in the ways the
// handler below touches them. Anything else - a std::function, a mutex, the
// logger - is off limits by construction because the handler cannot see it.
// ---------------------------------------------------------------------------

/// Write end of the self-pipe. Plain int: the handler only passes it to write().
volatile sig_atomic_t g_pipe_write_fd = -1;
/// Which signal arrived. sig_atomic_t is the only type a handler may store to.
volatile sig_atomic_t g_signal_number = 0;
/// Set once the first signal has been forwarded; a second one exits immediately.
volatile sig_atomic_t g_signal_seen = 0;

/// Guards against installing two guards at once.
std::atomic<bool> g_installed{false};

/// Async-signal-safe write of a NUL-terminated literal. Used only for the
/// second-signal path, where there is nothing left to defer to.
void emitRaw(const char * s)
{
  size_t n = 0;
  while (s[n] != '\0') {++n;}
  ssize_t done = 0;
  while (done < static_cast<ssize_t>(n)) {
    const ssize_t w = ::write(STDERR_FILENO, s + done, n - done);
    if (w <= 0) {break;}
    done += w;
  }
}

// ---------------------------------------------------------------------------
// THE HANDLER
//
// Everything it calls must be on POSIX's async-signal-safe list:
//   write(2), _exit(2), and stores to volatile sig_atomic_t.
// Nothing else belongs here. No logging, no locks, no SDK, no C++ objects.
// ---------------------------------------------------------------------------
extern "C" void odin1SignalTrampoline(int sig)
{
  const int saved_errno = errno;      // a handler must not clobber errno

  g_signal_number = sig;

  if (g_signal_seen) {
    // Second Ctrl+C: the operator has waited through the grace period and wants
    // out now. _exit(2) is async-signal-safe and skips every destructor, which
    // is exactly what we want while a worker may still be inside the SDK.
    emitRaw("\n[odin1_control] second signal - exiting immediately.\n");
    ::_exit(kUndrainableExitCode);
  }
  g_signal_seen = 1;

  const int fd = static_cast<int>(g_pipe_write_fd);
  if (fd >= 0) {
    const unsigned char byte = 1;
    ssize_t w;
    do {
      w = ::write(fd, &byte, 1);
    } while (w < 0 && errno == EINTR);
    // A full pipe (EAGAIN) is fine: one byte is all the reader needs.
    (void)w;
  }

  errno = saved_errno;
}

int g_pipe_read_fd = -1;

}  // namespace

SignalShutdownGuard::SignalShutdownGuard(
  DrainFn drain, DeferredFn deferred, std::chrono::milliseconds grace)
: drain_(std::move(drain)), deferred_(std::move(deferred)), grace_(grace)
{
  bool expected = false;
  if (!g_installed.compare_exchange_strong(expected, true)) {
    emitRaw("[odin1_control] a SignalShutdownGuard is already installed; ignoring.\n");
    return;
  }

  int fds[2] = {-1, -1};
  if (::pipe(fds) != 0) {
    emitRaw("[odin1_control] could not create the shutdown self-pipe.\n");
    g_installed = false;
    return;
  }
  ::fcntl(fds[0], F_SETFD, FD_CLOEXEC);
  ::fcntl(fds[1], F_SETFD, FD_CLOEXEC);
  // Non-blocking write end so the handler can never block, however unlikely.
  ::fcntl(fds[1], F_SETFL, ::fcntl(fds[1], F_GETFL, 0) | O_NONBLOCK);

  g_pipe_read_fd = fds[0];
  g_pipe_write_fd = fds[1];

  // Thread before handlers: a signal arriving between the two would otherwise
  // write into a pipe nobody is reading yet.
  thread_ = std::thread([this] {run();});

  struct sigaction sa;
  sa.sa_handler = &odin1SignalTrampoline;
  sigemptyset(&sa.sa_mask);
  sa.sa_flags = SA_RESTART;   // do not turn every blocking syscall into EINTR
  ::sigaction(SIGINT, &sa, nullptr);
  ::sigaction(SIGTERM, &sa, nullptr);

  installed_ = true;
}

void SignalShutdownGuard::run()
{
  // Blocks here until the handler writes, or until the destructor closes the
  // write end (read() then returns 0 and the thread simply ends).
  unsigned char byte = 0;
  ssize_t n;
  do {
    n = ::read(g_pipe_read_fd, &byte, 1);
  } while (n < 0 && errno == EINTR);

  if (n <= 0) {
    return;
  }

  const int sig = static_cast<int>(g_signal_number);

  // ---- from here on we are on a NORMAL thread ----------------------------
  // Locks, condition variables, logging and the SDK are all legal again.

  if (drain_ && !drain_(grace_)) {
    // Could not drain: a worker is still inside an uninterruptible SDK call.
    // Do NOT run the vendor teardown - it would call lidar_stop_stream() and
    // lidar_system_deinit() underneath that worker.
    hardExit("signal received while an uninterruptible device operation was running");
  }

  if (deferred_) {
    deferred_(sig);
  }

  // The vendor's handler ends in exit(); if a future version stops doing that,
  // still honour the signal rather than leaving a half-torn-down process up.
  ::_exit(0);
}

void SignalShutdownGuard::uninstall()
{
  if (!installed_) {
    return;
  }
  struct sigaction sa;
  sa.sa_handler = SIG_DFL;
  sigemptyset(&sa.sa_mask);
  sa.sa_flags = 0;
  ::sigaction(SIGINT, &sa, nullptr);
  ::sigaction(SIGTERM, &sa, nullptr);
  installed_ = false;
}

SignalShutdownGuard::~SignalShutdownGuard()
{
  uninstall();

  // Closing the write end wakes a thread that is still waiting for a signal.
  const int w = static_cast<int>(g_pipe_write_fd);
  g_pipe_write_fd = -1;
  if (w >= 0) {
    ::close(w);
  }

  if (thread_.joinable()) {
    if (thread_.get_id() == std::this_thread::get_id()) {
      // We ARE the shutdown thread: the deferred teardown called exit(), which
      // is now destroying the statics that own us. Joining would deadlock on
      // ourselves.
      thread_.detach();
    } else {
      thread_.join();
    }
  }

  if (g_pipe_read_fd >= 0) {
    ::close(g_pipe_read_fd);
    g_pipe_read_fd = -1;
  }
  g_installed = false;
}

}  // namespace odin1_control
