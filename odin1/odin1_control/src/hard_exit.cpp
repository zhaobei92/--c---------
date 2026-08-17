// Copyright 2026 Odin1 integration contributors
// Licensed under the Apache License, Version 2.0

#include "odin1_control/hard_exit.hpp"

#include <cstddef>
#include <unistd.h>

namespace odin1_control
{

void hardExit(const char * why) noexcept
{
  // write(2) only. This can run from a signal handler while a worker thread is
  // inside the SDK and may be holding the ROS logger's mutex; touching the
  // logger here would deadlock the handler instead of exiting it.
  auto emit = [](const char * s) {
      if (!s) {return;}
      size_t n = 0;
      while (s[n] != '\0') {++n;}
      ssize_t written = 0;
      while (written < static_cast<ssize_t>(n)) {
        const ssize_t w = ::write(STDERR_FILENO, s + written, n - written);
        if (w <= 0) {break;}
        written += w;
      }
    };

  emit("\n[odin1_control] FATAL: ");
  emit(why);
  emit(
    "\n[odin1_control] An uninterruptible SDK call is still running. Skipping ALL "
    "teardown (no lidar_stop_stream, no lidar_system_deinit, no destructors) and "
    "exiting immediately: continuing would tear the SDK down underneath a live "
    "transfer.\n[odin1_control] The module may still be streaming; it re-enumerates "
    "on the next connect. Power-cycle it if the next start reports a busy device.\n");

  ::_exit(kUndrainableExitCode);
}

}  // namespace odin1_control
